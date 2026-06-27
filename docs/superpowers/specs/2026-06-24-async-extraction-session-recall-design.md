# Async Extraction + Session Recall — Design

**Date:** 2026-06-24
**Status:** Draft for review
**Scope:** Phase 1 (in-runtime). Phase 2 (external worker) deferred.

## Problem

When `document-validator` runs on Gemini Enterprise → Agent Runtime, processing
a large PDF can exceed the frontend's per-turn timeout. Observed behaviour: the
agent finishes server-side (the completed message is visible in the Agent Engine
backend), but the Gemini Enterprise frontend has already timed out, never
displays the result, and **does not backfill** the missed message when the user
continues the conversation.

Root cause is a structural conflict:

- **Keep the turn open** (today's `start_job`/`check_job` polling) → CPU stays
  allocated and the work finishes, but the turn exceeds the frontend timeout.
- **Return fast** → the frontend is satisfied, but the platform considers the
  request done.

Research findings that bound the problem:

- Agent Engine bidirectional streaming caps at **10 minutes**; the Google Front
  End returns 500 if no response within **10 minutes**. So the transport has an
  **absolute** ceiling — streaming keep-alive cannot extend past it.
- Google's official guidance for long tasks is exactly the async instinct:
  **break work into smaller pieces and use session/memory to persist state.**
- "Spawn background work that outlives the request" is **not** a documented,
  supported pattern, so post-fast-return background survival must be treated as
  unverified.

Sources:
- https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/bidirectional-streaming
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/retry-strategy
- https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/code-execution

## Goals

- A triggering turn returns fast (an acknowledgement), well before any timeout.
- The heavy work (PDF extraction + building the Criteria Checklist) continues
  after the turn returns.
- The result is surfaced to the user on their **next message**, by the agent
  actively re-emitting it in that turn's streamed response — **exactly once**.
- No work is ever lost: progress is checkpointed to GCS and resumable.

## Non-Goals

- Pushing a result into the frontend with **no** user action (refresh/reopen
  showing it automatically). The user's own observation confirms Gemini
  Enterprise does not re-read the Agent Engine session to backfill, so this is
  out of scope.
- Granular per-chunk manual confirmation (the previously-tried UX the user
  rejected as cumbersome).
- An external worker / queue. Deferred to Phase 2.

## Direction Decisions (settled during brainstorming)

1. **Surfacing model:** abandon "auto-appear on refresh"; use "agent actively
   re-emits the result on the user's next message." Relies only on guaranteed
   behaviour (a normal streamed response is displayed).
2. **Background scope:** extraction **+** building the Criteria Checklist, then
   stop at the existing Phase 1 human checkpoint. The work that needs no human
   judgement runs in the background; the valuable confirmation gate is kept.
3. **Where the work runs (Phase 1):** in-runtime (reuse the existing
   `start_job` background thread). The external-worker split (Phase 2) is
   deferred until larger files justify it.
4. **Safety net:** GCS checkpoint + resume-on-next-message. This is what makes
   shipping Phase 1 safe *without* first verifying background survival — if the
   platform throttles/kills the background thread after fast-return, the next
   message resumes from the last checkpoint.

## Architecture (Phase 1)

```
User turn (kickoff)
  └─ tool: enqueue_extraction(...) → start background thread, write job=queued → return job_id
  └─ LLM: "已收到，背景處理中" → TURN ENDS (no check_job polling)

Background thread (in-runtime)
  └─ extract_pdf_text.py over page ranges → write per-chunk checkpoint + heartbeat to GCS
  └─ build Criteria Checklist → write result, status=done, delivered=false

Any later user turn
  └─ before_agent_callback: read job state from GCS → state machine (below)
```

### Components

1. **Agent (existing ADK agent)** gains two things:
   - A tool that launches the extraction+checklist job (reusing the `start_job`
     thread mechanism) and **returns immediately** — the kickoff turn does
     **not** call `check_job`.
   - A **`before_agent_callback`** that runs deterministically at the start of
     every turn (not reliant on the LLM remembering) and applies the state
     machine.
2. **Background thread** — reuses `extract_pdf_text.py`; writes checkpoints,
   heartbeat, and final result to GCS via `gcs_state.py`.
3. **GCS job store** — one record per job (schema below), plus a `user_id →
   recent jobs` secondary index for the session-id-change fallback.

### Recall state machine (`before_agent_callback`, every turn)

| GCS job state | Action |
|---|---|
| `done`, not delivered | Inject "Criteria Checklist + summary" into this turn, mark `delivered=true` (atomic), resume the Phase 1 checkpoint flow |
| `running`, heartbeat fresh | Reply "still processing, X/Y pages done" |
| `running`, heartbeat stale (background was killed) | Re-launch background from checkpoint N, fast-return "continuing" |
| `failed` | Report the error, offer to retry |
| none | Normal conversation |

Same code path whether the background survives (one-shot) or is killed
(auto-resume on next message) — and never requires per-chunk manual confirms.

### GCS job record (schema)

```
key: jobs/{session_id}/{job_id}.json
{
  "job_id": "...",
  "session_id": "...",
  "user_id": "...",
  "status": "queued | running | done | failed",
  "progress": { "pages_done": N, "pages_total": M, "next_start": N+1 },
  "heartbeat": "<RFC3339 timestamp, updated each chunk>",
  "result_ref": "results/{session_id}/{job_id}/...",   // checklist + summary
  "delivered": false,
  "error": null
}
secondary index: users/{user_id}/recent_jobs.json  → [ {job_id, session_id, status} ]
```

## Edge Cases

- **session_id changes on close/reopen** — recall keys on `session_id`
  (primary). If a turn's `session_id` has no job but the user has a recent
  completed-undelivered job, fall back to the `user_id` index and surface it.
  (Whether GE preserves `session_id` across reopen is an open spike item; the
  fallback de-risks it without needing the answer first.)
- **Duplicate delivery** — `delivered` is flipped with a GCS
  generation-precondition compare-and-set; the racing loser treats it as
  already delivered and skips. Guarantees exactly-once surfacing.
- **Background killed after fast-return** — heartbeat goes stale; next turn
  resumes from `progress.next_start`. No full restart.
- **Extraction failure** — `status=failed` + `error`; surfaced next turn with a
  retry offer.
- **Unreadable/timed-out pages** — already handled by `extract_pdf_text.py`
  (flagged), and downstream by the Indeterminate / manual-review rules. No
  async-specific handling.
- **Checkpoint write interrupted** — per-chunk, idempotent by page range;
  write-data-then-update-pointer so a half-written chunk is never read as valid.

## Testing

- **pytest (mechanics only, per repo convention):**
  - state machine: each of the five branches produces the correct action
  - `delivered` idempotency: two consecutive recall calls surface once
  - resume: given a checkpoint at page N, relaunch covers the remaining range
  - GCS job-record read/write + atomic compare-and-set (mock GCS)
- **agents-cli eval (behaviour, not pytest):** kickoff turn returns a fast ack;
  the next message surfaces the checklist exactly once.
- No pytest assertions on LLM-generated content (repo rule).

## Open Items to Verify (spikes — not blockers)

1. Does Agent Engine sustain background CPU after a fast-return? Determines how
   often the resume path fires; the design works either way.
2. Does Gemini Enterprise preserve `session_id` across close/reopen? Determines
   how often the `user_id` fallback is needed.
3. Gemini Enterprise chat front-door exact timeout. The fast-return ack is
   effectively instant, so this is a confirmation, not a risk.

## Phase 2 (deferred)

If larger files start hitting the wall or the resume path fires too often,
externalize the heavy loop into a durable worker (e.g. Cloud Run Job triggered
by the kickoff tool). **Only "where the heavy loop runs" changes** — the GCS
state model, recall callback, resume logic, and `delivered` lock are all reused.
