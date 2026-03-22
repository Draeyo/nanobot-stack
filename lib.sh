#!/usr/bin/env bash
# ============================================================================
# lib.sh — shared functions and defaults for the nanobot stack scripts
# ============================================================================
# Sourced by deploy.sh, update.sh, rotate-secrets.sh.
# Loads stack.env then applies defaults for any unset variable.
# ============================================================================

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
STACK_ENV_FILE="${SCRIPT_DIR}/stack.env"

# --------------------------------------------------------------------------
# Load user config
# --------------------------------------------------------------------------
if [[ -f "$STACK_ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$STACK_ENV_FILE"
  set +a
else
  echo -e "\033[1;31m[-]\033[0m stack.env not found. Copy stack.env.example → stack.env and edit it." >&2
  exit 1
fi

# --------------------------------------------------------------------------
# Apply defaults for everything that wasn't set
# --------------------------------------------------------------------------
: "${DOMAIN:=example.com}"

: "${NANOBOT_SUBDOMAIN:=ai}"
: "${RAG_SUBDOMAIN:=rag}"
: "${LANGFUSE_SUBDOMAIN:=observability}"
: "${WEBUI_SUBDOMAIN:=chat}"
: "${AUTHENTIK_OUTPOST_FQDN:=auth.${DOMAIN}}"

NANOBOT_DOMAIN="${NANOBOT_SUBDOMAIN}.${DOMAIN}"
RAG_DOMAIN="${RAG_SUBDOMAIN}.${DOMAIN}"
LANGFUSE_DOMAIN="${LANGFUSE_SUBDOMAIN}.${DOMAIN}"
WEBUI_DOMAIN="${WEBUI_SUBDOMAIN}.${DOMAIN}"

: "${APP_USER:=nanobot}"
: "${APP_GROUP:=nanobot}"
: "${BASE_DIR:=/opt/nanobot-stack}"
: "${QDRANT_DATA_DIR:=/var/lib/qdrant}"
: "${QDRANT_CONFIG_DIR:=/etc/qdrant}"
: "${TRAEFIK_DYNAMIC_DIR:=/etc/traefik/dynamic}"
: "${LANGFUSE_DIR:=/opt/docker/langfuse}"
LOCAL_BIN_DIR="/usr/local/bin"

NANOBOT_HOME="${BASE_DIR}/nanobot"
RAG_HOME="${BASE_DIR}/rag-bridge"
RAG_DOCS_DIR="${BASE_DIR}/rag-docs"
RAG_STATE_DIR="${RAG_HOME}/state"
MCP_HOME="${BASE_DIR}/rag-mcp"
WEBUI_HOME="${BASE_DIR}/nanobot-webui"
VERSION_FILE="${BASE_DIR}/.version"

: "${NANOBOT_BIND:=127.0.0.1}"
: "${NANOBOT_PORT:=18790}"
: "${RAG_BIND:=127.0.0.1}"
: "${RAG_PORT:=8089}"
: "${QDRANT_BIND:=127.0.0.1}"
: "${QDRANT_HTTP_PORT:=6333}"
: "${QDRANT_GRPC_PORT:=6334}"
: "${LANGFUSE_BIND:=127.0.0.1}"
: "${LANGFUSE_WEB_PORT:=3300}"
: "${WEBUI_BIND:=127.0.0.1}"
: "${WEBUI_PORT:=18800}"

: "${QDRANT_VERSION:=v1.17.7}"
: "${INSTALL_NANOBOT_WEBUI:=false}"
: "${NANOBOT_DEFAULT_MODEL:=anthropic/claude-sonnet-4-20250514}"

: "${RERANKER_ENABLED:=true}"
: "${RERANKER_MODEL:=BAAI/bge-reranker-v2-m3}"
: "${RERANKER_DEVICE:=cpu}"
: "${SPARSE_VECTORS_ENABLED:=true}"
: "${EMBEDDING_CACHE_SIZE:=512}"
: "${EMBEDDING_CACHE_TTL:=3600}"
: "${EMBEDDING_BATCH_SIZE:=32}"
: "${MAX_CHUNK_CHARS:=1800}"
: "${CHUNK_OVERLAP:=200}"
: "${SEARCH_LIMIT:=8}"
: "${PREFETCH_MULTIPLIER:=4}"
: "${MAX_PREFETCH:=24}"
: "${AUTO_SUMMARIZE_MEMORY:=true}"
: "${DEFAULT_ANSWER_TASK:=retrieval_answer}"
: "${REMEMBER_RATE_CAPACITY:=30}"
: "${REMEMBER_RATE_REFILL:=0.5}"
: "${CB_FAILURE_THRESHOLD:=3}"
: "${CB_RECOVERY_TIMEOUT:=120}"
: "${LOG_LEVEL:=INFO}"

: "${INSTALL_OLLAMA:=true}"
: "${OLLAMA_BASE_URL:=http://127.0.0.1:11434/v1}"
: "${OLLAMA_API_KEY:=ollama}"
: "${OLLAMA_CHAT_MODEL:=qwen2.5:7b}"
: "${OLLAMA_EMBED_MODEL:=nomic-embed-text}"

: "${LANGFUSE_INIT_ORG_ID:=my-org}"
: "${LANGFUSE_INIT_ORG_NAME:=My Organisation}"
: "${LANGFUSE_INIT_PROJECT_ID:=nanobot}"
: "${LANGFUSE_INIT_PROJECT_NAME:=nanobot}"

# --------------------------------------------------------------------------
# Export everything so envsubst and child processes can see them
# --------------------------------------------------------------------------
export DOMAIN NANOBOT_SUBDOMAIN RAG_SUBDOMAIN LANGFUSE_SUBDOMAIN WEBUI_SUBDOMAIN
export AUTHENTIK_OUTPOST_FQDN NANOBOT_DOMAIN RAG_DOMAIN LANGFUSE_DOMAIN WEBUI_DOMAIN
export APP_USER APP_GROUP BASE_DIR QDRANT_DATA_DIR QDRANT_CONFIG_DIR
export TRAEFIK_DYNAMIC_DIR LANGFUSE_DIR LOCAL_BIN_DIR
export NANOBOT_HOME RAG_HOME RAG_DOCS_DIR RAG_STATE_DIR MCP_HOME WEBUI_HOME VERSION_FILE
export NANOBOT_BIND NANOBOT_PORT RAG_BIND RAG_PORT
export QDRANT_BIND QDRANT_HTTP_PORT QDRANT_GRPC_PORT
export LANGFUSE_BIND LANGFUSE_WEB_PORT WEBUI_BIND WEBUI_PORT
export QDRANT_VERSION INSTALL_NANOBOT_WEBUI NANOBOT_DEFAULT_MODEL
export RERANKER_ENABLED RERANKER_MODEL RERANKER_DEVICE SPARSE_VECTORS_ENABLED
export EMBEDDING_CACHE_SIZE EMBEDDING_CACHE_TTL EMBEDDING_BATCH_SIZE
export MAX_CHUNK_CHARS CHUNK_OVERLAP SEARCH_LIMIT PREFETCH_MULTIPLIER MAX_PREFETCH
export AUTO_SUMMARIZE_MEMORY DEFAULT_ANSWER_TASK
export REMEMBER_RATE_CAPACITY REMEMBER_RATE_REFILL CB_FAILURE_THRESHOLD CB_RECOVERY_TIMEOUT
export LOG_LEVEL
export INSTALL_OLLAMA OLLAMA_BASE_URL OLLAMA_API_KEY OLLAMA_CHAT_MODEL OLLAMA_EMBED_MODEL
export LANGFUSE_INIT_ORG_ID LANGFUSE_INIT_ORG_NAME
export LANGFUSE_INIT_PROJECT_ID LANGFUSE_INIT_PROJECT_NAME

# --------------------------------------------------------------------------
# Build the envsubst variable list (only our deploy-time variables)
# --------------------------------------------------------------------------
# This ensures runtime shell variables ($HOST, $PORT, etc.) in script
# templates are left untouched — only our known variables are substituted.
ENVSUBST_VARS='${DOMAIN} ${NANOBOT_SUBDOMAIN} ${RAG_SUBDOMAIN} ${LANGFUSE_SUBDOMAIN} ${WEBUI_SUBDOMAIN}'
ENVSUBST_VARS+=' ${AUTHENTIK_OUTPOST_FQDN} ${NANOBOT_DOMAIN} ${RAG_DOMAIN} ${LANGFUSE_DOMAIN} ${WEBUI_DOMAIN}'
ENVSUBST_VARS+=' ${APP_USER} ${APP_GROUP} ${BASE_DIR} ${QDRANT_DATA_DIR} ${QDRANT_CONFIG_DIR}'
ENVSUBST_VARS+=' ${TRAEFIK_DYNAMIC_DIR} ${LANGFUSE_DIR} ${LOCAL_BIN_DIR}'
ENVSUBST_VARS+=' ${NANOBOT_HOME} ${RAG_HOME} ${RAG_DOCS_DIR} ${RAG_STATE_DIR} ${MCP_HOME} ${WEBUI_HOME} ${VERSION_FILE}'
ENVSUBST_VARS+=' ${NANOBOT_BIND} ${NANOBOT_PORT} ${RAG_BIND} ${RAG_PORT}'
ENVSUBST_VARS+=' ${QDRANT_BIND} ${QDRANT_HTTP_PORT} ${QDRANT_GRPC_PORT}'
ENVSUBST_VARS+=' ${LANGFUSE_BIND} ${LANGFUSE_WEB_PORT} ${WEBUI_BIND} ${WEBUI_PORT}'
ENVSUBST_VARS+=' ${QDRANT_VERSION} ${INSTALL_NANOBOT_WEBUI} ${NANOBOT_DEFAULT_MODEL}'
ENVSUBST_VARS+=' ${RERANKER_ENABLED} ${RERANKER_MODEL} ${RERANKER_DEVICE} ${SPARSE_VECTORS_ENABLED}'
ENVSUBST_VARS+=' ${EMBEDDING_CACHE_SIZE} ${EMBEDDING_CACHE_TTL} ${EMBEDDING_BATCH_SIZE}'
ENVSUBST_VARS+=' ${MAX_CHUNK_CHARS} ${CHUNK_OVERLAP} ${SEARCH_LIMIT} ${PREFETCH_MULTIPLIER} ${MAX_PREFETCH}'
ENVSUBST_VARS+=' ${AUTO_SUMMARIZE_MEMORY} ${DEFAULT_ANSWER_TASK}'
ENVSUBST_VARS+=' ${REMEMBER_RATE_CAPACITY} ${REMEMBER_RATE_REFILL} ${CB_FAILURE_THRESHOLD} ${CB_RECOVERY_TIMEOUT}'
ENVSUBST_VARS+=' ${LOG_LEVEL}'
ENVSUBST_VARS+=' ${INSTALL_OLLAMA} ${OLLAMA_BASE_URL} ${OLLAMA_API_KEY} ${OLLAMA_CHAT_MODEL} ${OLLAMA_EMBED_MODEL}'
ENVSUBST_VARS+=' ${LANGFUSE_INIT_ORG_ID} ${LANGFUSE_INIT_ORG_NAME} ${LANGFUSE_INIT_PROJECT_ID} ${LANGFUSE_INIT_PROJECT_NAME}'
export ENVSUBST_VARS

# --------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------
log()  { echo -e "\033[1;32m[+]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
err()  { echo -e "\033[1;31m[-]\033[0m $*" >&2; }
random_b64() { openssl rand -base64 "$1" | tr -d '\n'; }
random_hex() { openssl rand -hex "$1"; }

# Render a .template file by substituting only deploy-time variables.
# Runtime shell variables ($HOST, $PORT, etc.) are preserved.
# Usage: render_template input.template > output.file
render_template() {
  envsubst "$ENVSUBST_VARS" < "$1"
}

# Render a template and install it to a destination with given permissions.
# Usage: install_template src.template dest [mode]
install_template() {
  local src="$1" dest="$2" mode="${3:-0644}"
  render_template "$src" > "$dest"
  chmod "$mode" "$dest"
}

# Wait for an HTTP endpoint to return 200, with timeout.
# Usage: wait_for_health URL [timeout_seconds] [label]
wait_for_health() {
  local url="$1" timeout="${2:-60}" label="${3:-$1}"
  local elapsed=0
  while (( elapsed < timeout )); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      log "  ✓ ${label} is healthy"
      return 0
    fi
    sleep 2
    elapsed=$(( elapsed + 2 ))
  done
  warn "  ✗ ${label} did not become healthy within ${timeout}s"
  return 1
}

# Read the bridge token (if it exists).
load_bridge_token() {
  BRIDGE_TOKEN=""
  if [[ -f "$RAG_HOME/.bridge_token" ]]; then
    BRIDGE_TOKEN="$(cat "$RAG_HOME/.bridge_token")"
  fi
  export BRIDGE_TOKEN
}

# Read the qdrant API key (if it exists).
load_qdrant_key() {
  QDRANT_API_KEY=""
  if [[ -f "$QDRANT_CONFIG_DIR/.api_key" ]]; then
    QDRANT_API_KEY="$(cat "$QDRANT_CONFIG_DIR/.api_key")"
  fi
  export QDRANT_API_KEY
}

# Read current stack version (0 if not installed).
current_version() {
  if [[ -f "$VERSION_FILE" ]]; then
    cat "$VERSION_FILE"
  else
    echo "0"
  fi
}
