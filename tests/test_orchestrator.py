import base64
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.config import AppConfig
from portfolio_news_agent.orchestrator import OrchestratorDependencies, run_once
from portfolio_news_agent.storage import migrate
from portfolio_news_agent.telegram_sender import TelegramSendError


class FakeGmailIntegration:
    def __init__(self, messages):
        self.messages = {message["id"]: message for message in messages}
        self.marked_read = []

    def search_messages(self, query):
        return [{"id": message_id} for message_id in self.messages]

    def get_message(self, message_id):
        return self.messages[message_id]

    def mark_read(self, gmail_message_id):
        self.marked_read.append(gmail_message_id)


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

    def create_structured_response(self, *, model, messages, schema):
        return self.response


class TelegramCapture:
    def __init__(self):
        self.messages = []

    def __call__(self, *, bot_token, chat_id, text):
        self.messages.append({"bot_token": bot_token, "chat_id": chat_id, "text": text})
        return {"ok": True}


class FailingTelegram:
    def __call__(self, *, bot_token, chat_id, text):
        raise TelegramSendError("Telegram down")


class BrokenGmailIntegration:
    marked_read = []

    def search_messages(self, query):
        raise RuntimeError("gmail query failed")

    def get_message(self, message_id):
        raise AssertionError("get_message should not be called")


class OrchestratorTests(unittest.TestCase):
    def test_run_once_processes_relevant_article_and_marks_message_read(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            self._write_portfolio(portfolio_path, [{"Symbol": "AEM", "Name": "Agnico Eagle Mines"}])
            connection = sqlite3.connect(":memory:")
            migrate(connection)
            gmail = FakeGmailIntegration(
                [
                    self._message(
                        "gmail-1",
                        '<a href="https://seekingalpha.com/article/123-aem-update?utm=email">Read</a>',
                    )
                ]
            )
            article_session = FakeArticleSession(
                {
                    "https://seekingalpha.com/article/123-aem-update": (
                        "<html><head><link rel='canonical' "
                        "href='https://seekingalpha.com/article/123-aem-update'></head>"
                        "<article><h1>AEM update</h1><p>Margins improved.</p></article></html>"
                    )
                }
            )
            telegram = TelegramCapture()
            dependencies = OrchestratorDependencies(
                gmail_client=gmail,
                gmail_actions=gmail,
                article_session=article_session,
                analysis_client=FakeAnalysisClient(
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
                ),
                telegram_sender=telegram,
            )

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=dependencies,
                connection=connection,
            )
            summary = connection.execute(
                "SELECT symbol, inferred_sentiment, action_relevance FROM article_asset_summaries"
            ).fetchone()
            link_status = connection.execute(
                "SELECT status FROM gmail_article_links"
            ).fetchone()["status"]
            message_status = connection.execute(
                "SELECT status FROM gmail_messages"
            ).fetchone()["status"]

        self.assertEqual(result.status, "success")
        self.assertEqual(result.summaries_created, 1)
        self.assertEqual(tuple(summary), ("AEM", "bullish", "material_news"))
        self.assertEqual(link_status, "processed_relevant")
        self.assertEqual(message_status, "processed_relevant")
        self.assertEqual(gmail.marked_read, ["gmail-1"])
        self.assertEqual(len(telegram.messages), 1)
        self.assertIn("AEM - bullish - material_news", telegram.messages[0]["text"])

    def test_run_once_records_irrelevant_article_without_marking_read(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            self._write_portfolio(portfolio_path, [{"Symbol": "AEM", "Name": "Agnico Eagle Mines"}])
            connection = sqlite3.connect(":memory:")
            migrate(connection)
            gmail = FakeGmailIntegration(
                [
                    self._message(
                        "gmail-1",
                        '<a href="https://seekingalpha.com/article/123-aem-update">Read</a>',
                    )
                ]
            )
            dependencies = OrchestratorDependencies(
                gmail_client=gmail,
                gmail_actions=gmail,
                article_session=FakeArticleSession(
                    {
                        "https://seekingalpha.com/article/123-aem-update": (
                            "<article><h1>AEM update</h1><p>Unrelated text.</p></article>"
                        )
                    }
                ),
                analysis_client=FakeAnalysisClient(
                    {
                        "relevant_assets": [],
                        "irrelevant_reason": "No material effect on current holdings.",
                    }
                ),
                telegram_sender=TelegramCapture(),
            )

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=dependencies,
                connection=connection,
            )
            link_status = connection.execute(
                "SELECT status FROM gmail_article_links"
            ).fetchone()["status"]
            summary_count = connection.execute(
                "SELECT COUNT(*) FROM article_asset_summaries"
            ).fetchone()[0]

        self.assertEqual(result.status, "success")
        self.assertEqual(result.summaries_created, 0)
        self.assertEqual(link_status, "irrelevant_seen")
        self.assertEqual(summary_count, 0)
        self.assertEqual(gmail.marked_read, [])

    def test_run_once_records_failed_extract_and_leaves_message_unread(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            self._write_portfolio(portfolio_path, [{"Symbol": "AEM", "Name": "Agnico Eagle Mines"}])
            connection = sqlite3.connect(":memory:")
            migrate(connection)
            gmail = FakeGmailIntegration(
                [
                    self._message(
                        "gmail-1",
                        '<a href="https://seekingalpha.com/article/123-aem-update">Read</a>',
                    )
                ]
            )

            class BrokenArticleSession:
                def open(self, url):
                    raise RuntimeError("browser failed")

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=OrchestratorDependencies(
                    gmail_client=gmail,
                    gmail_actions=gmail,
                    article_session=BrokenArticleSession(),
                    analysis_client=FakeAnalysisClient({"relevant_assets": [], "irrelevant_reason": None}),
                    telegram_sender=TelegramCapture(),
                ),
                connection=connection,
            )
            link_status = connection.execute(
                "SELECT status FROM gmail_article_links"
            ).fetchone()["status"]

        self.assertEqual(result.status, "partial_failed")
        self.assertEqual(link_status, "failed_extract")
        self.assertEqual(gmail.marked_read, [])

    def test_run_once_records_failed_telegram_and_leaves_message_unread(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            self._write_portfolio(portfolio_path, [{"Symbol": "AEM", "Name": "Agnico Eagle Mines"}])
            connection = sqlite3.connect(":memory:")
            migrate(connection)
            gmail = FakeGmailIntegration(
                [
                    self._message(
                        "gmail-1",
                        '<a href="https://seekingalpha.com/article/123-aem-update">Read</a>',
                    )
                ]
            )

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=OrchestratorDependencies(
                    gmail_client=gmail,
                    gmail_actions=gmail,
                    article_session=FakeArticleSession(
                        {
                            "https://seekingalpha.com/article/123-aem-update": (
                                "<article><h1>AEM update</h1><p>Margins improved.</p></article>"
                            )
                        }
                    ),
                    analysis_client=FakeAnalysisClient(
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
                                    "forward_data": [],
                                    "action_relevance": "material_news",
                                    "short_summary": "Relevant article.",
                                    "confidence": 0.9,
                                }
                            ],
                            "irrelevant_reason": None,
                        }
                    ),
                    telegram_sender=FailingTelegram(),
                ),
                connection=connection,
            )
            link_status = connection.execute(
                "SELECT status FROM gmail_article_links"
            ).fetchone()["status"]

        self.assertEqual(result.status, "partial_failed")
        self.assertEqual(link_status, "failed_telegram")
        self.assertEqual(gmail.marked_read, [])

    def test_run_once_marks_run_failed_when_scan_crashes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            portfolio_path = workspace / "portfolio.csv"
            self._write_portfolio(portfolio_path, [{"Symbol": "AEM", "Name": "Agnico Eagle Mines"}])
            connection = sqlite3.connect(":memory:")
            migrate(connection)

            with self.assertRaisesRegex(RuntimeError, "gmail query failed"):
                run_once(
                    config=self._config(workspace, portfolio_path),
                    dependencies=OrchestratorDependencies(
                        gmail_client=BrokenGmailIntegration(),
                        gmail_actions=BrokenGmailIntegration(),
                        article_session=FakeArticleSession({}),
                        analysis_client=FakeAnalysisClient(
                            {"relevant_assets": [], "irrelevant_reason": None}
                        ),
                        telegram_sender=TelegramCapture(),
                    ),
                    connection=connection,
                )
            run_row = connection.execute(
                "SELECT status, error, finished_at IS NOT NULL FROM runs"
            ).fetchone()

        self.assertEqual(tuple(run_row), ("failed", "gmail query failed", 1))

    def _config(self, workspace: Path, portfolio_path: Path) -> AppConfig:
        return AppConfig(
            portfolio_file=portfolio_path,
            gmail_sender="account@seekingalpha.com",
            database_path=workspace / "portfolio_news.db",
            browser_profile_dir=workspace / "browser-profile",
            openai_model="gpt-5-nano",
            prompt_version="v1",
            openai_api_key="openai-key",
            telegram_bot_token="telegram-token",
            telegram_chat_id="12345",
        )

    def _write_portfolio(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Symbol", "Name"])
            writer.writeheader()
            writer.writerows(rows)

    def _message(self, message_id: str, html_body: str):
        encoded = base64.urlsafe_b64encode(html_body.encode("utf-8")).decode("ascii")
        return {
            "id": message_id,
            "threadId": "thread-1",
            "internalDate": "1779549600000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "account@seekingalpha.com"},
                    {"name": "Subject", "value": "Seeking Alpha article"},
                ],
                "parts": [{"mimeType": "text/html", "body": {"data": encoded}}],
            },
        }
