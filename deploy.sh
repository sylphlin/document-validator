#!/usr/bin/env bash
# ==============================================================================
# Deploy Document Validator to Vertex AI Agent Runtime for Gemini Enterprise
#
# Usage:
#   ./deploy.sh [OPTIONS]
#   ./deploy.sh [PROJECT_ID] [REGION]   (legacy positional syntax supported)
#
# Options:
#   -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
#   -r, --region REGION          Google Cloud Region (default: us-central1)
#   -b, --bucket BUCKET_NAME     Custom staging GCS bucket name
#   -s, --service-account SA     Custom service account email
#       --ge APP_ID              Gemini Enterprise App ID or full resource name
#       --ge-location LOCATION   Gemini Enterprise location (default: global)
#       --skip-ge                Skip linking agent to Gemini Enterprise
#   -n, --dry-run                Preview deployment commands without executing
#   -h, --help                   Show this help message and exit
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
GE_DIR="$REPO_ROOT/gemini-enterprise"

# ------------------------------------------------------------------------------
# 1. Load Environment Configuration
# ------------------------------------------------------------------------------
if [ -f "$REPO_ROOT/.env" ]; then
    echo "[*] Loading environment variables from: $REPO_ROOT/.env"
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
elif [ -f "$GE_DIR/.env" ]; then
    echo "[*] Loading environment variables from: $GE_DIR/.env"
    set -a
    # shellcheck disable=SC1091
    source "$GE_DIR/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
PROJECT_NUMBER="${GCP_PROJECT_NUMBER:-${PROJECT_NUMBER:-}}"
REGION="${GCP_REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
if [ "$REGION" = "global" ]; then
    REGION="us-central1"
fi
STAGING_BUCKET_URI="${STAGING_BUCKET:-}"
SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-${SERVICE_ACCOUNT:-}}"
GE_APP="${GEMINI_ENTERPRISE_APP_ID:-}"
GE_LOCATION="${GEMINI_ENTERPRISE_LOCATION:-global}"
SKIP_GE=false
DRY_RUN=false

usage() {
    cat <<EOF
Usage: ./deploy.sh [OPTIONS]
       ./deploy.sh [PROJECT_ID] [REGION]

Deploy Document Validator to Vertex AI Agent Runtime for Gemini Enterprise.

Environment Variables (.env or shell):
  GOOGLE_CLOUD_PROJECT          Target GCP Project ID
  GOOGLE_CLOUD_LOCATION         Target GCP Region (default: us-central1)
  STAGING_BUCKET                GCS bucket for ADK deployment staging
  AGENT_ENGINE_ID               Existing Reasoning Engine ID for in-place updates
  AGENT_CPU / AGENT_MEMORY      Container resources (default: 8 CPU / 32Gi RAM)
  MODEL                         Gemini model (default: gemini-3.8-flash)
  GEMINI_ENTERPRISE_APP_ID      Gemini Enterprise App ID / Resource name

Options:
  -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
  -r, --region REGION          Google Cloud Region (default: us-central1)
  -b, --bucket BUCKET_NAME     Custom staging GCS bucket name
  -s, --service-account SA     Custom service account email
      --ge APP_ID              Gemini Enterprise App ID or full resource name
      --ge-location LOCATION   Gemini Enterprise location (default: global)
      --skip-ge                Skip linking agent to Gemini Enterprise
  -n, --dry-run                Preview deployment commands without executing
  -h, --help                   Show this help message and exit

Examples:
  ./deploy.sh                                     # Deploys with automatic gcloud provisioning
  ./deploy.sh -p my-project -r us-central1        # Deploys to a specific project and region
  ./deploy.sh --ge my-ge-app                      # Deploys and links to a specific GE app
  ./deploy.sh --dry-run
EOF
    exit 0
}

# ------------------------------------------------------------------------------
# 2. Parse Command-Line Flags (Supports both named flags and legacy positional args)
# ------------------------------------------------------------------------------
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--project)
            PROJECT_ID="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -b|--bucket)
            STAGING_BUCKET_URI="$2"
            shift 2
            ;;
        -s|--service-account)
            SERVICE_ACCOUNT="$2"
            shift 2
            ;;
        --ge)
            GE_APP="$2"
            shift 2
            ;;
        --ge-location)
            GE_LOCATION="$2"
            shift 2
            ;;
        --skip-ge)
            SKIP_GE=true
            shift
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        -*)
            echo "[!] Error: Unknown argument \"$1\""
            usage
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

if [ ${#POSITIONAL[@]} -ge 1 ] && [ -n "${POSITIONAL[0]}" ]; then
    PROJECT_ID="${POSITIONAL[0]}"
fi
if [ ${#POSITIONAL[@]} -ge 2 ] && [ -n "${POSITIONAL[1]}" ]; then
    REGION="${POSITIONAL[1]}"
fi

echo "=================================================================="
echo "🚀 Gemini Enterprise Agent Deployment — Document Validator"
echo "   Target: Google Cloud Vertex AI Agent Runtime"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 3. Prerequisites & Context Resolution
# ------------------------------------------------------------------------------
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "your-project-id" ]; then
    if command -v gcloud &> /dev/null; then
        PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    fi
fi

if [ -z "$PROJECT_ID" ]; then
    echo "Error: GOOGLE_CLOUD_PROJECT is not set. Pass -p <project-id> or configure .env." >&2
    exit 1
fi

if [ -z "$PROJECT_NUMBER" ] && command -v gcloud &> /dev/null; then
    PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
fi

if [ -z "$STAGING_BUCKET_URI" ] || [[ "$STAGING_BUCKET_URI" == *"your-project-id"* ]]; then
    STAGING_BUCKET_URI="gs://document-validator-staging-${PROJECT_ID}"
elif [[ "$STAGING_BUCKET_URI" != gs://* ]]; then
    STAGING_BUCKET_URI="gs://${STAGING_BUCKET_URI}"
fi

if [ -z "$SERVICE_ACCOUNT" ]; then
    SERVICE_ACCOUNT="document-validator-sa@${PROJECT_ID}.iam.gserviceaccount.com"
fi

echo "[✓] Target GCP Project: $PROJECT_ID"
echo "[✓] Target Region:      $REGION"
echo "[✓] Staging Bucket:     $STAGING_BUCKET_URI"
echo "[✓] Session Bucket:     gs://document-validator-sessions-${PROJECT_ID}"
echo "[✓] Service Account:    $SERVICE_ACCOUNT"

# ------------------------------------------------------------------------------
# 4. Step 1: Provision Google Cloud Infrastructure via setup.sh
# ------------------------------------------------------------------------------
if [ -x "$REPO_ROOT/setup.sh" ]; then
    echo ""
    echo "[*] Step 1: Provisioning Google Cloud infrastructure via ./setup.sh..."
    SETUP_CMD=(
        "$REPO_ROOT/setup.sh"
        --project "$PROJECT_ID"
        --region "$REGION"
        --bucket "${STAGING_BUCKET_URI#gs://}"
        --service-account "$SERVICE_ACCOUNT"
        --create-sa
    )
    if [ "$DRY_RUN" = true ]; then
        SETUP_CMD+=(--dry-run)
    fi
    "${SETUP_CMD[@]}"
fi

# ------------------------------------------------------------------------------
# 5. Step 2: Package & Deploy to Vertex AI Agent Runtime via ADK
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 2: Deploying Document Validator to Vertex AI Agent Runtime..."

AGENT_NAME=$(grep '^name:' "$GE_DIR/skill/SKILL.md" | head -1 | sed 's/name: *//')
AGENT_ENGINE_ID_ARGS=()
if [ -n "${AGENT_ENGINE_ID:-}" ]; then
    AGENT_ENGINE_ID_ARGS=(--agent_engine_id="${AGENT_ENGINE_ID}")
    echo "    [*] Updating existing Agent Runtime instance: ${AGENT_ENGINE_ID}"
fi

if [ "$DRY_RUN" = true ]; then
    echo "    [Dry-Run] Would stage $GE_DIR/agent, $GE_DIR/skill, $GE_DIR/requirements.txt"
    echo "    [Dry-Run] Resource limits: cpu=${AGENT_CPU:-8}, memory=${AGENT_MEMORY:-32Gi}"
    echo "    [Dry-Run] Command: adk deploy agent_engine --project=$PROJECT_ID --region=$REGION --display_name=$AGENT_NAME --artifact_service_uri=$STAGING_BUCKET_URI ${AGENT_ENGINE_ID_ARGS[*]}"
    REASONING_ENGINE_ID="projects/${PROJECT_NUMBER:-000000}/locations/${REGION}/reasoningEngines/${AGENT_ENGINE_ID:-DRY_RUN_ID}"
else
    if [ ! -d "$REPO_ROOT/.venv" ]; then
        echo "    [*] Creating virtual environment..."
        python3 -m venv "$REPO_ROOT/.venv"
    fi
    echo "    [*] Installing dependencies..."
    "$REPO_ROOT/.venv/bin/pip" install --upgrade pip -q
    "$REPO_ROOT/.venv/bin/pip" install "google-adk[gcp]" -r "$GE_DIR/requirements.txt" -q

    # Build minimal staging directory with a valid Python identifier name
    STAGING_BASE="/tmp/agent_deploy_$$"
    mkdir -p "${STAGING_BASE}"

    cp -r "$GE_DIR/agent/" "${STAGING_BASE}/"
    cp -r "$GE_DIR/skill/" "${STAGING_BASE}/"
    cp "$GE_DIR/requirements.txt" "${STAGING_BASE}/"

    if [ -f "$REPO_ROOT/.env" ]; then
        grep -vE '^(AGENT_ENGINE_ID|AGENT_CPU|AGENT_MEMORY|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION|GEMINI_ENTERPRISE_APP_ID|GEMINI_ENTERPRISE_LOCATION)=' "$REPO_ROOT/.env" \
            | grep -vE '^[A-Za-z_][A-Za-z0-9_]*=[[:space:]]*$' \
            > "${STAGING_BASE}/.env" || true
    fi
    {
        echo "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
        echo "GOOGLE_CLOUD_LOCATION=${REGION}"
    } >> "${STAGING_BASE}/.env"

    cat > "${STAGING_BASE}/.agent_engine_config.json" <<EOF
{
  "resource_limits": {
    "cpu": "${AGENT_CPU:-8}",
    "memory": "${AGENT_MEMORY:-32Gi}"
  }
}
EOF

    DEPLOY_LOG="$(mktemp)"
    trap 'rm -rf "${STAGING_BASE}" "${DEPLOY_LOG}"' EXIT

    "${REPO_ROOT}/.venv/bin/adk" deploy agent_engine \
        --project="${PROJECT_ID}" \
        --region="${REGION}" \
        --display_name="${AGENT_NAME}" \
        --artifact_service_uri="${STAGING_BUCKET_URI}" \
        "${AGENT_ENGINE_ID_ARGS[@]}" \
        "${STAGING_BASE}" 2>&1 | tee "${DEPLOY_LOG}"

    REASONING_ENGINE_ID=$(grep -oE 'projects/[0-9]+/locations/[a-z0-9-]+/reasoningEngines/[0-9]+' "${DEPLOY_LOG}" | tail -1 || true)

    # Persist AGENT_ENGINE_ID back into .env and gemini-enterprise/deployment_metadata.json
    if [ -n "${REASONING_ENGINE_ID}" ]; then
        NEW_ID="${REASONING_ENGINE_ID##*/}"
        if [ -f "$REPO_ROOT/.env" ]; then
            if grep -q '^AGENT_ENGINE_ID=' "$REPO_ROOT/.env"; then
                awk -v id="${NEW_ID}" '/^AGENT_ENGINE_ID=/{print "AGENT_ENGINE_ID="id; next} {print}' "$REPO_ROOT/.env" > "$REPO_ROOT/.env.tmp" && mv "$REPO_ROOT/.env.tmp" "$REPO_ROOT/.env"
            else
                echo "AGENT_ENGINE_ID=${NEW_ID}" >> "$REPO_ROOT/.env"
            fi
        fi

        cat > "$GE_DIR/deployment_metadata.json" <<EOF
{
  "remote_agent_runtime_id": "${REASONING_ENGINE_ID}",
  "deployment_target": "agent_runtime",
  "is_a2a": false,
  "deployment_timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
    fi
fi

echo ""
echo "=================================================================="
echo "✅ Vertex AI Agent Runtime Deployment Complete!"
echo "👉 Reasoning Engine ID: ${REASONING_ENGINE_ID:-projects/$PROJECT_ID/locations/$REGION/reasoningEngines/...}"
echo "👉 Console: https://console.cloud.google.com/vertex-ai/reasoning-engines?project=$PROJECT_ID"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 6. Step 3: Gemini Enterprise Registration (Automated via agents-cli or Manual)
# ------------------------------------------------------------------------------
if [ "$SKIP_GE" = true ]; then
    echo ""
    echo "[*] Step 3: Skipping Gemini Enterprise registration (--skip-ge specified)."
else
    if [ -z "$GE_APP" ] && command -v agents-cli &> /dev/null && [ "$DRY_RUN" = false ]; then
        echo ""
        echo "[*] Step 3: Auto-discovering Gemini Enterprise apps in project $PROJECT_ID..."
        LIST_OUTPUT="$(agents-cli publish gemini-enterprise --list --project-id "$PROJECT_ID" 2>/dev/null || agents-cli publish gemini-enterprise --list --project "$PROJECT_ID" 2>/dev/null || true)"

        DISCOVERED_APP="$(python3 -c '
import json, sys
data_str = sys.stdin.read().strip()
for line in data_str.splitlines():
    line = line.strip()
    if line.startswith("{") and "apps" in line:
        try:
            d = json.loads(line)
            apps = d.get("apps", [])
            if apps:
                print(apps[0].get("name", ""))
                sys.exit(0)
        except Exception:
            pass
' <<< "$LIST_OUTPUT")"

        if [ -n "$DISCOVERED_APP" ]; then
            GE_APP="$DISCOVERED_APP"
            echo "[✓] Auto-selected Gemini Enterprise App: $GE_APP"
        fi
    fi

    if [ -n "$GE_APP" ] && command -v agents-cli &> /dev/null; then
        echo ""
        echo "[*] Registering Agent to Gemini Enterprise..."

        if [[ "$GE_APP" != projects/* ]]; then
            if [ -n "$PROJECT_NUMBER" ]; then
                GE_APP="projects/${PROJECT_NUMBER}/locations/${GE_LOCATION}/collections/default_collection/engines/${GE_APP}"
            fi
        fi

        GE_DISPLAY_NAME="${GEMINI_DISPLAY_NAME:-Document Validator}"
        GE_DESCRIPTION="${GEMINI_DESCRIPTION:-Systematic document compliance and audit agent with confidence-scored gap analysis.}"
        GE_TOOL_DESCRIPTION="${GEMINI_TOOL_DESCRIPTION:-Validates pending documents and attachments against criteria documents (regulations, tender specs, review-committee comments) and produces structured compliance audit reports.}"

        PUBLISH_CMD=(
            agents-cli publish gemini-enterprise
            --gemini-enterprise-app-id "$GE_APP"
            --registration-type adk
            --display-name "$GE_DISPLAY_NAME"
            --description "$GE_DESCRIPTION"
            --tool-description "$GE_TOOL_DESCRIPTION"
        )
        if [ -n "${REASONING_ENGINE_ID:-}" ]; then
            PUBLISH_CMD+=(--agent-runtime-id "$REASONING_ENGINE_ID")
        fi
        if [ -n "$PROJECT_ID" ]; then
            PUBLISH_CMD+=(--project-id "$PROJECT_ID")
        fi
        if [ -n "$PROJECT_NUMBER" ]; then
            PUBLISH_CMD+=(--project-number "$PROJECT_NUMBER")
        fi

        if [ "$DRY_RUN" = true ]; then
            echo "    [Dry-Run] Executing: (cd gemini-enterprise && ${PUBLISH_CMD[*]})"
        else
            echo "    Command: (cd gemini-enterprise && ${PUBLISH_CMD[*]})"
            (cd "$GE_DIR" && "${PUBLISH_CMD[@]}")
            echo ""
            echo "=================================================================="
            echo "🎉 Agent successfully registered to Gemini Enterprise!"
            echo "=================================================================="
        fi
    else
        echo ""
        echo "=================================================================="
        echo "3. Connect to Gemini Enterprise Admin Console"
        echo "=================================================================="
        echo "   - Log in to your Gemini Enterprise Admin Console."
        echo "   - Navigate to 'Agents' in the left sidebar."
        echo "   - Click '+ Add Agent' and select 'Custom agent via Agent Engine'."
        echo "   - Enter the following Reasoning Engine Resource ID:"
        echo "     👉 ${REASONING_ENGINE_ID:-projects/$PROJECT_ID/locations/$REGION/reasoningEngines/...}"
        echo "   - Or re-run with: ./deploy.sh --ge <GEMINI_ENTERPRISE_APP_ID>"
        echo ""
    fi
fi
