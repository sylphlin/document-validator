#!/usr/bin/env bash
set -euo pipefail

CLEANUP=false
POSITIONAL=()

for arg in "$@"; do
  case $arg in
    --cleanup)
      CLEANUP=true
      ;;
    *)
      POSITIONAL+=("$arg")
      ;;
  esac
done

PROJECT_ID="${POSITIONAL[0]:?Usage: bash setup.sh <PROJECT_ID> [REGION] [--cleanup]}"
REGION="${POSITIONAL[1]:-us-central1}"
SESSION_BUCKET="document-validator-sessions-${PROJECT_ID}"
STAGING_BUCKET="${PROJECT_ID}-agent-staging"

echo "============================================================"
echo "GCP Resource Setup for document-validator"
echo "Project: $PROJECT_ID | Region: $REGION"
echo "============================================================"

gcloud config set project "$PROJECT_ID" --quiet

if [ "$CLEANUP" = true ]; then
  echo "⚠️  Performing cleanup of GCP resources..."
  echo "Removing GCS session bucket: gs://${SESSION_BUCKET}..."
  gcloud storage rm --recursive "gs://${SESSION_BUCKET}" 2>/dev/null || true
  echo "Removing GCS staging bucket: gs://${STAGING_BUCKET}..."
  gcloud storage rm --recursive "gs://${STAGING_BUCKET}" 2>/dev/null || true
  echo "✓ Cleanup complete."
  exit 0
fi

echo "⚠️  NOTE: Cloud Storage buckets incur standard GCP storage costs based on usage."
echo ""
echo "1. Enabling required GCP APIs..."
gcloud services enable \
  storage.googleapis.com \
  aiplatform.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  serviceusage.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com \
  telemetry.googleapis.com \
  --project="$PROJECT_ID" --quiet

echo ""
echo "2. Checking / Creating Cloud Storage session bucket: gs://${SESSION_BUCKET}..."
if ! gcloud storage buckets describe "gs://${SESSION_BUCKET}" --project="$PROJECT_ID" &>/dev/null; then
  gcloud storage buckets create "gs://${SESSION_BUCKET}" --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access
  echo "✓ Created session bucket."
else
  echo "✓ Session bucket already exists."
fi

echo ""
echo "3. Checking / Creating Cloud Storage staging bucket: gs://${STAGING_BUCKET}..."
if ! gcloud storage buckets describe "gs://${STAGING_BUCKET}" --project="$PROJECT_ID" &>/dev/null; then
  gcloud storage buckets create "gs://${STAGING_BUCKET}" --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access
  echo "✓ Created staging bucket."
else
  echo "✓ Staging bucket already exists."
fi

echo ""
echo "✓ GCP resource setup complete!"
