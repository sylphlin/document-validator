# agent/tools.py
import collections
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# check_job blocks for up to this long (polling internally) before returning
# "[status] running" if the job hasn't finished yet. Without this, nothing
# stops the model from calling check_job again the instant it gets "running"
# back — with no real time passing between calls, a multi-minute job turns
# into dozens of near-identical "still waiting" tool calls and narration
# lines in a row. Capped rather than unbounded so a single check_job call
# can't itself become the thing that runs long enough to trip a turn-level
# timeout.
DEFAULT_CHECK_JOB_WAIT_SECONDS = 5
MAX_CHECK_JOB_WAIT_SECONDS = 15

# A single container instance serves many conversations over its lifetime, not
# one container per conversation — every finished job's full stdout (e.g. an
# entire extracted PDF chunk's Markdown) would otherwise stay resident in
# memory forever. This caps how many finished jobs are kept at once; a job
# still "running" is never evicted regardless of how many others pile up.
MAX_RETAINED_JOBS = 50


def _available_scripts(scripts_dir: Path) -> list[str]:
    if not scripts_dir.is_dir():
        return []
    return sorted(p.name for p in scripts_dir.glob("*.py"))


def make_tools(skill_dir: Path, timeout: int = 60):
    """Return (start_job, check_job, read_asset) bound to skill_dir.

    Scripts run in a background thread so a single tool call never blocks the
    model for the script's full duration — start_job returns a job_id almost
    immediately, and check_job is polled for the result. This matters because a
    chat surface in front of this skill may apply its own timeout to a single
    conversational turn that is shorter than how long a script can legitimately
    take (e.g. extracting a 100+ page PDF) — a tool call that blocks for minutes
    risks tripping that turn-level timeout even though the script itself would
    have finished fine given more time.
    """
    scripts_dir = skill_dir / "scripts"
    jobs: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
    jobs_lock = threading.Lock()

    def _evict_old_jobs_locked():
        """Caller must hold jobs_lock. Evicts oldest *finished* jobs only."""
        if len(jobs) <= MAX_RETAINED_JOBS:
            return
        for jid in list(jobs.keys()):
            if len(jobs) <= MAX_RETAINED_JOBS:
                break
            if jobs[jid]["status"] != "running":
                del jobs[jid]

    def _run_job(job_id: str, script_path: Path, args: list[str]):
        # Use Popen directly (not subprocess.run) so a timeout can kill the
        # whole process group, not just this one PID. subprocess.run's own
        # timeout handling only kills the direct child — a script that spawns
        # its own workers (e.g. extract_pdf_text.py's ProcessPoolExecutor)
        # would leave those workers orphaned and still running, still holding
        # their memory, with nothing left to clean them up.
        process = subprocess.Popen(
            [sys.executable, str(script_path)] + list(args),
            cwd=str(skill_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            with jobs_lock:
                jobs[job_id] = {
                    "status": "done" if process.returncode == 0 else "failed",
                    "stdout": stdout,
                    "stderr": stderr,
                }
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Whatever the script printed before the timeout is still
            # recoverable via communicate() after the kill — surfacing it is
            # the difference between "timed out, no idea why" and "timed out
            # right after page 14", since the process is killed with no
            # other record of its progress.
            stdout, stderr = process.communicate()
            with jobs_lock:
                jobs[job_id] = {
                    "status": "failed",
                    "stdout": stdout or "",
                    "stderr": stderr or "",
                    "error": f"script timed out after {timeout}s",
                }
        except Exception as e:
            with jobs_lock:
                jobs[job_id] = {"status": "failed", "stdout": "", "stderr": "", "error": str(e)}

    def start_job(script: str, args: list[str]) -> str:
        script_path = (scripts_dir / script).resolve()
        try:
            script_path.relative_to(scripts_dir.resolve())
        except ValueError:
            return "[error] path traversal not allowed"

        if not script_path.exists():
            available = _available_scripts(scripts_dir)
            listing = ", ".join(available) if available else "(none)"
            return f"[error] script not found: {script}. Available scripts: {listing}"

        if script_path.suffix != ".py":
            return f"[error] only Python scripts (.py) are supported: {script}"

        job_id = uuid.uuid4().hex[:12]
        with jobs_lock:
            jobs[job_id] = {"status": "running"}
            _evict_old_jobs_locked()
        threading.Thread(target=_run_job, args=(job_id, script_path, list(args)), daemon=True).start()
        return job_id

    def check_job(job_id: str, wait_seconds: int = DEFAULT_CHECK_JOB_WAIT_SECONDS) -> str:
        wait_seconds = max(0, min(wait_seconds, MAX_CHECK_JOB_WAIT_SECONDS))
        deadline = time.time() + wait_seconds
        while True:
            with jobs_lock:
                job = jobs.get(job_id)

            if job is None:
                return f"[error] unknown job_id: {job_id}"

            if job["status"] != "running":
                break

            remaining = deadline - time.time()
            if remaining <= 0:
                return "[status] running"
            time.sleep(min(0.5, remaining))

        if job["status"] == "done":
            return job["stdout"]

        # failed
        message = job.get("error") or job.get("stderr") or "script failed with no output"
        details = ""
        if job.get("stdout", "").strip():
            details += f"\n--- partial stdout ---\n{job['stdout'].strip()}"
        if job.get("error") and job.get("stderr", "").strip():
            details += f"\n--- stderr ---\n{job['stderr'].strip()}"
        return f"[error] {message}{details}"

    def read_asset(path: str) -> str:
        """Read a text file from the skill directory."""
        skill_dir_resolved = skill_dir.resolve()
        asset_path = (skill_dir / path).resolve()
        try:
            asset_path.relative_to(skill_dir_resolved)
        except ValueError:
            return "[error] path traversal not allowed"

        if not asset_path.exists():
            return f"[error] file not found: {path}"

        if not asset_path.is_file():
            return f"[error] not a file: {path}"

        return asset_path.read_text(encoding="utf-8")

    def start_async_validation(
        criteria_refs: list[str],
        session_id: str,
        user_id: str,
        resume_job_id: str = "",
        response_language: str = "",
    ) -> str:
        """Launch background criteria extraction + checklist build, return a job_id now.

        Unlike start_job, this is fully detached and NOT bounded by the script
        timeout — it must outlive this turn. Do not poll it; the result is
        surfaced automatically on the user's next message. On resume, pass the
        existing resume_job_id so the background job continues from its GCS
        checkpoint instead of starting over.

        Pass response_language as whatever language you are currently
        responding to the user in (e.g. "Traditional Chinese", "Spanish") — no
        fixed list, just describe it. The background checklist-build call has
        no access to the conversation, so without this it cannot know what
        language to write the checklist in.
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
        if response_language:
            cmd += ["--response-language", response_language]
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

    # Set after definition (not as a static docstring) so the listed scripts
    # always reflect what's actually bundled with this skill right now —
    # giving the model a concrete menu instead of a generic description it
    # has to infer valid filenames from, which is what invites it to invent
    # a plausible-sounding script that doesn't exist.
    available = _available_scripts(scripts_dir)
    listing = ", ".join(available) if available else "(none)"
    start_job.__doc__ = (
        "Start a Python script from the skill's scripts/ directory in the background "
        "and return a job_id immediately — it does not wait for the script to finish.\n\n"
        f"Available scripts: {listing}\n"
        "Only call this with one of the exact filenames listed above — never invent "
        "a filename, even if it sounds plausible for what you're trying to do.\n"
        "Pass the returned job_id to check_job to poll for the result."
    )
    check_job.__doc__ = (
        "Poll a job started by start_job. Waits up to wait_seconds (default "
        f"{DEFAULT_CHECK_JOB_WAIT_SECONDS}, max {MAX_CHECK_JOB_WAIT_SECONDS}) for it to "
        "finish before returning — this call itself takes real time, so there's no need "
        "to call it again immediately after getting '[status] running' back. Returns the "
        "script's output if it finished, '[error] ...' if it failed, or '[status] running' "
        "if it's still going after the wait. Call again (optionally with a longer "
        "wait_seconds) if still running."
    )

    return start_job, check_job, read_asset, start_async_validation
