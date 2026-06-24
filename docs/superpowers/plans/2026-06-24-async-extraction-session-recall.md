# Async Extraction + Session Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a large-PDF validation kick off in the background and finish after the turn returns, then surface the built Criteria Checklist exactly once on the user's next message — fixing the Gemini Enterprise frontend timeout.

**Architecture:** A new detached background script (`run_async_validation.py`) fetches + extracts the criteria PDFs and builds the Criteria Checklist via a direct model call, writing progress/heartbeat/result to GCS through a shared `job_store` module. A `before_agent_callback` runs a deterministic recall state machine every turn: deliver-once (with an atomic `delivered` lock), report progress, resume a dead job, or surface a failure. The kickoff turn launches the job and returns immediately.

**Tech Stack:** Python 3.11+, google-adk (`before_agent_callback`, `types.Content`), google-cloud-storage (atomic writes via `if_generation_match`), google-genai (checklist build), pdfplumber (existing extraction). Tests: pytest via `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3`.

**Spec:** `docs/superpowers/specs/2026-06-24-async-extraction-session-recall-design.md`

---

## File Structure

- Create `skill/scripts/job_store.py` — GCS-backed job record store: read/write a job record, atomic `mark_delivered` via `if_generation_match`, per-user index, heartbeat/progress helpers. Importable by both the background script (same dir) and the agent (via importlib, like `agent/drive_tool.py` loads `fetch_drive_file.py`).
- Create `skill/scripts/run_async_validation.py` — detached background pipeline: fetch + extract criteria PDFs (resume-aware), build the checklist via the model, drive GCS status transitions and heartbeat.
- Create `agent/recall.py` — pure `decide_recall_action(job, now, stale_after)` + `build_recall_callback(...)` that reads GCS and acts.
- Modify `agent/tools.py` — add `start_async_validation(...)` to `make_tools` (detached `Popen`, returns `job_id` immediately).
- Modify `agent/agent.py` — register the new tool and wire `before_agent_callback`.
- Modify `skill/SKILL.md` — instruct the agent to use the async kickoff for large PDFs and NOT poll.
- Tests: `tests/test_recall.py`, `tests/test_job_store.py`, `tests/test_async_validation_tool.py`.

**Test command (used throughout):**
`/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest <path> -v`

---

## Task 1: Pure recall decision function

**Files:**
- Create: `agent/recall.py`
- Test: `tests/test_recall.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_recall.py
from agent.recall import decide_recall_action

NOW = 1000.0
STALE = 180.0


def _job(status, delivered=False, hb=NOW):
    return {"job_id": "j1", "status": status, "delivered": delivered, "heartbeat_epoch": hb}


def test_no_job_means_normal_conversation():
    assert decide_recall_action(None, NOW, STALE) == "none"


def test_done_undelivered_delivers():
    assert decide_recall_action(_job("done"), NOW, STALE) == "deliver"


def test_done_already_delivered_is_silent():
    assert decide_recall_action(_job("done", delivered=True), NOW, STALE) == "none"


def test_failed_undelivered_surfaces_once():
    assert decide_recall_action(_job("failed"), NOW, STALE) == "failed"


def test_failed_already_delivered_is_silent():
    assert decide_recall_action(_job("failed", delivered=True), NOW, STALE) == "none"


def test_running_with_fresh_heartbeat_reports_progress():
    assert decide_recall_action(_job("running", hb=NOW - 10), NOW, STALE) == "running"


def test_running_with_stale_heartbeat_resumes():
    assert decide_recall_action(_job("running", hb=NOW - 999), NOW, STALE) == "resume"


def test_queued_with_stale_heartbeat_resumes():
    assert decide_recall_action(_job("queued", hb=NOW - 999), NOW, STALE) == "resume"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_recall.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.recall'`

- [ ] **Step 3: Write minimal implementation**

```python
# agent/recall.py
"""Recall state machine for surfacing completed background jobs.

decide_recall_action is pure (no I/O) so the branch logic is unit-testable.
build_recall_callback wires it to GCS reads + the kickoff tool and returns a
before_agent_callback for the ADK LlmAgent.
"""

DELIVER = "deliver"
RUNNING = "running"
RESUME = "resume"
FAILED = "failed"
NONE = "none"


def decide_recall_action(job, now, stale_after):
    """Return the recall action for a job record (or None) at time `now`.

    A `delivered` flag makes both done and failed surface exactly once. A
    running/queued job whose heartbeat is older than `stale_after` seconds is
    assumed dead (the background work was throttled/killed after fast-return)
    and is resumed.
    """
    if not job:
        return NONE
    status = job.get("status")
    if status == "done":
        return NONE if job.get("delivered") else DELIVER
    if status == "failed":
        return NONE if job.get("delivered") else FAILED
    if status in ("queued", "running"):
        if now - job.get("heartbeat_epoch", 0) > stale_after:
            return RESUME
        return RUNNING
    return NONE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_recall.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add agent/recall.py tests/test_recall.py
git commit -m "feat: add pure recall decision function for async job surfacing"
```

---

## Task 2: GCS job store with atomic deliver-once

**Files:**
- Create: `skill/scripts/job_store.py`
- Test: `tests/test_job_store.py`

The store keys records at `{user_id}/{session_id}/jobs/{job_id}.json` and keeps a per-user index at `{user_id}/job_index.json` for the session-id-change fallback. All GCS functions accept an optional `bucket` so tests inject a fake; production uses `_default_bucket()` (same `document-validator-sessions-{PROJECT}` bucket as `gcs_state.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_job_store.py
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parent.parent / "skill" / "scripts" / "job_store.py"


def _load():
    spec = importlib.util.spec_from_file_location("job_store", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeBlob:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def exists(self):
        return self._path in self._store

    def download_as_text(self):
        return self._store[self._path][0]

    @property
    def generation(self):
        return self._store.get(self._path, (None, 0))[1]

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        from google.api_core.exceptions import PreconditionFailed

        cur_gen = self._store.get(self._path, (None, 0))[1]
        if if_generation_match is not None and if_generation_match != cur_gen:
            raise PreconditionFailed("generation mismatch")
        self._store[self._path] = (data, cur_gen + 1)


class _FakeBucket:
    def __init__(self):
        self._store = {}

    def blob(self, path):
        return _FakeBlob(self._store, path)


@pytest.fixture
def store():
    return _load()


def test_write_then_read_round_trips(store):
    bucket = _FakeBucket()
    rec = {"job_id": "j1", "status": "running", "delivered": False}
    store.write_job("u1", "s1", rec, bucket=bucket)
    assert store.read_job("u1", "s1", "j1", bucket=bucket)["status"] == "running"


def test_read_missing_returns_none(store):
    assert store.read_job("u1", "s1", "nope", bucket=_FakeBucket()) is None


def test_mark_delivered_wins_once_then_loses(store):
    bucket = _FakeBucket()
    store.write_job("u1", "s1", {"job_id": "j1", "status": "done", "delivered": False}, bucket=bucket)
    assert store.mark_delivered("u1", "s1", "j1", bucket=bucket) is True
    # second attempt sees delivered already true -> returns False
    assert store.mark_delivered("u1", "s1", "j1", bucket=bucket) is False
    assert store.read_job("u1", "s1", "j1", bucket=bucket)["delivered"] is True


def test_latest_undelivered_done_via_user_index(store):
    bucket = _FakeBucket()
    store.write_job("u1", "s_old", {"job_id": "j1", "status": "done", "delivered": False}, bucket=bucket)
    found = store.latest_undelivered_done_for_user("u1", bucket=bucket)
    assert found is not None and found["job_id"] == "j1"


def test_append_extract_accumulates_chunks(store):
    bucket = _FakeBucket()
    assert store.read_extract("u1", "s1", "j1", bucket=bucket) == ""
    store.append_extract("u1", "s1", "j1", "page1\n", bucket=bucket)
    store.append_extract("u1", "s1", "j1", "page2\n", bucket=bucket)
    assert store.read_extract("u1", "s1", "j1", bucket=bucket) == "page1\npage2\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_job_store.py -v`
Expected: FAIL with `FileNotFoundError`/`spec` load error (module does not exist yet)

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_job_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add skill/scripts/job_store.py tests/test_job_store.py
git commit -m "feat: add GCS job store with atomic deliver-once and per-user index"
```

---

## Task 3: `start_async_validation` tool (detached launch)

**Files:**
- Modify: `agent/tools.py` (add inside `make_tools`, return alongside existing tools — see Step 3)
- Test: `tests/test_async_validation_tool.py`

The existing `start_job` kills its subprocess at `timeout` (≤300s), so it cannot host a 10-minute job. This tool launches `run_async_validation.py` fully detached (`start_new_session=True`, output to `DEVNULL`, never waited on) and returns the `job_id` immediately.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_async_validation_tool.py
from pathlib import Path

from agent.tools import make_tools


def test_start_async_validation_launches_detached_and_returns_job_id(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")

    calls = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            calls["cmd"] = cmd
            calls["kwargs"] = kwargs

    monkeypatch.setattr("agent.tools.subprocess.Popen", _FakePopen)

    tools = make_tools(skill_dir, timeout=60)
    start_async_validation = tools[-1]  # last returned tool

    job_id = start_async_validation(["https://drive/x"], "sess-1", "user-1")

    assert job_id and isinstance(job_id, str)
    assert calls["kwargs"]["start_new_session"] is True
    assert "run_async_validation.py" in " ".join(calls["cmd"])
    assert "--session-id" in calls["cmd"] and "sess-1" in calls["cmd"]
    assert "--criteria" in calls["cmd"] and "https://drive/x" in calls["cmd"]


def test_start_async_validation_reuses_job_id_on_resume(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr("agent.tools.subprocess.Popen", lambda *a, **k: None)

    start_async_validation = make_tools(skill_dir, timeout=60)[-1]
    job_id = start_async_validation(["ref"], "s", "u", resume_job_id="fixed123")
    assert job_id == "fixed123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_async_validation_tool.py -v`
Expected: FAIL — `make_tools` returns 3 items so `tools[-1]` is `read_asset` and has the wrong signature / `AssertionError`

- [ ] **Step 3: Add the tool to `make_tools`**

In `agent/tools.py`, add this function inside `make_tools` (place it right after `read_asset` is defined, before the docstring-setting block near the end):

```python
    def start_async_validation(criteria_refs: list[str], session_id: str, user_id: str, resume_job_id: str = "") -> str:
        """Launch background criteria extraction + checklist build, return a job_id now.

        Unlike start_job, this is fully detached and NOT bounded by the script
        timeout — it must outlive this turn. Do not poll it; the result is
        surfaced automatically on the user's next message. On resume, pass the
        existing resume_job_id so the background job continues from its GCS
        checkpoint instead of starting over.
        """
        job_id = resume_job_id or uuid.uuid4().hex[:12]
        script_path = (scripts_dir / "run_async_validation.py").resolve()
        if not script_path.exists():
            return "[error] run_async_validation.py is not bundled with this skill"
        cmd = [
            sys.executable,
            str(script_path),
            "--job-id", job_id,
            "--session-id", session_id,
            "--user-id", user_id,
        ]
        for ref in criteria_refs:
            cmd += ["--criteria", ref]
        subprocess.Popen(
            cmd,
            cwd=str(skill_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return job_id
```

Then change the return line at the end of `make_tools` from:

```python
    return start_job, check_job, read_asset
```

to:

```python
    return start_job, check_job, read_asset, start_async_validation
```

**Changing the arity breaks every existing 3-target unpack** (`agent.py:29` and 22 sites in `tests/test_tools.py`), so fix them all in this same task to keep the suite green.

Update `agent/agent.py:29` from:

```python
    start_job, check_job, read_asset = make_tools(skill_dir, timeout=timeout)
```

to (names the 4th value; it's used in Task 6):

```python
    start_job, check_job, read_asset, start_async_validation = make_tools(skill_dir, timeout=timeout)
```

Fix all `tests/test_tools.py` unpacks mechanically — every site has exactly three targets before `= make_tools(`, so appending one `_` target makes them four:

Run: `sed -i '' 's/ = make_tools(/, _ = make_tools(/g' tests/test_tools.py`

(Verify: `grep -n "make_tools(" tests/test_tools.py` should now show four-target unpacks like `start_job, check_job, _, _ = make_tools(skill_dir)`.)

- [ ] **Step 4: Run the full suite to verify it stays green**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/ -q`
Expected: PASS — the new `test_async_validation_tool.py` passes and every previously-green test (including all of `test_tools.py` and `test_agent.py`) is still green.

- [ ] **Step 5: Commit**

```bash
git add agent/tools.py agent/agent.py tests/test_async_validation_tool.py tests/test_tools.py
git commit -m "feat: add detached start_async_validation tool"
```

---

## Task 4: Background pipeline script

**Files:**
- Create: `skill/scripts/run_async_validation.py`
- Test: `tests/test_run_async_validation.py` (pure helper only)

The script fetches each criteria ref, extracts it page-by-page (resuming from the GCS checkpoint), then builds the Criteria Checklist with a direct model call seeded by SKILL.md's Phase 1 rules.

- [ ] **Step 1: Write the failing test for the pure helper**

```python
# tests/test_run_async_validation.py
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "skill" / "scripts" / "run_async_validation.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_async_validation", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_phase1_prompt_section_extracts_phase_1_block():
    mod = _load()
    skill_md = "## Phase 0\nintake\n## Phase 1: Criteria Checklist Extraction\nDo X.\nDo Y.\n## Phase 2\nscore\n"
    section = mod.phase1_prompt_section(skill_md)
    assert "Do X." in section and "Do Y." in section
    assert "score" not in section and "intake" not in section


def test_chunk_ranges_splits_and_resumes():
    mod = _load()
    assert mod.chunk_ranges(1, 45, 20) == [(1, 20), (21, 40), (41, 45)]
    assert mod.chunk_ranges(41, 45, 20) == [(41, 45)]   # resume mid-file
    assert mod.chunk_ranges(50, 45, 20) == []           # already past the end
    assert mod.chunk_ranges(1, 20, 20) == [(1, 20)]     # exact multiple
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_run_async_validation.py -v`
Expected: FAIL (module does not exist)

- [ ] **Step 3: Write the script**

```python
# skill/scripts/run_async_validation.py
#!/usr/bin/env python3
"""Detached background pipeline: fetch + extract criteria PDFs, build the
Criteria Checklist, and record status/progress/result in GCS via job_store.

Launched by agent.tools.start_async_validation. Must run to completion after
the triggering turn returns. Resumable: re-running with the same --job-id
continues from the GCS checkpoint. The agent never polls this; the result is
surfaced by the recall callback on the user's next message.
"""

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
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
    out = str(Path(tempfile.gettempdir()) / f"criteria_{abs(hash(ref))}.pdf")
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


def _build_checklist(extracted_md, rules):
    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
    )
    prompt = (
        "Follow these rules to build the Criteria Checklist as a Markdown table.\n\n"
        f"=== RULES (from SKILL.md Phase 1) ===\n{rules}\n\n"
        f"=== EXTRACTED CRITERIA TEXT ===\n{extracted_md}\n\n"
        "Output only the checklist table and any per-row notes the rules require."
    )
    resp = client.models.generate_content(
        model=os.environ.get("MODEL", "gemini-3.5-flash"), contents=prompt
    )
    return resp.text


def run(job_id, session_id, user_id, criteria_refs):
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
    rec.setdefault("delivered", False)
    job_store.write_job(user_id, session_id, rec)
    progress = rec.get("progress") or {"file_index": 0, "next_start": 1}
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
    checklist = _build_checklist(job_store.read_extract(user_id, session_id, job_id), rules)

    rec = job_store.read_job(user_id, session_id, job_id) or {"job_id": job_id}
    rec.update({"job_id": job_id, "status": "done", "delivered": False, "result": checklist, "heartbeat_epoch": time.time()})
    job_store.write_job(user_id, session_id, rec)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--criteria", action="append", default=[])
    args = parser.parse_args()
    try:
        run(args.job_id, args.session_id, args.user_id, args.criteria)
    except Exception as e:  # noqa: BLE001 — record any failure for the recall callback
        rec = job_store.read_job(args.user_id, args.session_id, args.job_id) or {"job_id": args.job_id}
        rec.update({"job_id": args.job_id, "status": "failed", "delivered": False, "error": str(e), "heartbeat_epoch": time.time()})
        job_store.write_job(args.user_id, args.session_id, rec)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_run_async_validation.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add skill/scripts/run_async_validation.py tests/test_run_async_validation.py
git commit -m "feat: add background criteria extraction + checklist build pipeline"
```

---

## Task 5: Recall callback wiring

**Files:**
- Modify: `agent/recall.py` (add `build_recall_callback`)
- Test: `tests/test_recall.py` (add callback-behaviour tests)

- [ ] **Step 1: Add failing tests for the callback**

```python
# tests/test_recall.py  (append)
from agent.recall import build_recall_callback


class _FakeSession:
    id = "sess-1"


class _FakeCtx:
    user_id = "user-1"
    session = _FakeSession()


class _FakeStore:
    def __init__(self, job):
        self._job = job
        self.delivered = False
        self.resumed = False

    def read_job(self, u, s, j, bucket=None):
        return self._job

    def latest_undelivered_done_for_user(self, u, bucket=None):
        return None

    def find_active_job(self, u, s):
        return self._job

    def mark_delivered(self, u, s, j, bucket=None):
        self.delivered = True
        return True


def test_callback_delivers_checklist_content_once():
    store = _FakeStore({"job_id": "j1", "status": "done", "delivered": False, "result": "| ID | ... |"})
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    content = cb(_FakeCtx())
    assert content is not None
    assert "| ID | ... |" in content.parts[0].text
    assert store.delivered is True


def test_callback_returns_none_when_no_job():
    store = _FakeStore(None)
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    assert cb(_FakeCtx()) is None


def test_callback_resumes_stale_job():
    store = _FakeStore({"job_id": "j1", "status": "running", "delivered": False, "heartbeat_epoch": 0})
    resumed = {}
    cb = build_recall_callback(
        store,
        start_async_validation=lambda *a, **k: resumed.setdefault("id", k.get("resume_job_id")),
        stale_after=180,
    )
    content = cb(_FakeCtx())
    assert content is not None  # status message to the user
    assert resumed["id"] == "j1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_recall.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_recall_callback'`

- [ ] **Step 3: Add `build_recall_callback` to `agent/recall.py`**

Add at the end of `agent/recall.py`:

```python
import time

from google.genai import types


def build_recall_callback(job_store, start_async_validation, stale_after=180.0):
    """Return a before_agent_callback that surfaces completed background jobs.

    Returning a types.Content short-circuits the agent for that turn (used to
    deliver the checklist or a status line); returning None lets the agent run
    normally.
    """

    def _content(text):
        return types.Content(role="model", parts=[types.Part(text=text)])

    def before_agent_callback(callback_context):
        user_id = callback_context.user_id
        session_id = callback_context.session.id

        job = job_store.find_active_job(user_id, session_id)
        if job is None:
            job = job_store.latest_undelivered_done_for_user(user_id)
            if job is not None:
                session_id = job.get("session_id", session_id)

        action = decide_recall_action(job, time.time(), stale_after)

        if action == NONE:
            return None
        if action == DELIVER:
            if job_store.mark_delivered(user_id, session_id, job["job_id"]):
                return _content(
                    job.get("result", "")
                    + "\n\n以上是背景處理完成的查核清單，請確認內容無誤後我再開始評分。"
                )
            return None
        if action == FAILED:
            job_store.mark_delivered(user_id, session_id, job["job_id"])
            return _content(f"背景處理失敗：{job.get('error', '未知錯誤')}。需要我重試嗎？")
        if action == RUNNING:
            p = job.get("progress", {})
            return _content(f"還在背景處理中（{p.get('stage', '處理')}：{p.get('done', '?')}/{p.get('total', '?')}）。")
        if action == RESUME:
            start_async_validation([], session_id, user_id, resume_job_id=job["job_id"])
            return _content("先前的背景作業中斷了，已自動從上次進度接續處理。")
        return None

    return before_agent_callback
```

Add a `find_active_job` helper to `skill/scripts/job_store.py` (reads the newest non-delivered job for this session via the index):

```python
def find_active_job(user_id, session_id, bucket=None):
    bucket = bucket or _default_bucket()
    blob = bucket.blob(_index_path(user_id))
    if not blob.exists():
        return None
    entries = sorted(json.loads(blob.download_as_text()), key=lambda e: e.get("epoch", 0), reverse=True)
    for e in entries:
        if e.get("session_id") != session_id:
            continue
        rec = read_job(user_id, session_id, e["job_id"], bucket=bucket)
        if rec and not rec.get("delivered"):
            return rec
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_recall.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add agent/recall.py skill/scripts/job_store.py tests/test_recall.py
git commit -m "feat: add recall callback that surfaces async jobs once"
```

---

## Task 6: Wire tool + callback into the agent

**Files:**
- Modify: `agent/agent.py`
- Test: `tests/test_agent.py` (add registration test)

- [ ] **Step 1: Add a failing test**

```python
# tests/test_agent.py  (append)
def test_async_validation_tool_registered_when_scripts_present(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")
    # build_agent wires the recall callback only when job_store.py is present.
    (skill_dir / "scripts" / "job_store.py").write_text("def find_active_job(*a, **k):\n    return None\n", encoding="utf-8")
    agent = build_agent(skill_dir=skill_dir)
    assert any(getattr(t, "__name__", "") == "start_async_validation" for t in agent.tools)
    assert agent.before_agent_callback is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/test_agent.py::test_async_validation_tool_registered_when_scripts_present -v`
Expected: FAIL (`start_async_validation` not registered; `before_agent_callback` is None)

- [ ] **Step 3: Wire it in `agent/agent.py`**

The `make_tools` unpack was already widened to 4 values in Task 3, so
`start_async_validation` is already in scope. In the `if has_scripts:` block,
after appending `check_job`, also register the async tool and its description:

```python
        tools.append(start_async_validation)
        tool_lines.append(
            "- start_async_validation: kick off background criteria extraction + "
            "checklist build for large PDFs; returns a job_id immediately. Do NOT "
            "poll it — tell the user it's processing and end your turn. The result "
            "is surfaced automatically when they next message."
        )
```

Add the import near the top:

```python
from .recall import build_recall_callback
```

Build the callback and pass it to `LlmAgent`. Load `job_store` the same way `drive_tool.py` loads a script, just before constructing the agent:

```python
    import importlib.util as _ilu

    _js_path = skill_dir / "scripts" / "job_store.py"
    recall_callback = None
    if _js_path.exists():
        _spec = _ilu.spec_from_file_location("job_store", _js_path)
        _job_store = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_job_store)
        recall_callback = build_recall_callback(_job_store, start_async_validation)
```

Add `before_agent_callback=recall_callback,` to the `LlmAgent(...)` constructor call.

- [ ] **Step 4: Run the full suite**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/ -q`
Expected: PASS (all green, including the new registration test)

- [ ] **Step 5: Commit**

```bash
git add agent/agent.py tests/test_agent.py
git commit -m "feat: register async tool and recall callback on the agent"
```

---

## Task 7: SKILL.md instructions for the async path

**Files:**
- Modify: `skill/SKILL.md` (Phase 0.4 area and Running Scripts area)

- [ ] **Step 1: Add the async guidance**

In `skill/SKILL.md`, under the PDF-extraction guidance (§0.4) add this block (English only — repo rule: no Chinese in SKILL.md):

```markdown
**Large PDFs — kick off in the background.** When a criteria PDF is large
enough that extracting it inline would be slow, call `start_async_validation`
with the criteria document reference(s). It returns a job_id immediately and
runs extraction + Criteria Checklist building in the background. Do NOT poll it
and do NOT call check_job on it — instead, tell the user the document is being
processed and that the checklist will appear on their next message, then end
your turn. When the job finishes, the completed Criteria Checklist is surfaced
to the user automatically; you do not need to rebuild or re-announce it.
```

- [ ] **Step 2: Verify no Chinese characters were introduced**

Run: `grep -nP '[\x{4e00}-\x{9fff}]' skill/SKILL.md`
Expected: no output (exit code 1)

- [ ] **Step 3: Commit**

```bash
git add skill/SKILL.md
git commit -m "docs: instruct agent to use async kickoff for large PDFs"
```

---

## Task 8: Full-suite green + dependency check

**Files:** none (verification)

- [ ] **Step 1: Confirm `google-genai` is declared**

Run: `grep -n "google-genai" requirements.txt pyproject.toml`
Expected: present in `requirements.txt` (`google-genai>=2.4.0`). If missing from `pyproject.toml` runtime deps, add `"google-genai>=2.4.0",` to the `dependencies` list.

- [ ] **Step 2: Run the whole suite**

Run: `/Users/mini/Documents/agent-skill-wrapper/.venv/bin/python3 -m pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 3: Commit any dependency fix**

```bash
git add requirements.txt pyproject.toml
git commit -m "chore: ensure google-genai is a declared runtime dependency"
```

---

## Self-Review Notes (coverage against spec)

- **Fast-return kickoff** → Task 3 (`start_async_validation`, detached) + Task 7 (SKILL instructs no-poll, end turn).
- **In-runtime background extraction + checklist build (decision a)** → Task 4 (`run_async_validation.py`, direct model call seeded by SKILL.md Phase 1 rules).
- **GCS checkpoint + heartbeat + resume** → Task 2 (`touch`/`heartbeat_epoch`, durable `append_extract`), Task 4 (chunked page-range extraction that persists `{file_index, next_start}` after each chunk and resumes from it), Task 1 + Task 5 (`resume` action re-launches with the same job_id so it continues from the last chunk rather than page 1).
- **Deterministic recall via before_agent_callback** → Task 5 + Task 6.
- **Deliver exactly once (no duplicate output)** → Task 1 (`delivered` guard) + Task 2 (atomic `mark_delivered`) + Task 5 (mark before emitting).
- **session_id-change fallback** → Task 2 (`latest_undelivered_done_for_user`, per-user index) + Task 5 (fallback path).
- **Failure surfacing** → Task 4 (`status=failed`) + Task 1/Task 5 (`failed` action, surfaced once).
- **Open spike items** (background CPU survival, GE session_id stability, GE chat timeout) remain documented in the spec; the resume path + user-index fallback make the system correct regardless of their answers.

## Out of Scope (Phase 2, deferred)

Externalizing the background loop into a durable worker (e.g. Cloud Run Job). Only `start_async_validation`'s launch mechanism would change; `job_store`, `recall`, and the state machine are reused unchanged.
