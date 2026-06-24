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
