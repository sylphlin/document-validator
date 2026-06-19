import re
import yaml
from pathlib import Path


class SkillLoadError(Exception):
    pass


def load_skill(skill_dir: Path) -> tuple[str, dict]:
    """Parse SKILL.md; return (body, metadata)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise SkillLoadError(f"SKILL.md not found at {skill_md}")

    content = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    if not match:
        raise SkillLoadError("SKILL.md must begin with YAML frontmatter (--- ... ---)")

    metadata = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()

    if not metadata.get("name"):
        raise SkillLoadError("SKILL.md frontmatter must include 'name'")
    if not metadata.get("description"):
        raise SkillLoadError("SKILL.md frontmatter must include 'description'")

    return body, metadata
