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

PROJECT_ID="${POSITIONAL[0]:?Usage: bash deploy.sh <PROJECT_ID> [REGION] [--ge APP_ID]}"
REGION="${POSITIONAL[1]:-us-central1}"
SA_NAME="document-validator-runtime"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER=$(gcloud config get-value account 2>/dev/null)

echo "Deploying Document Conformance Validator to project: $PROJECT_ID (region: $REGION)"
[ -n "$GE_APP_ID" ] && echo "  + Gemini Enterprise registration (APP_ID: $GE_APP_ID)"

gcloud config set project "$PROJECT_ID"

gcloud services enable iam.googleapis.com --project="$PROJECT_ID" --quiet

if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Document Conformance Validator runtime" --project="$PROJECT_ID"
fi

for role in roles/aiplatform.user roles/serviceusage.serviceUsageConsumer roles/logging.logWriter roles/cloudtrace.agent; do
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

# Bucket-scoped storage.objectAdmin for GCS session bucket
SESSION_BUCKET="document-validator-sessions-${PROJECT_ID}"
gcloud storage buckets add-iam-policy-binding "gs://${SESSION_BUCKET}" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin 2>/dev/null || true

# Setup GCP resources
if [ -f setup.sh ]; then
    bash setup.sh "$PROJECT_ID" "$REGION"
fi

# Load .env if present for optional overrides
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-agent-staging}"
GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-}"
GOOGLE_OAUTH_CLIENT_SECRET="${GOOGLE_OAUTH_CLIENT_SECRET:-}"
PDF_EXTRACT_WORKERS="${PDF_EXTRACT_WORKERS:-4}"
PDF_PAGE_TIMEOUT_SECONDS="${PDF_PAGE_TIMEOUT_SECONDS:-30}"
MODEL_LOCATION="${MODEL_LOCATION:-global}"
MODEL="${MODEL:-gemini-3.5-flash}"
THINKING_LEVEL="${THINKING_LEVEL:-MEDIUM}"
SCRIPT_TIMEOUT_SECONDS="${SCRIPT_TIMEOUT_SECONDS:-300}"
AGENT_CPU="${AGENT_CPU:-8}"
AGENT_MEMORY="${AGENT_MEMORY:-32Gi}"
AGENT_WORKERS="${AGENT_WORKERS:-4}"

# Install & deploy
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv &>/dev/null; then
    echo "uv not found, attempting python3 -m pip install --user uv..."
    python3 -m pip install --user uv -q 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
fi

if command -v uv &>/dev/null; then
    uv venv --allow-existing .venv -q 2>/dev/null || { rm -rf .venv && uv venv .venv -q; }
    export PATH="$(pwd)/.venv/bin:$PATH"
    uv sync
    uv pip install google-agents-cli --python .venv/bin/python
else
    echo "Falling back to python3 -m venv..."
    rm -rf .venv
    python3 -m venv .venv
    export PATH="$(pwd)/.venv/bin:$PATH"
    pip install --upgrade pip -q
    pip install google-agents-cli -r requirements.txt -q
fi

UPDATE_ENV_LIST=()
for kv in \
  "STAGING_BUCKET=${STAGING_BUCKET}" \
  "GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}" \
  "GOOGLE_OAUTH_CLIENT_SECRET=${GOOGLE_OAUTH_CLIENT_SECRET}" \
  "PDF_EXTRACT_WORKERS=${PDF_EXTRACT_WORKERS}" \
  "PDF_PAGE_TIMEOUT_SECONDS=${PDF_PAGE_TIMEOUT_SECONDS}" \
  "MODEL_LOCATION=${MODEL_LOCATION}" \
  "MODEL=${MODEL}" \
  "THINKING_LEVEL=${THINKING_LEVEL}" \
  "SCRIPT_TIMEOUT_SECONDS=${SCRIPT_TIMEOUT_SECONDS}"
do
  val="${kv#*=}"
  if [ -n "$val" ]; then
    UPDATE_ENV_LIST+=("$kv")
  fi
done

IFS=,
UPDATE_ENV_STR="${UPDATE_ENV_LIST[*]}"
unset IFS

cp -r skill agent/skill 2>/dev/null || true

DEPLOY_OUTPUT=$(GOOGLE_CLOUD_PROJECT="$PROJECT_ID" GOOGLE_CLOUD_LOCATION="$REGION" \
  STAGING_BUCKET="$STAGING_BUCKET" \
  GOOGLE_OAUTH_CLIENT_ID="$GOOGLE_OAUTH_CLIENT_ID" \
  GOOGLE_OAUTH_CLIENT_SECRET="$GOOGLE_OAUTH_CLIENT_SECRET" \
  PDF_EXTRACT_WORKERS="$PDF_EXTRACT_WORKERS" \
  PDF_PAGE_TIMEOUT_SECONDS="$PDF_PAGE_TIMEOUT_SECONDS" \
  MODEL_LOCATION="$MODEL_LOCATION" \
  MODEL="$MODEL" \
  THINKING_LEVEL="$THINKING_LEVEL" \
  SCRIPT_TIMEOUT_SECONDS="$SCRIPT_TIMEOUT_SECONDS" \
  .venv/bin/agents-cli deploy --project "$PROJECT_ID" --region "$REGION" \
  --service-account "$SA_EMAIL" \
  --cpu "$AGENT_CPU" \
  --memory "$AGENT_MEMORY" \
  --num-workers "$AGENT_WORKERS" \
  --update-env-vars "$UPDATE_ENV_STR" \
  2>&1 | tee /dev/stderr) || true

rm -rf agent/skill 2>/dev/null || true

REASONING_ENGINE_ID=$(echo "$DEPLOY_OUTPUT" | grep -oP 'reasoningEngines/\K\d+' 2>/dev/null || echo "$DEPLOY_OUTPUT" | grep -oE 'reasoningEngines/[0-9]+' | cut -d/ -f2 | tail -1)
echo "Agent Engine deployment complete!"
[ -n "$REASONING_ENGINE_ID" ] && echo "  Reasoning Engine ID: $REASONING_ENGINE_ID"

# GE registration
if [ -n "$GE_APP_ID" ] && [ -n "$REASONING_ENGINE_ID" ]; then
    PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
    ACCESS_TOKEN=$(gcloud auth print-access-token)

    AGENT_NAME="$(basename $(pwd))"
    if command -v python3 &>/dev/null && [ -f agent.yaml ]; then
        DISPLAY_NAME=$(python3 -c '
import yaml
d = yaml.safe_load(open("agent.yaml"))
dn = d.get("displayName", {})
print(dn.get("en", dn) if isinstance(dn, dict) else dn)
' 2>/dev/null || echo "$AGENT_NAME")
        AGENT_DESC=$(python3 -c '
import yaml
d = yaml.safe_load(open("agent.yaml"))
desc = d.get("description", {})
print(desc.get("en", desc) if isinstance(desc, dict) else desc)
' 2>/dev/null || echo "$DISPLAY_NAME")
    else
        DISPLAY_NAME="$AGENT_NAME"
        AGENT_DESC="$AGENT_NAME"
    fi

    REGISTER_RESPONSE=$(curl -s -X POST \
      "https://discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUM}/locations/global/collections/default_collection/engines/${GE_APP_ID}/assistants/default_assistant/agents" \
      -H "Authorization: Bearer ${ACCESS_TOKEN}" \
      -H "Content-Type: application/json" \
      -H "X-Goog-User-Project: ${PROJECT_NUM}" \
      -d "{
        \"displayName\": \"${DISPLAY_NAME}\",
        \"description\": \"${AGENT_DESC}\",
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
