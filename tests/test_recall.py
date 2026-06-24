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
