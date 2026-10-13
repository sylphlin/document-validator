# Document Validator - Operational Invariants for AI Clients

When you execute tasks or skills from this plugin, you MUST follow these operational rules:

## 1. Strict Read-Only Execution & Direct CLI Invocation (Do Not Modify Plugin Code)
- All Python scripts (`skills/document-validator/scripts/*.py`) and configuration files are read-only tools.
- Do NOT edit, patch, or rewrite any files in this plugin with file-editing tools or shell commands.
- Do NOT write ad-hoc temporary Python scripts to parse PDFs or fetch Google Drive files; always use the bundled scripts (`extract_pdf_text.py` and `fetch_drive_file.py`).
- Resolve `<PLUGIN_ROOT>` as two directory levels above `skills/document-validator/SKILL.md` (`../../`, e.g., `~/.gemini/config/plugins/document-validator`).
- When running in a local CLI or IDE environment (such as Google Antigravity or Claude Code) where ADK `start_job` / `check_job` tools are not present, invoke `python3 skills/document-validator/scripts/extract_pdf_text.py` and `python3 skills/document-validator/scripts/fetch_drive_file.py` directly from `<PLUGIN_ROOT>`. Skip `gcs_state.py` and `start_async_validation` when running locally without cloud session IDs.

## 2. Fail-Fast on External Access & Authentication Errors
- If `fetch_drive_file.py` or Google Cloud services fail due to missing Application Default Credentials (`401`, `RefreshError`), permission denials (`403 Forbidden`, `404 Not Found`), or missing Python dependencies (`pdfplumber`, `pypdfium2`):
  - Stop immediately.
  - Report the exact error message and exit status to the user.
  - Provide a clear remediation command (e.g., `gcloud auth application-default login --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly"`, sharing the Drive file with the active credential identity, or running `./setup.sh`).
  - Never guess or fabricate document contents from filenames or URLs.

## 3. Mandatory 4-Phase Workflow & User Checkpoint
- Always follow the 4-phase workflow defined in `skills/document-validator/SKILL.md`:
  - **Phase 0 (Intake)**: Inventory all Criteria Documents (`[C-1]`, `[C-2]`, ...) and Pending Documents (`[P-1]`, `[P-2]`, ...), and extract PDF pages in ~20-page chunks.
  - **Phase 1 (Criteria Checklist Extraction & Checkpoint)**: Parse every requirement (`Disqualifying`, `Mandatory`, `Conditional`, `Advisory`) into the structured Criteria Checklist table (`REQ-{ID}`) and **wait for explicit user confirmation** before scoring.
  - **Phase 2 (Document Matching & Scoring)**: Score each requirement with page/section citations; never assign a numeric compliance score to unread visual/scanned content (mark as `Indeterminate` 🔍 and route to Manual Review).
  - **Phase 3 (Report Generation)**: Produce the complete Document Validation Report in the user's conversation language while keeping system identifiers (`REQ-{ID}`, `C-{N}`, `P-{N}`) untranslated.
