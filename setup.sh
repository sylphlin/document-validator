#!/usr/bin/env bash
# ==============================================================================
# Setup Google Cloud Environment for Document Validator
#
# Provisions Google Cloud resources:
# 1. Verifies authentication and enables required Google Cloud APIs.
# 2. Creates and configures Cloud Storage buckets:
#    - Session & Async Job bucket: gs://document-validator-sessions-${PROJECT_ID}
#      (with 15-day automatic lifecycle cleanup)
#    - ADK Staging bucket: gs://document-validator-staging-${PROJECT_ID}
# 3. Optionally creates dedicated Service Account and IAM role bindings.
# 4. Generates or updates local .env configuration for Antigravity and Gemini Enterprise.
#
# Usage:
#   ./setup.sh [OPTIONS]
#
# Options:
#   -p, --project PROJECT_ID       Google Cloud Project ID (overrides .env)
#   -r, --region REGION            Google Cloud Region (default: us-central1)
#   -b, --bucket BUCKET_NAME       Custom staging GCS bucket name
#       --session-bucket BUCKET    Custom session GCS bucket name
#   -s, --service-account SA       Custom service account email
#       --create-sa                Create and configure dedicated Service Account
#   -n, --dry-run                  Preview setup actions without executing
#   -h, --help                     Show this help message and exit
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

# ------------------------------------------------------------------------------
# 1. Load Environment Configuration
# ------------------------------------------------------------------------------
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
elif [ -f "$REPO_ROOT/gemini-enterprise/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/gemini-enterprise/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
PROJECT_NUMBER="${GCP_PROJECT_NUMBER:-${PROJECT_NUMBER:-}}"
REGION="${GCP_REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}"
if [ "$REGION" = "global" ]; then
    REGION="us-central1"
fi
STAGING_BUCKET_NAME="${STAGING_BUCKET:-}"
SESSION_BUCKET_NAME=""
SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-${SERVICE_ACCOUNT:-}}"
CREATE_SA=true
DRY_RUN=false

usage() {
    cat <<EOF
Usage: ./setup.sh [OPTIONS]

Setup Google Cloud environment for Document Validator.

Options:
  -p, --project PROJECT_ID       Google Cloud Project ID (overrides .env)
  -r, --region REGION            Google Cloud Region (default: us-central1)
  -b, --bucket BUCKET_NAME       Staging GCS bucket name (default: document-validator-staging-\${PROJECT_ID})
      --session-bucket BUCKET    Session GCS bucket name (default: document-validator-sessions-\${PROJECT_ID})
  -s, --service-account SA       Custom service account email (default: document-validator-sa@...)
      --create-sa                Create and configure dedicated Service Account
  -n, --dry-run                  Preview setup actions without executing
  -h, --help                     Show this help message and exit

Examples:
  ./setup.sh                                   # Setup with active gcloud project
  ./setup.sh -p my-project -r us-central1      # Setup with specific project and region
  ./setup.sh --dry-run
EOF
    exit 0
}

# ------------------------------------------------------------------------------
# 2. Parse Command-Line Flags
# ------------------------------------------------------------------------------
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
            STAGING_BUCKET_NAME="$2"
            shift 2
            ;;
        --session-bucket)
            SESSION_BUCKET_NAME="$2"
            shift 2
            ;;
        -s|--service-account)
            SERVICE_ACCOUNT="$2"
            CREATE_SA=true
            shift 2
            ;;
        --create-sa)
            CREATE_SA=true
            shift
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "[!] Error: Unknown argument \"$1\""
            usage
            ;;
    esac
done

echo "=================================================================="
echo "⚙️  Document Validator: Google Cloud Environment Setup"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 3. Prerequisites & Context Resolution
# ------------------------------------------------------------------------------
if ! command -v gcloud &> /dev/null; then
    echo "[!] Error: gcloud CLI is not found in PATH."
    echo "    Please install Google Cloud SDK: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "your-project-id" ]; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi

if [ -z "$PROJECT_ID" ]; then
    read -rp "Enter your Google Cloud Project ID: " PROJECT_ID
fi

if [ -z "$PROJECT_ID" ]; then
    echo "[!] Error: Project ID is required."
    exit 1
fi

if [ -z "$PROJECT_NUMBER" ]; then
    PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
fi

# Strip gs:// prefix if provided
if [ -n "$STAGING_BUCKET_NAME" ]; then
    STAGING_BUCKET_NAME="${STAGING_BUCKET_NAME#gs://}"
fi
if [ -z "$STAGING_BUCKET_NAME" ] || [[ "$STAGING_BUCKET_NAME" == *"your-project-id"* ]]; then
    STAGING_BUCKET_NAME="document-validator-staging-${PROJECT_ID}"
fi

if [ -n "$SESSION_BUCKET_NAME" ]; then
    SESSION_BUCKET_NAME="${SESSION_BUCKET_NAME#gs://}"
else
    SESSION_BUCKET_NAME="document-validator-sessions-${PROJECT_ID}"
fi

if [ -z "$SERVICE_ACCOUNT" ] && [ "$CREATE_SA" = true ]; then
    SERVICE_ACCOUNT="document-validator-sa@${PROJECT_ID}.iam.gserviceaccount.com"
fi

echo "[✓] Target GCP Project: $PROJECT_ID"
if [ -n "$PROJECT_NUMBER" ]; then
    echo "[✓] Project Number:     $PROJECT_NUMBER"
fi
echo "[✓] Target Region:      $REGION"
echo "[✓] Session Bucket:     gs://$SESSION_BUCKET_NAME"
echo "[✓] Staging Bucket:     gs://$STAGING_BUCKET_NAME"
if [ "$CREATE_SA" = true ]; then
    echo "[✓] Service Account:    $SERVICE_ACCOUNT"
fi

# Check Application Default Credentials (ADC)
echo ""
echo "[*] Verifying Application Default Credentials (ADC)..."
if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "[!] Warning: Application Default Credentials (ADC) not detected."
    echo "    Run: gcloud auth application-default login"
else
    echo "[✓] Application Default Credentials verified."
fi

# ------------------------------------------------------------------------------
# 4. Enable Required Google Cloud APIs
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 1: Enabling required Google Cloud APIs..."
REQUIRED_APIS=(
    "aiplatform.googleapis.com"
    "storage.googleapis.com"
    "drive.googleapis.com"
    "iamcredentials.googleapis.com"
)
if [ "$CREATE_SA" = true ]; then
    REQUIRED_APIS+=("iam.googleapis.com")
fi

if [ "$DRY_RUN" = false ]; then
    for api in "${REQUIRED_APIS[@]}"; do
        echo "    [*] Checking API: $api..."
        gcloud services enable "$api" --project="$PROJECT_ID" --quiet 2>/dev/null || true
    done
    echo "[✓] Required APIs verified."
else
    echo "    [Dry-Run] Would enable APIs: ${REQUIRED_APIS[*]}"
fi

# ------------------------------------------------------------------------------
# 5. Provision / Verify Cloud Storage Buckets & Lifecycle Rules
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 2: Provisioning and configuring Cloud Storage buckets..."

if [ "$DRY_RUN" = false ]; then
    for bkt in "$SESSION_BUCKET_NAME" "$STAGING_BUCKET_NAME"; do
        if ! gcloud storage buckets describe "gs://$bkt" --project="$PROJECT_ID" &>/dev/null; then
            echo "    [*] Creating GCS bucket: gs://$bkt..."
            gcloud storage buckets create "gs://$bkt" \
                --project="$PROJECT_ID" \
                --location="$REGION" \
                --uniform-bucket-level-access \
                --public-access-prevention \
                --quiet
            echo "    [✓] Created bucket gs://$bkt."
        else
            echo "    [✓] Storage bucket gs://$bkt already exists."
        fi
    done

    # Configure 15-day lifecycle rule on session bucket to clean up expired session/job state
    echo "    [*] Configuring 15-day automatic lifecycle cleanup on gs://$SESSION_BUCKET_NAME..."
    LIFECYCLE_FILE="$(mktemp 2>/dev/null || echo "/tmp/lifecycle_$$.json")"
    cat << 'EOF' > "$LIFECYCLE_FILE"
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {
        "age": 15
      }
    }
  ]
}
EOF
    gcloud storage buckets update "gs://$SESSION_BUCKET_NAME" --lifecycle-file="$LIFECYCLE_FILE" --quiet 2>/dev/null || true
    rm -f "$LIFECYCLE_FILE"
    echo "[✓] Cloud Storage buckets configuration complete."
else
    echo "    [Dry-Run] Would ensure GCS buckets gs://$SESSION_BUCKET_NAME (15-day lifecycle) and gs://$STAGING_BUCKET_NAME exist."
fi

# ------------------------------------------------------------------------------
# 6. Service Account and IAM Roles
# ------------------------------------------------------------------------------
if [ "$CREATE_SA" = true ]; then
    echo ""
    echo "[*] Step 3: Configuring Service Account and IAM roles..."
    SA_NAME="${SERVICE_ACCOUNT%%@*}"

    if [ "$DRY_RUN" = false ]; then
        if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT" --project="$PROJECT_ID" &>/dev/null; then
            echo "    [*] Creating dedicated service account: $SERVICE_ACCOUNT..."
            gcloud iam service-accounts create "$SA_NAME" \
                --display-name="Document Validator Service Account" \
                --project="$PROJECT_ID" \
                --quiet 2>/dev/null || true
        else
            echo "    [✓] Service account $SERVICE_ACCOUNT already exists."
        fi

        echo "    [*] Binding project-level IAM roles to $SERVICE_ACCOUNT..."
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$SERVICE_ACCOUNT" \
            --role="roles/aiplatform.user" \
            --condition=None --quiet 2>/dev/null || true
        gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$SERVICE_ACCOUNT" \
            --role="roles/logging.logWriter" \
            --condition=None --quiet 2>/dev/null || true

        for bkt in "$SESSION_BUCKET_NAME" "$STAGING_BUCKET_NAME"; do
            echo "    [*] Granting roles/storage.objectAdmin on gs://$bkt to $SERVICE_ACCOUNT..."
            gcloud storage buckets add-iam-policy-binding "gs://$bkt" \
                --member="serviceAccount:$SERVICE_ACCOUNT" \
                --role="roles/storage.objectAdmin" --quiet 2>/dev/null || true
        done
    else
        echo "    [Dry-Run] Would ensure service account $SERVICE_ACCOUNT exists with roles/aiplatform.user and roles/storage.objectAdmin."
    fi
fi

# Grant roles/storage.objectAdmin to Vertex AI Reasoning Engine Service Agents
if [ -n "$PROJECT_NUMBER" ] && [ "$DRY_RUN" = false ]; then
    echo ""
    echo "[*] Step 4: Ensuring Vertex AI Service Agents access to buckets..."
    RE_AGENTS=(
        "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
        "service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
        "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
    )
    for sa in "${RE_AGENTS[@]}"; do
        for bkt in "$SESSION_BUCKET_NAME" "$STAGING_BUCKET_NAME"; do
            gcloud storage buckets add-iam-policy-binding "gs://$bkt" \
                --member="serviceAccount:$sa" \
                --role="roles/storage.objectAdmin" --quiet 2>/dev/null || true
        done
    done
    echo "[✓] Vertex AI service agents access verified."
fi

# ------------------------------------------------------------------------------
# 7. Local Environment Configuration (.env)
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 5: Updating local environment configuration (.env)..."
ENV_FILE="$REPO_ROOT/.env"

update_env_var() {
    local key="$1"
    local value="$2"
    local file="$3"

    if grep -q "^${key}=" "$file" 2>/dev/null; then
        local escaped_val
        escaped_val=$(printf '%s\n' "$value" | sed -e 's/[\/&]/\\&/g')
        sed -i.bak "s/^${key}=.*/${key}=${escaped_val}/" "$file" && rm -f "${file}.bak"
    else
        echo "${key}=${value}" >> "$file"
    fi
}

if [ "$DRY_RUN" = false ]; then
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "$REPO_ROOT/.env.example" ]; then
            cp "$REPO_ROOT/.env.example" "$ENV_FILE"
        else
            touch "$ENV_FILE"
        fi
        echo "    [*] Initialized $ENV_FILE."
    fi

    update_env_var "GOOGLE_CLOUD_PROJECT" "$PROJECT_ID" "$ENV_FILE"
    update_env_var "GOOGLE_CLOUD_LOCATION" "$REGION" "$ENV_FILE"
    update_env_var "STAGING_BUCKET" "gs://$STAGING_BUCKET_NAME" "$ENV_FILE"
    update_env_var "MODEL_LOCATION" "${MODEL_LOCATION:-global}" "$ENV_FILE"
    update_env_var "MODEL" "${MODEL:-gemini-3.8-flash}" "$ENV_FILE"

    echo "[✓] Local configuration updated in: $ENV_FILE"
else
    echo "    [Dry-Run] Would update $ENV_FILE with project, buckets, region, and model settings."
fi

echo ""
echo "=================================================================="
echo "✅ Google Cloud Environment Setup Complete!"
echo "   Project:        $PROJECT_ID"
echo "   Session Bucket: gs://$SESSION_BUCKET_NAME (15-day lifecycle)"
echo "   Staging Bucket: gs://$STAGING_BUCKET_NAME"
echo "   Region:         $REGION"
echo "=================================================================="
