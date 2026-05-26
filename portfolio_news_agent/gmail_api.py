from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ModuleNotFoundError:
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailSetupError(RuntimeError):
    """Raised when Gmail OAuth files or dependencies are not ready."""


class GmailApiError(RuntimeError):
    """Raised when Gmail API calls fail after setup."""


class GmailApiClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    def search_messages(self, query: str) -> list[dict[str, Any]]:
        response = _execute_gmail_request(
            self._service.users()
            .messages()
            .list(userId="me", q=query)
        )
        return response.get("messages", [])

    def get_message(self, message_id: str) -> dict[str, Any]:
        return _execute_gmail_request(
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
        )

    def mark_read(self, gmail_message_id: str) -> None:
        _execute_gmail_request(
            self._service.users()
            .messages()
            .modify(
                userId="me",
                id=gmail_message_id,
                body={"removeLabelIds": ["UNREAD"]},
            )
        )


def build_gmail_service(
    *,
    credentials_path: str | Path,
    token_path: str | Path,
) -> Any:
    _ensure_google_dependencies(require_flow=False)
    credentials_path = Path(credentials_path)
    token_path = Path(token_path)
    credentials = None

    if token_path.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
        except Exception as exc:
            raise GmailSetupError(
                f"Gmail token file is invalid: {token_path}. Delete it and rerun OAuth."
            ) from exc

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            _ensure_google_dependencies(require_flow=True)
            validate_gmail_credentials_file(credentials_path)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path),
                GMAIL_SCOPES,
            )
            credentials = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=credentials)


def validate_gmail_credentials_file(credentials_path: str | Path) -> None:
    path = Path(credentials_path)
    if not path.exists():
        raise GmailSetupError(
            "Missing Google OAuth desktop client JSON at "
            f"{path}. Download it from Google Cloud Console and save it as "
            "data/secrets/gmail_credentials.json."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GmailSetupError(
            f"Gmail credentials file is not valid JSON: {path}."
        ) from exc

    if not isinstance(data, dict) or not (data.get("installed") or data.get("web")):
        raise GmailSetupError(
            f"Gmail credentials file is not a Google OAuth client JSON: {path}."
        )


def _execute_gmail_request(request: Any) -> Any:
    try:
        return request.execute()
    except Exception as exc:
        if _looks_like_google_http_error(exc):
            reason = _google_error_reason(exc)
            if reason == "accessNotConfigured":
                raise GmailSetupError(_gmail_api_disabled_message(exc)) from exc
            status = _google_error_status(exc)
            raise GmailApiError(f"Gmail API request failed with status {status}.") from exc
        raise


def _looks_like_google_http_error(exc: Exception) -> bool:
    return hasattr(exc, "resp") and hasattr(exc, "content")


def _google_error_reason(exc: Exception) -> str | None:
    payload = _google_error_payload(exc)
    errors = payload.get("error", {}).get("errors", [])
    if errors and isinstance(errors[0], dict):
        reason = errors[0].get("reason")
        if isinstance(reason, str):
            return reason
    reason = payload.get("error", {}).get("status")
    if isinstance(reason, str):
        return reason
    return None


def _google_error_status(exc: Exception) -> str:
    resp = getattr(exc, "resp", {})
    if isinstance(resp, dict):
        return str(resp.get("status") or "unknown")
    return str(getattr(resp, "status", "unknown"))


def _google_error_payload(exc: Exception) -> dict[str, Any]:
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    try:
        payload = json.loads(str(content))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _gmail_api_disabled_message(exc: Exception) -> str:
    message = _google_error_payload(exc).get("error", {}).get("message", "")
    project_id = _project_id_from_message(message)
    enable_url = "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
    if project_id:
        enable_url = f"{enable_url}?project={project_id}"
    return (
        "Gmail API is disabled for the Google Cloud project"
        f"{f' {project_id}' if project_id else ''}. Enable Gmail API at "
        f"{enable_url}, wait a few minutes, then rerun `--check-gmail`."
    )


def _project_id_from_message(message: Any) -> str | None:
    if not isinstance(message, str):
        return None
    marker = "project "
    start = message.find(marker)
    if start == -1:
        return None
    remainder = message[start + len(marker) :]
    project_id = []
    for character in remainder:
        if character.isdigit():
            project_id.append(character)
        elif project_id:
            break
    return "".join(project_id) or None


def _ensure_google_dependencies(*, require_flow: bool) -> None:
    values = [Request, Credentials, build]
    if require_flow:
        values.append(InstalledAppFlow)
    if any(value is None for value in values):
        raise GmailSetupError(
            "Gmail API dependencies are not installed. Run `pip install -r requirements.txt`."
        )
