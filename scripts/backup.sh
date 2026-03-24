#!/usr/bin/env bash
# backup.sh — Trigger a nanobot-stack backup via the bridge API
# Usage: ./scripts/backup.sh [BRIDGE_URL]
#
# Loads configuration from ./stack.env or /opt/nanobot-stack/stack.env if present.
# Override BRIDGE_URL by passing it as the first argument or setting BRIDGE_URL env var.

set -euo pipefail

# ---------------------------------------------------------------------------
# Load stack.env if present
# ---------------------------------------------------------------------------
if [[ -f "./stack.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "./stack.env"; set +a
elif [[ -f "/opt/nanobot-stack/stack.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "/opt/nanobot-stack/stack.env"; set +a
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BRIDGE_URL="${1:-${BRIDGE_URL:-http://localhost:8000}}"
ENDPOINT="${BRIDGE_URL}/api/backup/run"

# Guard: BACKUP_ENABLED must be true
BACKUP_ENABLED="${BACKUP_ENABLED:-false}"
if [[ "${BACKUP_ENABLED,,}" != "true" && "${BACKUP_ENABLED}" != "1" ]]; then
    echo "[backup.sh] BACKUP_ENABLED is not set to true. Backup is disabled."
    echo "  Set BACKUP_ENABLED=true in stack.env to enable backups."
    exit 1
fi

# ---------------------------------------------------------------------------
# Trigger backup
# ---------------------------------------------------------------------------
echo "[backup.sh] Triggering backup on ${ENDPOINT} ..."

RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "${ENDPOINT}" \
    -H "Content-Type: application/json")

HTTP_BODY=$(echo "${RESPONSE}" | head -n -1)
HTTP_CODE=$(echo "${RESPONSE}" | tail -n1)

if [[ "${HTTP_CODE}" -ne 200 ]]; then
    echo "[backup.sh] ERROR: bridge returned HTTP ${HTTP_CODE}"
    echo "${HTTP_BODY}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Display result
# ---------------------------------------------------------------------------
echo "[backup.sh] Backup response:"
echo "${HTTP_BODY}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    status = data.get('status', 'unknown')
    print(f'  Status          : {status}')
    print(f'  Backup ID       : {data.get(\"backup_id\", \"-\")}')
    print(f'  Archive path    : {data.get(\"archive_path\", \"-\")}')
    print(f'  Archive S3 key  : {data.get(\"archive_s3_key\", \"-\")}')
    print(f'  Size            : {data.get(\"size_bytes\", 0):,} bytes')
    print(f'  Collections     : {data.get(\"collections_count\", 0)}')
    print(f'  SQLite files    : {data.get(\"sqlite_files_count\", 0)}')
    print(f'  Encrypted       : {data.get(\"encrypted\", False)}')
    if data.get('error_msg'):
        print(f'  Error           : {data[\"error_msg\"]}')
    sys.exit(0 if status == 'success' else 1)
except json.JSONDecodeError:
    print(sys.stdin.read())
    sys.exit(1)
" || exit 1

echo "[backup.sh] Done."
