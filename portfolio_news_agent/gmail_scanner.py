from __future__ import annotations

import base64
import binascii
import html
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from portfolio_news_agent.storage import upsert_gmail_article_link, upsert_gmail_message


TERMINAL_LINK_STATUSES = {
    "duplicate_skipped",
    "irrelevant_seen",
    "processed_relevant",
}

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_SEEKING_ALPHA_URL_RE = re.compile(r"https?://(?:www\.)?seekingalpha\.com/[^\s\"'<>]+")


class GmailClient(Protocol):
    def search_messages(self, query: str) -> list[dict[str, Any]]:
        """Return Gmail message references for the query."""

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Return a Gmail message payload."""


@dataclass(frozen=True)
class GmailScanResult:
    emails_found: int
    links_found: int
    links_queued: int
    links_skipped: int


@dataclass(frozen=True)
class GmailMessageSummary:
    gmail_message_id: str
    gmail_thread_id: str
    sender: str | None
    subject: str | None
    received_at: str | None
    seeking_alpha_links: list[str]


def build_gmail_query(sender: str) -> str:
    return f"from:{sender} is:unread"


def summarize_gmail_message(message: dict[str, Any]) -> GmailMessageSummary:
    metadata = _message_metadata(message)
    body_text = "\n".join(_payload_texts(message.get("payload", {})))
    return GmailMessageSummary(
        gmail_message_id=str(metadata["gmail_message_id"]),
        gmail_thread_id=str(metadata["gmail_thread_id"] or ""),
        sender=metadata["sender"],
        subject=metadata["subject"],
        received_at=metadata["received_at"],
        seeking_alpha_links=extract_seeking_alpha_urls(body_text),
    )


def scan_unread_seeking_alpha_messages(
    connection: sqlite3.Connection,
    *,
    gmail_client: GmailClient,
    sender: str,
    portfolio_import_id: int,
    prompt_version: str,
    max_emails: int | None = None,
) -> GmailScanResult:
    query = build_gmail_query(sender)
    message_refs = gmail_client.search_messages(query)
    if max_emails is not None:
        message_refs = message_refs[: max(0, int(max_emails))]
    links_found = 0
    links_queued = 0
    links_skipped = 0

    for message_ref in message_refs:
        message = gmail_client.get_message(str(message_ref["id"]))
        summary = summarize_gmail_message(message)
        message_row_id = upsert_gmail_message(
            connection,
            gmail_message_id=summary.gmail_message_id,
            gmail_thread_id=summary.gmail_thread_id,
            sender=summary.sender,
            subject=summary.subject,
            received_at=summary.received_at,
            status="scanned",
        )
        urls = summary.seeking_alpha_links
        links_found += len(urls)

        for url in urls:
            if _terminal_link_exists(
                connection,
                gmail_message_id=message_row_id,
                portfolio_import_id=portfolio_import_id,
                prompt_version=prompt_version,
                source_url=url,
            ):
                links_skipped += 1
                continue
            upsert_gmail_article_link(
                connection,
                gmail_message_id=message_row_id,
                portfolio_import_id=portfolio_import_id,
                prompt_version=prompt_version,
                source_url=url,
                status="queued",
            )
            links_queued += 1

    return GmailScanResult(
        emails_found=len(message_refs),
        links_found=links_found,
        links_queued=links_queued,
        links_skipped=links_skipped,
    )


def extract_seeking_alpha_urls(text: str) -> list[str]:
    urls = []
    seen = set()
    for raw_url in _candidate_urls(html.unescape(text)):
        canonical_url = _canonicalize_url(raw_url)
        if not _is_supported_seeking_alpha_url(canonical_url):
            continue
        if canonical_url in seen:
            continue
        seen.add(canonical_url)
        urls.append(canonical_url)
    return urls


def _message_metadata(message: dict[str, Any]) -> dict[str, str | None]:
    headers = _headers_by_name(message.get("payload", {}).get("headers", []))
    return {
        "gmail_message_id": str(message["id"]),
        "gmail_thread_id": str(message.get("threadId") or ""),
        "sender": headers.get("from"),
        "subject": headers.get("subject"),
        "received_at": _received_at(message, headers),
    }


def _headers_by_name(headers: list[dict[str, str]]) -> dict[str, str]:
    return {
        str(header.get("name", "")).lower(): str(header.get("value", ""))
        for header in headers
        if header.get("name")
    }


def _received_at(message: dict[str, Any], headers: dict[str, str]) -> str | None:
    internal_date = message.get("internalDate")
    if internal_date:
        timestamp = int(internal_date) / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    return headers.get("date")


def _payload_texts(payload: dict[str, Any]) -> list[str]:
    texts = []
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data and mime_type in {"text/html", "text/plain", ""}:
        texts.append(_decode_body(body_data))

    for part in payload.get("parts", []) or []:
        texts.extend(_payload_texts(part))
    return texts


def _decode_body(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    return decoded.decode("utf-8", errors="replace")


def _canonicalize_url(url: str) -> str:
    split = urlsplit(url.rstrip(").,;"))
    host = split.netloc.lower()
    if host == "www.seekingalpha.com":
        host = "seekingalpha.com"
    return urlunsplit((split.scheme.lower(), host, split.path.rstrip("/"), "", ""))


def _candidate_urls(text: str) -> list[str]:
    candidates = []
    for match in _URL_RE.finditer(text):
        raw_url = match.group(0)
        if _SEEKING_ALPHA_URL_RE.match(raw_url):
            candidates.append(raw_url)
            continue
        decoded_url = _decode_sailthru_tracking_url(raw_url)
        if decoded_url:
            candidates.append(decoded_url)
    return candidates


def _decode_sailthru_tracking_url(raw_url: str) -> str | None:
    split = urlsplit(raw_url.rstrip(").,;"))
    if split.netloc.lower() != "email-st.seekingalpha.com":
        return None
    path_parts = [part for part in split.path.split("/") if part]
    if len(path_parts) < 3 or path_parts[0] != "click":
        return None
    for part in path_parts[2:]:
        decoded = _urlsafe_b64decode_text(unquote(part))
        if decoded and _SEEKING_ALPHA_URL_RE.match(decoded):
            nested_url = _nested_supported_url(decoded)
            return nested_url or decoded
    return None


def _urlsafe_b64decode_text(value: str) -> str | None:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return None
    return decoded.decode("utf-8", errors="replace")


def _is_supported_seeking_alpha_url(url: str) -> bool:
    split = urlsplit(url)
    return split.netloc == "seekingalpha.com" and split.path.startswith(("/article/", "/news/"))


def _nested_supported_url(url: str) -> str | None:
    split = urlsplit(url)
    if split.netloc not in {"seekingalpha.com", "www.seekingalpha.com"}:
        return None
    for values in parse_qs(split.query).values():
        for value in values:
            candidate = value
            if candidate.startswith(("/article/", "/news/")):
                candidate = urlunsplit((split.scheme or "https", "seekingalpha.com", candidate, "", ""))
            if _SEEKING_ALPHA_URL_RE.match(candidate) and _is_supported_seeking_alpha_url(
                _canonicalize_url(candidate)
            ):
                return candidate
    return None


def _terminal_link_exists(
    connection: sqlite3.Connection,
    *,
    gmail_message_id: int,
    portfolio_import_id: int,
    prompt_version: str,
    source_url: str,
) -> bool:
    row = connection.execute(
        """
        SELECT status
        FROM gmail_article_links
        WHERE gmail_message_id = ?
          AND portfolio_import_id = ?
          AND prompt_version = ?
          AND source_url = ?
        """,
        (gmail_message_id, portfolio_import_id, prompt_version, source_url),
    ).fetchone()
    return bool(row and row["status"] in TERMINAL_LINK_STATUSES)
