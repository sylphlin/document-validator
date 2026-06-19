# agent/tools.py
import subprocess
import sys
from pathlib import Path


def make_tools(skill_dir: Path, timeout: int = 60):
    """Return (run_script, read_asset) bound to skill_dir."""
    scripts_dir = skill_dir / "scripts"

    def run_script(script: str, args: list[str]) -> str:
        """Execute a Python script from the skill's scripts/ directory."""
        script_path = (scripts_dir / script).resolve()
        try:
            script_path.relative_to(scripts_dir.resolve())
        except ValueError:
            return "[error] path traversal not allowed"

        if not script_path.exists():
            return f"[error] script not found: {script}"

        if script_path.suffix != ".py":
            return f"[error] only Python scripts (.py) are supported: {script}"

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)] + list(args),
                cwd=str(skill_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                return f"[error] {result.stderr}"
            return result.stdout
        except subprocess.TimeoutExpired:
            return f"[error] script timed out after {timeout}s"

    def read_asset(path: str) -> str:
        """Read a text file from the skill directory."""
        skill_dir_resolved = skill_dir.resolve()
        asset_path = (skill_dir / path).resolve()
        try:
            asset_path.relative_to(skill_dir_resolved)
        except ValueError:
            return "[error] path traversal not allowed"

        if not asset_path.exists():
            return f"[error] file not found: {path}"

        if not asset_path.is_file():
            return f"[error] not a file: {path}"

        return asset_path.read_text(encoding="utf-8")

    return run_script, read_asset
