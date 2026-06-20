# tests/test_tools.py
import time
import pytest
from pathlib import Path
from agent.tools import make_tools


def _wait_for_result(check_job, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = check_job(job_id)
        if result != "[status] running":
            return result
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_start_job_executes_python(skill_dir):
    (skill_dir / "scripts" / "hello.py").write_text('print("hi")')
    start_job, check_job, _ = make_tools(skill_dir)
    job_id = start_job("hello.py", [])
    assert _wait_for_result(check_job, job_id) == "hi\n"


def test_start_job_passes_args(skill_dir):
    (skill_dir / "scripts" / "echo.py").write_text(
        "import sys; print(sys.argv[1])"
    )
    start_job, check_job, _ = make_tools(skill_dir)
    job_id = start_job("echo.py", ["world"])
    assert _wait_for_result(check_job, job_id) == "world\n"


def test_check_job_reports_running_before_completion(skill_dir):
    (skill_dir / "scripts" / "slow.py").write_text("import time; time.sleep(0.3)")
    start_job, check_job, _ = make_tools(skill_dir)
    job_id = start_job("slow.py", [])
    assert check_job(job_id) == "[status] running"
    _wait_for_result(check_job, job_id)


def test_check_job_returns_error_on_failure(skill_dir):
    (skill_dir / "scripts" / "fail.py").write_text("raise ValueError('boom')")
    start_job, check_job, _ = make_tools(skill_dir)
    job_id = start_job("fail.py", [])
    result = _wait_for_result(check_job, job_id)
    assert result.startswith("[error]")


def test_check_job_returns_error_for_unknown_job_id(skill_dir):
    _, check_job, _ = make_tools(skill_dir)
    result = check_job("nonexistent-job-id")
    assert result == "[error] unknown job_id: nonexistent-job-id"


def test_start_job_rejects_path_traversal(skill_dir):
    start_job, _, _ = make_tools(skill_dir)
    result = start_job("../../etc/passwd", [])
    assert result == "[error] path traversal not allowed"


def test_start_job_returns_error_for_missing_script(skill_dir):
    start_job, _, _ = make_tools(skill_dir)
    result = start_job("nonexistent.py", [])
    assert result.startswith("[error] script not found")


def test_start_job_error_lists_available_scripts(skill_dir):
    (skill_dir / "scripts" / "real.py").write_text("print('hi')")
    start_job, _, _ = make_tools(skill_dir)
    result = start_job("nonexistent.py", [])
    assert "real.py" in result


def test_start_job_docstring_lists_available_scripts(skill_dir):
    (skill_dir / "scripts" / "real.py").write_text("print('hi')")
    start_job, _, _ = make_tools(skill_dir)
    assert "real.py" in start_job.__doc__


def test_start_job_docstring_says_none_when_no_scripts(skill_dir):
    start_job, _, _ = make_tools(skill_dir)
    assert "(none)" in start_job.__doc__


def test_read_asset_returns_file_contents(skill_dir):
    (skill_dir / "references" / "guide.md").write_text("# Guide")
    _, _, read_asset = make_tools(skill_dir)
    assert read_asset("references/guide.md") == "# Guide"


def test_read_asset_rejects_path_traversal(skill_dir):
    _, _, read_asset = make_tools(skill_dir)
    result = read_asset("../../etc/passwd")
    assert result == "[error] path traversal not allowed"


def test_read_asset_returns_error_for_missing_file(skill_dir):
    _, _, read_asset = make_tools(skill_dir)
    result = read_asset("assets/missing.txt")
    assert result.startswith("[error] file not found")


def test_read_asset_returns_error_for_directory(skill_dir):
    _, _, read_asset = make_tools(skill_dir)
    result = read_asset("scripts")
    assert result.startswith("[error] not a file")


def test_start_job_rejects_non_python_extension(skill_dir):
    (skill_dir / "scripts" / "run.sh").write_text("echo hi")
    start_job, _, _ = make_tools(skill_dir)
    result = start_job("run.sh", [])
    assert result.startswith("[error] only Python scripts")


def test_check_job_includes_partial_output_on_timeout(skill_dir):
    (skill_dir / "scripts" / "slow.py").write_text(
        "import time, sys\n"
        "print('progress: page 1 done', flush=True)\n"
        "time.sleep(5)\n"
    )
    start_job, check_job, _ = make_tools(skill_dir, timeout=1)
    job_id = start_job("slow.py", [])
    result = _wait_for_result(check_job, job_id, timeout=5)
    assert result.startswith("[error] script timed out after 1s")
    assert "progress: page 1 done" in result


def test_timeout_kills_grandchild_processes(skill_dir, tmp_path):
    # A script that spawns its own subprocess (like extract_pdf_text.py's
    # ProcessPoolExecutor workers) — on timeout, the grandchild must die too,
    # not just the direct child, or it's orphaned and keeps holding memory.
    heartbeat = tmp_path / "heartbeat.txt"
    (skill_dir / "scripts" / "spawn_child.py").write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c',\n"
        f"    \"import time\\nwith open(r'{heartbeat}', 'w') as f:\\n\"\n"
        "    \"    for i in range(1000):\\n\"\n"
        "    \"        f.write(str(i)); f.flush(); f.seek(0)\\n\"\n"
        "    \"        time.sleep(0.1)\\n\"\n"
        "])\n"
        "time.sleep(100)\n"
    )
    start_job, check_job, _ = make_tools(skill_dir, timeout=1)
    job_id = start_job("spawn_child.py", [])
    time.sleep(0.3)
    assert heartbeat.exists(), "grandchild never started"

    _wait_for_result(check_job, job_id, timeout=5)

    reading_after_kill = heartbeat.read_text()
    time.sleep(0.5)
    reading_later = heartbeat.read_text()
    assert reading_after_kill == reading_later, "grandchild kept running after timeout"
