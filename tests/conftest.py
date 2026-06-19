# tests/conftest.py
import pytest
from pathlib import Path


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Temporary skill directory with scripts/ and assets/ subdirs."""
    d = tmp_path / "skill"
    (d / "scripts").mkdir(parents=True)
    (d / "assets").mkdir()
    (d / "references").mkdir()
    return d
