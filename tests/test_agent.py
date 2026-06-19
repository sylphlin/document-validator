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
    assert "run_script" not in agent.instruction(None)
    assert "read_asset" not in agent.instruction(None)


def test_build_agent_includes_run_script_when_scripts_present(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (skill_dir / "scripts" / "demo.py").write_text("print('hi')\n", encoding="utf-8")
    agent = build_agent(skill_dir=skill_dir)
    assert len(agent.tools) == 1
    assert "run_script" in agent.instruction(None)
    assert "read_asset" not in agent.instruction(None)


def test_build_agent_includes_read_asset_when_assets_present(tmp_path):
    skill_dir = _make_skill(tmp_path)
    (skill_dir / "assets").mkdir()
    (skill_dir / "assets" / "template.md").write_text("hello", encoding="utf-8")
    agent = build_agent(skill_dir=skill_dir)
    assert len(agent.tools) == 1
    assert "read_asset" in agent.instruction(None)
    assert "run_script" not in agent.instruction(None)
