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


class _FakePart:
    def __init__(self, text):
        self.text = text


class _FakeContent:
    def __init__(self, text):
        self.parts = [_FakePart(text)]


class _FakeCtx:
    user_id = "user-1"
    session = _FakeSession()
    user_content = None  # no input text by default — falls back to job's stored language

    def __init__(self, user_text=None):
        if user_text is not None:
            self.user_content = _FakeContent(user_text)


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


class _FakeStoreCrossSession:
    """No active job in the current session; a done job exists under an OLDER
    session. mark_delivered only succeeds when called with that older session_id
    — mirroring real GCS, where the blob is keyed by the job's own session.
    """

    def __init__(self):
        self._done = {"job_id": "j1", "session_id": "s_old", "status": "done",
                      "delivered": False, "result": "OLD-CHECKLIST"}
        self.delivered_with_session = None

    def find_active_job(self, u, s):
        return None  # nothing under the current (new) session

    def latest_undelivered_done_for_user(self, u, bucket=None):
        return self._done

    def mark_delivered(self, u, s, j, bucket=None):
        if s != "s_old":  # wrong session -> blob not found -> CAS fails
            return False
        self.delivered_with_session = s
        return True


def test_callback_delivers_in_english_when_job_response_language_is_en():
    store = _FakeStore({
        "job_id": "j1", "status": "done", "delivered": False,
        "result": "| ID | ... |", "response_language": "en",
    })
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    content = cb(_FakeCtx())
    assert "Criteria Checklist" in content.parts[0].text
    assert "查核清單" not in content.parts[0].text


def test_callback_delivers_in_chinese_when_response_language_missing():
    store = _FakeStore({"job_id": "j1", "status": "done", "delivered": False, "result": "X"})
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    content = cb(_FakeCtx())
    assert "查核清單" in content.parts[0].text


def test_callback_uses_current_message_language_over_stale_job_language():
    # The job was kicked off in an earlier (English) conversation, but the
    # CURRENT message that triggered this turn is in Traditional Chinese —
    # that should win, not the stale job language. Reproduces the "first
    # message of a new conversation comes back in English" report.
    store = _FakeStore({
        "job_id": "j1", "status": "done", "delivered": False,
        "result": "X", "response_language": "en",
    })
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    content = cb(_FakeCtx(user_text="請幫我查核這份文件"))
    assert "查核清單" in content.parts[0].text
    assert "Criteria Checklist" not in content.parts[0].text


def test_callback_falls_back_to_job_language_when_current_message_has_no_text():
    store = _FakeStore({
        "job_id": "j1", "status": "done", "delivered": False,
        "result": "X", "response_language": "en",
    })
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    content = cb(_FakeCtx())  # no user_content at all
    assert "Criteria Checklist" in content.parts[0].text


def test_callback_resume_forwards_detected_language_to_relaunch():
    store = _FakeStore({"job_id": "j1", "status": "running", "delivered": False, "heartbeat_epoch": 0})
    resumed = {}
    cb = build_recall_callback(
        store,
        start_async_validation=lambda *a, **k: resumed.update(k),
        stale_after=180,
    )
    cb(_FakeCtx(user_text="日本語でお願いします"))
    assert resumed.get("response_language") == "ja"


def test_callback_delivers_cross_session_job_using_record_session_id():
    # _FakeCtx.session.id is "sess-1" (a different/new session). The fallback
    # must mark delivered against the job record's own session ("s_old").
    store = _FakeStoreCrossSession()
    cb = build_recall_callback(store, start_async_validation=lambda *a, **k: "j1", stale_after=180)
    content = cb(_FakeCtx())
    assert content is not None
    assert "OLD-CHECKLIST" in content.parts[0].text
    assert store.delivered_with_session == "s_old"
