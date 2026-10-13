# tests/conftest.py
import sys
import pytest
from pathlib import Path

_GE_DIR = str(Path(__file__).resolve().parent.parent / "gemini-enterprise")
if _GE_DIR not in sys.path:
    sys.path.insert(0, _GE_DIR)


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Temporary skill directory with scripts/ and assets/ subdirs."""
    d = tmp_path / "skill"
    (d / "scripts").mkdir(parents=True)
    (d / "assets").mkdir()
    (d / "references").mkdir()
    return d
