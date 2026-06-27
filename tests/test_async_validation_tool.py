# tests/test_async_validation_tool.py
from pathlib import Path

from agent.tools import make_tools


def test_start_async_validation_launches_detached_and_returns_job_id(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")

    calls = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            calls["cmd"] = cmd
            calls["kwargs"] = kwargs

    monkeypatch.setattr("agent.tools.subprocess.Popen", _FakePopen)

    tools = make_tools(skill_dir, timeout=60)
    start_async_validation = tools[-1]  # last returned tool

    job_id = start_async_validation(["https://drive/x"], "sess-1", "user-1")

    assert job_id and isinstance(job_id, str)
    assert calls["kwargs"]["start_new_session"] is True
    assert "run_async_validation.py" in " ".join(calls["cmd"])
    assert "--session-id" in calls["cmd"] and "sess-1" in calls["cmd"]
    assert "--criteria" in calls["cmd"] and "https://drive/x" in calls["cmd"]


def test_start_async_validation_forwards_response_language(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")

    calls = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            calls["cmd"] = cmd

    monkeypatch.setattr("agent.tools.subprocess.Popen", _FakePopen)
    start_async_validation = make_tools(skill_dir, timeout=60)[-1]

    start_async_validation(["ref"], "s", "u", response_language="ja")

    assert "--response-language" in calls["cmd"] and "ja" in calls["cmd"]


def test_start_async_validation_omits_response_language_flag_when_blank(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")

    calls = {}
    monkeypatch.setattr("agent.tools.subprocess.Popen", lambda cmd, **k: calls.setdefault("cmd", cmd))
    start_async_validation = make_tools(skill_dir, timeout=60)[-1]

    start_async_validation(["ref"], "s", "u")

    assert "--response-language" not in calls["cmd"]


def test_start_async_validation_reuses_job_id_on_resume(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run_async_validation.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr("agent.tools.subprocess.Popen", lambda *a, **k: None)

    start_async_validation = make_tools(skill_dir, timeout=60)[-1]
    job_id = start_async_validation(["ref"], "s", "u", resume_job_id="fixed123")
    assert job_id == "fixed123"
