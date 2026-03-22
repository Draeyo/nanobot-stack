#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================================
# uninstall.sh — Complete removal of the nanobot stack
# ============================================================================
# Works even if stack.env has been deleted (uses defaults as fallback).
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_ENV_FILE="${SCRIPT_DIR}/stack.env"

# Try to source lib.sh, but don't fail if stack.env is missing
if [[ -f "$STACK_ENV_FILE" ]]; then
  # shellcheck source=lib.sh
  source "${SCRIPT_DIR}/lib.sh"
else
  # Fallback defaults when stack.env is gone
  APP_USER="nanobot"
  APP_GROUP="nanobot"
  BASE_DIR="/opt/nanobot-stack"
  NANOBOT_HOME="${BASE_DIR}/nanobot"
  RAG_HOME="${BASE_DIR}/rag-bridge"
  MCP_HOME="${BASE_DIR}/rag-mcp"
  WEBUI_HOME="${BASE_DIR}/nanobot-webui"
  QDRANT_DATA_DIR="/var/lib/qdrant"
  QDRANT_CONFIG_DIR="/etc/qdrant"
  TRAEFIK_DYNAMIC_DIR="/etc/traefik/dynamic"
  LANGFUSE_DIR="/opt/docker/langfuse"
  LOCAL_BIN_DIR="/usr/local/bin"
  log()  { echo -e "\033[1;32m[+]\033[0m $*"; }
  warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
  err()  { echo -e "\033[1;31m[-]\033[0m $*" >&2; }
fi

FORCE=false
for arg in "$@"; do
  [[ "$arg" == "--force" ]] && FORCE=true
done

if [[ "$EUID" -ne 0 ]]; then
  err "Run this script as root."
  exit 1
fi

confirm() {
  if $FORCE; then return 0; fi
  local msg="$1"
  read -rp "$msg [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

echo ""
echo "============================================"
echo "  nanobot-stack — UNINSTALL"
echo "============================================"
echo ""
warn "This will permanently remove the nanobot stack and all its data."
echo ""

if ! confirm "Proceed with uninstallation?"; then
  log "Aborted."
  exit 0
fi

# --------------------------------------------------------------------------
# 1. Stop and disable services
# --------------------------------------------------------------------------
log "Stopping services..."
for svc in nanobot nanobot-webui nanobot-rag nanobot-rag-mcp-health qdrant; do
  systemctl stop "$svc" 2>/dev/null || true
  systemctl disable "$svc" 2>/dev/null || true
done
systemctl stop nanobot-rag-ingest.timer 2>/dev/null || true
systemctl disable nanobot-rag-ingest.timer 2>/dev/null || true

# --------------------------------------------------------------------------
# 2. Langfuse (Docker)
# --------------------------------------------------------------------------
if [[ -d "$LANGFUSE_DIR" ]]; then
  if confirm "Remove Langfuse Docker stack and data?"; then
    log "Stopping Langfuse..."
    systemctl stop langfuse-compose 2>/dev/null || true
    systemctl disable langfuse-compose 2>/dev/null || true
    cd "$LANGFUSE_DIR" && docker compose down -v 2>/dev/null || true
    rm -rf "$LANGFUSE_DIR"
    log "  Langfuse removed."
  fi
fi

# --------------------------------------------------------------------------
# 3. Remove systemd units
# --------------------------------------------------------------------------
log "Removing systemd units..."
rm -f /etc/systemd/system/nanobot.service
rm -f /etc/systemd/system/nanobot-rag.service
rm -f /etc/systemd/system/nanobot-rag-ingest.service
rm -f /etc/systemd/system/nanobot-rag-ingest.timer
rm -f /etc/systemd/system/nanobot-rag-mcp-health.service
rm -f /etc/systemd/system/nanobot-webui.service
rm -f /etc/systemd/system/qdrant.service
rm -f /etc/systemd/system/langfuse-compose.service
systemctl daemon-reload

# --------------------------------------------------------------------------
# 4. Remove Traefik config
# --------------------------------------------------------------------------
log "Removing Traefik config..."
rm -f "${TRAEFIK_DYNAMIC_DIR}/nanobot-stack.yaml"
rm -f "${TRAEFIK_DYNAMIC_DIR}/authentik-forwardauth.yaml"

# --------------------------------------------------------------------------
# 5. Remove helper scripts
# --------------------------------------------------------------------------
log "Removing helper scripts..."
rm -f "${LOCAL_BIN_DIR}/nanobot-start"
rm -f "${LOCAL_BIN_DIR}/nanobot-webui-start"
rm -f "${LOCAL_BIN_DIR}/nanobot-rag-ingest"
rm -f "${LOCAL_BIN_DIR}/nanobot-stack-selftest"
rm -f "${LOCAL_BIN_DIR}/nanobot-stack-restart"
rm -f "${LOCAL_BIN_DIR}/nanobot-setup-keys"
rm -f "${LOCAL_BIN_DIR}/nanobot-rotate-secrets"

# --------------------------------------------------------------------------
# 6. Remove data directories
# --------------------------------------------------------------------------
if confirm "Remove ALL data in ${BASE_DIR} (memories, documents, configs)?"; then
  log "Removing ${BASE_DIR}..."
  rm -rf "$BASE_DIR"
fi

if confirm "Remove Qdrant data in ${QDRANT_DATA_DIR}?"; then
  log "Removing Qdrant data..."
  rm -rf "$QDRANT_DATA_DIR"
  rm -rf "$QDRANT_CONFIG_DIR"
fi

# --------------------------------------------------------------------------
# 7. Qdrant binary
# --------------------------------------------------------------------------
if confirm "Remove Qdrant binary?"; then
  rm -f "${LOCAL_BIN_DIR}/qdrant"
fi

# --------------------------------------------------------------------------
# 8. Ollama (optional)
# --------------------------------------------------------------------------
if command -v ollama >/dev/null 2>&1; then
  if confirm "Remove Ollama and its models?"; then
    systemctl stop ollama 2>/dev/null || true
    systemctl disable ollama 2>/dev/null || true
    rm -f /usr/local/bin/ollama
    rm -rf /usr/share/ollama
    userdel ollama 2>/dev/null || true
    log "  Ollama removed."
  fi
fi

# --------------------------------------------------------------------------
# 9. PostgreSQL database
# --------------------------------------------------------------------------
if confirm "Remove PostgreSQL database 'nanobot_rag' and user?"; then
  sudo -u postgres psql -q -c "DROP DATABASE IF EXISTS nanobot_rag;" 2>/dev/null || true
  sudo -u postgres psql -q -c "DROP ROLE IF EXISTS nanobot_rag;" 2>/dev/null || true
  log "  PostgreSQL database removed."
fi

# --------------------------------------------------------------------------
# 10. System user
# --------------------------------------------------------------------------
if confirm "Remove system user '${APP_USER}'?"; then
  userdel "$APP_USER" 2>/dev/null || true
  log "  User removed."
fi

echo ""
log "Uninstallation complete."
log "Docker, PostgreSQL server, and system packages were left in place."
log "Remove them manually if desired: apt remove docker-ce postgresql"
