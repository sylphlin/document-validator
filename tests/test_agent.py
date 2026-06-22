import pytest
from pathlib import Path
from agent.agent import build_agent


def _make_skill(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill.\n---\nDo the task.\n",
        encoding="utf-8",
    )
    return skill_dir


def test_build_agent_uses_skill_name(tmp_path):
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    assert agent.name == "test_skill"


def test_build_agent_instruction_contains_skill_body(tmp_path):
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    assert "Do the task." in agent.instruction(None)


def test_build_agent_no_tools_when_no_scripts_or_assets(tmp_path):
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    assert agent.tools == []
    assert "start_job" not in agent.instruction(None)
    assert "check_job" not in agent.instruction(None)
    assert "read_asset" not in agent.instruction(None)


def test_build_agent_includes_job_tools_when_scripts_present(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (skill_dir / "scripts" / "demo.py").write_text("print('hi')\n", encoding="utf-8")
    agent = build_agent(skill_dir=skill_dir)
    assert len(agent.tools) == 2
    assert "start_job" in agent.instruction(None)
    assert "check_job" in agent.instruction(None)
    assert "read_asset" not in agent.instruction(None)


def test_build_agent_includes_read_asset_when_assets_present(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (skill_dir / "assets").mkdir()
    (skill_dir / "assets" / "template.md").write_text("hello", encoding="utf-8")
    agent = build_agent(skill_dir=skill_dir)
    assert len(agent.tools) == 1
    assert "read_asset" in agent.instruction(None)
    assert "start_job" not in agent.instruction(None)


class _FakeSession:
    def __init__(self, session_id):
        self.id = session_id


class _FakeContext:
    def __init__(self, session_id, user_id):
        self.session = _FakeSession(session_id)
        self.user_id = user_id


def test_build_agent_instruction_includes_ids_when_context_has_session(tmp_path):
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    ctx = _FakeContext(session_id="sess-123", user_id="user-456")
    instruction = agent.instruction(ctx)
    assert "sess-123" in instruction
    assert "user-456" in instruction


def test_build_agent_instruction_omits_ids_when_context_has_no_session(tmp_path):
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    assert "session ID" not in agent.instruction(None)

    class _ContextWithoutSession:
        pass

    assert "session ID" not in agent.instruction(_ContextWithoutSession())


def test_build_agent_excludes_oauth_drive_tool_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    assert "fetch_drive_file_oauth" not in agent.instruction(None)


def test_build_agent_includes_oauth_drive_tool_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    skill_dir = _make_skill(tmp_path)
    agent = build_agent(skill_dir=skill_dir)
    assert any(getattr(t, "__name__", "") == "fetch_drive_file_oauth" for t in agent.tools)
    assert "fetch_drive_file_oauth" in agent.instruction(None)
