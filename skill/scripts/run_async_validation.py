#!/usr/bin/env python3
"""Detached background pipeline: fetch + extract criteria PDFs, build the
Criteria Checklist, and record status/progress/result in GCS via job_store.

Launched by agent.tools.start_async_validation. Must run to completion after
the triggering turn returns. Resumable: re-running with the same --job-id
continues from the GCS checkpoint. The agent never polls this; the result is
surfaced by the recall callback on the user's next message.
"""

import argparse
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


job_store = _load("job_store")


def phase1_prompt_section(skill_md):
    """Return the '## Phase 1' section of SKILL.md (rules for building the checklist)."""
    lines = skill_md.splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith("## Phase 1"):
            capturing = True
            continue
        if capturing and line.startswith("## ") and not line.startswith("## Phase 1"):
            break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _fetch_to_local(ref):
    """Fetch a Drive ref (or pass through a local path) to a local PDF path."""
    if os.path.exists(ref):
        return ref
    out = str(Path(tempfile.gettempdir()) / f"criteria_{hashlib.sha1(ref.encode('utf-8')).hexdigest()[:12]}.pdf")
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "fetch_drive_file.py"), ref, "--out", out],
        check=True,
    )
    return out


CHUNK_PAGES = 20


def chunk_ranges(next_start, total, size):
    """Inclusive (start, end) page ranges covering next_start..total in `size` steps.

    Returns [] when next_start is already past total (nothing left to extract) —
    that's how resume detects a finished file.
    """
    ranges = []
    s = next_start
    while s <= total:
        ranges.append((s, min(s + size - 1, total)))
        s += size
    return ranges


def _pdf_page_count(pdf_path):
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def _extract_range(pdf_path, start, end, out_path):
    subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "extract_pdf_text.py"), pdf_path,
         "--start", str(start), "--end", str(end), "--out", out_path],
        check=True,
    )
    return Path(out_path).read_text(encoding="utf-8")


def _build_checklist(extracted_md, rules, response_language=""):
    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
    )
    # response_language is free text describing whatever language the agent was
    # responding in when it kicked this job off (no fixed list) — the
    # background pipeline has no conversation context of its own, so without
    # this it has no signal for what language to write in.
    language_name = response_language or "Traditional Chinese (zh-TW) — unspecified, default per SKILL.md"
    prompt = (
        "Follow these rules to build the Criteria Checklist as a Markdown table.\n\n"
        f"=== RULES (from SKILL.md Phase 1) ===\n{rules}\n\n"
        f"=== EXTRACTED CRITERIA TEXT ===\n{extracted_md}\n\n"
        f"Write the checklist (headers, labels, and any notes) in {language_name}, "
        "regardless of what language the extracted criteria text above is in.\n"
        "Output only the checklist table and any per-row notes the rules require."
    )
    resp = client.models.generate_content(
        model=os.environ.get("MODEL", "gemini-3.5-flash"), contents=prompt
    )
    return resp.text


def _start_heartbeat(user_id, session_id, job_id, interval=30):
    """Touch heartbeat_epoch on a timer so a long extract/checklist call (which
    emits no chunk-boundary heartbeat) isn't mistaken for a dead job by the
    recall callback — which would otherwise launch a duplicate worker. Worst
    case this reverts progress by one chunk, which is already idempotent.
    """
    stop = threading.Event()

    def _beat():
        while not stop.wait(interval):
            try:
                job_store.touch(user_id, session_id, job_id)
            except Exception:
                pass

    threading.Thread(target=_beat, daemon=True).start()
    return stop


def run(job_id, session_id, user_id, criteria_refs, response_language=""):
    """Resume-aware: continues from the job record's progress {file_index, next_start}.

    Each chunk is appended to the durable accumulator BEFORE progress advances,
    so a kill leaves at most one chunk to be re-extracted (idempotent), never a
    gap. Re-running with the same job_id picks up where it stopped.
    """
    skill_md = (SCRIPTS_DIR.parent / "SKILL.md").read_text(encoding="utf-8")
    rules = phase1_prompt_section(skill_md)

    rec = job_store.read_job(user_id, session_id, job_id) or {"job_id": job_id}
    # Persist criteria_refs on first run; recover them on resume (the recall
    # callback re-launches with an empty list, relying on this record).
    if criteria_refs:
        rec["criteria_refs"] = criteria_refs
    else:
        criteria_refs = rec.get("criteria_refs", [])
    # Same recovery pattern for response_language: the resume call (recall
    # callback) doesn't know it, so fall back to whatever the first run persisted.
    if response_language:
        rec["response_language"] = response_language
    else:
        response_language = rec.get("response_language", "")
    rec.setdefault("delivered", False)
    # Persist session_id/user_id in the record so the cross-session recall
    # fallback (latest_undelivered_done_for_user) can mark the right blob
    # delivered even when the frontend hands the agent a new session_id.
    rec["session_id"] = session_id
    rec["user_id"] = user_id
    job_store.write_job(user_id, session_id, rec)
    progress = rec.get("progress") or {"file_index": 0, "next_start": 1}

    heartbeat_stop = _start_heartbeat(user_id, session_id, job_id)
    try:
        job_store.touch(user_id, session_id, job_id, status="running", progress={**progress, "stage": "extract"})

        for fi in range(progress.get("file_index", 0), len(criteria_refs)):
            local = _fetch_to_local(criteria_refs[fi])
            total = _pdf_page_count(local)
            start_at = progress["next_start"] if fi == progress.get("file_index", 0) else 1
            for (s, e) in chunk_ranges(start_at, total, CHUNK_PAGES):
                out = str(Path(tempfile.gettempdir()) / f"{job_id}_{fi}_{s}.md")
                text = _extract_range(local, s, e, out)
                job_store.append_extract(user_id, session_id, job_id, text)
                job_store.touch(
                    user_id, session_id, job_id, status="running",
                    progress={"file_index": fi, "next_start": e + 1, "stage": "extract", "total_pages": total},
                )
            job_store.touch(
                user_id, session_id, job_id, status="running",
                progress={"file_index": fi + 1, "next_start": 1, "stage": "extract"},
            )

        job_store.touch(user_id, session_id, job_id, status="running", progress={"stage": "checklist"})
        checklist = _build_checklist(
            job_store.read_extract(user_id, session_id, job_id), rules, response_language
        )

        rec = job_store.read_job(user_id, session_id, job_id) or {"job_id": job_id}
        rec.update({
            "job_id": job_id, "session_id": session_id, "user_id": user_id,
            "status": "done", "delivered": False, "result": checklist, "heartbeat_epoch": time.time(),
        })
        job_store.write_job(user_id, session_id, rec)
    finally:
        heartbeat_stop.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--criteria", action="append", default=[])
    parser.add_argument("--response-language", default="")
    args = parser.parse_args()
    try:
        run(args.job_id, args.session_id, args.user_id, args.criteria, args.response_language)
    except Exception as e:  # noqa: BLE001 — record any failure for the recall callback
        rec = job_store.read_job(args.user_id, args.session_id, args.job_id) or {"job_id": args.job_id}
        rec.update({
            "job_id": args.job_id, "session_id": args.session_id, "user_id": args.user_id,
            "status": "failed", "delivered": False, "error": str(e), "heartbeat_epoch": time.time(),
        })
        job_store.write_job(args.user_id, args.session_id, rec)
        sys.exit(1)


if __name__ == "__main__":
    main()
