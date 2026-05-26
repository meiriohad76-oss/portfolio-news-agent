from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from portfolio_news_agent.article_browser import (
    BrowserSession,
    detect_access_state,
    extract_article_from_html,
    fetch_article_with_session,
)
from portfolio_news_agent.commodity_mapper import build_commodity_exposures
from portfolio_news_agent.config import AppConfig
from portfolio_news_agent.gmail_scanner import (
    GmailClient,
    GmailMessageSummary,
    build_gmail_query,
    summarize_gmail_message,
)
from portfolio_news_agent.openai_analyzer import AnalysisClient, analyze_article
from portfolio_news_agent.portfolio_importer import import_portfolio_file
from portfolio_news_agent.storage import get_assets_for_import, migrate


DEFAULT_SEEKING_ALPHA_URL = "https://seekingalpha.com"
SEEKING_ALPHA_MANUAL_PROMPT = (
    "Use the opened Seeking Alpha browser window to log in or complete any "
    "access challenge, then press Enter here to continue."
)


@dataclass(frozen=True)
class GmailAccessResult:
    query: str
    emails_found: int
    links_found: int
    messages: list[GmailMessageSummary]


@dataclass(frozen=True)
class SeekingAlphaSessionResult:
    url: str
    access_state: str
    canonical_url: str
    headline: str | None
    body_characters: int


@dataclass(frozen=True)
class ArticleAnalysisProbeResult:
    url: str
    canonical_url: str
    headline: str | None
    body_characters: int
    assets_loaded: int
    relevant_assets: list[dict]
    irrelevant_reason: str | None


def check_gmail_access(
    *,
    config: AppConfig,
    gmail_client: GmailClient,
    limit: int = 5,
) -> GmailAccessResult:
    query = build_gmail_query(config.gmail_sender)
    message_refs = gmail_client.search_messages(query)
    messages = [
        summarize_gmail_message(gmail_client.get_message(str(message_ref["id"])))
        for message_ref in message_refs[:limit]
    ]
    return GmailAccessResult(
        query=query,
        emails_found=len(message_refs),
        links_found=sum(len(message.seeking_alpha_links) for message in messages),
        messages=messages,
    )


def check_seeking_alpha_session(
    url: str = DEFAULT_SEEKING_ALPHA_URL,
    *,
    session: BrowserSession,
    prompt: Callable[[str], None] = input,
) -> SeekingAlphaSessionResult:
    html = session.open(url)
    access_state = detect_access_state(html)
    manual_open = getattr(session, "open_for_manual_session", None)
    if access_state != "accessible" and callable(manual_open):
        html = manual_open(
            url,
            prompt=prompt,
            prompt_message=SEEKING_ALPHA_MANUAL_PROMPT,
        )
        access_state = detect_access_state(html)

    if access_state != "accessible":
        return SeekingAlphaSessionResult(
            url=url,
            access_state=access_state,
            canonical_url=url,
            headline=None,
            body_characters=0,
        )

    article = extract_article_from_html(html, source_url=url)
    return SeekingAlphaSessionResult(
        url=url,
        access_state=access_state,
        canonical_url=article.canonical_url,
        headline=article.headline,
        body_characters=len(article.body_text),
    )


def analyze_url_against_portfolio(
    *,
    config: AppConfig,
    url: str,
    article_session: BrowserSession,
    analysis_client: AnalysisClient,
    connection: sqlite3.Connection | None = None,
    prompt: Callable[[str], None] = input,
) -> ArticleAnalysisProbeResult:
    owns_connection = connection is None
    if connection is None:
        connection = sqlite3.connect(":memory:")

    try:
        migrate(connection)
        portfolio_import = import_portfolio_file(connection, config.portfolio_file)
        assets = get_assets_for_import(connection, portfolio_import.import_id)
        commodity_exposures = build_commodity_exposures(
            connection,
            portfolio_import.import_id,
            overrides=config.commodity_exposure_overrides,
        )
        article = fetch_article_with_session(url, session=article_session, prompt=prompt)
        analysis = analyze_article(
            client=analysis_client,
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
        return ArticleAnalysisProbeResult(
            url=url,
            canonical_url=article.canonical_url,
            headline=article.headline,
            body_characters=len(article.body_text),
            assets_loaded=len(assets),
            relevant_assets=analysis["relevant_assets"],
            irrelevant_reason=analysis.get("irrelevant_reason"),
        )
    finally:
        if owns_connection:
            connection.close()
