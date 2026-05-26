import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portfolio_news_agent.gmail_api import (
    GmailApiClient,
    GmailApiError,
    GmailSetupError,
    build_gmail_service,
    validate_gmail_credentials_file,
)


class FakeExecute:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeFailingExecute:
    def __init__(self, exc):
        self.exc = exc

    def execute(self):
        raise self.exc


class FakeMessages:
    def __init__(self):
        self.calls = []

    def list(self, userId, q):
        self.calls.append(("list", userId, q))
        return FakeExecute({"messages": [{"id": "gmail-1"}]})

    def get(self, userId, id, format):
        self.calls.append(("get", userId, id, format))
        return FakeExecute({"id": id, "payload": {}})

    def modify(self, userId, id, body):
        self.calls.append(("modify", userId, id, body))
        return FakeExecute({"id": id})


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self):
        self.messages = FakeMessages()

    def users(self):
        return FakeUsers(self.messages)


class FakeServiceWithMessages:
    def __init__(self, messages):
        self.messages = messages

    def users(self):
        return FakeUsers(self.messages)


class FakeHttpError(Exception):
    def __init__(self, status, reason, content):
        self.resp = {"status": str(status), "reason": reason}
        self.content = content
        self.uri = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

    def __str__(self):
        return self.content.decode("utf-8", errors="replace")


class GmailApiTests(unittest.TestCase):
    def test_client_search_get_and_mark_read_use_expected_gmail_calls(self):
        service = FakeService()
        client = GmailApiClient(service)

        self.assertEqual(client.search_messages("from:a is:unread"), [{"id": "gmail-1"}])
        self.assertEqual(client.get_message("gmail-1")["id"], "gmail-1")
        client.mark_read("gmail-1")

        self.assertEqual(
            service.messages.calls,
            [
                ("list", "me", "from:a is:unread"),
                ("get", "me", "gmail-1", "full"),
                ("modify", "me", "gmail-1", {"removeLabelIds": ["UNREAD"]}),
            ],
        )

    def test_client_reports_disabled_gmail_api_as_setup_error(self):
        class FailingMessages:
            def list(self, userId, q):
                return FakeFailingExecute(
                    FakeHttpError(
                        403,
                        "Forbidden",
                        (
                            b'{"error":{"code":403,"message":"Gmail API has not been used '
                            b'in project 1040268071697 before or it is disabled.","errors":'
                            b'[{"reason":"accessNotConfigured"}]}}'
                        ),
                    )
                )

        client = GmailApiClient(FakeServiceWithMessages(FailingMessages()))

        with self.assertRaises(GmailSetupError) as context:
            client.search_messages("from:a is:unread")

        message = str(context.exception)
        self.assertIn("Gmail API is disabled", message)
        self.assertIn("1040268071697", message)
        self.assertIn("gmail.googleapis.com", message)

    def test_client_reports_other_gmail_http_errors_without_traceback_details(self):
        class FailingMessages:
            def get(self, userId, id, format):
                return FakeFailingExecute(
                    FakeHttpError(
                        500,
                        "Internal Server Error",
                        b'{"error":{"message":"backend failed secret-value"}}',
                    )
                )

        client = GmailApiClient(FakeServiceWithMessages(FailingMessages()))

        with self.assertRaises(GmailApiError) as context:
            client.get_message("gmail-1")

        self.assertIn("Gmail API request failed", str(context.exception))
        self.assertIn("500", str(context.exception))
        self.assertNotIn("secret-value", str(context.exception))

    def test_build_gmail_service_refreshes_existing_token(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            token_path = Path(tmp_dir) / "token.json"
            credentials_path = Path(tmp_dir) / "credentials.json"
            token_path.write_text("{}", encoding="utf-8")
            credentials_path.write_text("{}", encoding="utf-8")

            class FakeCredentials:
                valid = False
                expired = True
                refresh_token = "refresh-token"

                def refresh(self, request):
                    self.valid = True

                def to_json(self):
                    return '{"token":"refreshed"}'

            with (
                patch("portfolio_news_agent.gmail_api.Credentials") as credentials_cls,
                patch("portfolio_news_agent.gmail_api.Request"),
                patch("portfolio_news_agent.gmail_api.build") as build,
            ):
                credentials_cls.from_authorized_user_file.return_value = FakeCredentials()
                build.return_value = "service"

                service = build_gmail_service(
                    credentials_path=credentials_path,
                    token_path=token_path,
                )
                token_text = token_path.read_text(encoding="utf-8")

        self.assertEqual(service, "service")
        self.assertEqual(token_text, '{"token":"refreshed"}')
        build.assert_called_once()

    def test_validate_gmail_credentials_file_reports_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            credentials_path = Path(tmp_dir) / "gmail_credentials.json"

            with self.assertRaises(GmailSetupError) as context:
                validate_gmail_credentials_file(credentials_path)

        self.assertIn("Google OAuth desktop client JSON", str(context.exception))
        self.assertIn("gmail_credentials.json", str(context.exception))

    def test_validate_gmail_credentials_file_reports_invalid_json_without_leaking_content(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            credentials_path = Path(tmp_dir) / "gmail_credentials.json"
            credentials_path.write_text("{not-json secret-value", encoding="utf-8")

            with self.assertRaises(GmailSetupError) as context:
                validate_gmail_credentials_file(credentials_path)

        self.assertIn("valid JSON", str(context.exception))
        self.assertNotIn("secret-value", str(context.exception))

    def test_validate_gmail_credentials_file_requires_oauth_client_shape(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            credentials_path = Path(tmp_dir) / "gmail_credentials.json"
            credentials_path.write_text('{"api_key":"secret-value"}', encoding="utf-8")

            with self.assertRaises(GmailSetupError) as context:
                validate_gmail_credentials_file(credentials_path)

        self.assertIn("OAuth client", str(context.exception))
        self.assertNotIn("secret-value", str(context.exception))

    def test_build_gmail_service_validates_credentials_before_oauth_flow(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            credentials_path = Path(tmp_dir) / "gmail_credentials.json"
            token_path = Path(tmp_dir) / "gmail_token.json"

            with (
                patch("portfolio_news_agent.gmail_api.InstalledAppFlow") as flow_cls,
                self.assertRaises(GmailSetupError),
            ):
                build_gmail_service(
                    credentials_path=credentials_path,
                    token_path=token_path,
                )

        flow_cls.from_client_secrets_file.assert_not_called()
