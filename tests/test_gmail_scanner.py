import base64
import sqlite3
import unittest

from portfolio_news_agent.gmail_scanner import (
    build_gmail_query,
    extract_seeking_alpha_urls,
    scan_unread_seeking_alpha_messages,
)
from portfolio_news_agent.storage import (
    create_portfolio_import,
    migrate,
    update_gmail_article_link_status,
    upsert_gmail_article_link,
    upsert_gmail_message,
)


class FakeGmailClient:
    def __init__(self, messages):
        self.messages = {message["id"]: message for message in messages}
        self.queries = []

    def search_messages(self, query):
        self.queries.append(query)
        return [{"id": message_id} for message_id in self.messages]

    def get_message(self, message_id):
        return self.messages[message_id]


class GmailScannerTests(unittest.TestCase):
    def test_query_uses_sender_and_unread_filter(self):
        self.assertEqual(
            build_gmail_query("account@seekingalpha.com"),
            "from:account@seekingalpha.com is:unread",
        )

    def test_extracts_and_canonicalizes_seeking_alpha_urls(self):
        body = """
        <a href="https://seekingalpha.com/article/123-aem-update?mailingid=abc">Read</a>
        https://seekingalpha.com/news/456-gold-rallies?utm_source=email
        <a href="https://example.com/not-seeking-alpha">Ignore</a>
        """

        urls = extract_seeking_alpha_urls(body)

        self.assertEqual(
            urls,
            [
                "https://seekingalpha.com/article/123-aem-update",
                "https://seekingalpha.com/news/456-gold-rallies",
            ],
        )

    def test_extracts_article_urls_from_sailthru_tracking_links(self):
        target = "https://seekingalpha.com/article/4907992-stock-market-update?mailingid=abc"
        encoded_target = base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
        body = f"""
        <a href="https://email-st.seekingalpha.com/click/45849149.12747/{encoded_target}/abc123">
          Read article
        </a>
        """

        urls = extract_seeking_alpha_urls(body)

        self.assertEqual(
            urls,
            ["https://seekingalpha.com/article/4907992-stock-market-update"],
        )

    def test_extracts_news_urls_from_sailthru_tracking_links(self):
        target = "https://seekingalpha.com/news/4596018-arista-ai-tailwinds?source=email"
        encoded_target = base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
        body = (
            "https://email-st.seekingalpha.com/click/45849149.12747/"
            f"{encoded_target}/tracking"
        )

        urls = extract_seeking_alpha_urls(body)

        self.assertEqual(
            urls,
            ["https://seekingalpha.com/news/4596018-arista-ai-tailwinds"],
        )

    def test_extracts_nested_ref_url_from_sailthru_email_auth_links(self):
        target = (
            "https://seekingalpha.com/account/email-auth?"
            "ref=https%3A%2F%2Fseekingalpha.com%2Farticle%2F4908284-axon-opportunity"
            "%3Fposition%3Drta_analysis_fullsummary_main_0_title"
            "&sailthru_auth_param=secret-value"
        )
        encoded_target = base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii").rstrip("=")
        body = (
            "https://email-st.seekingalpha.com/click/45849156.2841/"
            f"{encoded_target}/tracking"
        )

        urls = extract_seeking_alpha_urls(body)

        self.assertEqual(
            urls,
            ["https://seekingalpha.com/article/4908284-axon-opportunity"],
        )

    def test_scan_stores_message_metadata_and_link_rows(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        message = self._message(
            message_id="gmail-1",
            thread_id="thread-1",
            subject="AEM update",
            html_body=(
                '<a href="https://seekingalpha.com/article/123-aem-update?utm=email">'
                "Read article</a>"
            ),
        )
        client = FakeGmailClient([message])

        result = scan_unread_seeking_alpha_messages(
            connection,
            gmail_client=client,
            sender="account@seekingalpha.com",
            portfolio_import_id=import_id,
            prompt_version="v1",
        )
        stored_message = connection.execute(
            "SELECT gmail_message_id, gmail_thread_id, sender, subject, status FROM gmail_messages"
        ).fetchone()
        stored_links = connection.execute(
            "SELECT source_url, status FROM gmail_article_links ORDER BY id"
        ).fetchall()

        self.assertEqual(client.queries, ["from:account@seekingalpha.com is:unread"])
        self.assertEqual(result.emails_found, 1)
        self.assertEqual(result.links_found, 1)
        self.assertEqual(result.links_queued, 1)
        self.assertEqual(tuple(stored_message), ("gmail-1", "thread-1", "account@seekingalpha.com", "AEM update", "scanned"))
        self.assertEqual(
            [tuple(row) for row in stored_links],
            [("https://seekingalpha.com/article/123-aem-update", "queued")],
        )

    def test_scan_skips_terminal_link_for_same_import_and_prompt_version(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        message_row_id = upsert_gmail_message(
            connection,
            gmail_message_id="gmail-1",
            gmail_thread_id="thread-1",
            sender="account@seekingalpha.com",
            subject="AEM update",
            received_at="2026-05-23T10:00:00+00:00",
            status="scanned",
        )
        link_id = upsert_gmail_article_link(
            connection,
            gmail_message_id=message_row_id,
            portfolio_import_id=import_id,
            prompt_version="v1",
            source_url="https://seekingalpha.com/article/123-aem-update",
        )
        update_gmail_article_link_status(
            connection,
            link_id=link_id,
            status="irrelevant_seen",
        )
        client = FakeGmailClient(
            [
                self._message(
                    message_id="gmail-1",
                    thread_id="thread-1",
                    subject="AEM update",
                    html_body='<a href="https://seekingalpha.com/article/123-aem-update">Read</a>',
                )
            ]
        )

        result = scan_unread_seeking_alpha_messages(
            connection,
            gmail_client=client,
            sender="account@seekingalpha.com",
            portfolio_import_id=import_id,
            prompt_version="v1",
        )
        link_rows = connection.execute(
            "SELECT status FROM gmail_article_links WHERE source_url = ?",
            ("https://seekingalpha.com/article/123-aem-update",),
        ).fetchall()

        self.assertEqual(result.links_found, 1)
        self.assertEqual(result.links_queued, 0)
        self.assertEqual(result.links_skipped, 1)
        self.assertEqual([row["status"] for row in link_rows], ["irrelevant_seen"])

    def test_scan_creates_new_link_when_prompt_version_changes(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        message_row_id = upsert_gmail_message(
            connection,
            gmail_message_id="gmail-1",
            gmail_thread_id="thread-1",
            sender="account@seekingalpha.com",
            subject="AEM update",
            received_at="2026-05-23T10:00:00+00:00",
            status="scanned",
        )
        old_link_id = upsert_gmail_article_link(
            connection,
            gmail_message_id=message_row_id,
            portfolio_import_id=import_id,
            prompt_version="v1",
            source_url="https://seekingalpha.com/article/123-aem-update",
        )
        update_gmail_article_link_status(connection, link_id=old_link_id, status="irrelevant_seen")
        client = FakeGmailClient(
            [
                self._message(
                    message_id="gmail-1",
                    thread_id="thread-1",
                    subject="AEM update",
                    html_body='<a href="https://seekingalpha.com/article/123-aem-update">Read</a>',
                )
            ]
        )

        result = scan_unread_seeking_alpha_messages(
            connection,
            gmail_client=client,
            sender="account@seekingalpha.com",
            portfolio_import_id=import_id,
            prompt_version="v2",
        )

        self.assertEqual(result.links_queued, 1)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM gmail_article_links").fetchone()[0],
            2,
        )

    def test_scan_can_limit_unread_messages(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        client = FakeGmailClient(
            [
                self._message(
                    message_id=f"gmail-{index}",
                    thread_id=f"thread-{index}",
                    subject=f"AEM update {index}",
                    html_body=(
                        f'<a href="https://seekingalpha.com/article/{index}-aem-update">'
                        "Read</a>"
                    ),
                )
                for index in range(3)
            ]
        )

        result = scan_unread_seeking_alpha_messages(
            connection,
            gmail_client=client,
            sender="account@seekingalpha.com",
            portfolio_import_id=import_id,
            prompt_version="v2",
            max_emails=2,
        )

        self.assertEqual(result.emails_found, 2)
        self.assertEqual(result.links_queued, 2)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM gmail_messages").fetchone()[0],
            2,
        )

    def _message(self, message_id, thread_id, subject, html_body):
        encoded_body = base64.urlsafe_b64encode(html_body.encode("utf-8")).decode("ascii")
        return {
            "id": message_id,
            "threadId": thread_id,
            "internalDate": "1779549600000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "account@seekingalpha.com"},
                    {"name": "Subject", "value": subject},
                    {"name": "Date", "value": "Sat, 23 May 2026 10:00:00 +0000"},
                ],
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {"data": encoded_body},
                    }
                ],
            },
        }
