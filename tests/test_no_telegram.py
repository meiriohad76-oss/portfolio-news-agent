from pathlib import Path

from portfolio_news_agent.config import load_config
from portfolio_news_agent.preflight import check_local_setup


def test_runtime_config_and_preflight_have_no_telegram_contract(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)

    portfolio_path = tmp_path / "portfolio.csv"
    portfolio_path.write_text("Symbol,Name\nAEM,Agnico Eagle Mines\n", encoding="utf-8")
    credentials_path = tmp_path / "data" / "secrets" / "gmail_credentials.json"
    credentials_path.parent.mkdir(parents=True)
    credentials_path.write_text('{"installed": {}}', encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=test-openai-key\n", encoding="utf-8")
    config_path.write_text(
        "\n".join(
            [
                f'portfolio_file: "{portfolio_path}"',
                'gmail_sender: "account@seekingalpha.com"',
                f'database_path: "{tmp_path / "data" / "portfolio_news.db"}"',
                f'browser_profile_dir: "{tmp_path / "data" / "browser-profile"}"',
                f'gmail_credentials_path: "{credentials_path}"',
                f'gmail_token_path: "{tmp_path / "data" / "secrets" / "gmail_token.json"}"',
                'openai_model: "gpt-5-nano"',
                "telegram_enabled: true",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path=config_path, env_path=env_path)
    status = check_local_setup(config_path=config_path, env_path=env_path)

    assert not hasattr(config, "telegram_enabled")
    assert not hasattr(config, "telegram_bot_token")
    assert not hasattr(config, "telegram_chat_id")
    assert status.ready_for_full_run
    assert all("TELEGRAM" not in blocker for blocker in status.blockers)


def test_product_code_no_longer_contains_telegram_sender_or_configuration():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "portfolio_news_agent" / "telegram_sender.py").exists()

    product_files = [
        *root.glob("portfolio_news_agent/*.py"),
        root / "config.yaml",
        root / "config.example.yaml",
        root / ".env.example",
    ]
    for path in product_files:
        text = path.read_text(encoding="utf-8")
        assert "telegram" not in text.lower(), path
