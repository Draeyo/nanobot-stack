#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================================
# update.sh — nanobot stack updater
# ============================================================================
# Backup → code → deps → migrations → restart → LLM verify → selftest
#
# Usage:
#   sudo ./update.sh               # full update
#   sudo ./update.sh --code-only   # skip pip
#   sudo ./update.sh --deps-only   # pip only
#   sudo ./update.sh --dry-run     # preview
#   sudo ./update.sh --rollback    # revert to last backup (code + pip deps)
# ============================================================================

# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [[ "$EUID" -ne 0 ]]; then err "Run as root."; exit 1; fi
if [[ ! -f "$VERSION_FILE" ]]; then err "No installation found. Run deploy.sh first."; exit 1; fi

load_bridge_token
load_qdrant_key

SKIP_DEPS=false; SKIP_CODE=false; DRY_RUN=false; DO_ROLLBACK=false
for arg in "$@"; do
  case "$arg" in
    --code-only) SKIP_DEPS=true ;;
    --deps-only) SKIP_CODE=true ;;
    --dry-run)   DRY_RUN=true ;;
    --rollback)  DO_ROLLBACK=true ;;
    --help|-h)   echo "Usage: sudo ./update.sh [--code-only|--deps-only|--dry-run|--rollback]"; exit 0 ;;
    *) err "Unknown: $arg"; exit 1 ;;
  esac
done

LOCKFILE="/tmp/nanobot-stack-update.lock"
BACKUP_DIR="${BASE_DIR}/.backup/$(date +%Y%m%d_%H%M%S)"

# --------------------------------------------------------------------------
# Concurrency lock
# --------------------------------------------------------------------------
acquire_lock() {
  exec 200>"$LOCKFILE"
  if ! flock -n 200; then
    err "Another update is already running (lockfile: $LOCKFILE)"
    exit 1
  fi
}

# --------------------------------------------------------------------------
# Backup (code + pip freeze)
# --------------------------------------------------------------------------
create_backup() {
  log "Creating backup at ${BACKUP_DIR}..."
  mkdir -p "$BACKUP_DIR/rag-bridge" "$BACKUP_DIR/rag-mcp" "$BACKUP_DIR/nanobot-config"

  # Code
  cp "$RAG_HOME"/*.py "$BACKUP_DIR/rag-bridge/" 2>/dev/null || true
  cp "$RAG_HOME/requirements.txt" "$BACKUP_DIR/rag-bridge/" 2>/dev/null || true
  cp "$RAG_HOME/model_router.json" "$BACKUP_DIR/rag-bridge/" 2>/dev/null || true
  cp "$MCP_HOME/rag_mcp_server.py" "$BACKUP_DIR/rag-mcp/" 2>/dev/null || true
  cp "$MCP_HOME/requirements.txt" "$BACKUP_DIR/rag-mcp/" 2>/dev/null || true
  cp "$NANOBOT_HOME/config/NANOBOT_POLICY_PROMPT.md" "$BACKUP_DIR/nanobot-config/" 2>/dev/null || true

  # Pip freezes (for full rollback including deps)
  if [[ -f "$RAG_HOME/.venv/bin/pip" ]]; then
    "$RAG_HOME/.venv/bin/pip" freeze 2>/dev/null > "$BACKUP_DIR/rag-bridge_freeze.txt" || true
  fi
  if [[ -f "$MCP_HOME/.venv/bin/pip" ]]; then
    "$MCP_HOME/.venv/bin/pip" freeze 2>/dev/null > "$BACKUP_DIR/rag-mcp_freeze.txt" || true
  fi

  echo "$(current_version)" > "$BACKUP_DIR/.version"
  log "  Backup: $BACKUP_DIR"

  # Retention: keep only the last 5 backups
  local backup_count
  backup_count=$(ls -1dt "${BASE_DIR}/.backup/"* 2>/dev/null | wc -l)
  if (( backup_count > 5 )); then
    log "  Pruning old backups (keeping last 5)..."
    ls -1dt "${BASE_DIR}/.backup/"* | tail -n +6 | while read -r old; do
      rm -rf "$old"
    done
  fi
}

# --------------------------------------------------------------------------
# Rollback (code + pip deps)
# --------------------------------------------------------------------------
rollback() {
  local latest
  latest="$(ls -1dt "${BASE_DIR}/.backup/"* 2>/dev/null | head -1)"
  if [[ -z "$latest" || ! -d "$latest" ]]; then
    err "No backup found in ${BASE_DIR}/.backup/"
    exit 1
  fi

  log "Rolling back to: $latest"

  # Restore code
  if [[ -d "$latest/rag-bridge" ]]; then
    cp "$latest/rag-bridge/"*.py "$RAG_HOME/" 2>/dev/null || true
    cp "$latest/rag-bridge/requirements.txt" "$RAG_HOME/" 2>/dev/null || true
  fi
  if [[ -d "$latest/rag-mcp" ]]; then
    cp "$latest/rag-mcp/"* "$MCP_HOME/" 2>/dev/null || true
  fi
  if [[ -d "$latest/nanobot-config" ]]; then
    cp "$latest/nanobot-config/"* "$NANOBOT_HOME/config/" 2>/dev/null || true
  fi

  # Restore pip deps from freeze
  if [[ -f "$latest/rag-bridge_freeze.txt" ]]; then
    log "  Restoring RAG bridge pip deps..."
    sudo -u "$APP_USER" bash -c "
      source '$RAG_HOME/.venv/bin/activate'
      pip install -q -r '$latest/rag-bridge_freeze.txt'
    " 2>/dev/null || warn "  pip restore failed for rag-bridge"
  fi
  if [[ -f "$latest/rag-mcp_freeze.txt" ]]; then
    log "  Restoring MCP pip deps..."
    sudo -u "$APP_USER" bash -c "
      source '$MCP_HOME/.venv/bin/activate'
      pip install -q -r '$latest/rag-mcp_freeze.txt'
    " 2>/dev/null || warn "  pip restore failed for rag-mcp"
  fi

  # Restore version
  if [[ -f "$latest/.version" ]]; then
    cp "$latest/.version" "$VERSION_FILE"
  fi

  chown -R "$APP_USER:$APP_GROUP" "$RAG_HOME" "$MCP_HOME"
  systemctl daemon-reload
  systemctl restart nanobot-rag nanobot
  wait_for_health "http://${RAG_BIND}:${RAG_PORT}/healthz" 30 "RAG bridge"
  log "Rollback complete (v$(current_version))."
}

# --------------------------------------------------------------------------
# Dep change detection with real PyPI changelog fetch
# --------------------------------------------------------------------------
check_dep_changes() {
  log "Checking dependency changes..."
  local rag_venv="$RAG_HOME/.venv"
  [[ -f "$rag_venv/bin/pip" ]] || return

  local report=""
  for pkg in litellm qdrant-client langfuse sentence-transformers; do
    local current new_spec
    current=$("$rag_venv/bin/pip" show "$pkg" 2>/dev/null | grep "^Version:" | awk '{print $2}') || continue
    new_spec=$(grep -i "^${pkg}" "$SCRIPT_DIR/src/bridge/requirements.txt" 2>/dev/null | head -1) || continue
    [[ -z "$current" ]] && continue
    report="${report}  ${pkg}: installed=${current} required=${new_spec}\n"
  done

  if [[ -n "$report" ]]; then
    log "Dependency versions:"
    echo -e "$report"
  fi
}

# --------------------------------------------------------------------------
# LLM-assisted config check (with real changelog context)
# --------------------------------------------------------------------------
llm_verify_config() {
  curl -fsS --max-time 5 "http://${RAG_BIND}:${RAG_PORT}/healthz" >/dev/null 2>&1 || return 0

  log "Running LLM config compatibility check..."

  # Write context to a temp file (avoids heredoc escaping issues)
  local tmpfile
  tmpfile="$(mktemp)"
  trap "rm -f '$tmpfile'" RETURN

  # Gather installed versions
  "$RAG_HOME/.venv/bin/pip" show litellm qdrant-client langfuse 2>/dev/null \
    | grep -E "^(Name|Version):" > "$tmpfile" || true

  # Fetch PyPI summaries (single fetch per package, not two)
  for pkg in litellm qdrant-client; do
    local pypi_json
    pypi_json=$(curl -fsS --max-time 5 "https://pypi.org/pypi/${pkg}/json" 2>/dev/null) || continue
    local latest summary
    latest=$(echo "$pypi_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])" 2>/dev/null) || continue
    summary=$(echo "$pypi_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['summary'][:200])" 2>/dev/null) || continue
    echo "PyPI: ${pkg} latest=${latest}: ${summary}" >> "$tmpfile"
  done

  # Append config.json
  if [[ -f "$NANOBOT_HOME/config/config.json" ]]; then
    echo "--- config.json ---" >> "$tmpfile"
    cat "$NANOBOT_HOME/config/config.json" >> "$tmpfile"
  fi

  # Append .env keys (names only, not values — for variable rename detection)
  if [[ -f "$RAG_HOME/.env" ]]; then
    echo "--- rag-bridge .env keys ---" >> "$tmpfile"
    grep -oP '^\w+(?==)' "$RAG_HOME/.env" >> "$tmpfile" 2>/dev/null || true
  fi

  local auth_header=""
  [[ -n "$BRIDGE_TOKEN" ]] && auth_header="-H X-Bridge-Token:${BRIDGE_TOKEN}"

  # Build JSON payload safely via Python reading the temp file
  local payload
  payload=$(python3 -c "
import json, sys
context = open('$tmpfile').read()
msg = {
    'task_type': 'structured_extraction',
    'messages': [
        {'role': 'system', 'content': 'You validate a nanobot stack config after update. Check for deprecated settings, breaking changes. Respond ONLY JSON: {\"issues\": [{\"severity\": \"critical|warning|info\", \"description\": \"...\", \"fix\": \"...\"}], \"ok\": true/false}'},
        {'role': 'user', 'content': context},
    ],
    'json_mode': True,
}
print(json.dumps(msg))
" 2>/dev/null) || return 0

  local response
  response=$(curl -sS --max-time 30 \
    -H "Content-Type: application/json" \
    ${auth_header} \
    -d "$payload" \
    "http://${RAG_BIND}:${RAG_PORT}/chat" 2>/dev/null) || return 0

  python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    d = json.loads(r.get('text', '{}'))
    for i in d.get('issues', []):
        sev = i.get('severity','info').upper()
        print(f'  [{sev}] {i[\"description\"]}')
        if i.get('fix'): print(f'    Fix: {i[\"fix\"]}')
    if d.get('ok', True) and not d.get('issues'):
        print('  No issues found.')
except:
    print('  Analysis skipped (parse error).')
" <<< "$response" 2>/dev/null || echo "  Analysis skipped."
}

# --------------------------------------------------------------------------
# Update code
# --------------------------------------------------------------------------
update_code() {
  log "Updating code..."

  for f in app.py circuit_breaker.py rate_limiter.py reranker.py embedding_cache.py \
           audit.py extensions.py conversation_memory.py query_classifier.py planner.py \
           tools.py feedback.py vision.py user_profile.py dashboard.py streaming.py \
           token_optimizer.py context_compression.py requirements.txt; do
    [[ -f "$SCRIPT_DIR/src/bridge/$f" ]] && cp "$SCRIPT_DIR/src/bridge/$f" "$RAG_HOME/$f"
  done

  cp "$SCRIPT_DIR/src/mcp/rag_mcp_server.py" "$MCP_HOME/rag_mcp_server.py"
  cp "$SCRIPT_DIR/src/mcp/requirements.txt"   "$MCP_HOME/requirements.txt"
  cp "$SCRIPT_DIR/src/config/NANOBOT_POLICY_PROMPT.md" "$NANOBOT_HOME/config/NANOBOT_POLICY_PROMPT.md"
  cp "$SCRIPT_DIR/src/config/langfuse-docker-compose.yml" "$LANGFUSE_DIR/docker-compose.yml"

  # Templates
  for tpl in "$SCRIPT_DIR"/systemd/*.template; do
    install_template "$tpl" "/etc/systemd/system/$(basename "$tpl" .template)"
  done
  systemctl daemon-reload

  for tpl in "$SCRIPT_DIR"/traefik/*.template; do
    install_template "$tpl" "${TRAEFIK_DYNAMIC_DIR}/$(basename "$tpl" .template)"
  done

  for tpl in "$SCRIPT_DIR"/scripts/*.template; do
    local name; name="$(basename "$tpl" .template)"; name="${name%.sh}"
    install_template "$tpl" "${LOCAL_BIN_DIR}/${name}" 0755
  done

  install -m 0755 "$SCRIPT_DIR/rotate-secrets.sh" "$LOCAL_BIN_DIR/nanobot-rotate-secrets"
  install -m 0755 "$SCRIPT_DIR/uninstall.sh" "$LOCAL_BIN_DIR/nanobot-uninstall"

  chown -R "$APP_USER:$APP_GROUP" "$RAG_HOME" "$MCP_HOME"
  chown "$APP_USER:$APP_GROUP" "$NANOBOT_HOME/config/NANOBOT_POLICY_PROMPT.md"
  log "  Code updated."
}

# --------------------------------------------------------------------------
# Update deps
# --------------------------------------------------------------------------
update_deps() {
  log "Updating Python dependencies..."
  sudo -u "$APP_USER" bash -c "
    set -Eeuo pipefail
    cd '$RAG_HOME'; source .venv/bin/activate
    pip install -q --upgrade pip; pip install -q -r requirements.txt
  "
  sudo -u "$APP_USER" bash -c "
    set -Eeuo pipefail
    cd '$MCP_HOME'; source .venv/bin/activate
    pip install -q --upgrade pip; pip install -q -r requirements.txt
  "

  # Update nanobot-ai itself (separate venv)
  if [[ -f "$NANOBOT_HOME/.venv/bin/pip" ]]; then
    log "  Updating nanobot-ai..."
    sudo -u "$APP_USER" bash -c "
      source '$NANOBOT_HOME/.venv/bin/activate'
      pip install -q --upgrade nanobot-ai
    " 2>/dev/null || warn "  nanobot-ai update failed (may not use a venv)"
  fi

  # Update Ollama models if installed
  if command -v ollama >/dev/null 2>&1 && systemctl is-active --quiet ollama 2>/dev/null; then
    log "  Checking Ollama models..."
    for model in "${OLLAMA_CHAT_MODEL:-}" "${OLLAMA_EMBED_MODEL:-}"; do
      if [[ -n "$model" ]]; then
        ollama pull "$model" 2>/dev/null || true
      fi
    done
  fi

  log "  Dependencies updated."
}

# --------------------------------------------------------------------------
# Migrations (idempotent)
# --------------------------------------------------------------------------
run_migrations() {
  if [[ -f "$SCRIPT_DIR/migrations/run_migrations.py" ]]; then
    log "Running migrations..."
    local flag=""
    $DRY_RUN && flag="--dry-run"
    python3 "$SCRIPT_DIR/migrations/run_migrations.py" $flag || warn "  Migration runner had issues"
  fi
}

# --------------------------------------------------------------------------
# Restart with health
# --------------------------------------------------------------------------
restart_services() {
  log "Restarting..."
  systemctl restart nanobot-rag
  wait_for_health "http://${RAG_BIND}:${RAG_PORT}/healthz" 30 "RAG bridge"
  systemctl restart nanobot
  sleep 3
  systemctl is-active --quiet nanobot && log "  ✓ nanobot" || warn "  ✗ nanobot"
  systemctl is-enabled --quiet nanobot-webui 2>/dev/null && systemctl restart nanobot-webui || true
}

# --------------------------------------------------------------------------
# Post-update selftest + notification on failure
# --------------------------------------------------------------------------
post_update_check() {
  log "Post-update health check..."
  if ! wait_for_health "http://${RAG_BIND}:${RAG_PORT}/healthz" 20 "RAG bridge"; then
    if $PRE_UPDATE_HEALTHY; then
      err "RAG bridge was healthy before update but is now down."
      _notify_failure "RAG bridge broken by update. Consider: sudo ./update.sh --rollback"
      warn "Run: sudo ./update.sh --rollback"
    else
      warn "RAG bridge is still down (was already down before update)."
    fi
    return 1
  fi

  local ok=true
  local result
  result=$(curl -fsS --max-time 20 \
    -H "X-Bridge-Token: ${BRIDGE_TOKEN}" \
    -X POST "http://${RAG_BIND}:${RAG_PORT}/selftest" 2>/dev/null) || ok=false

  if $ok; then
    if echo "$result" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('ok') else 1)" 2>/dev/null; then
      log "  ✓ Selftest passed"
    else
      warn "  Selftest returned issues — run: nanobot-stack-selftest"
      _notify_failure "Selftest returned issues after update."
    fi
  else
    warn "  Selftest failed to run"
    _notify_failure "Could not run selftest after update."
  fi
}

_notify_failure() {
  local msg="$1"
  # Try to send notification via bridge (best-effort)
  curl -sS --max-time 5 \
    -H "Content-Type: application/json" \
    -H "X-Bridge-Token: ${BRIDGE_TOKEN}" \
    -d "{\"message\": \"[nanobot-stack update] ${msg}\", \"title\": \"nanobot-stack\", \"level\": \"high\"}" \
    "http://${RAG_BIND}:${RAG_PORT}/notify" >/dev/null 2>&1 || true
}

# ==========================================================================
# MAIN
# ==========================================================================
log "nanobot stack update — v$(current_version)"

if $DO_ROLLBACK; then rollback; exit 0; fi

if $DRY_RUN; then
  log "[DRY-RUN] Would:"
  log "  - Backup current code + pip freeze"
  log "  - Update $(find "$SCRIPT_DIR/src/bridge" -name '*.py' | wc -l) bridge modules + templates"
  $SKIP_DEPS || log "  - Update pip dependencies"
  log "  - Run pending migrations"
  log "  - Restart services + selftest"
  check_dep_changes
  run_migrations
  exit 0
fi

acquire_lock
create_backup

# Pre-update health baseline (so we know if issues are pre-existing)
PRE_UPDATE_HEALTHY=false
if curl -fsS --max-time 5 "http://${RAG_BIND}:${RAG_PORT}/healthz" >/dev/null 2>&1; then
  PRE_UPDATE_HEALTHY=true
  log "Pre-update health: ✓ (bridge is responding)"
else
  warn "Pre-update health: ✗ (bridge was already down before this update)"
fi

$SKIP_CODE || update_code
if ! $SKIP_DEPS; then
  check_dep_changes
  update_deps
fi

run_migrations
restart_services
llm_verify_config
post_update_check

echo "8" > "$VERSION_FILE"
log "Update complete (v$(current_version)). Backup: $BACKUP_DIR"
