---
name: portal-ready
description: Prepare an ADK agent project for Agent Portal. Analyzes code, generates agent.yaml + deploy.sh, adapts code for Agent Engine (GeminiWithLocation, env vars, custom SA), and runs agents-cli scaffold enhance.
disable-model-invocation: true
allowed-tools: Bash(grep *) Bash(find *) Bash(cat *) Bash(ls *) Bash(uvx *)
---

# Prepare Agent for Portal

You are helping a developer prepare their ADK agent project for inclusion in the Agent Portal. Follow these steps in order. At each step, present your findings and ask the developer to confirm before proceeding.

---

## Step 1: Analyze the project

Read the project's README.md, pyproject.toml, and agent code to understand:
- What the agent does
- What industry, persona, and use case it fits (the portal's three-dimensional tags)
- What tools and capabilities it has
- What the agent directory name is (the directory containing agent.py with root_agent)

If you cannot find an agent.py with a `root_agent` definition, stop and tell the developer this is required.

---

## Step 2: Generate agent.yaml

```yaml
name: <kebab-case name>
displayName:
  zh: <中文显示名>
  en: <English display name>
description:
  zh: <中文描述，2-3句>
  en: <English description, 2-3 sentences>
```

> agent.yaml is a slim manifest. The catalog tags (industry / persona / use case),
> media, and the User Guide are entered in the portal's **Contribute** form and
> stored in Firestore — do not put them in agent.yaml.

Present to the developer for confirmation before writing.

---

## Step 3: Scan dependencies

Scan ALL Python files for three categories of dependencies.

### 3.1 GCP Resources

| Pattern | Service |
|---|---|
| `google.cloud.storage` / `gcs` | Cloud Storage |
| `google.cloud.bigquery` / `bigquery` | BigQuery |
| `google.cloud.firestore` / `firestore` | Firestore |
| `google.cloud.secretmanager` / `secret` | Secret Manager |
| `google.cloud.tasks` | Cloud Tasks |
| `google.cloud.spanner` | Spanner |
| `google.cloud.pubsub` | Pub/Sub |
| `google.cloud.discoveryengine` | Vertex AI Search |
| `google.cloud.alloydb` / `psycopg2` | AlloyDB |

Also check `.env.example`, `.env`, config files, seed data directories.

### 3.2 Dependent Services

Services that must be deployed **before** the agent:

| Pattern | Type |
|---|---|
| `mcp_server/` directory with `deploy.sh` or `Dockerfile` | MCP Server (Cloud Run) |
| `a2a_server/` directory or `to_a2a` / `a2a_sdk` imports | A2A Service |

If detected → deploy.sh will use **multi-stage mode** (Template B).

### 3.3 Environment Variables

Scan for `os.environ[...]` and `os.environ.get(...)`. Collect all required env vars.

**Critical rules:**
- Local env vars are **NOT** automatically forwarded to Agent Engine — must use `--update-env-vars`
- Names starting with `GOOGLE_CLOUD_AGENT_ENGINE` are **reserved** — must be renamed in Step 7

### 3.4 Model Location

Check `model=` in Agent() or Gemini() calls:
- `gemini-3.5-flash`, `gemini-3-flash-preview` → only available at `location=global`
- If Agent Engine runs in `us-central1` but model needs `global` → needs **GeminiWithLocation** (Step 7)

### 3.5 Needs Custom SA?

The agent needs a custom Service Account if ANY of these are true:
- Uses `generate_signed_url` / signBlob (presigned URLs)
- Uses Cloud Storage (read/write objects)
- Invokes a Cloud Run service (MCP Server)
- Uses Cloud Tasks

If none apply, the default platform-managed SA is sufficient.

### Present findings

Organize as:
1. **GCP Resources** — handled by setup.sh
2. **Dependent Services** — handled by deploy.sh multi-stage
3. **External APIs** — user must provide
4. **Environment variables** — list for `--update-env-vars`
5. **Reserved env var names** — must rename
6. **Model location** — needs GeminiWithLocation or not
7. **Custom SA** — needed or not, and why

Ask: "Are there any services or data dependencies I missed?"

---

## Step 4: Check or generate setup.sh

### If setup.sh exists — validate it

| Check | What to look for |
|---|---|
| PROJECT_ID parameterized | Accepts as argument, no hardcoded project IDs |
| No sensitive info | No API keys, passwords, or secrets |
| Dependency consistency | All GCP services from Step 3 have setup commands |
| API enablement | Has `gcloud services enable` for each required API |
| IAM roles | Sets up necessary roles |
| Cleanup support | `--cleanup` flag |
| Error handling | `set -e` |
| Idempotent | Handles already-existing resources gracefully |
| Cost warning | Mentions paid resources |

### If setup.sh does not exist

Generate one if GCP resources were detected in Step 3. Skip if none.

### Dependent service scripts

If dependent services were detected, verify their scripts exist:

| Type | Required | Verify |
|---|---|---|
| MCP Server | `mcp_server/deploy.sh` + `Dockerfile` or `cloudbuild.yaml` | Accepts `--project` and `--region` |
| A2A Service | `a2a_server/deploy.sh` | Accepts `--project` and `--region` |

If missing, tell the developer to create it. The skill does NOT generate dependent service scripts.

---

## Step 5: Configure pyproject.toml

Check if pyproject.toml contains `[tool.agents-cli]`. Add if missing:

```toml
[tool.agents-cli]
agent_directory = "<detected_agent_directory>"

[tool.agents-cli.create_params]
deployment_target = "agent_runtime"
```

`[tool.agent-starter-pack]` is **not required** — only `[tool.agents-cli]` is needed.

---

## Step 6: Run agents-cli scaffold enhance

```bash
uvx google-agents-cli scaffold enhance --agent-directory <agent_dir> --deployment-target agent_runtime -s --yes
```

This generates `agent_runtime_app.py` and other deployment files. Show the output to the developer.

---

## Step 7: Adapt code for Agent Engine

Apply these changes **after** Step 6, since enhance may generate files that need modification.

### 7.1 Check agent_runtime_app.py

agents-cli expects `agent_runtime` attribute (not `app`):

```python
# Verify this file exports: agent_runtime
# Example: from .app import app as agent_runtime
```

If enhance generated it correctly, no change needed. If it exports `app` instead of `agent_runtime`, fix it.

### 7.2 GeminiWithLocation (if needed per Step 3.4)

Create `<agent_dir>/llm.py`:

```python
from functools import cached_property

from google import genai
from google.adk.models.google_llm import Gemini
from pydantic import Field


class GeminiWithLocation(Gemini):
    """Subclass of Gemini to ensure location is passed to the internal Client."""

    location: str = Field(default="global", description="Vertex AI location")

    @cached_property
    def api_client(self) -> genai.Client:
        return genai.Client(
            location=self.location,
            http_options=genai.types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
                base_url=self.base_url,
            ),
        )
```

Update agent.py:
```python
from google.adk.models.google_llm import Gemini  # not from google.adk.models
from .llm import GeminiWithLocation

# Change: model=Gemini(model="gemini-3.5-flash", ...)
# To:     model=GeminiWithLocation(model="gemini-3.5-flash", location="global", ...)
```

### 7.3 Rename reserved env vars (if needed per Step 3.3)

```python
# GOOGLE_CLOUD_AGENT_ENGINE_* is reserved
# Change: os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
# To:     os.environ.get("AGENT_ENGINE_ID")
```

Update all references in code AND deploy.sh env vars.

Present all proposed code changes. Confirm before applying.

---

## Step 8: Generate deploy.sh

Choose template based on Step 3 findings:
- **No dependent services** → Template A
- **Has dependent services** → Template B

### Common elements (both templates)

Both templates include:
- `--ge APP_ID` support for Gemini Enterprise registration
- `--update-env-vars` to forward all env vars from Step 3.3 to Agent Engine
- `|| true` after agents-cli deploy (workaround for agents-cli v0.2.0 metadata bug)

If **custom SA needed** (per Step 3.5), also include:
- Create SA `{agent-name}-runtime`
- Project roles: `aiplatform.user`, `serviceusage.serviceUsageConsumer`, `logging.logWriter`
- Self-signBlob: `serviceAccountTokenCreator` on itself
- Deployer impersonation: `iam.serviceAccountUser`
- `--service-account` flag for agents-cli deploy
- Bucket-scoped `storage.objectAdmin` (if GCS used)
- `run.invoker` on Cloud Run service (if MCP Server)

If **custom SA not needed**, use simpler deploy without SA creation, no `--service-account` flag.

### Template A: Standard deploy.sh

```bash
#!/bin/bash
set -e

GE_APP_ID=""
POSITIONAL=()
for arg in "$@"; do
  case $arg in
    --ge=*) GE_APP_ID="${arg#*=}" ;;
    --ge) GE_APP_ID="__NEXT__" ;;
    *)
      if [ "$GE_APP_ID" = "__NEXT__" ]; then
        GE_APP_ID="$arg"
      else
        POSITIONAL+=("$arg")
      fi
      ;;
  esac
done

if [ -n "$GE_APP_ID" ]; then
  GE_APP_ID="${GE_APP_ID%/}"
fi

PROJECT_ID="${POSITIONAL[0]:?Usage: bash deploy.sh <PROJECT_ID> [REGION] [--ge APP_ID]}"
REGION="${POSITIONAL[1]:-us-central1}"
# >>> If custom SA needed, add these lines:
SA_NAME="<AGENT_NAME>-runtime"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER=$(gcloud config get-value account 2>/dev/null)
# <<<

echo "Deploying <AGENT_DISPLAY_NAME> to project: $PROJECT_ID (region: $REGION)"
[ -n "$GE_APP_ID" ] && echo "  + Gemini Enterprise registration (APP_ID: $GE_APP_ID)"

gcloud config set project "$PROJECT_ID"

# >>> If custom SA needed, add SA creation + IAM block:
gcloud services enable iam.googleapis.com --project="$PROJECT_ID" --quiet

if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    gcloud iam service-accounts create "$SA_NAME" \
        --display-name="<AGENT_DISPLAY_NAME> runtime" --project="$PROJECT_ID"
fi

for role in roles/aiplatform.user roles/serviceusage.serviceUsageConsumer roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SA_EMAIL}" --role="$role" --condition=None --quiet 2>/dev/null || true
done

gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/iam.serviceAccountTokenCreator \
    --project="$PROJECT_ID" 2>/dev/null || true

if [ -n "$DEPLOYER" ]; then
    gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
        --member="user:${DEPLOYER}" --role=roles/iam.serviceAccountUser \
        --project="$PROJECT_ID" 2>/dev/null || true
fi
# <<<

# Setup GCP resources
if [ -f setup.sh ]; then
    bash setup.sh "$PROJECT_ID" "$REGION"
fi

# Install & deploy
uv sync
uv pip install google-agents-cli --python .venv/bin/python

DEPLOY_OUTPUT=$(GOOGLE_CLOUD_PROJECT="$PROJECT_ID" GOOGLE_CLOUD_LOCATION="$REGION" \
  <ENV_VARS> \
  .venv/bin/agents-cli deploy --project "$PROJECT_ID" --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --update-env-vars "<UPDATE_ENV_VARS>" \
  2>&1 | tee /dev/stderr) || true
# >>> If no custom SA, remove --service-account line <<<

REASONING_ENGINE_ID=$(echo "$DEPLOY_OUTPUT" | grep -oP 'reasoningEngines/\K\d+' | tail -1)
echo "Agent Engine deployment complete!"
[ -n "$REASONING_ENGINE_ID" ] && echo "  Reasoning Engine ID: $REASONING_ENGINE_ID"

# GE registration
if [ -n "$GE_APP_ID" ] && [ -n "$REASONING_ENGINE_ID" ]; then
    PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
    ACCESS_TOKEN=$(gcloud auth print-access-token)

    AGENT_NAME="$(basename $(pwd))"
    if command -v python3 &>/dev/null && [ -f agent.yaml ]; then
        DISPLAY_NAME=$(python3 -c "
import yaml
d = yaml.safe_load(open('agent.yaml'))
dn = d.get('displayName', {})
print(dn.get('en', dn) if isinstance(dn, dict) else dn)
" 2>/dev/null || echo "$AGENT_NAME")
        AGENT_DESC=$(python3 -c "
import yaml
d = yaml.safe_load(open('agent.yaml'))
desc = d.get('description', {})
print(desc.get('en', desc) if isinstance(desc, dict) else desc)
" 2>/dev/null || echo "$DISPLAY_NAME")
    else
        DISPLAY_NAME="$AGENT_NAME"
        AGENT_DESC="$AGENT_NAME"
    fi

    if [[ "$GE_APP_ID" == */reasoningEngines/* ]]; then
        DETECTED_GE_APP=$(.venv/bin/agents-cli publish gemini-enterprise --list --project "$PROJECT_ID" 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin).get('apps', [{}])[0].get('name', ''))" 2>/dev/null || true)
        if [ -n "$DETECTED_GE_APP" ]; then
            GE_APP_ID="$DETECTED_GE_APP"
        fi
    fi

    if [[ "$GE_APP_ID" == projects/* ]]; then
        GE_API_URL="https://discoveryengine.googleapis.com/v1alpha/${GE_APP_ID}/assistants/default_assistant/agents"
    elif [[ "$GE_APP_ID" == collections/* ]]; then
        GE_API_URL="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUM}/locations/global/${GE_APP_ID}/assistants/default_assistant/agents"
    elif [[ "$GE_APP_ID" == engines/* ]]; then
        GE_API_URL="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUM}/locations/global/collections/default_collection/${GE_APP_ID}/assistants/default_assistant/agents"
    else
        GE_API_URL="https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUM}/locations/global/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents"
    fi

    REGISTER_RESPONSE=$(curl -s -X POST \
      "$GE_API_URL" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Content-Type: application/json" \
      -H "X-Goog-User-Project: ${PROJECT_NUM}" \
      -d "{
        \"displayName\": \"${DISPLAY_NAME}\",
        \"description\": \"${AGENT_DESC}\",
        \"icon\": {
          \"uri\": \"https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/smart_toy/default/24px.svg\"
        },
        \"adk_agent_definition\": {
          \"tool_settings\": { \"tool_description\": \"${AGENT_DESC}\" },
          \"provisioned_reasoning_engine\": {
            \"reasoning_engine\": \"projects/${PROJECT_NUM}/locations/${REGION}/reasoningEngines/${REASONING_ENGINE_ID}\"
          }
        }
      }")

    if echo "$REGISTER_RESPONSE" | grep -q '"name"'; then
        echo "Gemini Enterprise registration successful!"
    else
        echo "Gemini Enterprise registration failed:"
        echo "$REGISTER_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$REGISTER_RESPONSE"
    fi
elif [ -n "$GE_APP_ID" ] && [ -z "$REASONING_ENGINE_ID" ]; then
    echo "Skipping GE registration (Agent Engine deploy failed — no Reasoning Engine ID)"
fi

echo "Deployment complete!"
```

### Template B: Multi-stage deploy.sh

Same structure as Template A, but insert dependent service deployment **between** setup.sh and agents-cli deploy:

```bash
# ... (same pre-flight as Template A) ...

# ── Step N: Deploy dependent service ─────────────────────────────────
echo "Deploying <SERVICE_NAME> to Cloud Run..."
bash mcp_server/deploy.sh --project="$PROJECT_ID" --region="$REGION"

MCP_SERVER_URL=$(gcloud run services describe <SERVICE_NAME> \
    --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")

# Grant custom SA permission to invoke MCP Server
gcloud run services add-iam-policy-binding <SERVICE_NAME> \
    --region="$REGION" --project="$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role=roles/run.invoker 2>/dev/null || true

# ... (then agents-cli deploy with MCP_SERVER_URL in --update-env-vars) ...
```

For agents with GCS bucket access, add bucket-scoped IAM instead of project-scoped:
```bash
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin 2>/dev/null || true
```

### Placeholders to replace

| Placeholder | Source |
|---|---|
| `<AGENT_NAME>` | agent.yaml `name` (kebab-case) |
| `<AGENT_DISPLAY_NAME>` | agent.yaml `displayName.en` |
| `<SERVICE_NAME>` | Cloud Run service name from mcp_server/deploy.sh |
| `<ENV_VARS>` | All os.environ vars, as `KEY="$VALUE"` before the command |
| `<UPDATE_ENV_VARS>` | Comma-separated `KEY=${VALUE}` for `--update-env-vars` |

Present the generated deploy.sh to the developer. Confirm before writing.

---

## Step 9: Verify

```bash
ls agent.yaml deploy.sh
ls <agent_dir>/agent.py <agent_dir>/agent_runtime_app.py
grep "root_agent" <agent_dir>/agent.py
grep "agent_runtime" <agent_dir>/agent_runtime_app.py
grep "agents-cli" pyproject.toml
```

Present summary:

```
✓ agent.yaml        — slim manifest (name + bilingual displayName/description)
✓ deploy.sh         — custom SA + --service-account + --update-env-vars + GE
✓ agent.py          — exports root_agent
✓ agent_runtime_app — exports agent_runtime
✓ pyproject.toml    — [tool.agents-cli] configured
✓ setup.sh          — GCP resource setup (if applicable)
✓ llm.py            — GeminiWithLocation (if applicable)

Deployment flow:
  bash deploy.sh <PROJECT_ID>                  # Agent Engine only
  bash deploy.sh <PROJECT_ID> --ge <APP_ID>    # + Gemini Enterprise

Next: submit a PR to https://github.com/olifei/agent-hub
```


