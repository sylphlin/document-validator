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


import re
import time

from google.genai import types

_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[぀-ヿ]")
_CJK = re.compile(r"[一-鿿]")
# A handful of common characters that only have a Simplified form (their
# Traditional counterpart is a different glyph) — enough to tell zh-CN from
# zh-TW for short status-line purposes without a full conversion table.
_SIMPLIFIED_ONLY_CHARS = set("国会议为这达过给来还后么问题没经济发动业产")


def _detect_language(text):
    """Best-effort script detection for the *current* turn's input text.

    Used only to pick which canned-message template set to respond with —
    not to translate content — so a coarse heuristic is fine. Returns None
    when the text gives no signal (e.g. empty), letting the caller fall back
    to the job's persisted response_language.
    """
    if not text:
        return None
    if _HANGUL.search(text):
        return "ko"
    if _KANA.search(text):
        return "ja"
    if _CJK.search(text):
        return "zh-CN" if any(ch in _SIMPLIFIED_ONLY_CHARS for ch in text) else "zh-TW"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return None


def _user_text(callback_context):
    content = getattr(callback_context, "user_content", None)
    if not content or not getattr(content, "parts", None):
        return ""
    return "".join(getattr(p, "text", None) or "" for p in content.parts)

# Canned status lines the recall callback returns directly (it short-circuits
# the model, so SKILL.md's "Response Language" rule can't apply on its own —
# these have to be picked explicitly from the job's persisted response_language).
_MESSAGES = {
    "en": {
        "deliver_suffix": "\n\nAbove is the Criteria Checklist built in the background. "
        "Let me know if it looks right and I'll start scoring.",
        "failed": "Background processing failed: {error}. Want me to retry?",
        "running": "Still processing in the background ({stage}: {done}/{total}).",
        "resumed": "The previous background job was interrupted; it's been automatically resumed from where it left off.",
        "unknown_error": "unknown error",
    },
    "zh-TW": {
        "deliver_suffix": "\n\n以上是背景處理完成的查核清單，請確認內容無誤後我再開始評分。",
        "failed": "背景處理失敗：{error}。需要我重試嗎？",
        "running": "還在背景處理中（{stage}：{done}/{total}）。",
        "resumed": "先前的背景作業中斷了，已自動從上次進度接續處理。",
        "unknown_error": "未知錯誤",
    },
    "zh-CN": {
        "deliver_suffix": "\n\n以上是后台处理完成的查核清单，请确认内容无误后我再开始评分。",
        "failed": "后台处理失败：{error}。需要我重试吗？",
        "running": "还在后台处理中（{stage}：{done}/{total}）。",
        "resumed": "先前的后台作业中断了，已自动从上次进度接续处理。",
        "unknown_error": "未知错误",
    },
    "ja": {
        "deliver_suffix": "\n\n以上はバックグラウンドで作成されたチェックリストです。内容に問題なければ採点を始めます。",
        "failed": "バックグラウンド処理に失敗しました：{error}。再試行しますか？",
        "running": "まだバックグラウンドで処理中です（{stage}：{done}/{total}）。",
        "resumed": "以前のバックグラウンド処理が中断されていたため、続きから自動的に再開しました。",
        "unknown_error": "不明なエラー",
    },
    "ko": {
        "deliver_suffix": "\n\n위는 백그라운드에서 생성된 체크리스트입니다. 내용에 문제가 없으면 채점을 시작하겠습니다.",
        "failed": "백그라운드 처리에 실패했습니다: {error}. 다시 시도할까요?",
        "running": "아직 백그라운드에서 처리 중입니다 ({stage}: {done}/{total}).",
        "resumed": "이전 백그라운드 작업이 중단되어 마지막 진행 상태에서 자동으로 재개했습니다.",
        "unknown_error": "알 수 없는 오류",
    },
}


def _messages_for(job, detected_language=None):
    # The current message's detected language wins over whatever language the
    # job happened to be running in — that's stale by definition (recorded
    # when the background job was kicked off, possibly in an earlier session).
    language = detected_language or job.get("response_language", "")
    return _MESSAGES.get(language, _MESSAGES["zh-TW"])


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
        detected = _detect_language(_user_text(callback_context))
        msgs = _messages_for(job, detected)
        if action == DELIVER:
            if job_store.mark_delivered(user_id, session_id, job["job_id"]):
                return _content(job.get("result", "") + msgs["deliver_suffix"])
            return None
        if action == FAILED:
            if job_store.mark_delivered(user_id, session_id, job["job_id"]):
                return _content(msgs["failed"].format(error=job.get("error", msgs["unknown_error"])))
            return None
        if action == RUNNING:
            p = job.get("progress", {})
            return _content(msgs["running"].format(
                stage=p.get("stage", "?"), done=p.get("done", "?"), total=p.get("total", "?")
            ))
        if action == RESUME:
            # Re-launch with the detected language so the resumed background
            # job's own checklist-build call also picks it up (run() persists
            # whatever non-empty language it's given, overwriting the stale one).
            start_async_validation(
                [], session_id, user_id, resume_job_id=job["job_id"], response_language=detected or ""
            )
            return _content(msgs["resumed"])
        return None

    return before_agent_callback
