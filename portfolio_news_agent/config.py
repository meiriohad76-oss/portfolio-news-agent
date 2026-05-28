from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class AppConfig:
    portfolio_file: Path
    gmail_sender: str
    database_path: Path
    browser_profile_dir: Path
    openai_model: str
    llm_provider: str = "openai"
    local_llm_base_url: str = ""
    local_llm_model: str = ""
    local_llm_timeout_seconds: float = 180.0
    browser_channel: str | None = None
    browser_cdp_url: str | None = None
    gmail_credentials_path: Path = Path("data/secrets/gmail_credentials.json")
    gmail_token_path: Path = Path("data/secrets/gmail_token.json")
    prompt_version: str = "v2"
    mark_relevant_as_read: bool = True
    leave_irrelevant_unread: bool = True
    commodity_exposure_overrides: dict[str, list[str]] = field(default_factory=dict)
    openai_api_key: str = ""

    @property
    def analysis_model(self) -> str:
        if self.llm_provider == "local_ollama":
            return self.local_llm_model
        return self.openai_model


_REQUIRED_CONFIG_KEYS = (
    "portfolio_file",
    "gmail_sender",
    "database_path",
    "browser_profile_dir",
    "openai_model",
)


def load_config(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
    *,
    require_openai: bool = True,
) -> AppConfig:
    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(f"Config file not found: {config_file}")

    raw_config = _read_yaml_mapping(config_file)
    missing_config = [key for key in _REQUIRED_CONFIG_KEYS if _is_blank(raw_config.get(key))]
    if missing_config:
        raise ConfigError("Missing required config values: " + ", ".join(missing_config))

    env_values = _read_env_file(Path(env_path))

    llm_provider = _as_llm_provider(raw_config.get("llm_provider", "openai"))
    openai_api_key = _secret_value("OPENAI_API_KEY", env_values)
    local_llm_base_url = _config_or_env(
        raw_config,
        env_values,
        key="local_llm_base_url",
        env_name="AGENCY_LOCAL_LLM_BASE_URL",
    )
    local_llm_model = _config_or_env(
        raw_config,
        env_values,
        key="local_llm_model",
        env_name="AGENCY_LOCAL_LLM_MODEL",
    )
    local_llm_timeout_seconds = _as_float(
        _config_or_env(
            raw_config,
            env_values,
            key="local_llm_timeout_seconds",
            env_name="AGENCY_LOCAL_LLM_TIMEOUT_SECONDS",
            default="180",
        ),
        field_name="local_llm_timeout_seconds",
    )

    missing_secrets = []
    if require_openai and llm_provider == "openai" and _is_blank(openai_api_key):
        missing_secrets.append("OPENAI_API_KEY")
    if require_openai and llm_provider == "local_ollama" and _is_blank(local_llm_base_url):
        missing_secrets.append("AGENCY_LOCAL_LLM_BASE_URL")
    if require_openai and llm_provider == "local_ollama" and _is_blank(local_llm_model):
        missing_secrets.append("AGENCY_LOCAL_LLM_MODEL")
    if missing_secrets:
        raise ConfigError("Missing required environment values: " + ", ".join(missing_secrets))

    database_path = Path(str(raw_config["database_path"]))
    browser_profile_dir = Path(str(raw_config["browser_profile_dir"]))
    gmail_credentials_path = Path(
        str(raw_config.get("gmail_credentials_path", "data/secrets/gmail_credentials.json"))
    )
    gmail_token_path = Path(str(raw_config.get("gmail_token_path", "data/secrets/gmail_token.json")))
    _create_data_directories(database_path, browser_profile_dir)
    gmail_credentials_path.parent.mkdir(parents=True, exist_ok=True)
    gmail_token_path.parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        portfolio_file=Path(str(raw_config["portfolio_file"])),
        gmail_sender=str(raw_config["gmail_sender"]),
        database_path=database_path,
        browser_profile_dir=browser_profile_dir,
        browser_channel=_as_browser_channel(raw_config.get("browser_channel")),
        browser_cdp_url=_optional_string(raw_config.get("browser_cdp_url")),
        gmail_credentials_path=gmail_credentials_path,
        gmail_token_path=gmail_token_path,
        openai_model=str(raw_config["openai_model"]),
        llm_provider=llm_provider,
        local_llm_base_url=local_llm_base_url,
        local_llm_model=local_llm_model,
        local_llm_timeout_seconds=local_llm_timeout_seconds,
        prompt_version=str(raw_config.get("prompt_version", "v2")),
        mark_relevant_as_read=_as_bool(raw_config.get("mark_relevant_as_read", True)),
        leave_irrelevant_unread=_as_bool(raw_config.get("leave_irrelevant_unread", True)),
        commodity_exposure_overrides=_as_string_lists(
            raw_config.get("commodity_exposure_overrides", {})
        ),
        openai_api_key=openai_api_key,
    )


def _create_data_directories(database_path: Path, browser_profile_dir: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    browser_profile_dir.mkdir(parents=True, exist_ok=True)


def _secret_value(name: str, env_values: dict[str, str]) -> str:
    env_file_value = env_values.get(name)
    if env_file_value is not None and env_file_value.strip():
        return env_file_value
    value = os.environ.get(name)
    if value is not None:
        return value
    return env_file_value or ""


def _config_or_env(
    raw_config: dict[str, Any],
    env_values: dict[str, str],
    *,
    key: str,
    env_name: str,
    default: str = "",
) -> str:
    value = raw_config.get(key)
    if value is not None and str(value).strip():
        return str(value).strip()
    env_file_value = env_values.get(env_name)
    if env_file_value is not None and env_file_value.strip():
        return env_file_value.strip()
    env_value = os.environ.get(env_name)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    return default


def _read_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = _strip_quotes(value.strip())
    return values


def _read_yaml_mapping(config_path: Path) -> dict[str, Any]:
    lines = config_path.read_text(encoding="utf-8").splitlines()
    data: dict[str, Any] = {}
    current_mapping_key: str | None = None

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            key, value = _split_yaml_pair(stripped, config_path)
            if value == "":
                data[key] = {}
                current_mapping_key = key
            else:
                data[key] = _parse_yaml_scalar(value)
                current_mapping_key = None
            continue

        if current_mapping_key is None:
            raise ConfigError(f"Unsupported nested YAML entry in {config_path}: {stripped}")

        child_key, child_value = _split_yaml_pair(stripped, config_path)
        current_mapping = data[current_mapping_key]
        if not isinstance(current_mapping, dict):
            raise ConfigError(f"Invalid nested YAML mapping in {config_path}: {stripped}")
        current_mapping[child_key] = _parse_yaml_scalar(child_value)

    return data


def _split_yaml_pair(line: str, config_path: Path) -> tuple[str, str]:
    if ":" not in line:
        raise ConfigError(f"Invalid YAML entry in {config_path}: {line}")
    key, value = line.split(":", 1)
    key = key.strip()
    if not key:
        raise ConfigError(f"Invalid YAML key in {config_path}: {line}")
    return key, value.strip()


def _parse_yaml_scalar(value: str) -> Any:
    if value == "[]":
        return []
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ConfigError(f"Expected boolean config value, got {value!r}")


def _as_float(value: Any, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Expected numeric config value for {field_name}, got {value!r}") from exc
    if result <= 0:
        raise ConfigError(f"{field_name} must be greater than 0")
    return result


def _as_llm_provider(value: Any) -> str:
    provider = str(value or "openai").strip().lower()
    if provider not in {"openai", "local_ollama"}:
        raise ConfigError("llm_provider must be one of: openai, local_ollama")
    return provider


def _as_string_lists(value: Any) -> dict[str, list[str]]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ConfigError("commodity_exposure_overrides must be a mapping")

    result: dict[str, list[str]] = {}
    for key, symbols in value.items():
        if not isinstance(symbols, list):
            raise ConfigError(f"commodity_exposure_overrides.{key} must be a list")
        result[str(key)] = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    return result


def _as_browser_channel(value: Any) -> str | None:
    if _is_blank(value) or str(value).strip().lower() == "chromium":
        return None
    channel = str(value).strip().lower()
    if channel not in {"chrome", "msedge"}:
        raise ConfigError("browser_channel must be one of: chrome, msedge, chromium")
    return channel


def _optional_string(value: Any) -> str | None:
    if _is_blank(value):
        return None
    return str(value).strip()


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""
