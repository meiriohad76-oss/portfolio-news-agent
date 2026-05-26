import sqlite3
import unittest

from portfolio_news_agent.gmail_mark_read import (
    GmailMarkReadError,
    mark_message_read_if_complete,
    should_mark_message_read,
)
from portfolio_news_agent.storage import (
    create_portfolio_import,
    insert_article_asset_summary,
    insert_asset,
    migrate,
    upsert_article,
    upsert_gmail_article_link,
    upsert_gmail_message,
)


class FakeGmailActions:
    def __init__(self, fail=False):
        self.fail = fail
        self.marked = []

    def mark_read(self, gmail_message_id):
        if self.fail:
            raise RuntimeError("Gmail refused")
        self.marked.append(gmail_message_id)


class GmailMarkReadTests(unittest.TestCase):
    def test_policy_requires_all_links_terminal_and_one_relevant_summary(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        message_id, import_id, article_id, link_id = self._message_article_link(
            connection,
            link_status="processed_relevant",
        )
        asset_id = insert_asset(
            connection,
            import_id=import_id,
            symbol="AEM",
            name="Agnico Eagle Mines",
        )
        insert_article_asset_summary(
            connection,
            article_id=article_id,
            gmail_message_id=message_id,
            gmail_article_link_id=link_id,
            portfolio_import_id=import_id,
            asset_id=asset_id,
            symbol="AEM",
            company_name="Agnico Eagle Mines",
            inferred_sentiment="bullish",
            action_relevance="material_news",
            short_summary="Relevant article.",
            prompt_version="v1",
        )

        self.assertTrue(should_mark_message_read(connection, message_id))

    def test_policy_rejects_all_irrelevant_message(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        message_id, _, _, _ = self._message_article_link(
            connection,
            link_status="irrelevant_seen",
        )

        self.assertFalse(should_mark_message_read(connection, message_id))

    def test_policy_rejects_any_failed_link(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        message_id, import_id, article_id, link_id = self._message_article_link(
            connection,
            link_status="processed_relevant",
        )
        upsert_gmail_article_link(
            connection,
            gmail_message_id=message_id,
            portfolio_import_id=import_id,
            prompt_version="v1",
            source_url="https://seekingalpha.com/article/failed",
            status="failed_extract",
        )
        asset_id = insert_asset(connection, import_id=import_id, symbol="AEM")
        insert_article_asset_summary(
            connection,
            article_id=article_id,
            gmail_message_id=message_id,
            gmail_article_link_id=link_id,
            portfolio_import_id=import_id,
            asset_id=asset_id,
            symbol="AEM",
            inferred_sentiment="bullish",
            action_relevance="material_news",
            short_summary="Relevant article.",
            prompt_version="v1",
        )

        self.assertFalse(should_mark_message_read(connection, message_id))

    def test_mark_action_updates_local_status_after_remote_success(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        message_id, import_id, article_id, link_id = self._message_article_link(
            connection,
            link_status="processed_relevant",
            gmail_message_id="gmail-remote-1",
        )
        asset_id = insert_asset(connection, import_id=import_id, symbol="AEM")
        insert_article_asset_summary(
            connection,
            article_id=article_id,
            gmail_message_id=message_id,
            gmail_article_link_id=link_id,
            portfolio_import_id=import_id,
            asset_id=asset_id,
            symbol="AEM",
            inferred_sentiment="bullish",
            action_relevance="material_news",
            short_summary="Relevant article.",
            prompt_version="v1",
        )
        gmail_actions = FakeGmailActions()

        marked = mark_message_read_if_complete(connection, message_id, gmail_actions)
        status = connection.execute(
            "SELECT status FROM gmail_messages WHERE id = ?",
            (message_id,),
        ).fetchone()["status"]

        self.assertTrue(marked)
        self.assertEqual(gmail_actions.marked, ["gmail-remote-1"])
        self.assertEqual(status, "processed_relevant")

    def test_mark_action_records_failure_and_raises(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        message_id, import_id, article_id, link_id = self._message_article_link(
            connection,
            link_status="processed_relevant",
        )
        asset_id = insert_asset(connection, import_id=import_id, symbol="AEM")
        insert_article_asset_summary(
            connection,
            article_id=article_id,
            gmail_message_id=message_id,
            gmail_article_link_id=link_id,
            portfolio_import_id=import_id,
            asset_id=asset_id,
            symbol="AEM",
            inferred_sentiment="bullish",
            action_relevance="material_news",
            short_summary="Relevant article.",
            prompt_version="v1",
        )

        with self.assertRaises(GmailMarkReadError):
            mark_message_read_if_complete(connection, message_id, FakeGmailActions(fail=True))

        status = connection.execute(
            "SELECT status FROM gmail_messages WHERE id = ?",
            (message_id,),
        ).fetchone()["status"]
        self.assertEqual(status, "failed_mark_read")

    def _message_article_link(
        self,
        connection,
        *,
        link_status,
        gmail_message_id="gmail-1",
    ):
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        message_id = upsert_gmail_message(
            connection,
            gmail_message_id=gmail_message_id,
            gmail_thread_id="thread-1",
            sender="account@seekingalpha.com",
            subject="AEM update",
            received_at="2026-05-23T10:00:00+00:00",
            status="scanned",
        )
        article_id = upsert_article(
            connection,
            canonical_url="https://seekingalpha.com/article/123",
            source_url="https://seekingalpha.com/article/123",
            headline="AEM update",
        )
        link_id = upsert_gmail_article_link(
            connection,
            gmail_message_id=message_id,
            portfolio_import_id=import_id,
            prompt_version="v1",
            source_url="https://seekingalpha.com/article/123",
            article_id=article_id,
            status=link_status,
        )
        return message_id, import_id, article_id, link_id
