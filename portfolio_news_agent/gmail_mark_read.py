from __future__ import annotations

import sqlite3
from typing import Protocol


TERMINAL_ACCEPTABLE_STATUSES = {
    "processed_relevant",
    "irrelevant_seen",
    "duplicate_skipped",
}


class GmailActions(Protocol):
    def mark_read(self, gmail_message_id: str) -> None:
        """Remove Gmail's UNREAD label from a message."""


class GmailMarkReadError(RuntimeError):
    """Raised when Gmail read-state modification fails."""


def should_mark_message_read(connection: sqlite3.Connection, gmail_message_row_id: int) -> bool:
    links = connection.execute(
        """
        SELECT status
        FROM gmail_article_links
        WHERE gmail_message_id = ?
        ORDER BY id
        """,
        (gmail_message_row_id,),
    ).fetchall()
    if not links:
        return False

    statuses = [row["status"] for row in links]
    if any(status not in TERMINAL_ACCEPTABLE_STATUSES for status in statuses):
        return False

    summary_count = connection.execute(
        """
        SELECT COUNT(*)
        FROM article_asset_summaries
        WHERE gmail_message_id = ?
        """,
        (gmail_message_row_id,),
    ).fetchone()[0]
    return summary_count > 0


def mark_message_read_if_complete(
    connection: sqlite3.Connection,
    gmail_message_row_id: int,
    gmail_actions: GmailActions,
) -> bool:
    if not should_mark_message_read(connection, gmail_message_row_id):
        return False

    row = connection.execute(
        "SELECT gmail_message_id FROM gmail_messages WHERE id = ?",
        (gmail_message_row_id,),
    ).fetchone()
    if row is None:
        raise GmailMarkReadError(f"Unknown Gmail message row id: {gmail_message_row_id}")

    try:
        gmail_actions.mark_read(row["gmail_message_id"])
    except Exception as exc:
        connection.execute(
            """
            UPDATE gmail_messages
            SET status = ?, status_detail = ?, last_attempt_at = datetime('now')
            WHERE id = ?
            """,
            ("failed_mark_read", str(exc), gmail_message_row_id),
        )
        connection.commit()
        raise GmailMarkReadError(str(exc)) from exc

    connection.execute(
        """
        UPDATE gmail_messages
        SET status = ?, status_detail = NULL, last_attempt_at = datetime('now')
        WHERE id = ?
        """,
        ("processed_relevant", gmail_message_row_id),
    )
    connection.commit()
    return True
