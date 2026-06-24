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
            if job_store.mark_delivered(user_id, session_id, job["job_id"]):
                return _content(f"背景處理失敗：{job.get('error', '未知錯誤')}。需要我重試嗎？")
            return None
        if action == RUNNING:
            p = job.get("progress", {})
            return _content(f"還在背景處理中（{p.get('stage', '處理')}：{p.get('done', '?')}/{p.get('total', '?')}）。")
        if action == RESUME:
            start_async_validation([], session_id, user_id, resume_job_id=job["job_id"])
            return _content("先前的背景作業中斷了，已自動從上次進度接續處理。")
        return None

    return before_agent_callback
