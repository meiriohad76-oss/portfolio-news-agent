import base64
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from portfolio_news_agent.config import AppConfig
from portfolio_news_agent.orchestrator import (
    OrchestratorDependencies,
    build_analysis_client,
    run_once,
)
from portfolio_news_agent.storage import migrate


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


class StateInspectingArticleSession:
    def __init__(self, connection, html_by_url):
        self.connection = connection
        self.html_by_url = html_by_url
        self.opened = []
        self.status_seen_before_open = None

    def open(self, url):
        self.opened.append(url)
        self.status_seen_before_open = self.connection.execute(
            "SELECT status, status_detail FROM gmail_article_links WHERE source_url = ?",
            (url,),
        ).fetchone()
        return self.html_by_url[url]


class RecoveringManualArticleSession:
    def __init__(self):
        self.opened = []
        self.manual_opened = []

    def open(self, url):
        self.opened.append(url)
        return "<html>Please sign in to continue</html>"

    def open_for_manual_session(self, url, *, prompt, prompt_message):
        self.manual_opened.append((url, prompt_message))
        return "<article><h1>AEM update</h1><p>AEM margins improved after login.</p></article>"


class FakeAnalysisClient:
    def __init__(self, response):
        self.response = response

    def create_structured_response(self, *, model, messages, schema):
        return self.response


class BrokenGmailIntegration:
    marked_read = []

    def search_messages(self, query):
        raise RuntimeError("gmail query failed")

    def get_message(self, message_id):
        raise AssertionError("get_message should not be called")


class OrchestratorTests(unittest.TestCase):
    def test_build_analysis_client_uses_local_ollama_when_configured(self):
        config = AppConfig(
            portfolio_file=Path("portfolio.csv"),
            gmail_sender="account@seekingalpha.com",
            database_path=Path("data/portfolio_news.db"),
            browser_profile_dir=Path("data/browser-profile"),
            openai_model="gpt-5-nano",
            llm_provider="local_ollama",
            local_llm_base_url="http://10.100.102.18:11434",
            local_llm_model="qwen3.5:4b",
        )

        client = build_analysis_client(config)

        self.assertEqual(client.__class__.__name__, "OllamaChatClient")
        self.assertEqual(client.base_url, "http://10.100.102.18:11434")

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

    def test_run_once_marks_article_link_processing_before_opening_browser(self):
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
            article_session = StateInspectingArticleSession(
                connection,
                {
                    "https://seekingalpha.com/article/123-aem-update": (
                        "<article><h1>AEM update</h1><p>Margins improved.</p></article>"
                    )
                },
            )
            dependencies = OrchestratorDependencies(
                gmail_client=gmail,
                gmail_actions=gmail,
                article_session=article_session,
                analysis_client=FakeAnalysisClient(
                    {
                        "relevant_assets": [],
                        "irrelevant_reason": "No material effect on current holdings.",
                    }
                ),
            )

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=dependencies,
                connection=connection,
            )

        self.assertEqual(result.status, "success")
        self.assertIsNotNone(article_session.status_seen_before_open)
        self.assertEqual(article_session.status_seen_before_open["status"], "processing")
        self.assertIn(
            "Opening article and running LLM analysis",
            article_session.status_seen_before_open["status_detail"],
        )

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
                ),
                connection=connection,
            )
            link_status = connection.execute(
                "SELECT status FROM gmail_article_links"
            ).fetchone()["status"]

        self.assertEqual(result.status, "partial_failed")
        self.assertEqual(link_status, "failed_extract")
        self.assertEqual(gmail.marked_read, [])

    def test_run_once_recovers_login_required_article_before_marking_failed(self):
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
            article_session = RecoveringManualArticleSession()

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=OrchestratorDependencies(
                    gmail_client=gmail,
                    gmail_actions=gmail,
                    article_session=article_session,
                    analysis_client=FakeAnalysisClient(
                        {
                            "relevant_assets": [],
                            "irrelevant_reason": "No material effect on current holdings.",
                        }
                    ),
                ),
                connection=connection,
            )
            link_row = connection.execute(
                "SELECT status, status_detail FROM gmail_article_links"
            ).fetchone()

        self.assertEqual(result.status, "success")
        self.assertEqual(tuple(link_row), ("irrelevant_seen", "No material effect on current holdings."))
        self.assertEqual(len(article_session.manual_opened), 1)
        self.assertIn("log in", article_session.manual_opened[0][1].lower())
        self.assertEqual(gmail.marked_read, [])

    def test_run_once_can_limit_processed_articles(self):
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
                        '<a href="https://seekingalpha.com/article/101-aem-update">One</a>'
                        '<a href="https://seekingalpha.com/article/102-aem-update">Two</a>'
                        '<a href="https://seekingalpha.com/article/103-aem-update">Three</a>',
                    )
                ]
            )
            article_session = FakeArticleSession(
                {
                    "https://seekingalpha.com/article/101-aem-update": (
                        "<article><h1>AEM one</h1><p>AEM margins improved.</p></article>"
                    ),
                    "https://seekingalpha.com/article/102-aem-update": (
                        "<article><h1>AEM two</h1><p>AEM costs declined.</p></article>"
                    ),
                    "https://seekingalpha.com/article/103-aem-update": (
                        "<article><h1>AEM three</h1><p>AEM output rose.</p></article>"
                    ),
                }
            )

            result = run_once(
                config=self._config(workspace, portfolio_path),
                dependencies=OrchestratorDependencies(
                    gmail_client=gmail,
                    gmail_actions=gmail,
                    article_session=article_session,
                    analysis_client=FakeAnalysisClient(
                        {
                            "relevant_assets": [],
                            "irrelevant_reason": "No material effect on current holdings.",
                        }
                    ),
                ),
                connection=connection,
                max_articles=2,
            )
            link_statuses = connection.execute(
                "SELECT status FROM gmail_article_links ORDER BY source_url"
            ).fetchall()

        self.assertEqual(result.status, "success")
        self.assertEqual(result.articles_processed, 2)
        self.assertEqual(len(article_session.opened), 2)
        self.assertEqual(
            [row["status"] for row in link_statuses],
            ["irrelevant_seen", "irrelevant_seen", "queued"],
        )
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
