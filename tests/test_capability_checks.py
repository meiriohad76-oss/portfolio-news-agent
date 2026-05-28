import base64
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.capability_checks import (
    analyze_url_against_portfolio,
    check_gmail_access,
    check_seeking_alpha_session,
)
from portfolio_news_agent.config import AppConfig


class FakeGmailClient:
    def __init__(self, messages):
        self.messages = {message["id"]: message for message in messages}
        self.queries = []
        self.fetched = []

    def search_messages(self, query):
        self.queries.append(query)
        return [{"id": message_id} for message_id in self.messages]

    def get_message(self, message_id):
        self.fetched.append(message_id)
        return self.messages[message_id]


class FakeManualSession:
    def __init__(self, open_html, manual_html=None):
        self.open_html = open_html
        self.manual_html = manual_html if manual_html is not None else open_html
        self.opened = []
        self.manual_calls = []

    def open(self, url):
        self.opened.append(url)
        return self.open_html

    def open_for_manual_session(self, url, *, prompt, prompt_message):
        self.manual_calls.append((url, prompt_message))
        prompt(prompt_message)
        return self.manual_html


class FakeArticleSession:
    def __init__(self, html_by_url):
        self.html_by_url = html_by_url
        self.opened = []

    def open(self, url):
        self.opened.append(url)
        return self.html_by_url[url]


class FakeAnalysisClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create_structured_response(self, *, model, messages, schema):
        self.calls.append({"model": model, "messages": messages, "schema": schema})
        return self.response


class CapabilityCheckTests(unittest.TestCase):
    def test_check_gmail_access_lists_unread_sa_messages_and_links(self):
        message = self._message(
            "gmail-1",
            "Portfolio update",
            '<a href="https://seekingalpha.com/article/123-aem-update?utm=email">Read</a>',
        )
        gmail = FakeGmailClient([message])

        result = check_gmail_access(
            config=self._config(Path("workspace"), Path("portfolio.csv")),
            gmail_client=gmail,
            limit=5,
        )

        self.assertEqual(gmail.queries, ["from:account@seekingalpha.com is:unread"])
        self.assertEqual(gmail.fetched, ["gmail-1"])
        self.assertEqual(result.emails_found, 1)
        self.assertEqual(result.links_found, 1)
        self.assertEqual(result.messages[0].subject, "Portfolio update")
        self.assertEqual(
            result.messages[0].seeking_alpha_links,
            ["https://seekingalpha.com/article/123-aem-update"],
        )

    def test_check_seeking_alpha_session_uses_manual_browser_and_reports_article_state(self):
        accessible_html = (
            "<html><head><link rel='canonical' href='https://seekingalpha.com/article/123-aem'>"
            "</head><article><h1>AEM update</h1><p>Readable text.</p></article></html>"
        )
        session = FakeManualSession(
            "<html>Please sign in to continue</html>",
            manual_html=accessible_html,
        )
        prompts = []

        result = check_seeking_alpha_session(
            "https://seekingalpha.com/article/123-aem",
            session=session,
            prompt=prompts.append,
        )

        self.assertEqual(result.access_state, "accessible")
        self.assertEqual(result.headline, "AEM update")
        self.assertEqual(result.body_characters, len("AEM update\nReadable text."))
        self.assertEqual(len(session.manual_calls), 1)
        self.assertIn("Seeking Alpha", prompts[0])

    def test_check_seeking_alpha_session_does_not_prompt_when_already_accessible(self):
        session = FakeManualSession(
            "<article><h1>AEM update</h1><p>Readable text.</p></article>"
        )
        prompts = []

        result = check_seeking_alpha_session(
            "https://seekingalpha.com/article/123-aem",
            session=session,
            prompt=prompts.append,
        )

        self.assertEqual(result.access_state, "accessible")
        self.assertEqual(result.headline, "AEM update")
        self.assertEqual(session.opened, ["https://seekingalpha.com/article/123-aem"])
        self.assertEqual(session.manual_calls, [])
        self.assertEqual(prompts, [])

    def test_check_seeking_alpha_session_can_skip_manual_recovery_for_automation(self):
        session = FakeManualSession("<html>Access to this page has been denied</html>")
        prompts = []

        result = check_seeking_alpha_session(
            "https://seekingalpha.com/article/123-aem",
            session=session,
            prompt=prompts.append,
            allow_manual_recovery=False,
        )

        self.assertEqual(result.access_state, "challenge_required")
        self.assertEqual(result.body_characters, 0)
        self.assertEqual(session.manual_calls, [])
        self.assertEqual(prompts, [])

    def test_analyze_url_fetches_article_imports_portfolio_and_returns_analysis(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            self._write_portfolio(
                portfolio_path,
                [{"Symbol": "AEM", "Name": "Agnico Eagle Mines"}],
            )
            session = FakeArticleSession(
                {
                    "https://seekingalpha.com/article/123-aem": (
                        "<article><h1>AEM update</h1><p>Margins improved.</p></article>"
                    )
                }
            )
            analysis_client = FakeAnalysisClient(
                {
                    "relevant_assets": [
                        {
                            "symbol": "AEM",
                            "company_name": "Agnico Eagle Mines",
                            "theme": "bullish",
                            "author_rating": None,
                            "quant_rating": None,
                            "wall_street_rating": None,
                            "inferred_sentiment": "bullish",
                            "price_targets": [],
                            "forward_data": ["Higher margins"],
                            "action_relevance": "material_news",
                            "short_summary": "Margins improved.",
                            "confidence": 0.9,
                        }
                    ],
                    "irrelevant_reason": None,
                }
            )

            connection = sqlite3.connect(":memory:")
            try:
                result = analyze_url_against_portfolio(
                    config=self._config(workspace, portfolio_path),
                    url="https://seekingalpha.com/article/123-aem",
                    article_session=session,
                    analysis_client=analysis_client,
                    connection=connection,
                )
            finally:
                connection.close()

        self.assertEqual(result.headline, "AEM update")
        self.assertEqual(result.assets_loaded, 1)
        self.assertEqual(result.relevant_assets[0]["symbol"], "AEM")
        self.assertEqual(result.irrelevant_reason, None)
        self.assertEqual(analysis_client.calls[0]["model"], "gpt-5-nano")

    def _config(self, workspace: Path, portfolio_path: Path) -> AppConfig:
        return AppConfig(
            portfolio_file=portfolio_path,
            gmail_sender="account@seekingalpha.com",
            database_path=workspace / "portfolio_news.db",
            browser_profile_dir=workspace / "browser-profile",
            openai_model="gpt-5-nano",
            prompt_version="v1",
            openai_api_key="openai-key",
        )

    def _write_portfolio(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Symbol", "Name"])
            writer.writeheader()
            writer.writerows(rows)

    def _message(self, message_id: str, subject: str, html_body: str):
        encoded = base64.urlsafe_b64encode(html_body.encode("utf-8")).decode("ascii")
        return {
            "id": message_id,
            "threadId": "thread-1",
            "internalDate": "1779549600000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "account@seekingalpha.com"},
                    {"name": "Subject", "value": subject},
                ],
                "parts": [{"mimeType": "text/html", "body": {"data": encoded}}],
            },
        }


if __name__ == "__main__":
    unittest.main()
