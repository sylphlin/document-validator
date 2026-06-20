#!/usr/bin/env python3
"""Persist files and checkpoint state to GCS, keyed by session/user ID.

Agent Engine containers are ephemeral and a conversation session can outlive
the container instance that started it — a later turn may land on a different
container with an empty /tmp, or the session may simply be resumed long after
the original container is gone. Anything written only to local disk is lost
in that case.

This script is only for things with no other durable source:
  - files the user pasted/uploaded directly into the conversation (a Google
    Drive link does NOT need this — re-fetch it from Drive again instead,
    since Drive is already a durable source)
  - derived state the agent built up itself, like the running Compliance
    Profile accumulated across several extraction chunks, which has no
    source to re-fetch from except redoing the whole reasoning pass

Auth uses Application Default Credentials (ADC), same as fetch_drive_file.py.
The bucket (document-validator-sessions-{GOOGLE_CLOUD_PROJECT}) must already
exist — this script does not create it — and the identity these credentials
resolve to needs read/write access to it (e.g. roles/storage.objectAdmin on
that bucket for the deployed service account).

Usage:
  python3 gcs_state.py upload-file --session-id ID --user-id UID --file /tmp/doc.pdf
  python3 gcs_state.py download-file --session-id ID --user-id UID --name doc.pdf --out /tmp/doc.pdf
  python3 gcs_state.py write-state --session-id ID --user-id UID --name compliance_profile --data '{"...": "..."}'
  echo '{"...": "..."}' | python3 gcs_state.py write-state --session-id ID --user-id UID --name compliance_profile
  python3 gcs_state.py read-state --session-id ID --user-id UID --name compliance_profile
"""

import argparse
import os
import sys


def get_bucket_name():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        print(
            "[error] GOOGLE_CLOUD_PROJECT is not set — it cannot be reliably "
            "auto-detected at runtime, so it must be set explicitly via the "
            "environment (see .env.example).",
            file=sys.stderr,
        )
        sys.exit(1)
    return f"document-validator-sessions-{project_id}"


def get_bucket():
    from google.cloud import storage

    client = storage.Client()
    return client.bucket(get_bucket_name())


def blob_path(user_id, session_id, *parts):
    return "/".join([user_id, session_id, *parts])


def cmd_upload_file(args):
    from google.cloud.exceptions import NotFound

    name = args.name or os.path.basename(args.file)
    bucket = get_bucket()
    path = blob_path(args.user_id, args.session_id, "files", name)
    try:
        bucket.blob(path).upload_from_filename(args.file)
    except NotFound:
        print(
            f"[error] bucket '{get_bucket_name()}' not found. Create it once "
            "(e.g. `gsutil mb gs://...`) and grant the deployed service "
            "account write access — this script does not create buckets.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Uploaded '{args.file}' to gs://{get_bucket_name()}/{path}", file=sys.stderr)


def cmd_download_file(args):
    from google.cloud.exceptions import NotFound

    bucket = get_bucket()
    path = blob_path(args.user_id, args.session_id, "files", args.name)
    blob = bucket.blob(path)
    if not blob.exists():
        print(f"[error] no such file in GCS state: {args.name}", file=sys.stderr)
        sys.exit(1)
    out_path = args.out or args.name
    try:
        blob.download_to_filename(out_path)
    except NotFound:
        print(f"[error] bucket '{get_bucket_name()}' not found.", file=sys.stderr)
        sys.exit(1)
    print(f"Downloaded gs://{get_bucket_name()}/{path} to '{out_path}'", file=sys.stderr)


def cmd_write_state(args):
    from google.cloud.exceptions import NotFound

    data = args.data
    if data is None:
        data = sys.stdin.read()
    bucket = get_bucket()
    path = blob_path(args.user_id, args.session_id, "state", f"{args.name}.json")
    try:
        bucket.blob(path).upload_from_string(data, content_type="application/json")
    except NotFound:
        print(
            f"[error] bucket '{get_bucket_name()}' not found. Create it once "
            "and grant the deployed service account write access.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Checkpointed state '{args.name}' to gs://{get_bucket_name()}/{path}", file=sys.stderr)


def cmd_read_state(args):
    bucket = get_bucket()
    path = blob_path(args.user_id, args.session_id, "state", f"{args.name}.json")
    blob = bucket.blob(path)
    if not blob.exists():
        print(f"[error] no checkpointed state found for '{args.name}'", file=sys.stderr)
        sys.exit(1)
    print(blob.download_as_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = {"session-id": "Current conversation session ID", "user-id": "Current user ID"}

    p = sub.add_parser("upload-file", help="Back up a local file to GCS")
    p.add_argument("--session-id", required=True, help=common["session-id"])
    p.add_argument("--user-id", required=True, help=common["user-id"])
    p.add_argument("--file", required=True, help="Local path to upload")
    p.add_argument("--name", default=None, help="Name to store it under (default: basename of --file)")
    p.set_defaults(func=cmd_upload_file)

    p = sub.add_parser("download-file", help="Restore a previously backed-up file from GCS")
    p.add_argument("--session-id", required=True, help=common["session-id"])
    p.add_argument("--user-id", required=True, help=common["user-id"])
    p.add_argument("--name", required=True, help="Name it was stored under")
    p.add_argument("--out", default=None, help="Local path to write to (default: --name)")
    p.set_defaults(func=cmd_download_file)

    p = sub.add_parser("write-state", help="Checkpoint a JSON state blob to GCS")
    p.add_argument("--session-id", required=True, help=common["session-id"])
    p.add_argument("--user-id", required=True, help=common["user-id"])
    p.add_argument("--name", required=True, help="State name, e.g. compliance_profile")
    p.add_argument("--data", default=None, help="JSON string to store (default: read from stdin)")
    p.set_defaults(func=cmd_write_state)

    p = sub.add_parser("read-state", help="Read back a checkpointed JSON state blob from GCS")
    p.add_argument("--session-id", required=True, help=common["session-id"])
    p.add_argument("--user-id", required=True, help=common["user-id"])
    p.add_argument("--name", required=True, help="State name, e.g. compliance_profile")
    p.set_defaults(func=cmd_read_state)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
