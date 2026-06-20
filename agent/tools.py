# agent/tools.py
import collections
import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

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

    def check_job(job_id: str) -> str:
        with jobs_lock:
            job = jobs.get(job_id)

        if job is None:
            return f"[error] unknown job_id: {job_id}"

        if job["status"] == "running":
            return "[status] running"

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
        "Poll a job started by start_job. Returns '[status] running' if it's still "
        "in progress, the script's output if it finished, or '[error] ...' if it "
        "failed. Call this again after a short wait if the status is still running."
    )

    return start_job, check_job, read_asset
