from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def connect_database(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS portfolio_imports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_path TEXT NOT NULL,
          source_hash TEXT NOT NULL,
          imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS assets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          import_id INTEGER NOT NULL,
          symbol TEXT NOT NULL,
          name TEXT,
          asset_type TEXT,
          sector TEXT,
          industry TEXT,
          priority INTEGER NOT NULL DEFAULT 1,
          price REAL,
          quant_rating TEXT,
          sa_analyst_rating TEXT,
          wall_street_rating TEXT,
          metadata_json TEXT,
          UNIQUE(import_id, symbol),
          FOREIGN KEY(import_id) REFERENCES portfolio_imports(id)
        );

        CREATE TABLE IF NOT EXISTS gmail_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          gmail_message_id TEXT NOT NULL UNIQUE,
          gmail_thread_id TEXT,
          sender TEXT,
          subject TEXT,
          received_at TEXT,
          status TEXT NOT NULL,
          status_detail TEXT,
          first_seen_at TEXT NOT NULL,
          last_attempt_at TEXT
        );

        CREATE TABLE IF NOT EXISTS articles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          canonical_url TEXT NOT NULL UNIQUE,
          source_url TEXT NOT NULL,
          headline TEXT,
          author TEXT,
          article_date TEXT,
          source TEXT NOT NULL DEFAULT 'Seeking Alpha',
          content_hash TEXT,
          first_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS gmail_article_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          gmail_message_id INTEGER NOT NULL,
          portfolio_import_id INTEGER NOT NULL,
          prompt_version TEXT NOT NULL,
          source_url TEXT NOT NULL,
          canonical_url TEXT,
          article_id INTEGER,
          status TEXT NOT NULL,
          status_detail TEXT,
          first_seen_at TEXT NOT NULL,
          last_attempt_at TEXT,
          UNIQUE(gmail_message_id, portfolio_import_id, prompt_version, source_url),
          FOREIGN KEY(gmail_message_id) REFERENCES gmail_messages(id),
          FOREIGN KEY(portfolio_import_id) REFERENCES portfolio_imports(id),
          FOREIGN KEY(article_id) REFERENCES articles(id)
        );

        CREATE TABLE IF NOT EXISTS article_asset_summaries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          article_id INTEGER NOT NULL,
          gmail_message_id INTEGER NOT NULL,
          gmail_article_link_id INTEGER NOT NULL,
          portfolio_import_id INTEGER NOT NULL,
          asset_id INTEGER,
          symbol TEXT NOT NULL,
          company_name TEXT,
          author_rating TEXT,
          quant_rating TEXT,
          wall_street_rating TEXT,
          inferred_sentiment TEXT NOT NULL,
          theme TEXT,
          price_targets_json TEXT,
          forward_data_json TEXT,
          action_relevance TEXT NOT NULL,
          short_summary TEXT NOT NULL,
          confidence REAL,
          llm_model TEXT,
          prompt_version TEXT NOT NULL,
          created_at TEXT NOT NULL,
          UNIQUE(article_id, symbol, portfolio_import_id, prompt_version),
          FOREIGN KEY(article_id) REFERENCES articles(id),
          FOREIGN KEY(gmail_message_id) REFERENCES gmail_messages(id),
          FOREIGN KEY(gmail_article_link_id) REFERENCES gmail_article_links(id),
          FOREIGN KEY(portfolio_import_id) REFERENCES portfolio_imports(id),
          FOREIGN KEY(asset_id) REFERENCES assets(id)
        );

        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          mode TEXT NOT NULL,
          emails_found INTEGER DEFAULT 0,
          articles_processed INTEGER DEFAULT 0,
          summaries_created INTEGER DEFAULT 0,
          status TEXT NOT NULL,
          error TEXT
        );
        """
    )
    connection.commit()


def create_portfolio_import(
    connection: sqlite3.Connection,
    *,
    source_path: str,
    source_hash: str,
    imported_at: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO portfolio_imports (source_path, source_hash, imported_at)
        VALUES (?, ?, ?)
        """,
        (source_path, source_hash, imported_at or _utc_now()),
    )
    connection.commit()
    return int(cursor.lastrowid)


def insert_asset(
    connection: sqlite3.Connection,
    *,
    import_id: int,
    symbol: str,
    name: str | None = None,
    asset_type: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
    priority: int = 1,
    price: float | None = None,
    quant_rating: str | None = None,
    sa_analyst_rating: str | None = None,
    wall_street_rating: str | None = None,
    metadata_json: str | None = None,
) -> int:
    symbol = symbol.strip().upper()
    cursor = connection.execute(
        """
        INSERT INTO assets (
          import_id, symbol, name, asset_type, sector, industry, priority, price,
          quant_rating, sa_analyst_rating, wall_street_rating, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(import_id, symbol) DO NOTHING
        """,
        (
            import_id,
            symbol,
            name,
            asset_type,
            sector,
            industry,
            priority,
            price,
            quant_rating,
            sa_analyst_rating,
            wall_street_rating,
            metadata_json,
        ),
    )
    connection.commit()
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    return _fetch_id(
        connection,
        "SELECT id FROM assets WHERE import_id = ? AND symbol = ?",
        (import_id, symbol),
    )


def upsert_gmail_message(
    connection: sqlite3.Connection,
    *,
    gmail_message_id: str,
    gmail_thread_id: str | None,
    sender: str | None,
    subject: str | None,
    received_at: str | None,
    status: str,
    status_detail: str | None = None,
) -> int:
    now = _utc_now()
    cursor = connection.execute(
        """
        INSERT INTO gmail_messages (
          gmail_message_id, gmail_thread_id, sender, subject, received_at, status,
          status_detail, first_seen_at, last_attempt_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gmail_message_id) DO UPDATE SET
          gmail_thread_id = excluded.gmail_thread_id,
          sender = excluded.sender,
          subject = excluded.subject,
          received_at = excluded.received_at,
          status = excluded.status,
          status_detail = excluded.status_detail,
          last_attempt_at = excluded.last_attempt_at
        """,
        (
            gmail_message_id,
            gmail_thread_id,
            sender,
            subject,
            received_at,
            status,
            status_detail,
            now,
            now,
        ),
    )
    connection.commit()
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    return _fetch_id(
        connection,
        "SELECT id FROM gmail_messages WHERE gmail_message_id = ?",
        (gmail_message_id,),
    )


def upsert_article(
    connection: sqlite3.Connection,
    *,
    canonical_url: str,
    source_url: str,
    headline: str | None = None,
    author: str | None = None,
    article_date: str | None = None,
    source: str = "Seeking Alpha",
    content_hash: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO articles (
          canonical_url, source_url, headline, author, article_date, source,
          content_hash, first_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url) DO NOTHING
        """,
        (
            canonical_url,
            source_url,
            headline,
            author,
            article_date,
            source,
            content_hash,
            _utc_now(),
        ),
    )
    connection.commit()
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    return _fetch_id(
        connection,
        "SELECT id FROM articles WHERE canonical_url = ?",
        (canonical_url,),
    )


def upsert_gmail_article_link(
    connection: sqlite3.Connection,
    *,
    gmail_message_id: int,
    portfolio_import_id: int,
    prompt_version: str,
    source_url: str,
    canonical_url: str | None = None,
    article_id: int | None = None,
    status: str = "queued",
    status_detail: str | None = None,
) -> int:
    now = _utc_now()
    cursor = connection.execute(
        """
        INSERT INTO gmail_article_links (
          gmail_message_id, portfolio_import_id, prompt_version, source_url,
          canonical_url, article_id, status, status_detail, first_seen_at,
          last_attempt_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gmail_message_id, portfolio_import_id, prompt_version, source_url)
        DO UPDATE SET
          canonical_url = COALESCE(excluded.canonical_url, gmail_article_links.canonical_url),
          article_id = COALESCE(excluded.article_id, gmail_article_links.article_id),
          status = excluded.status,
          status_detail = excluded.status_detail,
          last_attempt_at = excluded.last_attempt_at
        """,
        (
            gmail_message_id,
            portfolio_import_id,
            prompt_version,
            source_url,
            canonical_url,
            article_id,
            status,
            status_detail,
            now,
            now,
        ),
    )
    connection.commit()
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    return _fetch_id(
        connection,
        """
        SELECT id FROM gmail_article_links
        WHERE gmail_message_id = ?
          AND portfolio_import_id = ?
          AND prompt_version = ?
          AND source_url = ?
        """,
        (gmail_message_id, portfolio_import_id, prompt_version, source_url),
    )


def update_gmail_article_link_status(
    connection: sqlite3.Connection,
    *,
    link_id: int,
    status: str,
    status_detail: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE gmail_article_links
        SET status = ?, status_detail = ?, last_attempt_at = ?
        WHERE id = ?
        """,
        (status, status_detail, _utc_now(), link_id),
    )
    connection.commit()


def start_run(connection: sqlite3.Connection, *, mode: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO runs (started_at, mode, status)
        VALUES (?, ?, ?)
        """,
        (_utc_now(), mode, "running"),
    )
    connection.commit()
    return int(cursor.lastrowid)


def finish_run(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    status: str,
    emails_found: int = 0,
    articles_processed: int = 0,
    summaries_created: int = 0,
    error: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE runs
        SET finished_at = ?,
            status = ?,
            emails_found = ?,
            articles_processed = ?,
            summaries_created = ?,
            error = ?
        WHERE id = ?
        """,
        (
            _utc_now(),
            status,
            emails_found,
            articles_processed,
            summaries_created,
            error,
            run_id,
        ),
    )
    connection.commit()


def insert_article_asset_summary(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    gmail_message_id: int,
    gmail_article_link_id: int,
    portfolio_import_id: int,
    symbol: str,
    inferred_sentiment: str,
    action_relevance: str,
    short_summary: str,
    prompt_version: str,
    asset_id: int | None = None,
    company_name: str | None = None,
    author_rating: str | None = None,
    quant_rating: str | None = None,
    wall_street_rating: str | None = None,
    theme: str | None = None,
    price_targets_json: str | None = None,
    forward_data_json: str | None = None,
    confidence: float | None = None,
    llm_model: str | None = None,
) -> int:
    symbol = symbol.strip().upper()
    cursor = connection.execute(
        """
        INSERT INTO article_asset_summaries (
          article_id, gmail_message_id, gmail_article_link_id,
          portfolio_import_id, asset_id, symbol, company_name, author_rating,
          quant_rating, wall_street_rating, inferred_sentiment, theme,
          price_targets_json, forward_data_json, action_relevance, short_summary,
          confidence, llm_model, prompt_version, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(article_id, symbol, portfolio_import_id, prompt_version) DO NOTHING
        """,
        (
            article_id,
            gmail_message_id,
            gmail_article_link_id,
            portfolio_import_id,
            asset_id,
            symbol,
            company_name,
            author_rating,
            quant_rating,
            wall_street_rating,
            inferred_sentiment,
            theme,
            price_targets_json,
            forward_data_json,
            action_relevance,
            short_summary,
            confidence,
            llm_model,
            prompt_version,
            _utc_now(),
        ),
    )
    connection.commit()
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    return _fetch_id(
        connection,
        """
        SELECT id FROM article_asset_summaries
        WHERE article_id = ?
          AND symbol = ?
          AND portfolio_import_id = ?
          AND prompt_version = ?
        """,
        (article_id, symbol, portfolio_import_id, prompt_version),
    )


def get_article_asset_summaries(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM article_asset_summaries
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_assets_for_import(
    connection: sqlite3.Connection,
    portfolio_import_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM assets
        WHERE import_id = ?
        ORDER BY id
        """,
        (portfolio_import_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_queued_article_links(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT *
        FROM gmail_article_links
        WHERE status = 'queued'
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_id(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...],
) -> int:
    row = connection.execute(query, parameters).fetchone()
    if row is None:
        raise RuntimeError("Expected database row was not found")
    return int(row[0])


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
