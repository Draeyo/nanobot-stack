#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================================
# rotate-secrets.sh — Rotate internal secrets for the nanobot stack
# ============================================================================
# Rotates: bridge token, RAG postgres password, Langfuse secrets.
# Does NOT rotate: external API keys (OpenAI, Anthropic, OpenRouter).
#
# Usage:
#   sudo ./rotate-secrets.sh [--bridge] [--postgres] [--langfuse] [--all]
#
# With no flags, rotates everything (equivalent to --all).
# ============================================================================

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [[ "$EUID" -ne 0 ]]; then
  err "Run this script as root."
  exit 1
fi

ROTATE_BRIDGE=false
ROTATE_POSTGRES=false
ROTATE_LANGFUSE=false

if [[ $# -eq 0 ]]; then
  ROTATE_BRIDGE=true; ROTATE_POSTGRES=true; ROTATE_LANGFUSE=true
fi
for arg in "$@"; do
  case "$arg" in
    --bridge)   ROTATE_BRIDGE=true ;;
    --postgres) ROTATE_POSTGRES=true ;;
    --langfuse) ROTATE_LANGFUSE=true ;;
    --all)      ROTATE_BRIDGE=true; ROTATE_POSTGRES=true; ROTATE_LANGFUSE=true ;;
    *)          err "Unknown flag: $arg"; exit 1 ;;
  esac
done

# --------------------------------------------------------------------------
# Bridge token
# --------------------------------------------------------------------------
if $ROTATE_BRIDGE; then
  log "Rotating bridge token..."
  NEW_TOKEN="$(random_hex 32)"
  echo -n "$NEW_TOKEN" > "$RAG_HOME/.bridge_token"
  chown "$APP_USER:$APP_GROUP" "$RAG_HOME/.bridge_token"
  chmod 0600 "$RAG_HOME/.bridge_token"

  sed -i "s/^RAG_BRIDGE_TOKEN=.*/RAG_BRIDGE_TOKEN=${NEW_TOKEN}/" "$RAG_HOME/.env"
  sed -i "s/^RAG_BRIDGE_TOKEN=.*/RAG_BRIDGE_TOKEN=${NEW_TOKEN}/" "$NANOBOT_HOME/config/.env"
  sed -i "s/^RAG_BRIDGE_TOKEN=.*/RAG_BRIDGE_TOKEN=${NEW_TOKEN}/" "$MCP_HOME/.env"

  # Update nanobot config.json
  python3 -c "
import json
with open('$NANOBOT_HOME/config/config.json') as f:
    cfg = json.load(f)
for srv in cfg.get('tools',{}).get('mcpServers',{}).values():
    if 'env' in srv:
        srv['env']['RAG_BRIDGE_TOKEN'] = '$NEW_TOKEN'
with open('$NANOBOT_HOME/config/config.json', 'w') as f:
    json.dump(cfg, f, indent=2)
" 2>/dev/null || warn "Could not update config.json automatically"

  log "Bridge token rotated. Restarting services..."
  systemctl restart nanobot-rag nanobot
fi

# --------------------------------------------------------------------------
# RAG Postgres password
# --------------------------------------------------------------------------
if $ROTATE_POSTGRES; then
  log "Rotating RAG PostgreSQL password..."
  NEW_PG_PASS="$(random_b64 24)"
  PG_USER="$(grep '^POSTGRES_USER=' "$RAG_HOME/postgres.env" | cut -d= -f2)"
  PG_DB="$(grep '^POSTGRES_DB=' "$RAG_HOME/postgres.env" | cut -d= -f2)"

  sudo -u postgres psql -q -c "ALTER ROLE ${PG_USER} WITH PASSWORD '${NEW_PG_PASS}';"

  cat > "$RAG_HOME/postgres.env" <<EOF
POSTGRES_DB=${PG_DB}
POSTGRES_USER=${PG_USER}
POSTGRES_PASSWORD=${NEW_PG_PASS}
DATABASE_URL=postgresql://${PG_USER}:${NEW_PG_PASS}@127.0.0.1:5432/${PG_DB}
EOF
  chown "$APP_USER:$APP_GROUP" "$RAG_HOME/postgres.env"
  chmod 0600 "$RAG_HOME/postgres.env"

  log "Postgres password rotated. Restarting nanobot-rag..."
  systemctl restart nanobot-rag
fi

# --------------------------------------------------------------------------
# Langfuse secrets
# --------------------------------------------------------------------------
if $ROTATE_LANGFUSE; then
  log "Rotating Langfuse secrets..."
  sed -i "s/^SALT=.*/SALT=$(random_hex 32)/" "$LANGFUSE_DIR/.env"
  sed -i "s/^NEXTAUTH_SECRET=.*/NEXTAUTH_SECRET=$(random_hex 32)/" "$LANGFUSE_DIR/.env"
  sed -i "s/^ENCRYPTION_KEY=.*/ENCRYPTION_KEY=$(random_hex 32)/" "$LANGFUSE_DIR/.env"
  sed -i "s/^REDIS_AUTH=.*/REDIS_AUTH=$(random_b64 18)/" "$LANGFUSE_DIR/.env"

  log "Langfuse secrets rotated. Restarting stack..."
  systemctl restart langfuse-compose.service
fi

log "Secret rotation complete."
log "Run: nanobot-stack-selftest to verify."
