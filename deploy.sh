#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================================
# deploy.sh — nanobot stack v7 installer
# ============================================================================
# First-time install. For updates, use update.sh.
#
# Prerequisites:
#   1. Copy stack.env.example → stack.env and fill in your domain.
#   2. Run: sudo ./deploy.sh
#   3. Fill in API keys in the generated .env files.
#   4. Run: sudo systemctl restart nanobot-rag nanobot
#   5. Run: nanobot-stack-selftest
# ============================================================================

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

# --------------------------------------------------------------------------
# Preflight checks
# --------------------------------------------------------------------------
preflight_check() {
  log "Running preflight checks..."

  if [[ "$EUID" -ne 0 ]]; then
    err "Run this script as root."
    exit 1
  fi

  if [[ "$DOMAIN" == "example.com" ]]; then
    err "You must set DOMAIN in stack.env (still set to example.com)."
    exit 1
  fi

  # Validate domain format
  if ! echo "$DOMAIN" | grep -qP '^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)+$'; then
    err "Invalid DOMAIN format: '$DOMAIN'. Expected: yourdomain.com"
    exit 1
  fi

  # Disk space: need at least 5GB free on /opt
  local avail_kb
  avail_kb="$(df --output=avail /opt 2>/dev/null | tail -1 | tr -d ' ')"
  if [[ -n "$avail_kb" ]] && (( avail_kb < 5242880 )); then
    err "Less than 5GB free on /opt (${avail_kb}KB available). Aborting."
    exit 1
  fi

  # Port conflicts
  for p in "$NANOBOT_PORT" "$RAG_PORT" "$QDRANT_HTTP_PORT" "$QDRANT_GRPC_PORT" "$LANGFUSE_WEB_PORT"; do
    if ss -tlnp 2>/dev/null | grep -q ":${p} "; then
      warn "Port $p is already in use — may cause a conflict."
    fi
  done

  # Traefik
  if ! systemctl is-active --quiet traefik 2>/dev/null; then
    warn "Traefik is not running. Public endpoints won't work until it is started."
  else
    # Verify the 'websecure' entrypoint exists (our routes reference it)
    local traefik_ok=false
    for cfg in /etc/traefik/traefik.yml /etc/traefik/traefik.yaml /etc/traefik/traefik.toml; do
      if [[ -f "$cfg" ]] && grep -q "websecure" "$cfg" 2>/dev/null; then
        traefik_ok=true
        break
      fi
    done
    # Also check via API if Traefik dashboard is accessible
    if ! $traefik_ok; then
      if curl -fsS --max-time 3 "http://127.0.0.1:8080/api/entrypoints" 2>/dev/null | grep -q "websecure"; then
        traefik_ok=true
      fi
    fi
    if ! $traefik_ok; then
      warn "Traefik 'websecure' entrypoint not found. Our routes use entryPoints: [websecure]."
      warn "If your entrypoint is named differently, edit traefik/nanobot-stack.yaml.template."
    fi
  fi

  # DNS (non-blocking)
  if command -v host >/dev/null 2>&1; then
    for sub in "$NANOBOT_SUBDOMAIN" "$RAG_SUBDOMAIN" "$LANGFUSE_SUBDOMAIN" "$WEBUI_SUBDOMAIN"; do
      local fqdn="${sub}.${DOMAIN}"
      if ! host "$fqdn" >/dev/null 2>&1; then
        warn "DNS for ${fqdn} does not resolve."
      fi
    done
  fi

  # Re-install detection
  if [[ -f "$VERSION_FILE" ]]; then
    local current_version
    current_version="$(cat "$VERSION_FILE")"
    warn "Existing installation detected (v${current_version}). Consider using update.sh instead."
    read -rp "Continue with full deploy? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
      log "Aborted."
      exit 0
    fi
  fi

  log "Preflight checks passed."
}

# --------------------------------------------------------------------------
# OS packages
# --------------------------------------------------------------------------
install_base_packages() {
  log "Installing OS packages..."
  apt-get update -qq
  apt-get install -y -qq \
    ca-certificates curl wget gnupg lsb-release jq unzip tar \
    python3 python3-venv python3-pip python3-dev build-essential git \
    postgresql postgresql-contrib \
    libmagic1 pkg-config acl \
    gettext-base  # for envsubst

  # Install 'host' for DNS checks (may be in dnsutils or bind9-host)
  apt-get install -y -qq dnsutils 2>/dev/null || true

  if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    install -m 0755 /root/.local/bin/uv /usr/local/bin/uv
  fi
}

# --------------------------------------------------------------------------
# Docker (for Langfuse only)
# --------------------------------------------------------------------------
install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker already present."
    return
  fi
  log "Installing Docker Engine + compose plugin..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | tee /etc/apt/sources.list.d/docker.list >/dev/null
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
}

# --------------------------------------------------------------------------
# User and directories
# --------------------------------------------------------------------------
create_user_and_dirs() {
  log "Creating service user and directories..."
  if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --home "$BASE_DIR" --shell /usr/sbin/nologin "$APP_USER"
  fi

  mkdir -p \
    "$NANOBOT_HOME/config" "$NANOBOT_HOME/data" "$NANOBOT_HOME/workspace" \
    "$RAG_HOME" "$MCP_HOME" "$RAG_STATE_DIR" \
    "$RAG_DOCS_DIR/docs" "$RAG_DOCS_DIR/memory" "$RAG_DOCS_DIR/runbooks" \
    "$RAG_DOCS_DIR/projects" "$RAG_DOCS_DIR/conversations" \
    "$QDRANT_DATA_DIR" "$QDRANT_CONFIG_DIR" \
    "$TRAEFIK_DYNAMIC_DIR" \
    "$LANGFUSE_DIR/data/postgres" "$LANGFUSE_DIR/data/redis" \
    "$LANGFUSE_DIR/data/clickhouse" "$LANGFUSE_DIR/data/minio"

  chown -R "$APP_USER:$APP_GROUP" "$BASE_DIR" "$QDRANT_DATA_DIR"
}

# --------------------------------------------------------------------------
# Secrets generation (idempotent)
# --------------------------------------------------------------------------
generate_secrets() {
  log "Generating secrets (idempotent)..."

  # Bridge token
  if [[ ! -f "$RAG_HOME/.bridge_token" ]]; then
    random_hex 32 > "$RAG_HOME/.bridge_token"
    chown "$APP_USER:$APP_GROUP" "$RAG_HOME/.bridge_token"
    chmod 0600 "$RAG_HOME/.bridge_token"
    log "  → bridge token created"
  fi
  # Export for template rendering
  BRIDGE_TOKEN="$(cat "$RAG_HOME/.bridge_token")"
  export BRIDGE_TOKEN

  # Qdrant API key
  if [[ ! -f "$QDRANT_CONFIG_DIR/.api_key" ]]; then
    random_hex 24 > "$QDRANT_CONFIG_DIR/.api_key"
    chown "$APP_USER:$APP_GROUP" "$QDRANT_CONFIG_DIR/.api_key"
    chmod 0600 "$QDRANT_CONFIG_DIR/.api_key"
    log "  → qdrant API key created"
  fi
  QDRANT_API_KEY="$(cat "$QDRANT_CONFIG_DIR/.api_key")"
  export QDRANT_API_KEY

  # PostgreSQL
  if [[ ! -f "$RAG_HOME/postgres.env" ]]; then
    local pg_pass
    pg_pass="$(random_b64 24)"
    systemctl enable --now postgresql

    sudo -u postgres psql -q <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nanobot_rag') THEN
      CREATE ROLE nanobot_rag LOGIN PASSWORD '${pg_pass}';
   ELSE
      ALTER ROLE nanobot_rag WITH PASSWORD '${pg_pass}';
   END IF;
END
\$\$;
SQL
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='nanobot_rag'" | grep -q 1; then
      sudo -u postgres createdb -O nanobot_rag nanobot_rag
    fi

    cat > "$RAG_HOME/postgres.env" <<EOF
POSTGRES_DB=nanobot_rag
POSTGRES_USER=nanobot_rag
POSTGRES_PASSWORD=${pg_pass}
DATABASE_URL=postgresql://nanobot_rag:${pg_pass}@127.0.0.1:5432/nanobot_rag
EOF
    chown "$APP_USER:$APP_GROUP" "$RAG_HOME/postgres.env"
    chmod 0600 "$RAG_HOME/postgres.env"
    log "  → postgres credentials created"
  else
    systemctl enable --now postgresql
    log "  → postgres.env exists (skipped)"
  fi

  # Langfuse
  if [[ ! -f "$LANGFUSE_DIR/.env" ]]; then
    cat > "$LANGFUSE_DIR/.env" <<EOF
LANGFUSE_HOST=http://${LANGFUSE_BIND}:${LANGFUSE_WEB_PORT}
NEXTAUTH_URL=https://${LANGFUSE_DOMAIN}
NEXTAUTH_SECRET=$(random_hex 32)
SALT=$(random_hex 32)
ENCRYPTION_KEY=$(random_hex 32)
POSTGRES_DB=langfuse
POSTGRES_USER=langfuse
POSTGRES_PASSWORD=$(random_b64 24)
REDIS_AUTH=$(random_b64 18)
CLICKHOUSE_USER=langfuse
CLICKHOUSE_PASSWORD=$(random_b64 24)
MINIO_ROOT_USER=langfuse
MINIO_ROOT_PASSWORD=$(random_b64 24)
LANGFUSE_INIT_ORG_ID=${LANGFUSE_INIT_ORG_ID}
LANGFUSE_INIT_ORG_NAME=${LANGFUSE_INIT_ORG_NAME}
LANGFUSE_INIT_PROJECT_ID=${LANGFUSE_INIT_PROJECT_ID}
LANGFUSE_INIT_PROJECT_NAME=${LANGFUSE_INIT_PROJECT_NAME}
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=
LANGFUSE_INIT_PROJECT_SECRET_KEY=
EOF
    chmod 0600 "$LANGFUSE_DIR/.env"
    log "  → langfuse secrets created"
  else
    log "  → langfuse .env exists (skipped)"
  fi
}

# --------------------------------------------------------------------------
# Qdrant binary
# --------------------------------------------------------------------------
install_qdrant() {
  if [[ -x "$LOCAL_BIN_DIR/qdrant" ]]; then
    log "Qdrant binary already installed."
  else
    log "Installing Qdrant ${QDRANT_VERSION}..."
    local arch asset tmpdir
    arch="$(uname -m)"
    case "$arch" in
      x86_64)       asset="qdrant-x86_64-unknown-linux-gnu.tar.gz" ;;
      aarch64|arm64) asset="qdrant-aarch64-unknown-linux-gnu.tar.gz" ;;
      *) err "Unsupported architecture: $arch"; exit 1 ;;
    esac
    tmpdir="$(mktemp -d)"
    wget -qO "$tmpdir/qdrant.tgz" \
      "https://github.com/qdrant/qdrant/releases/download/${QDRANT_VERSION}/${asset}"
    tar -xzf "$tmpdir/qdrant.tgz" -C "$tmpdir"
    install -m 0755 "$tmpdir/qdrant" "$LOCAL_BIN_DIR/qdrant"
    rm -rf "$tmpdir"
  fi

  cat > "$QDRANT_CONFIG_DIR/config.yaml" <<EOF
log_level: INFO
service:
  host: ${QDRANT_BIND}
  http_port: ${QDRANT_HTTP_PORT}
  grpc_port: ${QDRANT_GRPC_PORT}
  api_key: "${QDRANT_API_KEY}"
storage:
  storage_path: "${QDRANT_DATA_DIR}"
EOF
  chown -R "$APP_USER:$APP_GROUP" "$QDRANT_CONFIG_DIR" "$QDRANT_DATA_DIR"
}

# --------------------------------------------------------------------------
# Deploy code files
# --------------------------------------------------------------------------
deploy_code() {
  log "Deploying code files..."

  # RAG bridge Python modules
  cp "$SCRIPT_DIR/src/bridge/app.py"              "$RAG_HOME/app.py"
  cp "$SCRIPT_DIR/src/bridge/circuit_breaker.py"   "$RAG_HOME/circuit_breaker.py"
  cp "$SCRIPT_DIR/src/bridge/rate_limiter.py"      "$RAG_HOME/rate_limiter.py"
  cp "$SCRIPT_DIR/src/bridge/reranker.py"          "$RAG_HOME/reranker.py"
  cp "$SCRIPT_DIR/src/bridge/embedding_cache.py"   "$RAG_HOME/embedding_cache.py"
  cp "$SCRIPT_DIR/src/bridge/audit.py"             "$RAG_HOME/audit.py"
  cp "$SCRIPT_DIR/src/bridge/extensions.py"        "$RAG_HOME/extensions.py"
  cp "$SCRIPT_DIR/src/bridge/conversation_memory.py" "$RAG_HOME/conversation_memory.py"
  cp "$SCRIPT_DIR/src/bridge/query_classifier.py"  "$RAG_HOME/query_classifier.py"
  cp "$SCRIPT_DIR/src/bridge/planner.py"           "$RAG_HOME/planner.py"
  cp "$SCRIPT_DIR/src/bridge/tools.py"             "$RAG_HOME/tools.py"
  cp "$SCRIPT_DIR/src/bridge/feedback.py"          "$RAG_HOME/feedback.py"
  cp "$SCRIPT_DIR/src/bridge/vision.py"            "$RAG_HOME/vision.py"
  cp "$SCRIPT_DIR/src/bridge/user_profile.py"      "$RAG_HOME/user_profile.py"
  cp "$SCRIPT_DIR/src/bridge/dashboard.py"         "$RAG_HOME/dashboard.py"
  cp "$SCRIPT_DIR/src/bridge/streaming.py"         "$RAG_HOME/streaming.py"
  cp "$SCRIPT_DIR/src/bridge/token_optimizer.py"   "$RAG_HOME/token_optimizer.py"
  cp "$SCRIPT_DIR/src/bridge/context_compression.py" "$RAG_HOME/context_compression.py"
  cp "$SCRIPT_DIR/src/bridge/requirements.txt"     "$RAG_HOME/requirements.txt"

  # Model router (preserve user customisations)
  if [[ ! -f "$RAG_HOME/model_router.json" ]]; then
    cp "$SCRIPT_DIR/src/config/model_router.json"  "$RAG_HOME/model_router.json"
  else
    log "  → model_router.json preserved (already exists)"
  fi

  # RAG bridge .env (only on first deploy)
  if [[ ! -f "$RAG_HOME/.env" ]]; then
    cat > "$RAG_HOME/.env" <<EOF
STATE_DIR=${RAG_STATE_DIR}
DOCS_DIR=${RAG_DOCS_DIR}
QDRANT_URL=http://${QDRANT_BIND}:${QDRANT_HTTP_PORT}
QDRANT_API_KEY=${QDRANT_API_KEY}
MODEL_ROUTER_FILE=${RAG_HOME}/model_router.json
RAG_BRIDGE_TOKEN=${BRIDGE_TOKEN}
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LANGFUSE_HOST=http://${LANGFUSE_BIND}:${LANGFUSE_WEB_PORT}
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
SEARCH_LIMIT=${SEARCH_LIMIT}
PREFETCH_MULTIPLIER=${PREFETCH_MULTIPLIER}
MAX_PREFETCH=${MAX_PREFETCH}
MAX_CHUNK_CHARS=${MAX_CHUNK_CHARS}
CHUNK_OVERLAP=${CHUNK_OVERLAP}
AUTO_SUMMARIZE_MEMORY=${AUTO_SUMMARIZE_MEMORY}
DEFAULT_ANSWER_TASK=${DEFAULT_ANSWER_TASK}
RERANKER_ENABLED=${RERANKER_ENABLED}
RERANKER_MODEL=${RERANKER_MODEL}
RERANKER_DEVICE=${RERANKER_DEVICE}
SPARSE_VECTORS_ENABLED=${SPARSE_VECTORS_ENABLED}
EMBEDDING_CACHE_SIZE=${EMBEDDING_CACHE_SIZE}
EMBEDDING_CACHE_TTL=${EMBEDDING_CACHE_TTL}
EMBEDDING_BATCH_SIZE=${EMBEDDING_BATCH_SIZE}
REMEMBER_RATE_CAPACITY=${REMEMBER_RATE_CAPACITY}
REMEMBER_RATE_REFILL=${REMEMBER_RATE_REFILL}
CB_FAILURE_THRESHOLD=${CB_FAILURE_THRESHOLD}
CB_RECOVERY_TIMEOUT=${CB_RECOVERY_TIMEOUT}
LOG_LEVEL=${LOG_LEVEL}
VISION_ENABLED=true
VISION_MAX_IMAGES_PER_DOC=5
VISION_MIN_IMAGE_BYTES=5000
SHELL_TIMEOUT=15
WEB_FETCH_TIMEOUT=30
WEB_FETCH_MAX_CHARS=15000
NOTIFICATION_WEBHOOK_URL=
FEEDBACK_BOOST_WEIGHT=0.1
FEEDBACK_MAX_BOOST=0.5
FEEDBACK_MIN_BOOST=-0.3
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_API_KEY=ollama
EOF
    chmod 0600 "$RAG_HOME/.env"
  fi

  # MCP server
  cp "$SCRIPT_DIR/src/mcp/rag_mcp_server.py" "$MCP_HOME/rag_mcp_server.py"
  cp "$SCRIPT_DIR/src/mcp/requirements.txt"   "$MCP_HOME/requirements.txt"
  if [[ ! -f "$MCP_HOME/.env" ]]; then
    cat > "$MCP_HOME/.env" <<EOF
RAG_BRIDGE_URL=http://${RAG_BIND}:${RAG_PORT}
RAG_BRIDGE_TOKEN=${BRIDGE_TOKEN}
EOF
    chmod 0600 "$MCP_HOME/.env"
  fi

  # Nanobot config
  cp "$SCRIPT_DIR/src/config/NANOBOT_POLICY_PROMPT.md" \
     "$NANOBOT_HOME/config/NANOBOT_POLICY_PROMPT.md"

  if [[ ! -f "$NANOBOT_HOME/config/.env" ]]; then
    cat > "$NANOBOT_HOME/config/.env" <<EOF
HOST=${NANOBOT_BIND}
PORT=${NANOBOT_PORT}
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
GITHUB_TOKEN=
RAG_BRIDGE_URL=http://${RAG_BIND}:${RAG_PORT}
RAG_BRIDGE_TOKEN=${BRIDGE_TOKEN}
QDRANT_URL=http://${QDRANT_BIND}:${QDRANT_HTTP_PORT}
NANOBOT_POLICY_FILE=${NANOBOT_HOME}/config/NANOBOT_POLICY_PROMPT.md
NANOBOT_CONFIG_FILE=${NANOBOT_HOME}/config/config.json
EOF
    chmod 0600 "$NANOBOT_HOME/config/.env"
  fi

  if [[ ! -f "$NANOBOT_HOME/config/config.json" ]]; then
    cat > "$NANOBOT_HOME/config/config.json" <<EOFCFG
{
  "agents": {
    "defaults": {
      "workspace": "${NANOBOT_HOME}/workspace",
      "model": "${NANOBOT_DEFAULT_MODEL}",
      "systemPromptFile": "${NANOBOT_HOME}/config/NANOBOT_POLICY_PROMPT.md",
      "stream": true
    }
  },
  "gateway": {
    "host": "${NANOBOT_BIND}",
    "port": ${NANOBOT_PORT},
    "stream": true
  },
  "tools": {
    "restrictToWorkspace": true,
    "mcpServers": {
      "ragbridge": {
        "command": "${MCP_HOME}/.venv/bin/python",
        "args": ["${MCP_HOME}/rag_mcp_server.py"],
        "env": {
          "RAG_BRIDGE_URL": "http://${RAG_BIND}:${RAG_PORT}",
          "RAG_BRIDGE_TOKEN": "${BRIDGE_TOKEN}"
        },
        "toolTimeout": 90,
        "enabledTools": ["*"]
      }
    }
  }
}
EOFCFG
    chmod 0640 "$NANOBOT_HOME/config/config.json"
  fi

  # Langfuse docker-compose (static, no secrets — .env file is separate)
  cp "$SCRIPT_DIR/src/config/langfuse-docker-compose.yml" "$LANGFUSE_DIR/docker-compose.yml"

  # Fix ownership
  chown -R "$APP_USER:$APP_GROUP" "$BASE_DIR"
}

# --------------------------------------------------------------------------
# Deploy rendered templates (systemd, traefik, scripts)
# --------------------------------------------------------------------------
deploy_templates() {
  log "Rendering and installing templates..."

  # Systemd units
  for tpl in "$SCRIPT_DIR"/systemd/*.template; do
    local name
    name="$(basename "$tpl" .template)"
    install_template "$tpl" "/etc/systemd/system/${name}"
  done
  systemctl daemon-reload

  # Traefik dynamic config
  for tpl in "$SCRIPT_DIR"/traefik/*.template; do
    local name
    name="$(basename "$tpl" .template)"
    install_template "$tpl" "${TRAEFIK_DYNAMIC_DIR}/${name}"
  done

  # Helper scripts
  for tpl in "$SCRIPT_DIR"/scripts/*.template; do
    local name
    name="$(basename "$tpl" .template)"
    name="${name%.sh}"  # remove .sh suffix if present to get clean command name
    install_template "$tpl" "${LOCAL_BIN_DIR}/${name}" 0755
  done

  # Rotate-secrets (not a template, just a direct copy that sources lib.sh)
  install -m 0755 "$SCRIPT_DIR/rotate-secrets.sh" "$LOCAL_BIN_DIR/nanobot-rotate-secrets"
}

# --------------------------------------------------------------------------
# Install Python venvs
# --------------------------------------------------------------------------
install_venvs() {
  log "Installing Python venvs..."

  # Nanobot
  if [[ ! -d "$NANOBOT_HOME/.venv" ]]; then
    sudo -u "$APP_USER" bash -c "
      set -Eeuo pipefail
      cd '$NANOBOT_HOME'
      python3 -m venv .venv
      source .venv/bin/activate
      pip install -q --upgrade pip wheel setuptools
      pip install -q nanobot-ai
    "
    log "  → nanobot venv created"
  fi

  # RAG bridge
  sudo -u "$APP_USER" bash -c "
    set -Eeuo pipefail
    cd '$RAG_HOME'
    python3 -m venv .venv 2>/dev/null || true
    source .venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
  "
  log "  → rag-bridge venv ready"

  # MCP server
  sudo -u "$APP_USER" bash -c "
    set -Eeuo pipefail
    cd '$MCP_HOME'
    python3 -m venv .venv 2>/dev/null || true
    source .venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
  "
  log "  → mcp venv ready"

  # Optional WebUI
  if [[ "$INSTALL_NANOBOT_WEBUI" == "true" ]]; then
    mkdir -p "$WEBUI_HOME"
    chown "$APP_USER:$APP_GROUP" "$WEBUI_HOME"
    if [[ ! -d "$WEBUI_HOME/.venv" ]]; then
      sudo -u "$APP_USER" bash -c "
        set -Eeuo pipefail
        cd '$WEBUI_HOME'
        python3 -m venv .venv
        source .venv/bin/activate
        pip install -q --upgrade pip
        pip install -q nanobot-webui
      "
      log "  → webui venv created"
    fi
  fi
}

# --------------------------------------------------------------------------
# Ollama (local LLM fallback, CPU-optimised)
# --------------------------------------------------------------------------
install_ollama() {
  if [[ "$INSTALL_OLLAMA" != "true" ]]; then
    warn "Skipping Ollama (INSTALL_OLLAMA != true)."
    return
  fi

  if command -v ollama >/dev/null 2>&1; then
    log "Ollama already installed: $(ollama --version 2>/dev/null || echo 'unknown')"
  else
    log "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
  fi

  # Ensure the systemd service is enabled
  systemctl enable --now ollama 2>/dev/null || true

  # Wait for Ollama to be ready
  log "Waiting for Ollama to start..."
  local retries=0
  while ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; do
    retries=$((retries + 1))
    if (( retries > 30 )); then
      warn "Ollama did not start within 30s. Models will be pulled on next restart."
      return
    fi
    sleep 1
  done

  # Pull models only if not already present
  local models_list
  models_list=$(ollama list 2>/dev/null || echo "")
  for model in "$OLLAMA_CHAT_MODEL" "$OLLAMA_EMBED_MODEL"; do
    if echo "$models_list" | grep -q "$(echo "$model" | cut -d: -f1)"; then
      log "  $model already present — skipping pull"
    else
      log "  Pulling $model (this may take a few minutes)..."
      ollama pull "$model" || warn "Failed to pull $model"
    fi
  done

  log "Ollama ready: chat=$OLLAMA_CHAT_MODEL, embed=$OLLAMA_EMBED_MODEL"
}

# --------------------------------------------------------------------------
# Enable and start services (with health waiting)
# --------------------------------------------------------------------------
enable_services() {
  log "Enabling and starting services..."
  systemctl enable --now qdrant
  wait_for_health "http://${QDRANT_BIND}:${QDRANT_HTTP_PORT}/healthz" 20 "Qdrant"

  systemctl enable --now langfuse-compose.service
  wait_for_health "http://${LANGFUSE_BIND}:${LANGFUSE_WEB_PORT}" 60 "Langfuse" || true

  systemctl enable --now nanobot-rag
  wait_for_health "http://${RAG_BIND}:${RAG_PORT}/healthz" 30 "RAG bridge"

  systemctl enable --now nanobot-rag-ingest.timer
  systemctl enable nanobot-rag-mcp-health.service  # enable only, not --now

  systemctl enable --now nanobot
  sleep 3
  if systemctl is-active --quiet nanobot; then
    log "  ✓ nanobot"
  else
    warn "  ✗ nanobot may not have started — check: journalctl -u nanobot -n 30"
  fi

  if [[ "$INSTALL_NANOBOT_WEBUI" == "true" ]]; then
    systemctl enable --now nanobot-webui
    sleep 2
    if systemctl is-active --quiet nanobot-webui; then
      log "  ✓ nanobot-webui"
    fi
  fi
}

# --------------------------------------------------------------------------
# Post-deploy: interactive key setup
# --------------------------------------------------------------------------
post_deploy_setup() {
  load_bridge_token

  echo ""
  log "Services are running. Now let's configure your API keys."
  echo ""
  read -rp "Set up API keys interactively now? [Y/n] " answer
  if [[ "$answer" != "n" && "$answer" != "N" ]]; then
    nanobot-setup-keys
    log "Restarting services with new keys..."
    nanobot-stack-restart
  else
    warn "Skipped. Run 'sudo nanobot-setup-keys' later to configure API keys."
  fi
}

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
show_summary() {
  load_bridge_token
  local token_display="${BRIDGE_TOKEN:0:8}..."

  cat <<EOF

========================================================
Deployment complete — nanobot stack v8
========================================================

Domain:  ${DOMAIN}
Version: $(current_version)

Local listeners:
  - nanobot:   http://${NANOBOT_BIND}:${NANOBOT_PORT}
  - rag:       http://${RAG_BIND}:${RAG_PORT}
  - qdrant:    http://${QDRANT_BIND}:${QDRANT_HTTP_PORT}  (API-key protected)
  - langfuse:  http://${LANGFUSE_BIND}:${LANGFUSE_WEB_PORT}
  - dashboard: http://${RAG_BIND}:${RAG_PORT}/dashboard?token=${BRIDGE_TOKEN}

Public endpoints (Traefik + Authentik):
  - ${NANOBOT_DOMAIN}
  - ${RAG_DOMAIN}
  - ${LANGFUSE_DOMAIN}
  - ${WEBUI_DOMAIN}$(if [[ "$INSTALL_NANOBOT_WEBUI" != "true" ]]; then echo "  (disabled)"; fi)

Bridge token: ${token_display}
  Full token:  cat ${RAG_HOME}/.bridge_token

Useful commands:
  nanobot-stack-selftest    — verify everything works
  nanobot-stack-restart     — ordered restart with health checks
  nanobot-setup-keys        — interactive API key configuration
  nanobot-rotate-secrets    — rotate internal secrets
  nanobot-rag-ingest        — trigger document ingestion now

EOF
}

# --------------------------------------------------------------------------
# Dry-run mode
# --------------------------------------------------------------------------
show_dry_run() {
  cat <<EOF
========================================================
DRY-RUN — deploy.sh would perform these actions:
========================================================

System:
  - Install packages: python3, postgresql, docker, etc.
  - Create user: ${APP_USER}
  - Create directories under ${BASE_DIR}

Secrets:
  - Generate bridge token, Qdrant API key, PostgreSQL password, Langfuse secrets
  - (skipped if already present — idempotent)

Components:
  - Install Qdrant binary ${QDRANT_VERSION} to ${LOCAL_BIN_DIR}/qdrant
  - Deploy Python code to ${RAG_HOME}/ ($(find "$SCRIPT_DIR/src/bridge" -name '*.py' | wc -l) modules)
  - Deploy MCP server to ${MCP_HOME}/
  - Create Python venvs and install dependencies
$(if [[ "$INSTALL_OLLAMA" == "true" ]]; then
  echo "  - Install Ollama + pull ${OLLAMA_CHAT_MODEL} and ${OLLAMA_EMBED_MODEL}"
fi)
$(if [[ "$INSTALL_NANOBOT_WEBUI" == "true" ]]; then
  echo "  - Install nanobot-webui"
fi)

Templates rendered to:
  - /etc/systemd/system/  (8 unit files)
  - ${TRAEFIK_DYNAMIC_DIR}/  (2 yaml files)
  - ${LOCAL_BIN_DIR}/  (6 helper scripts)

Services started:
  qdrant → langfuse → nanobot-rag → nanobot$(if [[ "$INSTALL_NANOBOT_WEBUI" == "true" ]]; then echo " → nanobot-webui"; fi)

Domain: ${DOMAIN}
Subdomains: ${NANOBOT_DOMAIN}, ${RAG_DOMAIN}, ${LANGFUSE_DOMAIN}, ${WEBUI_DOMAIN}

EOF
}

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
DRY_RUN=false
SKIP_INTERACTIVE=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --non-interactive) SKIP_INTERACTIVE=true ;;
    --help|-h)
      echo "Usage: sudo ./deploy.sh [--dry-run] [--non-interactive]"
      echo "  --dry-run          Show what would be done without making changes"
      echo "  --non-interactive  Skip interactive API key setup after deployment"
      exit 0
      ;;
  esac
done

main() {
  preflight_check

  if $DRY_RUN; then
    show_dry_run
    exit 0
  fi

  install_base_packages
  install_docker
  create_user_and_dirs
  generate_secrets
  install_qdrant
  deploy_code
  deploy_templates
  install_venvs
  install_ollama
  enable_services
  echo "8" > "$VERSION_FILE"
  chown "$APP_USER:$APP_GROUP" "$VERSION_FILE"
  show_summary

  if ! $SKIP_INTERACTIVE; then
    post_deploy_setup
  fi
}

main
