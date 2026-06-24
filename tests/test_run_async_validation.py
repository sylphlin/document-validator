# tests/test_run_async_validation.py
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "skill" / "scripts" / "run_async_validation.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_async_validation", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_phase1_prompt_section_extracts_phase_1_block():
    mod = _load()
    skill_md = "## Phase 0\nintake\n## Phase 1: Criteria Checklist Extraction\nDo X.\nDo Y.\n## Phase 2\nscore\n"
    section = mod.phase1_prompt_section(skill_md)
    assert "Do X." in section and "Do Y." in section
    assert "score" not in section and "intake" not in section


def test_chunk_ranges_splits_and_resumes():
    mod = _load()
    assert mod.chunk_ranges(1, 45, 20) == [(1, 20), (21, 40), (41, 45)]
    assert mod.chunk_ranges(41, 45, 20) == [(41, 45)]   # resume mid-file
    assert mod.chunk_ranges(50, 45, 20) == []           # already past the end
    assert mod.chunk_ranges(1, 20, 20) == [(1, 20)]     # exact multiple
