import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from portfolio_news_agent.config import AppConfig


class CliTests(unittest.TestCase):
    def test_run_agent_help_exits_successfully(self):
        result = subprocess.run(
            [sys.executable, "run_agent.py", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--once", result.stdout)
        self.assertIn("--config", result.stdout)

    def test_preflight_prints_local_setup_status_without_loading_runtime_config(self):
        status = type(
            "Status",
            (),
            {
                "config_file_exists": True,
                "env_file_exists": True,
                "portfolio_file_exists": True,
                "gmail_credentials_exists": False,
                "gmail_token_exists": False,
                "openai_api_key_present": True,
                "telegram_bot_token_present": False,
                "telegram_chat_id_present": False,
                "ready_for_gmail_check": False,
                "ready_for_analyze_url": True,
                "ready_for_full_run": False,
                "blockers": [
                    "gmail_credentials_path",
                    "TELEGRAM_BOT_TOKEN",
                    "TELEGRAM_CHAT_ID",
                ],
            },
        )()

        with patch("portfolio_news_agent.cli.check_local_setup", return_value=status):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--preflight"])

        self.assertEqual(exit_code, 0)
        self.assertIn("analyze_url=ready", output.getvalue())
        self.assertIn("gmail_check=blocked", output.getvalue())
        self.assertIn("gmail_credentials_path", output.getvalue())

    def test_init_local_prints_created_and_existing_paths(self):
        result = type(
            "InitResult",
            (),
            {
                "created": ["config.yaml", ".env"],
                "existing": ["data/secrets"],
                "errors": [],
            },
        )()

        with patch("portfolio_news_agent.cli.initialize_local_setup", return_value=result):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--init-local"])

        self.assertEqual(exit_code, 0)
        self.assertIn("Local init", output.getvalue())
        self.assertIn("created: config.yaml, .env", output.getvalue())
        self.assertIn("existing: data/secrets", output.getvalue())

    def test_start_browser_loads_config_and_prints_endpoint(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/sa-browser-profile"),
            openai_model="gpt-5-nano",
            browser_channel="chrome",
            browser_cdp_url="http://127.0.0.1:9222",
        )
        result = type(
            "StartResult",
            (),
            {
                "status": "started",
                "cdp_url": "http://127.0.0.1:9222",
                "pid": 12345,
                "command": ["chrome", "--remote-debugging-port=9222"],
                "ready": True,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config) as load_config,
            patch("portfolio_news_agent.cli.start_debug_browser", return_value=result) as start,
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--start-browser"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(load_config.call_args.kwargs["require_openai"], False)
        start.assert_called_once()
        self.assertIn("Browser session: started", output.getvalue())
        self.assertIn("http://127.0.0.1:9222", output.getvalue())

    def test_start_browser_returns_error_when_endpoint_is_not_ready(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/sa-browser-profile"),
            openai_model="gpt-5-nano",
            browser_channel="chrome",
            browser_cdp_url="http://127.0.0.1:9222",
        )
        result = type(
            "StartResult",
            (),
            {
                "status": "started_unreachable",
                "cdp_url": "http://127.0.0.1:9222",
                "pid": 12345,
                "command": ["chrome", "--remote-debugging-port=9222"],
                "ready": False,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config),
            patch("portfolio_news_agent.cli.start_debug_browser", return_value=result),
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--start-browser"])

        self.assertEqual(exit_code, 2)
        self.assertIn("CDP endpoint ready: no", output.getvalue())

    def test_check_sa_browser_uses_cdp_article_browser(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/sa-browser-profile"),
            openai_model="gpt-5-nano",
            browser_cdp_url="http://127.0.0.1:9222",
        )
        result = type(
            "SaResult",
            (),
            {
                "url": "https://seekingalpha.com",
                "access_state": "accessible",
                "headline": None,
                "canonical_url": "https://seekingalpha.com",
                "body_characters": 200,
            },
        )()
        launch_result = type(
            "StartResult",
            (),
            {
                "status": "already_running",
                "cdp_url": "http://127.0.0.1:9222",
                "pid": None,
                "command": [],
                "ready": True,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config),
            patch("portfolio_news_agent.cli.start_debug_browser", return_value=launch_result) as start,
            patch("portfolio_news_agent.cli.CDPArticleBrowser", return_value="cdp-session") as cdp,
            patch("portfolio_news_agent.cli.check_seeking_alpha_session", return_value=result) as check,
            patch("portfolio_news_agent.cli.requeue_retryable_article_links_for_config", return_value=5) as requeue,
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--check-sa-browser"])

        self.assertEqual(exit_code, 0)
        start.assert_called_once()
        cdp.assert_called_once_with(cdp_url="http://127.0.0.1:9222")
        check.assert_called_once()
        requeue.assert_called_once_with(config)
        self.assertIn("Seeking Alpha session: state=accessible", output.getvalue())
        self.assertIn("Requeued 5 previously failed Seeking Alpha article link(s)", output.getvalue())

    def test_article_access_error_prints_without_traceback(self):
        from portfolio_news_agent.article_browser import ArticleAccessError

        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/sa-browser-profile"),
            openai_model="gpt-5-nano",
            browser_cdp_url="http://127.0.0.1:9222",
        )
        launch_result = type(
            "StartResult",
            (),
            {
                "status": "started_unreachable",
                "cdp_url": "http://127.0.0.1:9222",
                "pid": 12345,
                "command": ["chrome", "--remote-debugging-port=9222"],
                "ready": False,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config),
            patch("portfolio_news_agent.cli.start_debug_browser", return_value=launch_result),
            patch(
                "portfolio_news_agent.cli.check_seeking_alpha_session",
                side_effect=ArticleAccessError("CDP browser endpoint is not reachable"),
            ),
        ):
            from portfolio_news_agent.cli import main

            stderr = StringIO()
            with patch("sys.stderr", stderr):
                exit_code = main(["--check-sa-browser"])

        self.assertEqual(exit_code, 2)
        self.assertIn("Article access error: CDP browser endpoint is not reachable", stderr.getvalue())

    def test_check_gmail_prints_setup_error_instead_of_traceback(self):
        from portfolio_news_agent.gmail_api import GmailSetupError

        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
            openai_api_key="openai-key-from-dotenv",
        )

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config),
            patch(
                "portfolio_news_agent.cli.build_gmail_service",
                side_effect=GmailSetupError("missing gmail_credentials.json"),
            ),
        ):
            from portfolio_news_agent.cli import main

            stderr = StringIO()
            with patch("sys.stderr", stderr):
                exit_code = main(["--check-gmail"])

        self.assertEqual(exit_code, 2)
        self.assertIn("Gmail setup error: missing gmail_credentials.json", stderr.getvalue())

    def test_once_loads_config_and_runs_orchestrator(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = workspace / "config.yaml"
            env_path = workspace / ".env"
            config_path.write_text(
                "\n".join(
                    [
                        f'portfolio_file: "{workspace / "portfolio.csv"}"',
                        'gmail_sender: "account@seekingalpha.com"',
                        f'database_path: "{workspace / "data" / "portfolio_news.db"}"',
                        f'browser_profile_dir: "{workspace / "data" / "browser-profile"}"',
                        'openai_model: "gpt-5-nano"',
                    ]
                ),
                encoding="utf-8",
            )
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=test-openai-key",
                        "TELEGRAM_BOT_TOKEN=test-telegram-token",
                        "TELEGRAM_CHAT_ID=12345",
                    ]
                ),
                encoding="utf-8",
            )

            with (
                patch("portfolio_news_agent.cli._prepare_article_browser_for_run") as prepare_browser,
                patch("portfolio_news_agent.cli.build_default_dependencies") as build_deps,
                patch("portfolio_news_agent.cli.run_once") as run_once,
            ):
                run_once.return_value = type(
                    "Result",
                    (),
                    {
                        "status": "success",
                        "emails_found": 1,
                        "articles_processed": 1,
                        "summaries_created": 1,
                        "failed_links": 0,
                    },
                )()
                from portfolio_news_agent.cli import main

                output = StringIO()
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "--once",
                            "--config",
                            str(config_path),
                            "--env-file",
                            str(env_path),
                        ]
                    )

        self.assertEqual(exit_code, 0)
        self.assertIn("Run finished: status=success", output.getvalue())
        prepare_browser.assert_called_once()
        build_deps.assert_called_once()
        run_once.assert_called_once()

    def test_once_passes_email_and_article_limits_to_orchestrator(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
            openai_api_key="openai-key-from-dotenv",
            telegram_enabled=False,
        )
        result = type(
            "Result",
            (),
            {
                "status": "success",
                "emails_found": 50,
                "articles_processed": 50,
                "summaries_created": 3,
                "failed_links": 0,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config),
            patch("portfolio_news_agent.cli._prepare_article_browser_for_run"),
            patch("portfolio_news_agent.cli.build_default_dependencies"),
            patch("portfolio_news_agent.cli.run_once", return_value=result) as run_once,
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--once", "--max-emails", "50", "--max-articles", "50"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(run_once.call_args.kwargs["max_emails"], 50)
        self.assertEqual(run_once.call_args.kwargs["max_articles"], 50)
        self.assertIn("emails=50", output.getvalue())

    def test_prepare_article_browser_requires_manual_login_ack_in_user_chrome(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            browser_cdp_url="http://127.0.0.1:9222",
            browser_channel="chrome",
            openai_model="gpt-5-nano",
            openai_api_key="openai-key-from-dotenv",
            telegram_enabled=False,
        )
        browser_result = type(
            "BrowserResult",
            (),
            {
                "status": "already_running",
                "cdp_url": "http://127.0.0.1:9222",
                "command": [],
                "pid": None,
                "ready": True,
            },
        )()

        class FakeSession:
            def __init__(self):
                self.manual_calls = []

            def open_for_manual_session(self, url, *, prompt, prompt_message):
                self.manual_calls.append((url, prompt_message))
                prompt(prompt_message)
                return "<html><body>Logged in Seeking Alpha session</body></html>"

        fake_session = FakeSession()
        prompts = []

        with (
            patch("portfolio_news_agent.cli.start_debug_browser", return_value=browser_result) as start_browser,
            patch("portfolio_news_agent.cli.CDPArticleBrowser", return_value=fake_session) as cdp,
            patch("portfolio_news_agent.cli.requeue_retryable_article_links_for_config", return_value=7) as requeue,
        ):
            from portfolio_news_agent.cli import _prepare_article_browser_for_run

            output = StringIO()
            with redirect_stdout(output):
                _prepare_article_browser_for_run(config, prompt=prompts.append)

        start_browser.assert_called_once()
        cdp.assert_called_once_with(cdp_url="http://127.0.0.1:9222")
        self.assertEqual(fake_session.manual_calls[0][0], "https://seekingalpha.com")
        self.assertIn("Log in", fake_session.manual_calls[0][1])
        self.assertIn("same Chrome session", fake_session.manual_calls[0][1])
        self.assertEqual(prompts, [fake_session.manual_calls[0][1]])
        requeue.assert_called_once_with(config)
        self.assertIn("login acknowledged", output.getvalue())
        self.assertIn("Requeued 7 previously failed Seeking Alpha article link(s)", output.getvalue())

    def test_prepare_article_browser_blocks_if_acknowledged_session_is_still_challenged(self):
        from portfolio_news_agent.article_browser import ArticleAccessError

        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            browser_cdp_url="http://127.0.0.1:9222",
            browser_channel="chrome",
            openai_model="gpt-5-nano",
            openai_api_key="openai-key-from-dotenv",
            telegram_enabled=False,
        )
        browser_result = type(
            "BrowserResult",
            (),
            {
                "status": "already_running",
                "cdp_url": "http://127.0.0.1:9222",
                "command": [],
                "pid": None,
                "ready": True,
            },
        )()

        class FakeSession:
            def open_for_manual_session(self, url, *, prompt, prompt_message):
                prompt(prompt_message)
                return "<html>Access to this page has been denied</html>"

        with (
            patch("portfolio_news_agent.cli.start_debug_browser", return_value=browser_result),
            patch("portfolio_news_agent.cli.CDPArticleBrowser", return_value=FakeSession()),
        ):
            from portfolio_news_agent.cli import _prepare_article_browser_for_run

            with self.assertRaisesRegex(ArticleAccessError, "still shows challenge_required"):
                _prepare_article_browser_for_run(config, prompt=lambda message: None)

    def test_check_gmail_loads_config_without_telegram_and_prints_probe_summary(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
            openai_api_key="openai-key-from-dotenv",
        )
        result = type(
            "GmailResult",
            (),
            {
                "query": "from:account@seekingalpha.com is:unread",
                "emails_found": 1,
                "links_found": 1,
                "messages": [
                    type(
                        "Message",
                        (),
                        {
                            "gmail_message_id": "gmail-1",
                            "subject": "Portfolio update",
                            "sender": "account@seekingalpha.com",
                            "received_at": "2026-05-23T10:00:00+00:00",
                            "seeking_alpha_links": [
                                "https://seekingalpha.com/article/123-aem"
                            ],
                        },
                    )()
                ],
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config) as load_config,
            patch("portfolio_news_agent.cli.build_gmail_service", return_value="gmail-service"),
            patch("portfolio_news_agent.cli.GmailApiClient", return_value="gmail-client"),
            patch("portfolio_news_agent.cli.check_gmail_access", return_value=result),
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--check-gmail"])

        self.assertEqual(exit_code, 0)
        load_config.assert_called_once()
        self.assertEqual(load_config.call_args.kwargs["require_openai"], False)
        self.assertEqual(load_config.call_args.kwargs["require_telegram"], False)
        self.assertIn("Gmail check: unread_messages=1", output.getvalue())
        self.assertIn("Portfolio update", output.getvalue())

    def test_open_sa_loads_config_without_secrets_and_prints_access_state(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
        )
        result = type(
            "SaResult",
            (),
            {
                "url": "https://seekingalpha.com",
                "access_state": "accessible",
                "headline": None,
                "canonical_url": "https://seekingalpha.com",
                "body_characters": 200,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config) as load_config,
            patch("portfolio_news_agent.cli.PlaywrightArticleBrowser"),
            patch("portfolio_news_agent.cli.check_seeking_alpha_session", return_value=result),
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["--open-sa"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(load_config.call_args.kwargs["require_openai"], False)
        self.assertEqual(load_config.call_args.kwargs["require_telegram"], False)
        self.assertIn("Seeking Alpha session: state=accessible", output.getvalue())

    def test_analyze_url_loads_config_without_telegram_and_prints_relevant_assets(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
            openai_api_key="openai-key-from-dotenv",
        )
        result = type(
            "AnalyzeResult",
            (),
            {
                "url": "https://seekingalpha.com/article/123-aem",
                "canonical_url": "https://seekingalpha.com/article/123-aem",
                "headline": "AEM update",
                "body_characters": 2500,
                "assets_loaded": 1,
                "relevant_assets": [
                    {
                        "symbol": "AEM",
                        "inferred_sentiment": "bullish",
                        "action_relevance": "material_news",
                        "short_summary": "Margins improved.",
                    }
                ],
                "irrelevant_reason": None,
            },
        )()

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config) as load_config,
            patch("portfolio_news_agent.cli.PlaywrightArticleBrowser"),
            patch("portfolio_news_agent.cli.OpenAIResponsesClient") as openai_client,
            patch("portfolio_news_agent.cli.analyze_url_against_portfolio", return_value=result),
        ):
            from portfolio_news_agent.cli import main

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["--analyze-url", "https://seekingalpha.com/article/123-aem"]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(load_config.call_args.kwargs["require_openai"], True)
        self.assertEqual(load_config.call_args.kwargs["require_telegram"], False)
        openai_client.assert_called_once_with(api_key="openai-key-from-dotenv")
        self.assertIn("Analyze URL: headline=AEM update", output.getvalue())
        self.assertIn("AEM | bullish | material_news", output.getvalue())

    def test_analyze_url_prints_llm_error_without_traceback(self):
        from portfolio_news_agent.openai_analyzer import LLMAnalysisError

        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
        )

        with (
            patch("portfolio_news_agent.cli.load_config", return_value=config),
            patch("portfolio_news_agent.cli.PlaywrightArticleBrowser"),
            patch("portfolio_news_agent.cli.OpenAIResponsesClient"),
            patch(
                "portfolio_news_agent.cli.analyze_url_against_portfolio",
                side_effect=LLMAnalysisError("OpenAI request failed: 401 invalid_issuer"),
            ),
        ):
            from portfolio_news_agent.cli import main

            stderr = StringIO()
            with patch("sys.stderr", stderr):
                exit_code = main(
                    ["--analyze-url", "https://seekingalpha.com/article/123-aem"]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("LLM analysis error: OpenAI request failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
