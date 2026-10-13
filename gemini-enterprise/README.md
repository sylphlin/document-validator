# Gemini Enterprise Document Validator Agent

**Gemini Enterprise Document Validator Agent** is a cloud document compliance and audit service built with **Google ADK (Agent Development Kit)** and **Gemini 3.8 Flash (`gemini-3.8-flash`)**. It deploys to **Google Cloud Vertex AI Agent Runtime** (Agent Engine) and registers with **Gemini Enterprise**.

---

## Architecture & Overview

This package wraps the canonical [`skills/document-validator`](../skills/document-validator) skill as a managed cloud agent (`LlmAgent`). Key capabilities include:

1. **Skill-Driven ADK Wrapper (`agent/`)**: Dynamically loads `skill/SKILL.md` as the agent's instruction and exposes bundled scripts (`extract_pdf_text.py`, `fetch_drive_file.py`, `gcs_state.py`) via non-blocking background job tools (`start_job` and `check_job`).
2. **Detached Async Validation & Session Recall (`run_async_validation.py` + `recall.py` + `job_store.py`)**: For large criteria PDFs, launches a detached background pipeline that extracts pages in chunks, builds the Criteria Checklist, checkpoints progress to `gs://document-validator-sessions-${GOOGLE_CLOUD_PROJECT}`, and surfaces the completed checklist on the user's next turn via `before_agent_callback`.
3. **Dual Google Drive Access Modes**:
   - **Per-User OAuth 2.0 (`agent/drive_tool.py`)**: Enabled when `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are configured; accesses Google Drive as the signed-in Gemini Enterprise user.
   - **Service Account / ADC (`skill/scripts/fetch_drive_file.py`)**: Default fallback using Application Default Credentials without requiring OAuth client setup.
4. **High-Throughput PDF & CAD Extraction (`extract_pdf_text.py`)**: Converts PDF pages to Markdown with table preservation (`pdfplumber`) and fast vector-dense CAD drawing classification/text recovery (`pypdfium2`) across parallel worker processes (`PDF_EXTRACT_WORKERS`).

---

## Directory Structure

```text
gemini-enterprise/
├── agent/
│   ├── __init__.py               # Exports root_agent for ADK loader
│   ├── agent.py                  # LlmAgent builder (model, retry, thinking, tools, recall callback)
│   ├── drive_tool.py             # Per-user OAuth 2.0 Google Drive tool (fetch_drive_file_oauth)
│   ├── recall.py                 # Session recall callback for detached background jobs
│   ├── skill_loader.py           # SKILL.md YAML frontmatter and body parser
│   └── tools.py                  # start_job, check_job, read_asset, start_async_validation
├── skill/                        # Downstream mirror of ../skills/document-validator/ (verified by tests/check_core_sync.py)
│   ├── SKILL.md
│   └── scripts/
│       ├── extract_pdf_text.py
│       ├── fetch_drive_file.py
│       ├── gcs_state.py
│       ├── job_store.py
│       └── run_async_validation.py
├── tests/
│   └── eval/                     # Behavioral evaluation suite for agents-cli eval
│       ├── eval_config.yaml
│       └── datasets/
│           └── basic-dataset.json
├── agents-cli-manifest.yaml      # agents-cli manifest (agent_directory: agent, target: agent_runtime)
├── deployment_metadata.json      # Tracks deployed Reasoning Engine resource name for agents-cli publish
├── deploy.sh                     # Wrapper forwarding to root ../deploy.sh
├── .env.example                  # Environment configuration template
├── pyproject.toml                # Package and evaluation dependencies
└── requirements.txt              # Runtime dependencies installed in Agent Runtime container
```

---

## Deploy to Vertex AI Agent Runtime

### Prerequisites

1. Authenticate Google Cloud ADC:
   ```bash
   gcloud auth application-default login
   ```
2. *(Optional)* Install `google-agents-cli` for automated Gemini Enterprise registration and behavioral evaluation:
   ```bash
   uv tool install google-agents-cli
   ```

### Run One-Click Deployment (`./deploy.sh`)

Run [`deploy.sh`](../deploy.sh) from the repository root (or `./deploy.sh` inside `gemini-enterprise/`) to provision cloud resources via `setup.sh`, deploy the agent to Vertex AI Agent Runtime, and register with Gemini Enterprise:

```bash
chmod +x setup.sh deploy.sh

# Deploy using .env or active gcloud configuration:
./deploy.sh

# Specify project ID and region explicitly:
./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1

# Preview commands without making cloud changes:
./deploy.sh --dry-run
```

---

## Behavioral Evaluation (`agents-cli eval`)

Run behavioral evaluations against `tests/eval/datasets/basic-dataset.json` from inside `gemini-enterprise/`:

```bash
cd gemini-enterprise
agents-cli eval generate
agents-cli eval grade
```
