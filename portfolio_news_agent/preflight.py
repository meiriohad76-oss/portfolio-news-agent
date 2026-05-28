from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portfolio_news_agent.config import ConfigError, load_config


@dataclass(frozen=True)
class LocalSetupStatus:
    config_file_exists: bool
    env_file_exists: bool
    portfolio_file_exists: bool
    gmail_credentials_exists: bool
    gmail_token_exists: bool
    openai_api_key_present: bool
    local_llm_present: bool
    ready_for_gmail_check: bool
    ready_for_analyze_url: bool
    ready_for_full_run: bool
    blockers: list[str]
    config_error: str | None = None


def check_local_setup(
    *,
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> LocalSetupStatus:
    config_file = Path(config_path)
    env_file = Path(env_path)

    if not config_file.exists():
        return _blocked_status(
            config_file_exists=False,
            env_file_exists=env_file.exists(),
            blockers=["config.yaml"],
            config_error=f"Config file not found: {config_file}",
        )

    try:
        config = load_config(
            config_path=config_file,
            env_path=env_file,
            require_openai=False,
        )
    except ConfigError as exc:
        return _blocked_status(
            config_file_exists=True,
            env_file_exists=env_file.exists(),
            blockers=["config.yaml"],
            config_error=str(exc),
        )

    portfolio_file_exists = config.portfolio_file.exists()
    gmail_credentials_exists = config.gmail_credentials_path.exists()
    gmail_token_exists = config.gmail_token_path.exists()
    openai_api_key_present = bool(config.openai_api_key.strip())
    local_llm_present = bool(config.local_llm_base_url.strip() and config.local_llm_model.strip())
    llm_ready = (
        openai_api_key_present
        if config.llm_provider == "openai"
        else local_llm_present
    )

    ready_for_gmail_check = gmail_credentials_exists
    ready_for_analyze_url = portfolio_file_exists and llm_ready
    ready_for_full_run = (
        ready_for_gmail_check
        and ready_for_analyze_url
    )

    blockers = []
    if not portfolio_file_exists:
        blockers.append("portfolio_file")
    if not gmail_credentials_exists:
        blockers.append("gmail_credentials_path")
    if config.llm_provider == "openai" and not openai_api_key_present:
        blockers.append("OPENAI_API_KEY")
    if config.llm_provider == "local_ollama" and not config.local_llm_base_url.strip():
        blockers.append("AGENCY_LOCAL_LLM_BASE_URL")
    if config.llm_provider == "local_ollama" and not config.local_llm_model.strip():
        blockers.append("AGENCY_LOCAL_LLM_MODEL")

    return LocalSetupStatus(
        config_file_exists=True,
        env_file_exists=env_file.exists(),
        portfolio_file_exists=portfolio_file_exists,
        gmail_credentials_exists=gmail_credentials_exists,
        gmail_token_exists=gmail_token_exists,
        openai_api_key_present=openai_api_key_present,
        local_llm_present=local_llm_present,
        ready_for_gmail_check=ready_for_gmail_check,
        ready_for_analyze_url=ready_for_analyze_url,
        ready_for_full_run=ready_for_full_run,
        blockers=blockers,
    )


def _blocked_status(
    *,
    config_file_exists: bool,
    env_file_exists: bool,
    blockers: list[str],
    config_error: str,
) -> LocalSetupStatus:
    return LocalSetupStatus(
        config_file_exists=config_file_exists,
        env_file_exists=env_file_exists,
        portfolio_file_exists=False,
        gmail_credentials_exists=False,
        gmail_token_exists=False,
        openai_api_key_present=False,
        local_llm_present=False,
        ready_for_gmail_check=False,
        ready_for_analyze_url=False,
        ready_for_full_run=False,
        blockers=blockers,
        config_error=config_error,
    )
