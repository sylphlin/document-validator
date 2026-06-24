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
