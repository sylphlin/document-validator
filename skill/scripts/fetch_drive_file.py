#!/usr/bin/env python3
"""Fetch a file (or list a folder) from Google Drive, by URL or bare file ID.

This calls the Google Drive API directly rather than relying on any chat-client
MCP connector, since this skill may run in environments — e.g. an ADK agent
deployed on Google Agent Engine — where no such connector exists. Authentication
uses Application Default Credentials (ADC): the deployed service account on
Google Cloud, or a locally configured `gcloud auth application-default login`
credential during development.

The target file (or every file inside, if it's a folder) must be shared with
whatever identity these credentials resolve to, or the API call fails with a
403/404. For a service account, that means sharing the file with the service
account's email address — not just "anyone with the link," which the service
account does not implicitly have access to.

Usage:
  python3 fetch_drive_file.py "https://drive.google.com/file/d/abc123/view" --out /tmp/doc.pdf
  python3 fetch_drive_file.py abc123 --out /tmp/doc.pdf
  python3 fetch_drive_file.py "https://drive.google.com/drive/folders/xyz789" --list-only
  python3 fetch_drive_file.py abc123          # no --out: just print metadata
  python3 fetch_drive_file.py abc123 --print-content   # plain-text/Markdown files only

--print-content reads a plain-text or Markdown file's actual content straight to
stdout — for a checklist or reference doc that's already text, not a PDF needing
extract_pdf_text.py. There is otherwise no supported way to get a downloaded
file's content back into context (read_asset only reaches files bundled with the
skill itself, not anything fetched at runtime) — use this instead of improvising
a workaround like writing to /dev/stdout.

Drive API calls automatically retry with exponential backoff on transient
errors (429 rate-limited, 500/502/503/504) — common on large downloads, which
need many chunk requests and can easily hit per-minute quota. A printed retry
message mid-run is expected and not a failure; only a final, unrecoverable
error after retries are exhausted should be treated as one.
"""

import argparse
import io
import random
import re
import sys
import time

from google.auth import default as google_auth_default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {"application/json"}

# Drive API rate-limits per-user/per-project quota with 429s under normal load
# (not just abuse) — a 100+MB download alone can take dozens of next_chunk()
# calls, each a fresh quota hit. google-api-python-client doesn't retry these
# on its own; an uncaught 429 partway through a long download would otherwise
# crash the whole job rather than just slowing down.
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5


def _with_retry(call):
    """Call a zero-arg API call, retrying transient errors with exponential backoff."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return call()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status not in TRANSIENT_STATUS_CODES or attempt == MAX_RETRIES:
                raise
            delay = (2**attempt) + random.uniform(0, 1)
            print(
                f"[{time.strftime('%H:%M:%S')}] Drive API returned {status}, "
                f"retrying in {delay:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)

DRIVE_URL_PATTERNS = [
    r"/file/d/([a-zA-Z0-9_-]+)",
    r"/folders/([a-zA-Z0-9_-]+)",
    r"/d/([a-zA-Z0-9_-]+)",  # docs.google.com/document|spreadsheets|presentation/d/{id}
    r"[?&]id=([a-zA-Z0-9_-]+)",
]

GOOGLE_NATIVE_MIME_PREFIX = "application/vnd.google-apps."
EXPORT_MIME_FOR_NATIVE = "application/pdf"


def extract_id(url_or_id):
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", url_or_id):
        return url_or_id
    for pattern in DRIVE_URL_PATTERNS:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract a Drive file/folder ID from: {url_or_id}")


def get_drive_service():
    credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def get_metadata(service, file_id):
    return _with_retry(lambda: service.files().get(fileId=file_id, fields="id, name, mimeType, size").execute())


def list_folder(service, folder_id):
    results = []
    page_token = None
    while True:
        response = _with_retry(
            lambda: service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
            )
            .execute()
        )
        results.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return results


def is_text_mime(mime_type):
    return mime_type.startswith(TEXT_MIME_PREFIXES) or mime_type in TEXT_MIME_TYPES


def download_text_content(service, file_id):
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = _with_retry(downloader.next_chunk)
    return buf.getvalue().decode("utf-8", errors="replace")


def download_file(service, file_id, mime_type, out_path):
    if mime_type.startswith(GOOGLE_NATIVE_MIME_PREFIX):
        request = service.files().export_media(fileId=file_id, mimeType=EXPORT_MIME_FOR_NATIVE)
    else:
        request = service.files().get_media(fileId=file_id)

    # MediaIoBaseDownload streams in chunks and writes each one as it arrives,
    # rather than request.execute() buffering the entire file in memory before
    # any of it reaches disk — for a 100+MB file that's a needless memory
    # spike on top of whatever else is running in the container. The logged
    # progress also gives something to report while this runs as a start_job
    # job — a multi-minute download otherwise has nothing to show until done.
    last_logged_percent = -1
    with open(out_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = _with_retry(downloader.next_chunk)
            if status:
                percent = int(status.progress() * 100)
                if percent >= last_logged_percent + 10:
                    print(f"[{time.strftime('%H:%M:%S')}] download progress: {percent}%", file=sys.stderr, flush=True)
                    last_logged_percent = percent


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url_or_id", help="Google Drive URL or bare file/folder ID")
    parser.add_argument("--out", default=None, help="Path to write the downloaded file to")
    parser.add_argument(
        "--list-only", action="store_true", help="If the target is a folder, list its contents instead of downloading"
    )
    parser.add_argument(
        "--print-content",
        action="store_true",
        help="Print a plain-text/Markdown file's content to stdout (not for PDFs — use extract_pdf_text.py)",
    )
    args = parser.parse_args()

    try:
        file_id = extract_id(args.url_or_id)
        service = get_drive_service()
        meta = get_metadata(service, file_id)
    except (ValueError, HttpError) as e:
        print(f"Could not access '{args.url_or_id}': {e}", file=sys.stderr)
        print(
            "Confirm the link is correct and the file is shared with the identity these "
            "credentials resolve to (for a service account, share it with the service "
            "account's email address — not just 'anyone with the link').",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if meta["mimeType"] == "application/vnd.google-apps.folder":
            children = list_folder(service, file_id)
            print(f"Folder: {meta['name']} ({len(children)} file(s))")
            for c in children:
                print(f"  {c['id']}  {c['mimeType']:50s}  {c['name']}")
            if not args.list_only:
                print(
                    "\nThis is a folder — fetch each file inside individually using its ID above.",
                    file=sys.stderr,
                )
            return

        if args.print_content:
            if meta["mimeType"].startswith(GOOGLE_NATIVE_MIME_PREFIX):
                print(
                    f"'{meta['name']}' is a Google-native document ({meta['mimeType']}). Fetch it "
                    "with --out instead (it auto-exports to PDF) and read it with extract_pdf_text.py.",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not is_text_mime(meta["mimeType"]):
                print(
                    f"'{meta['name']}' is {meta['mimeType']}, not a plain-text format — --print-content "
                    "only supports text/Markdown/JSON files. Use --out to save it instead, then "
                    "extract_pdf_text.py if it's a PDF.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(download_text_content(service, file_id))
            return

        if not args.out:
            print(f"{meta['name']}  ({meta['mimeType']}, {meta.get('size', '?')} bytes)  id={file_id}")
            return

        download_file(service, file_id, meta["mimeType"], args.out)
        print(f"Downloaded '{meta['name']}' ({meta['mimeType']}) to {args.out}", file=sys.stderr)
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        if status in TRANSIENT_STATUS_CODES:
            print(
                f"Drive API kept returning {status} after {MAX_RETRIES} retries — this is likely "
                "a quota/rate-limit issue, not a problem with the file. Wait a bit and retry.",
                file=sys.stderr,
            )
        else:
            print(f"Drive API error while fetching '{meta['name']}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
