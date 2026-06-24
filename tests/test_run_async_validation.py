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
        cur = self._store.get(self._path, (None, 0))[1]
        self._store[self._path] = (data, cur + 1)


class _FakeBucket:
    def __init__(self):
        self._store = {}

    def blob(self, path):
        return _FakeBlob(self._store, path)


def _stub_pipeline(mod, monkeypatch, bucket):
    monkeypatch.setattr(mod.job_store, "_default_bucket", lambda: bucket)
    monkeypatch.setattr(mod, "_pdf_page_count", lambda p: 5)
    monkeypatch.setattr(mod, "_extract_range", lambda p, s, e, o: f"pages {s}-{e}\n")
    monkeypatch.setattr(mod, "_build_checklist", lambda md, rules: "CHECKLIST")


def test_run_persists_session_id_and_marks_done(monkeypatch):
    mod = _load()
    bucket = _FakeBucket()
    _stub_pipeline(mod, monkeypatch, bucket)
    monkeypatch.setattr(mod, "_fetch_to_local", lambda ref: "/tmp/x.pdf")

    mod.run("job1", "sess-A", "user-1", ["refA"])

    rec = mod.job_store.read_job("user-1", "sess-A", "job1", bucket=bucket)
    assert rec["status"] == "done"
    assert rec["session_id"] == "sess-A"        # guards cross-session recall fallback
    assert rec["criteria_refs"] == ["refA"]
    assert "CHECKLIST" in rec["result"]


def test_run_recovers_criteria_refs_on_resume_with_empty_list(monkeypatch):
    mod = _load()
    bucket = _FakeBucket()
    _stub_pipeline(mod, monkeypatch, bucket)

    # Seed an interrupted job record (criteria_refs persisted, work not finished).
    mod.job_store.write_job("user-1", "sess-A", {
        "job_id": "job1", "session_id": "sess-A", "user_id": "user-1",
        "status": "running", "delivered": False, "criteria_refs": ["refA"],
        "progress": {"file_index": 0, "next_start": 1},
    }, bucket=bucket)

    fetched = []
    monkeypatch.setattr(mod, "_fetch_to_local", lambda ref: fetched.append(ref) or "/tmp/x.pdf")

    # Resume call passes an EMPTY list (as the recall callback does); run() must
    # recover the refs from the record and actually continue extraction.
    mod.run("job1", "sess-A", "user-1", [])

    assert fetched == ["refA"]
    assert mod.job_store.read_job("user-1", "sess-A", "job1", bucket=bucket)["status"] == "done"
