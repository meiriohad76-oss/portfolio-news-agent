import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.local_setup import initialize_local_setup


class LocalSetupTests(unittest.TestCase):
    def test_initialize_local_setup_creates_templates_and_configured_directories(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_example = workspace / "config.example.yaml"
            env_example = workspace / ".env.example"
            config_path = workspace / "config.yaml"
            env_path = workspace / ".env"
            config_example.write_text(
                "\n".join(
                    [
                        f'portfolio_file: "{workspace / "portfolio.csv"}"',
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
            env_example.write_text("OPENAI_API_KEY=\n", encoding="utf-8")

            result = initialize_local_setup(
                config_path=config_path,
                env_path=env_path,
                config_example_path=config_example,
                env_example_path=env_example,
            )

            self.assertTrue(config_path.exists())
            self.assertTrue(env_path.exists())
            self.assertTrue((workspace / "data" / "secrets").is_dir())
            self.assertTrue((workspace / "data" / "browser-profile").is_dir())
            self.assertIn(str(config_path), result.created)
            self.assertIn(str(env_path), result.created)
            self.assertEqual(
                (result.created + result.existing).count(
                    str(workspace / "data" / "secrets")
                ),
                1,
            )

    def test_initialize_local_setup_does_not_overwrite_existing_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = workspace / "config.yaml"
            env_path = workspace / ".env"
            env_example = workspace / ".env.example"
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
            env_path.write_text("OPENAI_API_KEY=keep-me\n", encoding="utf-8")
            env_example.write_text("OPENAI_API_KEY=\n", encoding="utf-8")

            result = initialize_local_setup(
                config_path=config_path,
                env_path=env_path,
                env_example_path=env_example,
            )

            self.assertEqual(env_path.read_text(encoding="utf-8"), "OPENAI_API_KEY=keep-me\n")
            self.assertIn(str(env_path), result.existing)


if __name__ == "__main__":
    unittest.main()
