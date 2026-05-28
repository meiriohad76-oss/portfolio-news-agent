import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from portfolio_news_agent.config import ConfigError, load_config


@contextmanager
def isolated_secret_environment(**overrides):
    names = {
        "OPENAI_API_KEY",
        "AGENCY_LOCAL_LLM_BASE_URL",
        "AGENCY_LOCAL_LLM_MODEL",
        *overrides.keys(),
    }
    old_values = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, value in old_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class ConfigLoadingTests(unittest.TestCase):
    def test_loads_yaml_and_env_and_creates_data_directories(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            config_path = workspace / "config.yaml"
            env_path = workspace / ".env"
            database_path = workspace / "data" / "portfolio_news.db"
            browser_profile_dir = workspace / "data" / "browser-profile"

            config_path.write_text(
                "\n".join(
                    [
                        f'portfolio_file: "{workspace / "portfolio.csv"}"',
                        'gmail_sender: "account@seekingalpha.com"',
                        f'database_path: "{database_path}"',
                        f'browser_profile_dir: "{browser_profile_dir}"',
                        'browser_cdp_url: "http://127.0.0.1:9222"',
                        f'gmail_credentials_path: "{workspace / "data" / "secrets" / "gmail_credentials.json"}"',
                        f'gmail_token_path: "{workspace / "data" / "secrets" / "gmail_token.json"}"',
                        'openai_model: "gpt-5-nano"',
                        'browser_channel: "chrome"',
                        'prompt_version: "v1"',
                        "mark_relevant_as_read: true",
                        "leave_irrelevant_unread: true",
                    ]
                ),
                encoding="utf-8",
            )
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=test-openai-key",
                    ]
                ),
                encoding="utf-8",
            )

            with isolated_secret_environment():
                config = load_config(config_path=config_path, env_path=env_path)

            self.assertEqual(config.gmail_sender, "account@seekingalpha.com")
            self.assertEqual(config.openai_api_key, "test-openai-key")
            self.assertEqual(config.browser_channel, "chrome")
            self.assertEqual(config.browser_cdp_url, "http://127.0.0.1:9222")
            self.assertEqual(config.prompt_version, "v1")
            self.assertEqual(
                config.gmail_credentials_path,
                workspace / "data" / "secrets" / "gmail_credentials.json",
            )
            self.assertTrue(database_path.parent.is_dir())
            self.assertTrue(browser_profile_dir.is_dir())

    def test_defaults_to_prompt_version_v2_when_omitted(self):
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
                    ]
                ),
                encoding="utf-8",
            )

            with isolated_secret_environment():
                config = load_config(config_path=config_path, env_path=env_path)

            self.assertEqual(config.prompt_version, "v2")

    def test_rejects_unknown_browser_channel(self):
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
                        'browser_channel: "firefox"',
                    ]
                ),
                encoding="utf-8",
            )
            env_path.write_text(
                "\n".join(
                    [
                        "OPENAI_API_KEY=test-openai-key",
                    ]
                ),
                encoding="utf-8",
            )

            with isolated_secret_environment():
                with self.assertRaises(ConfigError) as context:
                    load_config(config_path=config_path, env_path=env_path)

        self.assertIn("browser_channel", str(context.exception))

    def test_missing_required_secret_raises_without_leaking_secret_values(self):
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
                        "SOME_OTHER_SECRET=super-secret-token",
                    ]
                ),
                encoding="utf-8",
            )

            with isolated_secret_environment():
                with self.assertRaises(ConfigError) as context:
                    load_config(config_path=config_path, env_path=env_path)

            message = str(context.exception)
            self.assertIn("OPENAI_API_KEY", message)
            self.assertNotIn("super-secret-token", message)

    def test_env_file_values_override_process_environment_and_allow_utf8_bom(self):
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
                        "OPENAI_API_KEY=from-env-file",
                    ]
                ),
                encoding="utf-8-sig",
            )

            with isolated_secret_environment(OPENAI_API_KEY="from-process-env"):
                config = load_config(config_path=config_path, env_path=env_path)

            self.assertEqual(config.openai_api_key, "from-env-file")

    def test_can_skip_secret_requirements_for_capability_checks(self):
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

            with isolated_secret_environment():
                config = load_config(
                    config_path=config_path,
                    env_path=env_path,
                    require_openai=False,
                )

            self.assertEqual(config.openai_api_key, "")

    def test_can_use_local_ollama_without_openai_secret(self):
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
                        'llm_provider: "local_ollama"',
                        'local_llm_base_url: "http://10.100.102.18:11434"',
                        'local_llm_model: "qwen3.5:4b"',
                    ]
                ),
                encoding="utf-8",
            )

            with isolated_secret_environment():
                config = load_config(config_path=config_path, env_path=env_path)

            self.assertEqual(config.llm_provider, "local_ollama")
            self.assertEqual(config.local_llm_base_url, "http://10.100.102.18:11434")
            self.assertEqual(config.local_llm_model, "qwen3.5:4b")
            self.assertEqual(config.analysis_model, "qwen3.5:4b")
            self.assertEqual(config.openai_api_key, "")


if __name__ == "__main__":
    unittest.main()
