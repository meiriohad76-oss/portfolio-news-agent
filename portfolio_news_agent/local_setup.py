from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from portfolio_news_agent.config import ConfigError, load_config


@dataclass(frozen=True)
class LocalInitResult:
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def initialize_local_setup(
    *,
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
    config_example_path: str | Path = "config.example.yaml",
    env_example_path: str | Path = ".env.example",
) -> LocalInitResult:
    created: list[str] = []
    existing: list[str] = []
    errors: list[str] = []

    config_file = Path(config_path)
    env_file = Path(env_path)
    config_example = Path(config_example_path)
    env_example = Path(env_example_path)

    _copy_template_if_missing(
        target=config_file,
        template=config_example,
        created=created,
        existing=existing,
        errors=errors,
    )
    _copy_template_if_missing(
        target=env_file,
        template=env_example,
        created=created,
        existing=existing,
        errors=errors,
    )

    if config_file.exists():
        try:
            config = load_config(
                config_path=config_file,
                env_path=env_file,
                require_openai=False,
            )
            _ensure_directory(config.gmail_credentials_path.parent, created, existing)
            _ensure_directory(config.gmail_token_path.parent, created, existing)
            _ensure_directory(config.browser_profile_dir, created, existing)
            _ensure_directory(config.database_path.parent, created, existing)
        except ConfigError as exc:
            errors.append(f"config: {exc}")

    return LocalInitResult(created=created, existing=existing, errors=errors)


def _copy_template_if_missing(
    *,
    target: Path,
    template: Path,
    created: list[str],
    existing: list[str],
    errors: list[str],
) -> None:
    if target.exists():
        _append_unique(existing, str(target))
        return
    if not template.exists():
        errors.append(f"missing template: {template}")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    _append_unique(created, str(target))


def _ensure_directory(path: Path, created: list[str], existing: list[str]) -> None:
    path_text = str(path)
    if path_text in created or path_text in existing:
        return
    if path.exists():
        _append_unique(existing, path_text)
        return
    path.mkdir(parents=True, exist_ok=True)
    _append_unique(created, path_text)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
