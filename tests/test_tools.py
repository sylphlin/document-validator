# tests/test_tools.py
import pytest
from pathlib import Path
from agent.tools import make_tools


def test_run_script_executes_python(skill_dir):
    (skill_dir / "scripts" / "hello.py").write_text('print("hi")')
    run_script, _ = make_tools(skill_dir)
    assert run_script("hello.py", []) == "hi\n"


def test_run_script_passes_args(skill_dir):
    (skill_dir / "scripts" / "echo.py").write_text(
        "import sys; print(sys.argv[1])"
    )
    run_script, _ = make_tools(skill_dir)
    assert run_script("echo.py", ["world"]) == "world\n"


def test_run_script_returns_error_on_failure(skill_dir):
    (skill_dir / "scripts" / "fail.py").write_text("raise ValueError('boom')")
    run_script, _ = make_tools(skill_dir)
    result = run_script("fail.py", [])
    assert result.startswith("[error]")


def test_run_script_rejects_path_traversal(skill_dir):
    run_script, _ = make_tools(skill_dir)
    result = run_script("../../etc/passwd", [])
    assert result == "[error] path traversal not allowed"


def test_run_script_returns_error_for_missing_script(skill_dir):
    run_script, _ = make_tools(skill_dir)
    result = run_script("nonexistent.py", [])
    assert result.startswith("[error] script not found")


def test_read_asset_returns_file_contents(skill_dir):
    (skill_dir / "references" / "guide.md").write_text("# Guide")
    _, read_asset = make_tools(skill_dir)
    assert read_asset("references/guide.md") == "# Guide"


def test_read_asset_rejects_path_traversal(skill_dir):
    _, read_asset = make_tools(skill_dir)
    result = read_asset("../../etc/passwd")
    assert result == "[error] path traversal not allowed"


def test_read_asset_returns_error_for_missing_file(skill_dir):
    _, read_asset = make_tools(skill_dir)
    result = read_asset("assets/missing.txt")
    assert result.startswith("[error] file not found")


def test_read_asset_returns_error_for_directory(skill_dir):
    _, read_asset = make_tools(skill_dir)
    result = read_asset("scripts")
    assert result.startswith("[error] not a file")


def test_run_script_rejects_non_python_extension(skill_dir):
    (skill_dir / "scripts" / "run.sh").write_text("echo hi")
    run_script, _ = make_tools(skill_dir)
    result = run_script("run.sh", [])
    assert result.startswith("[error] only Python scripts")


def test_run_script_includes_partial_output_on_timeout(skill_dir):
    (skill_dir / "scripts" / "slow.py").write_text(
        "import time, sys\n"
        "print('progress: page 1 done', flush=True)\n"
        "time.sleep(5)\n"
    )
    run_script, _ = make_tools(skill_dir, timeout=1)
    result = run_script("slow.py", [])
    assert result.startswith("[error] script timed out after 1s")
    assert "progress: page 1 done" in result
