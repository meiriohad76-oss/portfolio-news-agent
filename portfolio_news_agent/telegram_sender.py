from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


Transport = Callable[[str, bytes], dict[str, Any]]


class TelegramConfigError(ValueError):
    """Raised when Telegram credentials are missing."""


class TelegramSendError(RuntimeError):
    """Raised when Telegram rejects or cannot complete a send request."""


def format_telegram_message(summary: dict[str, Any]) -> str:
    forward_data = _join_present(
        [
            _text(summary.get("price_targets_json")),
            _text(summary.get("forward_data_json")),
        ],
        separator=" | ",
        fallback="None",
    )
    return "\n".join(
        [
            f"{_text(summary.get('symbol'))} - "
            f"{_text(summary.get('inferred_sentiment'))} - "
            f"{_text(summary.get('action_relevance'))}",
            _text(summary.get("headline")),
            "",
            f"Author rating: {_text(summary.get('author_rating'), 'N/A')}",
            "Quant: "
            f"{_text(summary.get('quant_rating'), 'N/A')} | "
            f"Wall St: {_text(summary.get('wall_street_rating'), 'N/A')}",
            f"Forward data: {forward_data}",
            "",
            _text(summary.get("short_summary")),
            "",
            _text(summary.get("source_url")),
        ]
    ).strip()


def send_telegram_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    transport: Transport | None = None,
) -> dict[str, Any]:
    _validate_credentials(bot_token, chat_id)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    response = (transport or _default_transport)(url, payload)
    if not response.get("ok"):
        description = response.get("description") or "Telegram send failed"
        raise TelegramSendError(str(description))
    return response


def _default_transport(url: str, payload: bytes) -> dict[str, Any]:
    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TelegramSendError(f"Telegram HTTP error {exc.code}: {detail}") from exc
    except URLError as exc:
        raise TelegramSendError(f"Telegram connection error: {exc.reason}") from exc


def _validate_credentials(bot_token: str, chat_id: str) -> None:
    missing = []
    if not bot_token.strip():
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id.strip():
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise TelegramConfigError("Missing required Telegram values: " + ", ".join(missing))


def _join_present(
    values: list[str],
    *,
    separator: str,
    fallback: str,
) -> str:
    present = [value for value in values if value]
    if not present:
        return fallback
    return separator.join(present)


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback
