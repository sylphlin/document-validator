import os
import re
from pathlib import Path
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.genai import types
from .drive_tool import fetch_drive_file_oauth
from .skill_loader import load_skill
from .tools import make_tools
from .recall import build_recall_callback

load_dotenv()

# Override GOOGLE_CLOUD_LOCATION for model calls (Dockerfile sets us-central1 for Agent Runtime,
# but gemini-3.5-flash requires the global endpoint)
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
os.environ["GOOGLE_CLOUD_LOCATION"] = os.getenv("MODEL_LOCATION", "global")

_DEFAULT_SKILL_DIR = Path(os.getenv("SKILL_DIR", str(Path(__file__).parent.parent / "skill")))


def _has_files(directory: Path) -> bool:
    return directory.is_dir() and any(p.is_file() for p in directory.rglob("*"))


def build_agent(skill_dir: Path = _DEFAULT_SKILL_DIR) -> LlmAgent:
    skill_body, metadata = load_skill(skill_dir)
    timeout = int(os.getenv("SCRIPT_TIMEOUT_SECONDS", "60"))
    start_job, check_job, read_asset, start_async_validation = make_tools(skill_dir, timeout=timeout)

    # LlmAgent requires a valid Python identifier as name; sanitize all non-identifier chars
    raw_name = metadata["name"]
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", raw_name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    agent_name = sanitized

    # Only register/advertise a tool if the skill actually bundles content for
    # it. Mentioning a tool the skill has nothing to back (e.g. no scripts/)
    # invites the LLM to hallucinate a plausible-sounding filename to call.
    has_scripts = any((skill_dir / "scripts").glob("*.py"))
    has_assets = _has_files(skill_dir / "assets") or _has_files(skill_dir / "references")

    tools = []
    tool_lines = []
    if has_scripts:
        tools.append(start_job)
        tools.append(check_job)
        tool_lines.append(
            "- start_job: launch a Python script bundled with this skill in the "
            "background; returns a job_id immediately without waiting for it to finish"
        )
        tool_lines.append(
            "- check_job: poll a job_id from start_job for its status or result"
        )
        tools.append(start_async_validation)
        tool_lines.append(
            "- start_async_validation: kick off background criteria extraction + "
            "checklist build for large PDFs; returns a job_id immediately. Do NOT "
            "poll it — tell the user it's processing and end your turn. The result "
            "is surfaced automatically when they next message."
        )
    if has_assets:
        tools.append(read_asset)
        tool_lines.append("- read_asset: read a reference or asset file bundled with this skill")

    # Only registered when an OAuth client is actually configured for this
    # deployment — without it, the tool can't request user consent at all, and
    # advertising it would just invite the model to call a tool that can never
    # succeed. fetch_drive_file.py (service-account/ADC) remains the fallback.
    has_oauth_drive = bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID"))
    if has_oauth_drive:
        tools.append(fetch_drive_file_oauth)
        tool_lines.append(
            "- fetch_drive_file_oauth: fetch a Google Drive file/folder using the "
            "signed-in user's own Drive permissions (no service-account sharing "
            "needed) — prefer this over scripts/fetch_drive_file.py whenever it's "
            "available"
        )

    full_instruction = skill_body
    if tool_lines:
        full_instruction += (
            "\n---\nYou have access to the following tools when the skill"
            " instructions require them:\n" + "\n".join(tool_lines) + "\n"
        )

    def build_instruction(ctx):
        # ctx is None in unit tests that call agent.instruction(None) directly,
        # and ctx.session may be absent in other synthetic contexts — both are
        # fine to ignore, since the IDs are only needed for GCS state scripts,
        # which the skill instructs the model to skip when it has no IDs to pass.
        session = getattr(ctx, "session", None)
        if ctx is None or session is None:
            return full_instruction
        return full_instruction + (
            "\n---\nYour current session ID is: "
            f"{session.id}\nYour current user ID is: {ctx.user_id}\n"
            "Pass these as --session-id and --user-id to gcs_state.py when the "
            "skill instructions call for persisting or restoring state.\n"
        )

    import importlib.util as _ilu

    _js_path = skill_dir / "scripts" / "job_store.py"
    recall_callback = None
    if _js_path.exists():
        _spec = _ilu.spec_from_file_location("job_store", _js_path)
        _job_store = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_job_store)
        recall_callback = build_recall_callback(_job_store, start_async_validation)

    return LlmAgent(
        name=agent_name,
        before_agent_callback=recall_callback,
        # ADK does not retry 429 (RESOURCE_EXHAUSTED) by default — passing a
        # bare model-name string gets a Gemini wrapper with retry_options=None,
        # so a single rate-limit response fails the whole turn instead of
        # backing off and trying again. A long compliance-review conversation
        # makes enough model calls that hitting a transient 429 is expected,
        # not exceptional.
        model=Gemini(
            model=os.getenv("MODEL", "gemini-3.5-flash"),
            retry_options=types.HttpRetryOptions(
                attempts=5, initial_delay=1, max_delay=30, exp_base=2, jitter=1,
                http_status_codes=[429, 500, 502, 503, 504],
            ),
        ),
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=getattr(
                    types.ThinkingLevel,
                    os.getenv("THINKING_LEVEL", "MEDIUM").upper(),
                    types.ThinkingLevel.MEDIUM,
                ),
            ),
        ),
        # Passed as a callable (InstructionProvider) so ADK treats it as raw
        # text and skips {var} session-state templating — skill authors may
        # write literal curly braces (e.g. example placeholders) in SKILL.md.
        instruction=build_instruction,
        tools=tools,
    )


# Only instantiate if skill/ exists (Task 6 will create it)
if _DEFAULT_SKILL_DIR.exists():
    root_agent = build_agent()
