# skill/scripts/job_store.py
"""GCS-backed job records for async validation, with atomic deliver-once.

Shared by run_async_validation.py (writes status/progress/heartbeat/result)
and the agent's recall callback (reads + atomically marks delivered). Records
live in the same bucket as gcs_state.py: document-validator-sessions-{PROJECT}.
"""

import json
import os
import time
import uuid


def _bucket_name():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
    return f"document-validator-sessions-{project_id}"


def _default_bucket():
    from google.cloud import storage

    return storage.Client().bucket(_bucket_name())


def _job_path(user_id, session_id, job_id):
    return f"{user_id}/{session_id}/jobs/{job_id}.json"


def _index_path(user_id):
    return f"{user_id}/job_index.json"


def new_job_id():
    return uuid.uuid4().hex[:12]


def write_job(user_id, session_id, record, bucket=None):
    bucket = bucket or _default_bucket()
    bucket.blob(_job_path(user_id, session_id, record["job_id"])).upload_from_string(
        json.dumps(record), content_type="application/json"
    )
    _append_index(user_id, session_id, record["job_id"], bucket)


def read_job(user_id, session_id, job_id, bucket=None):
    bucket = bucket or _default_bucket()
    blob = bucket.blob(_job_path(user_id, session_id, job_id))
    if not blob.exists():
        return None
    return json.loads(blob.download_as_text())


def touch(user_id, session_id, job_id, *, status=None, progress=None, bucket=None):
    bucket = bucket or _default_bucket()
    rec = read_job(user_id, session_id, job_id, bucket=bucket) or {"job_id": job_id}
    if status is not None:
        rec["status"] = status
    if progress is not None:
        rec["progress"] = progress
    rec["heartbeat_epoch"] = time.time()
    write_job(user_id, session_id, rec, bucket=bucket)


def mark_delivered(user_id, session_id, job_id, bucket=None):
    """Atomically flip delivered=true. Returns True if this caller won the race."""
    from google.api_core.exceptions import PreconditionFailed

    bucket = bucket or _default_bucket()
    blob = bucket.blob(_job_path(user_id, session_id, job_id))
    if not blob.exists():
        return False
    rec = json.loads(blob.download_as_text())
    if rec.get("delivered"):
        return False
    rec["delivered"] = True
    try:
        blob.upload_from_string(
            json.dumps(rec),
            content_type="application/json",
            if_generation_match=blob.generation,
        )
    except PreconditionFailed:
        return False
    return True


def _append_index(user_id, session_id, job_id, bucket):
    blob = bucket.blob(_index_path(user_id))
    entries = json.loads(blob.download_as_text()) if blob.exists() else []
    entries = [e for e in entries if e.get("job_id") != job_id]
    entries.append({"job_id": job_id, "session_id": session_id, "epoch": time.time()})
    blob.upload_from_string(json.dumps(entries[-50:]), content_type="application/json")


def latest_undelivered_done_for_user(user_id, bucket=None):
    """Fallback when the session_id changed: newest done-undelivered job."""
    bucket = bucket or _default_bucket()
    blob = bucket.blob(_index_path(user_id))
    if not blob.exists():
        return None
    entries = sorted(json.loads(blob.download_as_text()), key=lambda e: e.get("epoch", 0), reverse=True)
    for e in entries:
        rec = read_job(user_id, e["session_id"], e["job_id"], bucket=bucket)
        if rec and rec.get("status") == "done" and not rec.get("delivered"):
            return rec
    return None


def _extract_path(user_id, session_id, job_id):
    return f"{user_id}/{session_id}/jobs/{job_id}/extracted.md"


def append_extract(user_id, session_id, job_id, text, bucket=None):
    """Append a freshly-extracted chunk to the job's durable accumulator.

    Single-writer (the background job), so read-modify-write is safe. Called
    *before* the matching progress update so a crash re-appends a chunk
    (tolerable duplication) rather than dropping one (a content gap).
    """
    bucket = bucket or _default_bucket()
    blob = bucket.blob(_extract_path(user_id, session_id, job_id))
    existing = blob.download_as_text() if blob.exists() else ""
    blob.upload_from_string(existing + text, content_type="text/markdown")


def read_extract(user_id, session_id, job_id, bucket=None):
    bucket = bucket or _default_bucket()
    blob = bucket.blob(_extract_path(user_id, session_id, job_id))
    return blob.download_as_text() if blob.exists() else ""
