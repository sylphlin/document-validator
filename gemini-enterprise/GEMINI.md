# Coding Agent Guide — Document Validator (Gemini Enterprise)

## Prerequisites

Install the CLI (one-time):
```bash
uv tool install google-agents-cli
```

---

## Development & Evaluation Workflow

### 1. Architecture & SSOT Invariant
- `gemini-enterprise/skill/` is a downstream mirror of `../skills/document-validator/` (the canonical Single Source of Truth).
- Always modify `../skills/document-validator/` first, synchronize to `gemini-enterprise/skill/`, and run `python3 ../tests/check_core_sync.py`.

### 2. Local Interactive Testing
Run the agent locally using the ADK playground:
```bash
agents-cli playground
```

### 3. Evaluation Loop (`agents-cli eval`)
Run evaluations from the `gemini-enterprise/` directory:
```bash
agents-cli eval generate \
  --eval-dataset-file-path tests/eval/datasets/basic-dataset.json \
  --output-dir artifacts/eval-traces

agents-cli eval grade \
  --eval-traces-path artifacts/eval-traces \
  --config-file-path tests/eval/eval_config.yaml \
  --output-dir artifacts/eval-results
```

### 4. Pre-Deployment Tests
From the repository root (`..`):
```bash
python3 tests/check_core_sync.py
pytest -v
```

### 5. Deploy & Publish to Gemini Enterprise
Deploy using the unified deployment script (which preserves the ADK Agent Engine multi-directory staging, CPU/Memory limits, and automatic registration via `agents-cli publish gemini-enterprise`):
```bash
./deploy.sh --project YOUR_PROJECT_ID --region us-central1
```
