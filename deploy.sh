#!/usr/bin/env bash
# Usage: ./deploy.sh [project-id] [region]
# Positional args override GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION from
# .env — handy for deploying the same checkout to a different project/region
# without editing .env. Omit either to fall back to .env (or, for region, the
# us-central1 default).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

if [ -n "${1:-}" ]; then
  GOOGLE_CLOUD_PROJECT="$1"
fi
if [ -n "${2:-}" ]; then
  GOOGLE_CLOUD_LOCATION="$2"
fi
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

# Validate required env vars
if [ -z "${GOOGLE_CLOUD_PROJECT:-}" ]; then
  echo "Error: GOOGLE_CLOUD_PROJECT is not set. Pass it as the first argument (./deploy.sh <project-id>) or copy .env.example to .env and fill in your values." >&2
  exit 1
fi
if [ -z "${STAGING_BUCKET:-}" ]; then
  echo "Error: STAGING_BUCKET is not set (e.g. gs://your-project-id-agent-staging)." >&2
  exit 1
fi

echo "============================================================"
echo "1. Setting up local environment"
echo "============================================================"
if [ ! -d .venv ]; then
  echo "   - Creating virtual environment..."
  python3 -m venv .venv
fi
echo "   - Installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install "google-adk[gcp]" -r requirements.txt -q

# Build minimal staging directory with a valid Python identifier name
# (ADK uses the directory basename as agent name — dots are not allowed)
STAGING_BASE="/tmp/agent_deploy_$$"
mkdir -p "${STAGING_BASE}"

cp -r agent/ "${STAGING_BASE}/"
cp -r skill/ "${STAGING_BASE}/"
cp requirements.txt "${STAGING_BASE}/"

# .env is the single source of truth — forward it as-is to the runtime,
# except deploy.sh's own bookkeeping (AGENT_ENGINE_ID, AGENT_CPU/MEMORY are
# already consumed above; not meant for the running agent), GOOGLE_CLOUD_PROJECT/
# GOOGLE_CLOUD_LOCATION (re-added below with the resolved values, so a project/
# region override from positional args actually reaches the deployed agent),
# and any leftover empty-value line, which the Agent Platform API rejects
# outright ("Required field is not set").
if [ -f .env ]; then
  grep -vE '^(AGENT_ENGINE_ID|AGENT_CPU|AGENT_MEMORY|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION)=' .env \
    | grep -vE '^[A-Za-z_][A-Za-z0-9_]*=[[:space:]]*$' \
    > "${STAGING_BASE}/.env"
fi
{
  echo "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}"
  echo "GOOGLE_CLOUD_LOCATION=${REGION}"
} >> "${STAGING_BASE}/.env"

# Container resource_limits are derived from .env (AGENT_CPU / AGENT_MEMORY)
# rather than a separate static file — keeps all tunables in one place.
cat > "${STAGING_BASE}/.agent_engine_config.json" <<EOF
{
  "resource_limits": {
    "cpu": "${AGENT_CPU:-4}",
    "memory": "${AGENT_MEMORY:-8Gi}"
  }
}
EOF

DEPLOY_LOG="$(mktemp)"
trap "rm -rf ${STAGING_BASE} ${DEPLOY_LOG}" EXIT

AGENT_NAME=$(grep '^name:' skill/SKILL.md | head -1 | sed 's/name: *//')

# Reuse the existing Agent Engine instance if AGENT_ENGINE_ID is set in .env,
# so this becomes a new revision of the same service instead of a brand new
# one. Clear AGENT_ENGINE_ID in .env to force a fresh instance.
AGENT_ENGINE_ID_ARGS=()
if [ -n "${AGENT_ENGINE_ID:-}" ]; then
  AGENT_ENGINE_ID_ARGS=(--agent_engine_id="${AGENT_ENGINE_ID}")
  echo "   - Updating existing instance: ${AGENT_ENGINE_ID}"
fi

echo ""
echo "============================================================"
echo "2. Deploying '${AGENT_NAME}' to Google Cloud Agent Runtime"
echo "============================================================"
echo "   - Project: ${GOOGLE_CLOUD_PROJECT}"
echo "   - Region:  ${REGION}"
echo ""

"${SCRIPT_DIR}/.venv/bin/adk" deploy agent_engine \
  --project="${GOOGLE_CLOUD_PROJECT}" \
  --region="${REGION}" \
  --display_name="${AGENT_NAME}" \
  --artifact_service_uri="${STAGING_BUCKET}" \
  "${AGENT_ENGINE_ID_ARGS[@]}" \
  "${STAGING_BASE}" 2>&1 | tee "${DEPLOY_LOG}"

REASONING_ENGINE_ID=$(grep -oE 'projects/[0-9]+/locations/[a-z0-9-]+/reasoningEngines/[0-9]+' "${DEPLOY_LOG}" | tail -1)

# Persist the instance ID back into .env so the next deploy updates this same
# instance instead of creating a new one.
if [ -n "${REASONING_ENGINE_ID}" ] && [ -f .env ]; then
  NEW_ID="${REASONING_ENGINE_ID##*/}"
  if grep -q '^AGENT_ENGINE_ID=' .env; then
    awk -v id="${NEW_ID}" '/^AGENT_ENGINE_ID=/{print "AGENT_ENGINE_ID="id; next} {print}' .env > .env.tmp && mv .env.tmp .env
  else
    echo "AGENT_ENGINE_ID=${NEW_ID}" >> .env
  fi
fi

echo ""
echo "============================================================"
echo "3. Connect to Gemini Enterprise Admin Console"
echo "============================================================"
echo "   - Log in to your Gemini Enterprise Admin Console."
echo "   - Navigate to 'Agents' in the left sidebar."
echo "   - Click '+ Add Agent' and select 'Custom agent via Agent Engine'."
echo "   - Enter the following Reasoning Engine Resource ID (Copy & Paste):"
echo "     👉 ${REASONING_ENGINE_ID:-projects/$GOOGLE_CLOUD_PROJECT/locations/$REGION/reasoningEngines/...}"
echo ""
