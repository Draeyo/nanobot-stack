#!/usr/bin/env bash
# restore.sh — Restore a nanobot-stack backup from a local archive
# Usage: ./scripts/restore.sh <archive_path> [QDRANT_URL]
#
# Steps:
#   1. Safety confirmation prompt
#   2. Optional Fernet decryption (if .enc archive)
#   3. docker compose down
#   4. Extract tar.gz to temp dir
#   5. Copy SQLite files back to STATE_DIR
#   6. Upload Qdrant snapshots via REST API
#   7. docker compose up -d
#   8. Show success message

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
# Arguments & config
# ---------------------------------------------------------------------------
ARCHIVE_PATH="${1:-}"
QDRANT_URL="${2:-${QDRANT_URL:-http://localhost:6333}}"
STATE_DIR="${RAG_STATE_DIR:-/opt/nanobot-stack/rag-bridge/state}"
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"

if [[ -z "${ARCHIVE_PATH}" ]]; then
    echo "Usage: $0 <archive_path> [QDRANT_URL]"
    echo "  archive_path  : path to the .tar.gz or .tar.gz.enc backup archive"
    echo "  QDRANT_URL    : Qdrant REST URL (default: http://localhost:6333)"
    exit 1
fi

if [[ ! -f "${ARCHIVE_PATH}" ]]; then
    echo "[restore.sh] ERROR: Archive not found: ${ARCHIVE_PATH}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1 — Safety confirmation
# ---------------------------------------------------------------------------
echo ""
echo "=========================================================="
echo "  NANOBOT-STACK RESTORE"
echo "=========================================================="
echo ""
echo "  Archive : ${ARCHIVE_PATH}"
echo "  Qdrant  : ${QDRANT_URL}"
echo "  State   : ${STATE_DIR}"
echo ""
echo "  This will STOP all services and restore from backup."
echo "  ALL CURRENT DATA WILL BE OVERWRITTEN."
echo ""
read -r -p "  Type RESTORE to confirm: " CONFIRMATION

if [[ "${CONFIRMATION}" != "RESTORE" ]]; then
    echo "[restore.sh] Aborted — confirmation not received."
    exit 1
fi

echo ""
echo "[restore.sh] Starting restore procedure..."

# ---------------------------------------------------------------------------
# Step 2 — Optional Fernet decryption
# ---------------------------------------------------------------------------
WORK_ARCHIVE="${ARCHIVE_PATH}"

if [[ "${ARCHIVE_PATH}" == *.enc ]]; then
    if [[ -z "${BACKUP_ENCRYPTION_KEY}" ]]; then
        echo "[restore.sh] ERROR: Archive is encrypted (.enc) but BACKUP_ENCRYPTION_KEY is not set."
        exit 1
    fi

    DECRYPTED_PATH="${ARCHIVE_PATH%.enc}"
    echo "[restore.sh] Decrypting archive..."
    python3 - <<PYEOF
import sys
from pathlib import Path
from cryptography.fernet import Fernet

key = "${BACKUP_ENCRYPTION_KEY}".strip()
fernet = Fernet(key.encode())

enc_path = Path("${ARCHIVE_PATH}")
out_path = Path("${DECRYPTED_PATH}")

ciphertext = enc_path.read_bytes()
plaintext = fernet.decrypt(ciphertext)
out_path.write_bytes(plaintext)
print(f"[restore.sh] Decrypted to: {out_path}")
PYEOF
    WORK_ARCHIVE="${DECRYPTED_PATH}"
fi

# ---------------------------------------------------------------------------
# Step 3 — docker compose down
# ---------------------------------------------------------------------------
echo "[restore.sh] Stopping services..."
if [[ -f "docker-compose.yml" || -f "docker-compose.yaml" ]]; then
    docker compose down
else
    echo "[restore.sh] WARNING: No docker-compose.yml found in current directory — skipping service stop."
fi

# ---------------------------------------------------------------------------
# Step 4 — Extract archive
# ---------------------------------------------------------------------------
RESTORE_TMP=$(mktemp -d -t nanobot-restore-XXXXXX)
echo "[restore.sh] Extracting archive to ${RESTORE_TMP} ..."
tar -xzf "${WORK_ARCHIVE}" -C "${RESTORE_TMP}"

# Cleanup decrypted temp file if we created one
if [[ "${WORK_ARCHIVE}" != "${ARCHIVE_PATH}" ]]; then
    rm -f "${WORK_ARCHIVE}"
fi

# ---------------------------------------------------------------------------
# Step 5 — Copy SQLite files back
# ---------------------------------------------------------------------------
SQLITE_DIR="${RESTORE_TMP}/sqlite"
if [[ -d "${SQLITE_DIR}" ]]; then
    echo "[restore.sh] Restoring SQLite databases to ${STATE_DIR} ..."
    mkdir -p "${STATE_DIR}"
    cp -v "${SQLITE_DIR}"/*.db "${STATE_DIR}/" 2>/dev/null || echo "[restore.sh] No .db files found in archive."
else
    echo "[restore.sh] WARNING: No sqlite/ directory in archive — skipping SQLite restore."
fi

# Restore stack.env if present
if [[ -f "${RESTORE_TMP}/stack.env" ]]; then
    echo "[restore.sh] Restoring stack.env..."
    cp -v "${RESTORE_TMP}/stack.env" /opt/nanobot-stack/stack.env
fi

# ---------------------------------------------------------------------------
# Step 6 — Upload Qdrant snapshots
# ---------------------------------------------------------------------------
SNAPSHOTS_DIR="${RESTORE_TMP}/qdrant_snapshots"
if [[ -d "${SNAPSHOTS_DIR}" ]]; then
    echo "[restore.sh] Restoring Qdrant snapshots..."

    # Wait for Qdrant to be available (it may start before docker compose up)
    # We do the Qdrant restore AFTER docker compose up (step 7) is complete
    # For now, we collect snapshot paths and upload after startup
    SNAPSHOT_FILES=("${SNAPSHOTS_DIR}"/*)
    SNAPSHOT_COUNT=${#SNAPSHOT_FILES[@]}
    echo "[restore.sh] Found ${SNAPSHOT_COUNT} snapshot(s) to restore."
else
    SNAPSHOT_COUNT=0
    echo "[restore.sh] WARNING: No qdrant_snapshots/ directory in archive — skipping Qdrant restore."
fi

# ---------------------------------------------------------------------------
# Step 7 — docker compose up
# ---------------------------------------------------------------------------
echo "[restore.sh] Starting services..."
if [[ -f "docker-compose.yml" || -f "docker-compose.yaml" ]]; then
    docker compose up -d
    echo "[restore.sh] Waiting 15 seconds for services to stabilise..."
    sleep 15
fi

# ---------------------------------------------------------------------------
# Step 6 (continued) — Upload Qdrant snapshots after startup
# ---------------------------------------------------------------------------
if [[ "${SNAPSHOT_COUNT}" -gt 0 && -d "${SNAPSHOTS_DIR}" ]]; then
    echo "[restore.sh] Uploading Qdrant snapshots..."
    for SNAPSHOT_FILE in "${SNAPSHOTS_DIR}"/*; do
        FILENAME=$(basename "${SNAPSHOT_FILE}")
        # Extract collection name: filename format is <collection>_<snapshot>.snapshot
        COLLECTION=$(echo "${FILENAME}" | sed 's/_[^_]*$//')

        echo "[restore.sh]   Uploading snapshot for collection: ${COLLECTION}"
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "${QDRANT_URL}/collections/${COLLECTION}/snapshots/upload" \
            -H "Content-Type: multipart/form-data" \
            -F "snapshot=@${SNAPSHOT_FILE}")

        if [[ "${HTTP_CODE}" -eq 200 ]]; then
            echo "[restore.sh]   OK: ${COLLECTION}"
        else
            echo "[restore.sh]   WARNING: Upload returned HTTP ${HTTP_CODE} for ${COLLECTION}"
        fi
    done
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
rm -rf "${RESTORE_TMP}"

# ---------------------------------------------------------------------------
# Step 8 — Success
# ---------------------------------------------------------------------------
echo ""
echo "=========================================================="
echo "  RESTORE COMPLETE"
echo "=========================================================="
echo ""
echo "  Archive  : ${ARCHIVE_PATH}"
echo "  Services : docker compose up -d (running)"
echo ""
echo "  Verify the stack with: docker compose ps"
echo ""
