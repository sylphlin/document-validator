"""ADK-native Google Drive fetch tool using per-user OAuth 2.0 consent.

This only works when ADK can supply a ToolContext to drive the consent flow —
true for a deployed agent (Agent Engine / Gemini Enterprise), never for a
subprocess script invoked via start_job/check_job. That's why this tool lives
in agent/ as a proper ADK FunctionTool, not in skill/scripts/ alongside the
portable scripts.

The payoff: once the signed-in user grants consent, Drive calls run as that
user, so files just need to be shared with the user the normal way — no
per-file sharing with a deployed service account required. This is registered
in agent/agent.py only when GOOGLE_OAUTH_CLIENT_ID is configured; otherwise the
agent falls back to skill/scripts/fetch_drive_file.py (Application Default
Credentials — the service account on a real deployment).

Drive-API logic (extract_id, get_metadata, list_folder, download helpers) is
reused directly from fetch_drive_file.py rather than duplicated — those
functions already take an authenticated `service` object as a parameter and
don't care how it was authenticated, so the only thing that differs here is
how that service gets built.
"""

import importlib.util
import os
import tempfile
from pathlib import Path

from fastapi.openapi.models import OAuth2, OAuthFlowAuthorizationCode, OAuthFlows
from google.adk.auth import AuthConfig, AuthCredential, AuthCredentialTypes
from google.adk.auth.auth_credential import OAuth2Auth
from google.adk.tools import ToolContext
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


def _load_fetch_drive_file_module():
    script_path = Path(__file__).parent.parent / "skill" / "scripts" / "fetch_drive_file.py"
    spec = importlib.util.spec_from_file_location("fetch_drive_file", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_fdf = _load_fetch_drive_file_module()


def _build_auth_config() -> AuthConfig:
    client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
    auth_scheme = OAuth2(
        flows=OAuthFlows(
            authorizationCode=OAuthFlowAuthorizationCode(
                authorizationUrl="https://accounts.google.com/o/oauth2/v2/auth",
                tokenUrl="https://oauth2.googleapis.com/token",
                scopes={DRIVE_SCOPE: "Read-only access to Google Drive"},
            )
        )
    )
    raw_credential = AuthCredential(
        auth_type=AuthCredentialTypes.OAUTH2,
        oauth2=OAuth2Auth(client_id=client_id, client_secret=client_secret),
    )
    return AuthConfig(auth_scheme=auth_scheme, raw_auth_credential=raw_credential)


def fetch_drive_file_oauth(url_or_id: str, tool_context: ToolContext) -> dict:
    """Fetch a Google Drive file or list a folder, using the signed-in user's own
    Drive permissions instead of a service account.

    Prefer this over the fetch_drive_file.py script whenever it's available —
    files only need to be shared with the user normally, not with a deployed
    service account.

    Args:
        url_or_id: A Google Drive URL or bare file/folder ID.

    Returns a dict. If the user hasn't granted Drive access yet, returns
    {"status": "pending_auth", ...} — tell the user to complete the Google
    sign-in prompt, then call this again with the same url_or_id. Otherwise
    returns {"status": "ok", ...} with the file's metadata and content/path,
    {"status": "folder", ...} with its contents, or {"status": "error", ...}.
    """
    auth_config = _build_auth_config()
    auth_response = tool_context.get_auth_response(auth_config)
    if auth_response is None or not auth_response.oauth2 or not auth_response.oauth2.access_token:
        tool_context.request_credential(auth_config)
        return {
            "status": "pending_auth",
            "message": (
                "Waiting for the user to sign in and grant Drive access. Tell the "
                "user to complete the Google sign-in prompt, then call this tool "
                "again with the same url_or_id."
            ),
        }

    creds = Credentials(token=auth_response.oauth2.access_token)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        file_id = _fdf.extract_id(url_or_id)
        meta = _fdf.get_metadata(service, file_id)
    except Exception as e:
        return {"status": "error", "message": f"Could not access '{url_or_id}': {e}"}

    if meta["mimeType"] == "application/vnd.google-apps.folder":
        children = _fdf.list_folder(service, file_id)
        return {"status": "folder", "name": meta["name"], "files": children}

    if meta["mimeType"].startswith(_fdf.GOOGLE_NATIVE_MIME_PREFIX):
        out_path = str(Path(tempfile.gettempdir()) / f"{file_id}.pdf")
        _fdf.download_file(service, file_id, meta["mimeType"], out_path)
        return {"status": "ok", "name": meta["name"], "mimeType": "application/pdf", "path": out_path}

    if _fdf.is_text_mime(meta["mimeType"]):
        content = _fdf.download_text_content(service, file_id)
        return {"status": "ok", "name": meta["name"], "mimeType": meta["mimeType"], "content": content}

    out_path = str(Path(tempfile.gettempdir()) / f"{file_id}_{meta['name']}")
    _fdf.download_file(service, file_id, meta["mimeType"], out_path)
    return {"status": "ok", "name": meta["name"], "mimeType": meta["mimeType"], "path": out_path}
