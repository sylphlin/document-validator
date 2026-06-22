# tests/test_drive_tool.py
from unittest.mock import MagicMock

import pytest

from agent.drive_tool import fetch_drive_file_oauth


class _FakeOAuth2:
    def __init__(self, access_token=None):
        self.access_token = access_token


class _FakeAuthCredential:
    def __init__(self, access_token=None):
        self.oauth2 = _FakeOAuth2(access_token) if access_token else None


class _FakeToolContext:
    def __init__(self, auth_response=None):
        self._auth_response = auth_response
        self.requested_credential = False

    def get_auth_response(self, auth_config):
        return self._auth_response

    def request_credential(self, auth_config):
        self.requested_credential = True


@pytest.fixture(autouse=True)
def oauth_client_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret")


def test_returns_pending_auth_when_no_credential_yet():
    tool_context = _FakeToolContext(auth_response=None)
    result = fetch_drive_file_oauth("abc123", tool_context)
    assert result["status"] == "pending_auth"
    assert tool_context.requested_credential is True


def test_returns_pending_auth_when_credential_has_no_token():
    tool_context = _FakeToolContext(auth_response=_FakeAuthCredential(access_token=None))
    result = fetch_drive_file_oauth("abc123", tool_context)
    assert result["status"] == "pending_auth"
    assert tool_context.requested_credential is True


def test_fetches_text_file_content_once_authorized(monkeypatch):
    tool_context = _FakeToolContext(auth_response=_FakeAuthCredential(access_token="real-token"))

    monkeypatch.setattr("agent.drive_tool.Credentials", MagicMock())
    monkeypatch.setattr("agent.drive_tool.build", MagicMock(return_value=MagicMock()))

    import agent.drive_tool as drive_tool_module

    monkeypatch.setattr(drive_tool_module._fdf, "extract_id", lambda url_or_id: "file123")
    monkeypatch.setattr(
        drive_tool_module._fdf,
        "get_metadata",
        lambda service, file_id: {"id": file_id, "name": "notes.md", "mimeType": "text/markdown"},
    )
    monkeypatch.setattr(drive_tool_module._fdf, "download_text_content", lambda service, file_id: "# Notes\ncontent")

    result = fetch_drive_file_oauth("https://drive.google.com/file/d/file123/view", tool_context)

    assert result["status"] == "ok"
    assert result["name"] == "notes.md"
    assert result["content"] == "# Notes\ncontent"
    assert tool_context.requested_credential is False


def test_lists_folder_contents_once_authorized(monkeypatch):
    tool_context = _FakeToolContext(auth_response=_FakeAuthCredential(access_token="real-token"))

    monkeypatch.setattr("agent.drive_tool.Credentials", MagicMock())
    monkeypatch.setattr("agent.drive_tool.build", MagicMock(return_value=MagicMock()))

    import agent.drive_tool as drive_tool_module

    monkeypatch.setattr(drive_tool_module._fdf, "extract_id", lambda url_or_id: "folder123")
    monkeypatch.setattr(
        drive_tool_module._fdf,
        "get_metadata",
        lambda service, file_id: {
            "id": file_id,
            "name": "Submissions",
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    monkeypatch.setattr(
        drive_tool_module._fdf,
        "list_folder",
        lambda service, folder_id: [{"id": "f1", "name": "doc.pdf", "mimeType": "application/pdf"}],
    )

    result = fetch_drive_file_oauth("https://drive.google.com/drive/folders/folder123", tool_context)

    assert result["status"] == "folder"
    assert result["name"] == "Submissions"
    assert result["files"] == [{"id": "f1", "name": "doc.pdf", "mimeType": "application/pdf"}]


def test_returns_error_when_drive_api_fails(monkeypatch):
    tool_context = _FakeToolContext(auth_response=_FakeAuthCredential(access_token="real-token"))

    monkeypatch.setattr("agent.drive_tool.Credentials", MagicMock())
    monkeypatch.setattr("agent.drive_tool.build", MagicMock(return_value=MagicMock()))

    import agent.drive_tool as drive_tool_module

    def _raise_extract_id(url_or_id):
        raise ValueError("Could not extract a Drive file/folder ID from: not-a-valid-id")

    monkeypatch.setattr(drive_tool_module._fdf, "extract_id", _raise_extract_id)

    result = fetch_drive_file_oauth("not-a-valid-id", tool_context)

    assert result["status"] == "error"
    assert "not-a-valid-id" in result["message"]
