#!/usr/bin/env python3
"""
tests/check_core_sync.py - Drift guard between the canonical Agent Plugin skill
(`skills/document-validator/`) and its downstream mirror inside the Gemini
Enterprise package (`gemini-enterprise/skill/`), plus designated model invariant
verification.

Usage:
    python3 tests/check_core_sync.py
"""

import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL_DIR = ROOT / "skills" / "document-validator"
ENTERPRISE_SKILL_DIR = ROOT / "gemini-enterprise" / "skill"
ENTERPRISE_AGENT_DIR = ROOT / "gemini-enterprise" / "agent"

SKILL_FILES = [
    "SKILL.md",
    "scripts/extract_pdf_text.py",
    "scripts/fetch_drive_file.py",
    "scripts/gcs_state.py",
    "scripts/job_store.py",
    "scripts/run_async_validation.py",
]


def _diff(a_path: Path, b_path: Path) -> str:
    a_text = a_path.read_text(encoding="utf-8")
    b_text = b_path.read_text(encoding="utf-8")
    if a_text == b_text:
        return ""
    return "".join(
        difflib.unified_diff(
            a_text.splitlines(keepends=True),
            b_text.splitlines(keepends=True),
            fromfile=str(a_path.relative_to(ROOT)),
            tofile=str(b_path.relative_to(ROOT)),
        )
    )


def get_designated_models() -> set[str]:
    """Parse approved designated models from .env.example as the baseline specification."""
    models = set()
    for env_path in (ROOT / ".env.example", ROOT / "gemini-enterprise" / ".env.example"):
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("MODEL="):
                    val = line.split("=", 1)[1].strip()
                    if val:
                        models.add(val)
    return models


def check_model_invariants() -> list[str]:
    """Ensure no code mistakenly introduces or falls back to models outside .env.example."""
    designated_models = get_designated_models()
    errors = []
    model_pattern = re.compile(r"gemini-[0-9]+(?:\.[0-9]+)?-[a-z0-9\-]+")

    check_dirs = [
        CANONICAL_SKILL_DIR / "scripts",
        ENTERPRISE_SKILL_DIR / "scripts",
        ENTERPRISE_AGENT_DIR,
    ]
    for d in check_dirs:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.py")):
            text = p.read_text(encoding="utf-8")
            for match in model_pattern.finditer(text):
                found_model = match.group(0)
                if found_model not in designated_models:
                    errors.append(
                        f"[UNAPPROVED MODEL] {p.relative_to(ROOT)}: Found non-designated model '{found_model}'. "
                        f"Only designated models ({', '.join(sorted(designated_models))}) are allowed."
                    )
    return errors


def main() -> int:
    failures = []

    # 1. Model invariant validation
    model_errors = check_model_invariants()
    if model_errors:
        designated = get_designated_models()
        print("[FAIL] Model Invariant Check Failed:\n")
        print("\n".join(model_errors))
        print(
            f"\n[RULE] Only designated models ({', '.join(sorted(designated))}) may be used. "
            "To upgrade or change models, update .env / .env.example.\n"
        )
        return 1

    # 2. Dual-copy sync validation
    for rel_path in SKILL_FILES:
        a = CANONICAL_SKILL_DIR / rel_path
        b = ENTERPRISE_SKILL_DIR / rel_path
        if not a.exists() or not b.exists():
            failures.append(f"{a.relative_to(ROOT)} or {b.relative_to(ROOT)} is missing.")
            continue
        diff = _diff(a, b)
        if diff:
            failures.append(diff)

    if failures:
        print("[FAIL] skills/document-validator and gemini-enterprise/skill have drifted apart:\n")
        print("\n".join(failures))
        print(
            "\n[CANONICAL SOURCE OF TRUTH]\n"
            "  'skills/document-validator/' is the primary upstream source of truth.\n"
            "  'gemini-enterprise/skill/' is the downstream sync mirror.\n"
            "  Always port upstream improvements from 'skills/document-validator/' to 'gemini-enterprise/skill/'.\n"
        )
        return 1

    print("[OK] skills/document-validator and gemini-enterprise/skill are in sync and model invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
