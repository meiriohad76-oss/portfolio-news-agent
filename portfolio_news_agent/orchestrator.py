from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from portfolio_news_agent.article_browser import (
    ArticleAccessError,
    BrowserSession,
    CDPArticleBrowser,
    PlaywrightArticleBrowser,
    fetch_article_with_session,
)
from portfolio_news_agent.commodity_mapper import build_commodity_exposures
from portfolio_news_agent.config import AppConfig
from portfolio_news_agent.gmail_api import GmailApiClient, build_gmail_service
from portfolio_news_agent.gmail_mark_read import GmailActions, mark_message_read_if_complete
from portfolio_news_agent.gmail_scanner import GmailClient, scan_unread_seeking_alpha_messages
from portfolio_news_agent.openai_analyzer import (
    AnalysisClient,
    LLMAnalysisError,
    OpenAIResponsesClient,
    analyze_article,
)
from portfolio_news_agent.portfolio_importer import import_portfolio_file
from portfolio_news_agent.storage import (
    connect_database,
    finish_run,
    get_assets_for_import,
    get_queued_article_links,
    insert_article_asset_summary,
    start_run,
    update_gmail_article_link_status,
    upsert_article,
)
from portfolio_news_agent.telegram_sender import (
    TelegramConfigError,
    TelegramSendError,
    format_telegram_message,
    send_telegram_message,
)


TelegramSender = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class OrchestratorDependencies:
    gmail_client: GmailClient
    gmail_actions: GmailActions
    article_session: BrowserSession
    analysis_client: AnalysisClient
    telegram_sender: TelegramSender = send_telegram_message


@dataclass(frozen=True)
class RunOnceResult:
    status: str
    emails_found: int
    articles_processed: int
    summaries_created: int
    failed_links: int


def build_default_dependencies(config: AppConfig) -> OrchestratorDependencies:
    gmail_service = build_gmail_service(
        credentials_path=config.gmail_credentials_path,
        token_path=config.gmail_token_path,
    )
    gmail_client = GmailApiClient(gmail_service)
    return OrchestratorDependencies(
        gmail_client=gmail_client,
        gmail_actions=gmail_client,
        article_session=build_article_session(config),
        analysis_client=OpenAIResponsesClient(api_key=config.openai_api_key),
    )


def build_article_session(config: AppConfig) -> BrowserSession:
    if config.browser_cdp_url:
        return CDPArticleBrowser(cdp_url=config.browser_cdp_url)
    return PlaywrightArticleBrowser(
        profile_dir=config.browser_profile_dir,
        browser_channel=config.browser_channel,
    )


def run_once(
    *,
    config: AppConfig,
    dependencies: OrchestratorDependencies,
    connection: sqlite3.Connection | None = None,
    max_emails: int | None = None,
    max_articles: int | None = None,
) -> RunOnceResult:
    owns_connection = connection is None
    if connection is None:
        connection = connect_database(config.database_path)

    run_id: int | None = None
    try:
        run_id = start_run(connection, mode="once")
        portfolio_import = import_portfolio_file(connection, config.portfolio_file)
        assets = get_assets_for_import(connection, portfolio_import.import_id)
        commodity_exposures = build_commodity_exposures(
            connection,
            portfolio_import.import_id,
            overrides=config.commodity_exposure_overrides,
        )
        scan_result = scan_unread_seeking_alpha_messages(
            connection,
            gmail_client=dependencies.gmail_client,
            sender=config.gmail_sender,
            portfolio_import_id=portfolio_import.import_id,
            prompt_version=config.prompt_version,
            max_emails=max_emails,
        )

        articles_processed = 0
        summaries_created = 0
        failed_links = 0
        touched_message_ids: set[int] = set()

        for link in get_queued_article_links(connection, limit=max_articles):
            touched_message_ids.add(int(link["gmail_message_id"]))
            try:
                update_gmail_article_link_status(
                    connection,
                    link_id=int(link["id"]),
                    status="processing",
                    status_detail="Opening article and running LLM analysis",
                )
                created = _process_link(
                    connection=connection,
                    config=config,
                    dependencies=dependencies,
                    link=link,
                    assets=assets,
                    commodity_exposures=commodity_exposures,
                )
                articles_processed += 1
                summaries_created += created
            except ArticleAccessError as exc:
                failed_links += 1
                update_gmail_article_link_status(
                    connection,
                    link_id=int(link["id"]),
                    status="failed_access",
                    status_detail=str(exc),
                )
            except LLMAnalysisError as exc:
                failed_links += 1
                update_gmail_article_link_status(
                    connection,
                    link_id=int(link["id"]),
                    status="failed_llm",
                    status_detail=str(exc),
                )
            except (TelegramConfigError, TelegramSendError) as exc:
                failed_links += 1
                update_gmail_article_link_status(
                    connection,
                    link_id=int(link["id"]),
                    status="failed_telegram",
                    status_detail=str(exc),
                )
            except Exception as exc:
                failed_links += 1
                update_gmail_article_link_status(
                    connection,
                    link_id=int(link["id"]),
                    status="failed_extract",
                    status_detail=str(exc),
                )

        for gmail_message_id in touched_message_ids:
            mark_message_read_if_complete(connection, gmail_message_id, dependencies.gmail_actions)

        status = "partial_failed" if failed_links else "success"
        finish_run(
            connection,
            run_id=run_id,
            status=status,
            emails_found=scan_result.emails_found,
            articles_processed=articles_processed,
            summaries_created=summaries_created,
            error=None if not failed_links else f"{failed_links} link(s) failed",
        )
        return RunOnceResult(
            status=status,
            emails_found=scan_result.emails_found,
            articles_processed=articles_processed,
            summaries_created=summaries_created,
            failed_links=failed_links,
        )
    except Exception as exc:
        if run_id is not None:
            finish_run(
                connection,
                run_id=run_id,
                status="failed",
                error=str(exc),
            )
        raise
    finally:
        if owns_connection:
            connection.close()


def _process_link(
    *,
    connection: sqlite3.Connection,
    config: AppConfig,
    dependencies: OrchestratorDependencies,
    link: dict[str, Any],
    assets: list[dict[str, Any]],
    commodity_exposures: dict[str, Any],
) -> int:
    article = fetch_article_with_session(
        str(link["source_url"]),
        session=dependencies.article_session,
        allow_manual_recovery=False,
    )
    article_id = upsert_article(
        connection,
        canonical_url=article.canonical_url,
        source_url=article.source_url,
        headline=article.headline,
        author=article.author,
        article_date=article.article_date,
    )
    analysis = analyze_article(
        client=dependencies.analysis_client,
        model=config.openai_model,
        article={
            "headline": article.headline,
            "author": article.author,
            "article_date": article.article_date,
            "source_url": article.source_url,
            "body_text": article.body_text,
        },
        portfolio_assets=assets,
        commodity_exposures=commodity_exposures,
        prompt_version=config.prompt_version,
    )
    relevant_assets = analysis["relevant_assets"]
    if not relevant_assets:
        update_gmail_article_link_status(
            connection,
            link_id=int(link["id"]),
            status="irrelevant_seen",
            status_detail=analysis.get("irrelevant_reason"),
        )
        return 0

    assets_by_symbol = {asset["symbol"]: asset for asset in assets}
    summaries_created = 0
    for summary in relevant_assets:
        symbol = summary["symbol"]
        asset = assets_by_symbol.get(symbol, {})
        summary_id = insert_article_asset_summary(
            connection,
            article_id=article_id,
            gmail_message_id=int(link["gmail_message_id"]),
            gmail_article_link_id=int(link["id"]),
            portfolio_import_id=int(link["portfolio_import_id"]),
            asset_id=asset.get("id"),
            symbol=symbol,
            company_name=summary.get("company_name"),
            author_rating=summary.get("author_rating"),
            quant_rating=summary.get("quant_rating"),
            wall_street_rating=summary.get("wall_street_rating"),
            inferred_sentiment=summary["inferred_sentiment"],
            theme=summary.get("theme"),
            price_targets_json=json.dumps(summary.get("price_targets", [])),
            forward_data_json=json.dumps(summary.get("forward_data", [])),
            action_relevance=summary["action_relevance"],
            short_summary=summary["short_summary"],
            confidence=summary.get("confidence"),
            llm_model=config.openai_model,
            prompt_version=config.prompt_version,
        )
        telegram_payload = {
            **summary,
            "headline": article.headline,
            "source_url": article.source_url,
            "price_targets_json": json.dumps(summary.get("price_targets", [])),
            "forward_data_json": json.dumps(summary.get("forward_data", [])),
        }
        if config.telegram_enabled:
            dependencies.telegram_sender(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                text=format_telegram_message(telegram_payload),
            )
        if summary_id:
            summaries_created += 1

    update_gmail_article_link_status(
        connection,
        link_id=int(link["id"]),
        status="processed_relevant",
        status_detail=None,
    )
    return summaries_created
