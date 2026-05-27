import sqlite3
import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.storage import (
    connect_database,
    create_portfolio_import,
    finish_run,
    get_article_asset_summaries,
    insert_article_asset_summary,
    insert_asset,
    migrate,
    start_run,
    update_gmail_article_link_status,
    upsert_article,
    upsert_gmail_article_link,
    upsert_gmail_message,
)


class StorageTests(unittest.TestCase):
    def test_migration_creates_expected_tables(self):
        with sqlite3.connect(":memory:") as connection:
            migrate(connection)

            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertTrue(
            {
                "portfolio_imports",
                "assets",
                "gmail_messages",
                "gmail_article_links",
                "articles",
                "article_asset_summaries",
                "runs",
            }.issubset(table_names)
        )

    def test_connect_database_creates_parent_directory_and_enables_foreign_keys(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            database_path = Path(tmp_dir) / "nested" / "portfolio_news.db"
            connection = connect_database(database_path)
            try:
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            finally:
                connection.close()

            self.assertTrue(database_path.exists())
            self.assertEqual(foreign_keys, 1)

    def test_duplicate_article_insert_returns_existing_id(self):
        with sqlite3.connect(":memory:") as connection:
            migrate(connection)

            first_id = upsert_article(
                connection,
                canonical_url="https://seekingalpha.com/article/1",
                source_url="https://email.example/article/1",
                headline="AEM update",
            )
            second_id = upsert_article(
                connection,
                canonical_url="https://seekingalpha.com/article/1",
                source_url="https://email.example/article/1?utm=duplicate",
                headline="AEM update duplicate",
            )

        self.assertEqual(first_id, second_id)

    def test_gmail_article_link_upsert_and_status_update(self):
        with sqlite3.connect(":memory:") as connection:
            migrate(connection)
            import_id = create_portfolio_import(
                connection,
                source_path="portfolio.csv",
                source_hash="hash-1",
            )
            message_id = upsert_gmail_message(
                connection,
                gmail_message_id="gmail-1",
                gmail_thread_id="thread-1",
                sender="account@seekingalpha.com",
                subject="Article",
                received_at="2026-05-23T10:00:00Z",
                status="scanned",
            )

            first_link_id = upsert_gmail_article_link(
                connection,
                gmail_message_id=message_id,
                portfolio_import_id=import_id,
                prompt_version="v1",
                source_url="https://seekingalpha.com/article/1",
            )
            second_link_id = upsert_gmail_article_link(
                connection,
                gmail_message_id=message_id,
                portfolio_import_id=import_id,
                prompt_version="v1",
                source_url="https://seekingalpha.com/article/1",
            )
            update_gmail_article_link_status(
                connection,
                link_id=first_link_id,
                status="irrelevant_seen",
                status_detail="No current holding was affected.",
            )
            row = connection.execute(
                "SELECT status, status_detail FROM gmail_article_links WHERE id = ?",
                (first_link_id,),
            ).fetchone()

        self.assertEqual(first_link_id, second_link_id)
        self.assertEqual(tuple(row), ("irrelevant_seen", "No current holding was affected."))

    def test_conflicting_gmail_message_upsert_returns_existing_message_id_after_asset_inserts(self):
        with sqlite3.connect(":memory:") as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            migrate(connection)
            import_id = create_portfolio_import(
                connection,
                source_path="portfolio.csv",
                source_hash="hash-1",
            )
            message_id = upsert_gmail_message(
                connection,
                gmail_message_id="gmail-1",
                gmail_thread_id="thread-1",
                sender="account@seekingalpha.com",
                subject="Article",
                received_at="2026-05-23T10:00:00Z",
                status="scanned",
            )
            insert_asset(connection, import_id=import_id, symbol="AAPL", name="Apple")
            insert_asset(connection, import_id=import_id, symbol="MSFT", name="Microsoft")

            existing_message_id = upsert_gmail_message(
                connection,
                gmail_message_id="gmail-1",
                gmail_thread_id="thread-1",
                sender="account@seekingalpha.com",
                subject="Article refreshed",
                received_at="2026-05-23T10:01:00Z",
                status="scanned",
            )

            self.assertEqual(existing_message_id, message_id)
            link_id = upsert_gmail_article_link(
                connection,
                gmail_message_id=existing_message_id,
                portfolio_import_id=import_id,
                prompt_version="v1",
                source_url="https://seekingalpha.com/article/1",
            )

        self.assertGreater(link_id, 0)

    def test_run_lifecycle_records_finish_state(self):
        with sqlite3.connect(":memory:") as connection:
            migrate(connection)

            run_id = start_run(connection, mode="once")
            finish_run(
                connection,
                run_id=run_id,
                status="success",
                emails_found=2,
                articles_processed=1,
                summaries_created=1,
            )
            row = connection.execute(
                """
                SELECT mode, status, emails_found, articles_processed, summaries_created,
                       finished_at IS NOT NULL
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()

        self.assertEqual(tuple(row), ("once", "success", 2, 1, 1, 1))

    def test_summary_insert_is_unique_by_article_symbol_import_and_prompt(self):
        with sqlite3.connect(":memory:") as connection:
            migrate(connection)
            import_id = create_portfolio_import(
                connection,
                source_path="portfolio.csv",
                source_hash="hash-1",
            )
            asset_id = insert_asset(
                connection,
                import_id=import_id,
                symbol="AEM",
                name="Agnico Eagle Mines",
            )
            message_id = upsert_gmail_message(
                connection,
                gmail_message_id="gmail-1",
                gmail_thread_id="thread-1",
                sender="account@seekingalpha.com",
                subject="AEM article",
                received_at="2026-05-23T10:00:00Z",
                status="scanned",
            )
            article_id = upsert_article(
                connection,
                canonical_url="https://seekingalpha.com/article/1",
                source_url="https://seekingalpha.com/article/1",
                headline="AEM thesis update",
            )
            link_id = upsert_gmail_article_link(
                connection,
                gmail_message_id=message_id,
                portfolio_import_id=import_id,
                prompt_version="v1",
                source_url="https://seekingalpha.com/article/1",
                article_id=article_id,
                status="processed_relevant",
            )

            first_summary_id = insert_article_asset_summary(
                connection,
                article_id=article_id,
                gmail_message_id=message_id,
                gmail_article_link_id=link_id,
                portfolio_import_id=import_id,
                asset_id=asset_id,
                symbol="AEM",
                company_name="Agnico Eagle Mines",
                inferred_sentiment="somewhat_bullish",
                action_relevance="portfolio_attention",
                short_summary="Gold margin outlook improved.",
                prompt_version="v1",
            )
            second_summary_id = insert_article_asset_summary(
                connection,
                article_id=article_id,
                gmail_message_id=message_id,
                gmail_article_link_id=link_id,
                portfolio_import_id=import_id,
                asset_id=asset_id,
                symbol="AEM",
                company_name="Agnico Eagle Mines",
                inferred_sentiment="bearish",
                action_relevance="risk_warning",
                short_summary="This duplicate should not overwrite the first row.",
                prompt_version="v1",
            )
            summaries = get_article_asset_summaries(connection)

        self.assertEqual(first_summary_id, second_summary_id)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["symbol"], "AEM")
        self.assertEqual(summaries[0]["inferred_sentiment"], "somewhat_bullish")
