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
