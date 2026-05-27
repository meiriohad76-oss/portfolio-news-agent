import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from portfolio_news_agent.preflight import check_local_setup


@contextmanager
def isolated_preflight_environment():
    names = {"OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    old_values = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in old_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class PreflightTests(unittest.TestCase):
    def test_check_local_setup_reports_ready_capabilities_without_requiring_token(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            portfolio_path.write_text("Symbol,Name\nAEM,Agnico Eagle Mines\n", encoding="utf-8")
            env_path = workspace / ".env"
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
            credentials_path = workspace / "data" / "secrets" / "gmail_credentials.json"
            config_path = workspace / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        f'portfolio_file: "{portfolio_path}"',
                        'gmail_sender: "account@seekingalpha.com"',
                        f'database_path: "{workspace / "data" / "portfolio_news.db"}"',
                        f'browser_profile_dir: "{workspace / "data" / "browser-profile"}"',
                        f'gmail_credentials_path: "{credentials_path}"',
                        f'gmail_token_path: "{workspace / "data" / "secrets" / "gmail_token.json"}"',
                        'openai_model: "gpt-5-nano"',
                    ]
                ),
                encoding="utf-8",
            )
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")

            with isolated_preflight_environment():
                status = check_local_setup(config_path=config_path, env_path=env_path)

        self.assertTrue(status.config_file_exists)
        self.assertTrue(status.env_file_exists)
        self.assertTrue(status.portfolio_file_exists)
        self.assertTrue(status.gmail_credentials_exists)
        self.assertFalse(status.gmail_token_exists)
        self.assertTrue(status.openai_api_key_present)
        self.assertTrue(status.telegram_bot_token_present)
        self.assertTrue(status.telegram_chat_id_present)
        self.assertTrue(status.ready_for_gmail_check)
        self.assertTrue(status.ready_for_analyze_url)
        self.assertTrue(status.ready_for_full_run)
        self.assertEqual(status.blockers, [])

    def test_check_local_setup_reports_missing_external_files_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = workspace / "config.yaml"
            env_path = workspace / ".env"
            config_path.write_text(
                "\n".join(
                    [
                        f'portfolio_file: "{workspace / "missing.csv"}"',
                        'gmail_sender: "account@seekingalpha.com"',
                        f'database_path: "{workspace / "data" / "portfolio_news.db"}"',
                        f'browser_profile_dir: "{workspace / "data" / "browser-profile"}"',
                        f'gmail_credentials_path: "{workspace / "data" / "secrets" / "gmail_credentials.json"}"',
                        f'gmail_token_path: "{workspace / "data" / "secrets" / "gmail_token.json"}"',
                        'openai_model: "gpt-5-nano"',
                    ]
                ),
                encoding="utf-8",
            )

            with isolated_preflight_environment():
                status = check_local_setup(config_path=config_path, env_path=env_path)

        self.assertFalse(status.env_file_exists)
        self.assertFalse(status.portfolio_file_exists)
        self.assertFalse(status.gmail_credentials_exists)
        self.assertFalse(status.openai_api_key_present)
        self.assertFalse(status.ready_for_gmail_check)
        self.assertFalse(status.ready_for_analyze_url)
        self.assertFalse(status.ready_for_full_run)
        self.assertIn("portfolio_file", status.blockers)
        self.assertIn("gmail_credentials_path", status.blockers)
        self.assertIn("OPENAI_API_KEY", status.blockers)
        self.assertIn("TELEGRAM_BOT_TOKEN", status.blockers)
        self.assertIn("TELEGRAM_CHAT_ID", status.blockers)

    def test_check_local_setup_does_not_require_telegram_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            portfolio_path.write_text("Symbol,Name\nAAPL,Apple\n", encoding="utf-8")
            env_path = workspace / ".env"
            env_path.write_text("OPENAI_API_KEY=test-openai-key", encoding="utf-8")
            credentials_path = workspace / "data" / "secrets" / "gmail_credentials.json"
            config_path = workspace / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        f'portfolio_file: "{portfolio_path}"',
                        'gmail_sender: "account@seekingalpha.com"',
                        f'database_path: "{workspace / "data" / "portfolio_news.db"}"',
                        f'browser_profile_dir: "{workspace / "data" / "browser-profile"}"',
                        f'gmail_credentials_path: "{credentials_path}"',
                        f'gmail_token_path: "{workspace / "data" / "secrets" / "gmail_token.json"}"',
                        'openai_model: "gpt-5-nano"',
                        "telegram_enabled: false",
                    ]
                ),
                encoding="utf-8",
            )
            credentials_path.parent.mkdir(parents=True)
            credentials_path.write_text('{"installed": {}}', encoding="utf-8")

            with isolated_preflight_environment():
                status = check_local_setup(config_path=config_path, env_path=env_path)

        self.assertTrue(status.ready_for_full_run)
        self.assertEqual(status.blockers, [])
        self.assertFalse(status.telegram_bot_token_present)
        self.assertFalse(status.telegram_chat_id_present)


if __name__ == "__main__":
    unittest.main()
