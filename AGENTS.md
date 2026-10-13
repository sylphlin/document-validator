# Document Validator — Workspace & Development Rules (AGENTS.md)

This file defines the authoritative rules for AI Coding Agents (Google Antigravity / Jetski, Claude Code, Codex, etc.) working in this repository. It covers both **Client Execution Invariants** (when running document compliance validation for end users) and **Repository Engineering Standards** (when developing, maintaining, or extending this project).

---

## Part I: Operational Invariants (When Executing Document Validation Tasks)

1. **Strict Toolset Execution Only (No Ad-Hoc Scripts)**:
   - Execute all PDF page-to-Markdown extraction and Google Drive fetching exclusively via the official scripts in `skills/document-validator/scripts/` (`extract_pdf_text.py` and `fetch_drive_file.py`).
   - Writing temporary Python scripts for PDF parsing or Drive downloading is **STRICTLY FORBIDDEN**.
2. **Direct CLI Invocation from `<PLUGIN_ROOT>` (Local Plugin Mode)**:
   - Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/document-validator/SKILL.md` (`../../`, e.g., `~/.gemini/config/plugins/document-validator`).
   - In local IDE/CLI environments without ADK `start_job`/`check_job` tools, run `python3 skills/document-validator/scripts/extract_pdf_text.py` and `python3 skills/document-validator/scripts/fetch_drive_file.py` directly from `<PLUGIN_ROOT>`.
3. **Fail-Fast on External Infrastructure & Auth Errors**:
   - Whenever encountering external authentication (`401`, `RefreshError`), Google Drive permission denials (`403`, `404`), or cloud storage errors, stop immediately, report the exact error and exit status, and instruct the user to run `./setup.sh` or `gcloud auth application-default login`.
   - Never fabricate or infer document contents from URLs or filenames.
4. **Evidence Integrity & Phase 1 Checkpoint**:
   - Always present the extracted Criteria Checklist at the end of Phase 1 and wait for user confirmation before proceeding to Phase 2 scoring.
   - Never assign Compliant/Partial/Weak/Missing scores to unread visual, scanned, or timed-out pages; mark them `Indeterminate` (🔍) and route them to the Manual Review queue.

---

## Part II: Repository Development & Engineering Standards (When Developing This Project)

When modifying code, skill instructions, infrastructure scripts, or documentation in this repository, you MUST adhere to the following engineering standards:

### 1. Single Source of Truth (SSOT) Directory Architecture (Agent Plugins 1.0 Specification)
- **Canonical Skill Location**: All core skill instructions (`SKILL.md`) and portable scripts (`scripts/*.py`) physically reside inside `skills/document-validator/` in compliance with the [Agent Plugins 1.0 Specification](https://agent-plugins.org/specification) (§4.2 & §7.1). Do not create root-level symlinks.
- **Downstream Enterprise Mirror**: `gemini-enterprise/skill/` is a downstream mirror required for packaging and deploying the Gemini Enterprise ADK agent (`gemini-enterprise/agent/`). Always edit files under `skills/document-validator/` first, then synchronize changes to `gemini-enterprise/skill/` and verify with `python3 tests/check_core_sync.py`.

### 2. Universal Multi-Language Support
- **Dynamic Response Language**: The skill responds in the language the user is writing in (e.g., Traditional Chinese `zh-TW`, Simplified Chinese `zh-CN`, English, Japanese, Korean) regardless of the language of the analyzed documents, while preserving system tracking identifiers (`REQ-{ID}`, `C-{N}`, `P-{N}`) untranslated.

### 3. Architectural & Model Invariants
- **Designated Models via `.env`**: Load models dynamically via `MODEL` (`gemini-3.8-flash`) from `.env` / `.env.example`. Never hardcode non-designated or invented model IDs.
- **Preserve Gemini Enterprise Runtime Feasibility**: Keep the battle-tested `gemini-enterprise/agent/` ADK wrapper (`LlmAgent`, `start_job`/`check_job`, `start_async_validation` + `recall.py` session recall, and `drive_tool.py` per-user OAuth 2.0 flow) intact.
- **Mandatory Test & Sync Gate**: Run the drift guard and complete unit test suite before committing any change:
  ```bash
  python3 tests/check_core_sync.py
  pytest -v
  ```

### 4. Antigravity Plugin Architecture, 5-Language Parity, & Commit Policy
- **Plugin & README Synchronization**: Keep [plugin.json](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/plugin.json), [rules/AGENTS.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/rules/AGENTS.md), [skills/document-validator/SKILL.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/skills/document-validator/SKILL.md), and all 5 language READMEs ([README.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/README.md), [README.zh-TW.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/README.zh-TW.md), [README.zh-CN.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/README.zh-CN.md), [README.ja.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/README.ja.md), [README.ko.md](file:///usr/local/google/home/sylph/Documents/Antigravity/document-validator/README.ko.md)) synchronized at all times.
- **Human Authorship Only**: Author all commits as `sylphlin <sylph.lin@gmail.com>` with zero AI assistant branding or `Co-Authored-By` trailers.
