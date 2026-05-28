from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_news_agent.article_browser import (
    ArticleAccessError,
    CDPArticleBrowser,
    PlaywrightArticleBrowser,
)
from portfolio_news_agent.browser_launcher import start_debug_browser
from portfolio_news_agent.capability_checks import (
    DEFAULT_SEEKING_ALPHA_URL,
    analyze_url_against_portfolio,
    check_gmail_access,
    check_seeking_alpha_session,
)
from portfolio_news_agent.config import ConfigError, load_config
from portfolio_news_agent.gmail_api import GmailApiClient, GmailSetupError, build_gmail_service
from portfolio_news_agent.local_setup import initialize_local_setup
from portfolio_news_agent.logging_setup import configure_logging
from portfolio_news_agent.openai_analyzer import OpenAIResponsesClient
from portfolio_news_agent.openai_analyzer import LLMAnalysisError
from portfolio_news_agent.orchestrator import build_default_dependencies, run_once
from portfolio_news_agent.preflight import check_local_setup
from portfolio_news_agent.storage import connect_database, requeue_retryable_article_links


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_agent.py",
        description="Run the Portfolio News Agent.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run one manual processing pass.",
    )
    mode.add_argument(
        "--check-gmail",
        action="store_true",
        help="Check Gmail OAuth/API access and preview unread Seeking Alpha messages.",
    )
    mode.add_argument(
        "--open-sa",
        nargs="?",
        const=DEFAULT_SEEKING_ALPHA_URL,
        metavar="URL",
        help="Open Seeking Alpha with the persistent browser profile and report access state.",
    )
    mode.add_argument(
        "--analyze-url",
        metavar="URL",
        help="Open one Seeking Alpha article and analyze it against the portfolio.",
    )
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="Check local setup readiness without contacting external services.",
    )
    mode.add_argument(
        "--init-local",
        action="store_true",
        help="Create safe local config/env scaffolding and data directories.",
    )
    mode.add_argument(
        "--start-browser",
        action="store_true",
        help="Start the dedicated Chrome/Edge browser session for Seeking Alpha.",
    )
    mode.add_argument(
        "--check-sa-browser",
        nargs="?",
        const=DEFAULT_SEEKING_ALPHA_URL,
        metavar="URL",
        help="Check Seeking Alpha access through the running CDP browser session.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the dotenv secrets file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not any(
        (
            args.once,
            args.check_gmail,
            args.open_sa,
            args.analyze_url,
            args.preflight,
            args.init_local,
            args.start_browser,
            args.check_sa_browser,
        )
    ):
        parser.print_help()
        return 0

    try:
        if args.init_local:
            result = initialize_local_setup(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
            )
            _print_local_init(result)
            return 0

        if args.preflight:
            status = check_local_setup(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
            )
            _print_preflight(status)
            return 0

        if args.start_browser:
            config = load_config(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
                require_openai=False,
                require_telegram=False,
            )
            result = start_debug_browser(
                profile_dir=config.browser_profile_dir,
                cdp_url=_required_cdp_url(config),
                browser_channel=config.browser_channel or "chrome",
            )
            _print_browser_launch(result)
            return 0 if result.ready else 2

        if args.check_sa_browser:
            config = load_config(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
                require_openai=False,
                require_telegram=False,
            )
            cdp_url = _ensure_cdp_browser_started(config)
            result = check_seeking_alpha_session(
                args.check_sa_browser,
                session=CDPArticleBrowser(cdp_url=cdp_url),
            )
            _print_seeking_alpha_check(result)
            if result.access_state == "accessible":
                _print_requeued_links(requeue_retryable_article_links_for_config(config))
            return 0

        if args.check_gmail:
            config = load_config(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
                require_openai=False,
                require_telegram=False,
            )
            gmail_service = build_gmail_service(
                credentials_path=config.gmail_credentials_path,
                token_path=config.gmail_token_path,
            )
            result = check_gmail_access(
                config=config,
                gmail_client=GmailApiClient(gmail_service),
            )
            _print_gmail_check(result)
            return 0

        if args.open_sa:
            config = load_config(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
                require_openai=False,
                require_telegram=False,
            )
            result = check_seeking_alpha_session(
                args.open_sa,
                session=_article_session_for_config(config),
            )
            _print_seeking_alpha_check(result)
            return 0

        if args.analyze_url:
            config = load_config(
                config_path=Path(args.config),
                env_path=Path(args.env_file),
                require_openai=True,
                require_telegram=False,
            )
            result = analyze_url_against_portfolio(
                config=config,
                url=args.analyze_url,
                article_session=_article_session_for_config(config),
                analysis_client=OpenAIResponsesClient(api_key=config.openai_api_key),
            )
            _print_analyze_url_result(result)
            return 0

        config = load_config(config_path=Path(args.config), env_path=Path(args.env_file))
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except GmailSetupError as exc:
        print(f"Gmail setup error: {exc}", file=sys.stderr)
        return 2
    except ArticleAccessError as exc:
        print(f"Article access error: {exc}", file=sys.stderr)
        return 2
    except LLMAnalysisError as exc:
        print(f"LLM analysis error: {exc}", file=sys.stderr)
        return 2

    try:
        _prepare_article_browser_for_run(config)
        result = run_once(
            config=config,
            dependencies=build_default_dependencies(config),
        )
    except GmailSetupError as exc:
        print(f"Gmail setup error: {exc}", file=sys.stderr)
        return 2
    print(
        "Run finished: "
        f"status={result.status}, "
        f"emails={result.emails_found}, "
        f"articles={result.articles_processed}, "
        f"summaries={result.summaries_created}, "
        f"failed_links={result.failed_links}"
    )
    return 0


def _prepare_article_browser_for_run(config) -> None:
    if not config.browser_cdp_url:
        return
    cdp_url = _ensure_cdp_browser_started(config)
    result = check_seeking_alpha_session(
        DEFAULT_SEEKING_ALPHA_URL,
        session=CDPArticleBrowser(cdp_url=cdp_url),
        allow_manual_recovery=False,
    )
    _print_seeking_alpha_check(result)
    if result.access_state != "accessible":
        raise ArticleAccessError(
            "Seeking Alpha is not accessible in the dedicated browser session. "
            "Complete login/challenge in the opened browser, then rerun --once."
        )
    requeued = requeue_retryable_article_links_for_config(config)
    _print_requeued_links(requeued)


def requeue_retryable_article_links_for_config(config) -> int:
    connection = connect_database(config.database_path)
    try:
        return requeue_retryable_article_links(connection)
    finally:
        connection.close()


def _print_requeued_links(count: int) -> None:
    if count:
        print(f"Requeued {count} previously failed Seeking Alpha article link(s).")


def _print_gmail_check(result) -> None:
    print(
        "Gmail check: "
        f"unread_messages={result.emails_found}, "
        f"links_in_preview={result.links_found}, "
        f"query={result.query!r}"
    )
    for message in result.messages:
        print(
            f"- {message.gmail_message_id} | "
            f"{message.subject or '(no subject)'} | "
            f"links={len(message.seeking_alpha_links)}"
        )
        for link in message.seeking_alpha_links:
            print(f"  {link}")


def _print_preflight(status) -> None:
    print(
        "Preflight: "
        f"gmail_check={_ready_label(status.ready_for_gmail_check)}, "
        f"analyze_url={_ready_label(status.ready_for_analyze_url)}, "
        f"full_run={_ready_label(status.ready_for_full_run)}"
    )
    print(f"- config.yaml: {_exists_label(status.config_file_exists)}")
    print(f"- .env: {_exists_label(status.env_file_exists)}")
    print(f"- portfolio_file: {_exists_label(status.portfolio_file_exists)}")
    print(f"- gmail_credentials.json: {_exists_label(status.gmail_credentials_exists)}")
    print(f"- gmail_token.json: {_exists_label(status.gmail_token_exists)}")
    print(f"- OPENAI_API_KEY: {_present_label(status.openai_api_key_present)}")
    print(f"- TELEGRAM_BOT_TOKEN: {_present_label(status.telegram_bot_token_present)}")
    print(f"- TELEGRAM_CHAT_ID: {_present_label(status.telegram_chat_id_present)}")
    config_error = getattr(status, "config_error", None)
    if config_error:
        print(f"Config error: {config_error}")
    if status.blockers:
        print("Blockers: " + ", ".join(status.blockers))
    else:
        print("Blockers: none")


def _print_local_init(result) -> None:
    print("Local init")
    print(f"- created: {_joined_or_none(result.created)}")
    print(f"- existing: {_joined_or_none(result.existing)}")
    print(f"- errors: {_joined_or_none(result.errors)}")


def _print_browser_launch(result) -> None:
    print(
        "Browser session: "
        f"{result.status}, endpoint={result.cdp_url}, pid={result.pid or '(existing)'}"
    )
    ready = getattr(result, "ready", None)
    if ready is not None:
        print(f"CDP endpoint ready: {'yes' if ready else 'no'}")
    if result.command:
        print("Command: " + " ".join(result.command))


def _required_cdp_url(config) -> str:
    if not config.browser_cdp_url:
        raise ConfigError("browser_cdp_url is required for CDP browser commands")
    return config.browser_cdp_url


def _ensure_cdp_browser_started(config) -> str:
    cdp_url = _required_cdp_url(config)
    result = start_debug_browser(
        profile_dir=config.browser_profile_dir,
        cdp_url=cdp_url,
        browser_channel=config.browser_channel or "chrome",
    )
    if result.status != "already_running" or not result.ready:
        _print_browser_launch(result)
    if not result.ready:
        raise ArticleAccessError(
            f"CDP browser endpoint is not reachable at {cdp_url}. "
            "Run .\\.venv\\Scripts\\python run_agent.py --start-browser and keep that browser open."
        )
    return cdp_url


def _article_session_for_config(config):
    if config.browser_cdp_url:
        return CDPArticleBrowser(cdp_url=config.browser_cdp_url)
    return PlaywrightArticleBrowser(
        profile_dir=config.browser_profile_dir,
        browser_channel=config.browser_channel,
    )


def _joined_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _ready_label(value: bool) -> str:
    return "ready" if value else "blocked"


def _exists_label(value: bool) -> str:
    return "exists" if value else "missing"


def _present_label(value: bool) -> str:
    return "present" if value else "missing"


def _print_seeking_alpha_check(result) -> None:
    print(
        "Seeking Alpha session: "
        f"state={result.access_state}, "
        f"headline={result.headline or '(none)'}, "
        f"body_chars={result.body_characters}"
    )
    print(f"Canonical URL: {result.canonical_url}")


def _print_analyze_url_result(result) -> None:
    print(f"Analyze URL: headline={result.headline or '(none)'}")
    print(f"Canonical URL: {result.canonical_url}")
    print(
        f"Portfolio assets loaded: {result.assets_loaded}; "
        f"article_chars={result.body_characters}; "
        f"relevant_assets={len(result.relevant_assets)}"
    )
    if result.relevant_assets:
        for asset in result.relevant_assets:
            print(
                f"- {asset['symbol']} | "
                f"{asset['inferred_sentiment']} | "
                f"{asset['action_relevance']} | "
                f"{asset['short_summary']}"
            )
    else:
        print(f"Irrelevant reason: {result.irrelevant_reason or '(none)'}")
