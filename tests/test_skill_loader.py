import pytest
from pathlib import Path
from agent.skill_loader import load_skill, SkillLoadError


def _write_skill(tmp_path: Path, content: str) -> Path:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def test_load_skill_returns_body_and_metadata(tmp_path):
    content = (
        "---\n"
        "name: my-skill\n"
        "description: Does things.\n"
        "---\n"
        "# Instructions\n\nDo the thing.\n"
    )
    skill_dir = _write_skill(tmp_path, content)
    body, metadata = load_skill(skill_dir)
    assert metadata["name"] == "my-skill"
    assert metadata["description"] == "Does things."
    assert body == "# Instructions\n\nDo the thing."


def test_load_skill_strips_body_whitespace(tmp_path):
    content = "---\nname: s\ndescription: d\n---\n\n  body  \n"
    skill_dir = _write_skill(tmp_path, content)
    body, _ = load_skill(skill_dir)
    assert body == "body"


def test_load_skill_raises_if_skill_md_missing(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    with pytest.raises(SkillLoadError, match="SKILL.md not found"):
        load_skill(skill_dir)


def test_load_skill_raises_if_missing_frontmatter(tmp_path):
    skill_dir = _write_skill(tmp_path, "no frontmatter here")
    with pytest.raises(SkillLoadError, match="frontmatter"):
        load_skill(skill_dir)


def test_load_skill_raises_if_name_missing(tmp_path):
    content = "---\ndescription: d\n---\nbody"
    skill_dir = _write_skill(tmp_path, content)
    with pytest.raises(SkillLoadError, match="name"):
        load_skill(skill_dir)


def test_load_skill_raises_if_description_missing(tmp_path):
    content = "---\nname: my-skill\n---\nbody"
    skill_dir = _write_skill(tmp_path, content)
    with pytest.raises(SkillLoadError, match="description"):
        load_skill(skill_dir)
