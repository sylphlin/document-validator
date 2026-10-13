#!/usr/bin/env bash
# ==============================================================================
# Gemini Enterprise Agent Deployment Forwarder
#
# Delegates to the canonical root deployment script: ../deploy.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DEPLOY="$SCRIPT_DIR/../deploy.sh"

if [ -f "$ROOT_DEPLOY" ]; then
    exec "$ROOT_DEPLOY" "$@"
else
    echo "[!] Error: Root deploy.sh not found at $ROOT_DEPLOY"
    exit 1
fi
