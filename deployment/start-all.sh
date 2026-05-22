#!/usr/bin/env bash
# Start all Flogo Agent Studio services (macOS / Linux)
# Equivalent to start-all.ps1 for Windows

set -uo pipefail   # -e removed: non-critical steps use explicit error handling

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

mkdir -p logs data/feedback

# ── Helpers ───────────────────────────────────────────────────────────────────
wait_for_port() {
  local label=$1 port=$2 timeout=${3:-30}
  local i=0
  printf 'Waiting for %s (port %s)' "$label" "$port"
  while (( i < timeout )); do
    if nc -z -w1 127.0.0.1 "$port" 2>/dev/null; then
      echo " ✓"
      return 0
    fi
    printf '.'
    sleep 1
    (( i++ )) || true
  done
  echo " TIMEOUT (${timeout}s) — continuing anyway"
  return 0
}

check_port() {
  # Returns 0 (success) if port is up within timeout, 1 if not.
  local label=$1 port=$2 timeout=${3:-30}
  local i=0
  printf 'Checking %-20s (port %s)' "$label" "$port"
  while (( i < timeout )); do
    if nc -z -w1 127.0.0.1 "$port" 2>/dev/null; then
      echo " ✓"
      return 0
    fi
    printf '.'
    sleep 1
    (( i++ )) || true
  done
  echo " ✗ NOT READY (${timeout}s)"
  return 1
}

# ── Stop any process currently holding one of our ports ──────────────────────
# Targets only the ports this script owns. Infrastructure ports (Weaviate,
# Postgres, Elasticsearch, Docker) are never touched.
#
# Static ports  : Flogo services + UIs + runtime manager
# Dynamic ports : per-agent runtime pool 7200-7299 (managed by deployment.py)
MANAGED_PORTS=(
  7025   # forge-ui (platform)
  7050   # runtime-manager (deployment.py)
  7097   # rule-engine (platform)
  7020   # platform-service: design + feedback (merged)
  7010   # agent-builder (platform)
  7333   # mcp-server (platform)
  # Legacy static agent ports — cleaned up on startup so orphaned processes don't block port pool
  7080   # chainlit-ui (legacy static; now per-agent via deployment.py)
  7001   # agent-chat+sse (legacy static; now per-agent via deployment.py)
  7002   # ingestion  (legacy static; now per-agent via deployment.py)
  7005   # sse-stream REST  (merged into agent-chat; legacy static)
  7099   # sse-stream events (merged into agent-chat; legacy static)
)
# Append the full per-agent runtime pool
for _p in $(seq 7200 7299); do MANAGED_PORTS+=($_p); done
unset _p

_kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
  [[ -z "$pids" ]] && return 0
  local pid_list; pid_list=$(echo "$pids" | tr '\n' ' ')
  echo "  port ${port}: stopping PID(s) ${pid_list% }"
  echo "$pids" | xargs kill -TERM 2>/dev/null || true
  return 0
}

stop_existing_services() {
  echo ""
  echo "── Stopping existing services ──────────────────────────────────────────"
  local found=0
  for port in "${MANAGED_PORTS[@]}"; do
    local pids
    pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
      _kill_port "$port"
      (( found++ )) || true
    fi
  done

  if (( found > 0 )); then
    printf 'Waiting for %d port(s) to release' "$found"
    local wait=0
    while (( wait < 8 )); do
      local still=0
      for port in "${MANAGED_PORTS[@]}"; do
        pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
          (( still++ )) || true
        fi
      done
      (( still == 0 )) && break
      printf '.'
      sleep 1
      (( wait++ )) || true
    done
    echo ""

    # Force-kill anything still alive
    for port in "${MANAGED_PORTS[@]}"; do
      local pids
      pids=$(lsof -ti "tcp:${port}" 2>/dev/null || true)
      if [[ -n "$pids" ]]; then
        echo "  port ${port}: force-killing PID(s) $(echo "$pids" | tr '\n' ' ')"
        echo "$pids" | xargs kill -9 2>/dev/null || true
      fi
    done
    sleep 0.5
    echo "  Done — cleared ${found} port(s)."
  else
    echo "  No existing services found on managed ports."
  fi
  echo ""
}

# ── Elasticsearch index cleanup ───────────────────────────────────────────────
# Indexes are cleared on each startup for fresh logs.
# Gracefully skipped if Elasticsearch is unavailable or curl is missing.
ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"
ELASTICSEARCH_INDEX_PATTERN="${ELASTICSEARCH_INDEX_PATTERN:-flogo-*}"

cleanup_elastic_indexes() {
  echo ""
  echo "── Elasticsearch index cleanup ────────────────────────────────────────"

  if ! command -v curl &>/dev/null; then
    echo "SKIP  elastic cleanup (curl not available)"
    return 0
  fi

  # Quick reachability check — 2s timeout so it doesn't block startup
  if ! curl -sf --connect-timeout 2 "${ELASTICSEARCH_URL}/_cluster/health" -o /dev/null 2>/dev/null; then
    echo "SKIP  elastic cleanup (Elasticsearch not reachable at ${ELASTICSEARCH_URL})"
    return 0
  fi

  echo "Clearing indexes matching: ${ELASTICSEARCH_INDEX_PATTERN}"

  local indexes
  indexes=$(curl -sf --connect-timeout 2 \
    "${ELASTICSEARCH_URL}/_cat/indices/${ELASTICSEARCH_INDEX_PATTERN}?h=index" \
    2>/dev/null || true)

  if [[ -z "$indexes" ]]; then
    echo "  No matching indexes found — nothing to clean."
    echo ""
    return 0
  fi

  local count=0
  while IFS= read -r idx; do
    [[ -z "$idx" ]] && continue
    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" -X DELETE \
      --connect-timeout 2 "${ELASTICSEARCH_URL}/${idx}" 2>/dev/null || echo "000")
    if [[ "$http_code" == "200" ]]; then
      echo "  Deleted: ${idx}"
      (( count++ )) || true
    else
      echo "  WARN: could not delete ${idx} (HTTP ${http_code}) — skipping"
    fi
  done <<< "$indexes"

  echo "  Done — cleared ${count} index(es)."
  echo ""
}

# Clean Elasticsearch indexes so each startup begins with fresh logs.
# Safe to call even if Elastic is not running.
cleanup_elastic_indexes

# Stop any services already occupying our ports — prevents "address already in use".
stop_existing_services

# ── Clear log files ───────────────────────────────────────────────────────────
echo "── Clearing log files ─────────────────────────────────────────────────────"
mkdir -p "$PROJECT_ROOT/logs"
log_count=0
for f in "$PROJECT_ROOT/logs/"*.log; do
  [[ -f "$f" ]] || continue
  > "$f"   # truncate in place (preserves any open file handles)
  (( log_count++ )) || true
done
if (( log_count > 0 )); then
  echo "  Cleared ${log_count} log file(s) in logs/"
else
  echo "  No log files to clear."
fi
echo ""

# ── Build Flogo service binaries ──────────────────────────────────────────────
# Detects flogobuild from tools/flogobuild/<os>_<arch>/ (committed) or PATH.
# Rebuilds any binary that is missing or older than its .flogo source file.
# Set BUILD_BINARIES=never to skip entirely (e.g. if you pre-built manually).
# Set FLOGOBUILD_CONTEXT to override the flogobuild context (default: flogo-studio).

_detect_flogobuild() {
  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')   # darwin | linux
  arch=$(uname -m)                               # arm64  | x86_64
  [[ "$arch" == "x86_64" ]] && arch="amd64"
  local tool="$PROJECT_ROOT/tools/flogobuild/${os}_${arch}/flogobuild"
  if [[ -x "$tool" ]]; then echo "$tool"; return 0; fi
  if command -v flogobuild &>/dev/null; then echo "flogobuild"; return 0; fi
  echo ""; return 1
}

_build_flogo_services() {
  local fb; fb=$(_detect_flogobuild)
  if [[ -z "$fb" ]]; then
    echo "SKIP  build step (flogobuild not found in tools/ or PATH)"
    return 0
  fi

  local ctx="${FLOGOBUILD_CONTEXT:-flogo-studio}"
  local need_build=()

  for fdir in "services/platform/flogo" "services/agent/flogo"; do
    for flogo in "$PROJECT_ROOT/$fdir/"*.flogo; do
      [[ -f "$flogo" ]] || continue
      local svc; svc=$(basename "$flogo" .flogo)
      local binf="$PROJECT_ROOT/bin/$svc"
      if [[ ! -f "$binf" ]] || [[ "$flogo" -nt "$binf" ]]; then
        need_build+=("$fdir/$(basename "$flogo"):$svc")
      fi
    done
  done

  if [[ ${#need_build[@]} -eq 0 ]]; then
    echo "  All binaries up to date."
    return 0
  fi

  echo "  flogobuild: $fb  context: $ctx"
  mkdir -p "$PROJECT_ROOT/bin"
  local failed=()
  for entry in "${need_build[@]}"; do
    IFS=: read -r flogo_rel svc <<< "$entry"
    printf '  Compiling %-32s' "$svc ..."
    if "$fb" build-exe \
        -f "$PROJECT_ROOT/$flogo_rel" \
        -c "$ctx" \
        -n "$svc" \
        -o "$PROJECT_ROOT/bin" > /tmp/flogobuild-${svc}.log 2>&1; then
      echo "✓"
    else
      echo "✗ FAILED"
      echo "     -- build log --"
      tail -10 "/tmp/flogobuild-${svc}.log" | sed 's/^/     /'
      failed+=("$svc")
    fi
  done

  if [[ ${#failed[@]} -gt 0 ]]; then
    echo ""
    echo "ERROR: Build failed for: ${failed[*]}"
    echo "Fix the errors above and re-run start-all.sh."
    exit 1
  fi
}

BUILD_BINARIES="${BUILD_BINARIES:-auto}"
echo ""
echo "── Building Flogo service binaries ────────────────────────────────────"
if [[ "$BUILD_BINARIES" != "never" ]]; then
  _build_flogo_services
else
  echo "  Skipped (BUILD_BINARIES=never)"
fi

# ── Service-specific env vars (read by Flogo via FLOGO_APP_PROPS_ENV=auto) ───
export FLOGO_APP_PROPS_ENV=auto
export RULES_PATH="./config/rules"
export FEEDBACK_DIR="./data/feedback"
export FEEDBACK_LOG_PATH="./data/feedback/feedback.jsonl"

# ── OpenTelemetry ─────────────────────────────────────────────────────────────
# Set OTEL_ENABLED=false to skip OTel (e.g. when observability stack is not up)
OTEL_ENABLED="${OTEL_ENABLED:-true}"

if [[ "$OTEL_ENABLED" == "true" ]]; then
  # Flogo-native OTel env vars (from wi-contrib/integrations/opentelemetry):
  #   FLOGO_OTEL_TRACE=true     — enables the OTel tracer registration
  #   FLOGO_OTEL_OTLP_ENDPOINT  — gRPC: host:port (no scheme), HTTP: http://host:port
  #   FLOGO_OTEL_METRICS=true   — enables OTel metrics export
  export FLOGO_OTEL_TRACE="true"
  export FLOGO_OTEL_METRICS="true"
  export FLOGO_OTEL_OTLP_ENDPOINT="localhost:4317"   # gRPC (no http:// = gRPC insecure)

  # Flogo-specific logging + span type
  export FLOGO_OTEL_SPAN_KIND="SERVER"          # REST services are SERVER spans
  export FLOGO_LOG_CTX="TRUE"                   # inject trace_id/span_id into JSON logs
  export FLOGO_LOG_FORMAT="JSON"                # machine-readable logs for Fluent Bit
  export FLOGO_ENV="dev"                        # sets deployment.environment in traces
  export FLOGO_LOG_CTX_FIELDS="service.namespace=flogo-agent-studio,service.environment=dev"

  echo "OTel → gRPC localhost:4317 (FLOGO_OTEL_TRACE=true)"
else
  echo "OTel disabled (OTEL_ENABLED=false)"
fi

# ── Forge UI (AgentForge — port 7025) ───────────────────────────────────────
if [[ -d "services/platform/ui/forge" ]] && command -v npm &>/dev/null; then
  if [[ ! -d "services/platform/ui/forge/node_modules" ]]; then
    echo "Installing Forge dependencies..."
    npm --prefix services/platform/ui/forge install --silent
  fi
  npm --prefix services/platform/ui/forge run dev > logs/forge.log 2>&1 &
  echo "START forge-ui    (port 7025, pid $!)"
else
  echo "SKIP  forge-ui    (services/platform/ui/forge not found or npm unavailable)"
fi

# NOTE: Chainlit UI is NOT started here. Each activated agent gets its own
# dedicated Chainlit instance started by the runtime-manager (deployment.py)
# on a dynamic port in the 7200–7299 pool.

# ── Wait for Forge UI to be ready before starting Flogo services ─────────────
echo ""
wait_for_port "forge-ui" 7025 30
echo ""

# ── Platform services only ────────────────────────────────────────────────────
# Agent services (chat+sse, ingestion, chainlit) are NOT started here.
# The runtime-manager (deployment.py) starts a dedicated set per agent when
# that agent is activated from the AgentForge UI.
SERVICES=(
  "bin/rule-engine-service:rule-engine:7097"      # shared Flogo analyser
  "bin/platform-service:platform:7020"            # design + feedback (merged)
  "bin/agent-builder-service:agent-builder:7010"  # LLM config generation
  "bin/mcp-server:mcp-server:7333"                # MCP gateway
)

started=0
skipped=0

for entry in "${SERVICES[@]}"; do
  IFS=: read -r exe name port <<< "$entry"
  if [[ ! -f "$exe" ]]; then
    echo "SKIP  $name  (binary not found: $exe)"
    ((skipped++)) || true
    continue
  fi
  if [[ ! -x "$exe" ]]; then
    chmod +x "$exe"
  fi
  # For services with 2.26.3-incompatible embedded JSON, use -app to override with fixed source file.
  # mcp-server: OTel tracing disabled — MCP trigger tracingMiddleware panics on nil params.
  APP_OVERRIDE=""
  for _fdir in "services/platform/flogo" "services/agent/flogo"; do
    if [[ -f "$_fdir/${name}-service.flogo" ]]; then
      APP_OVERRIDE="-app $_fdir/${name}-service.flogo"
      break
    elif [[ -f "$_fdir/${name}.flogo" ]]; then
      APP_OVERRIDE="-app $_fdir/${name}.flogo"
      break
    fi
  done

  # Per-service env file — supplies all app properties as env vars so that
  # FLOGO_APP_PROPS_ENV=auto resolves them without emitting startup WARNings.
  # Launched via services/launch.py because some property names (e.g.
  # VECTORDB_VECTORDB-WEAVIATE_TIMEOUT_(SECONDS)) contain characters that bash
  # export cannot handle; Python's os.execve passes them directly to the kernel.
  SVC_ENV_FILE=""
  for _edir in "services/platform/env" "services/agent/env"; do
    if [[ -f "$_edir/${name}-service.env" ]]; then
      SVC_ENV_FILE="$_edir/${name}-service.env"
      break
    elif [[ -f "$_edir/${name}.env" ]]; then
      SVC_ENV_FILE="$_edir/${name}.env"
      break
    fi
  done

  if [[ "$name" == "mcp-server" ]]; then
    OTEL_SERVICE_NAME="${name}" FLOGO_OTEL_TRACE="false" \
      python3 services/launch.py "$SVC_ENV_FILE" "$exe" $APP_OVERRIDE > "logs/${name}.log" 2>&1 &
  else
    OTEL_SERVICE_NAME="${name}" \
      python3 services/launch.py "$SVC_ENV_FILE" "$exe" $APP_OVERRIDE > "logs/${name}.log" 2>&1 &
  fi
  echo "START $name  (port $port, pid $!)"
  ((started++)) || true
  sleep 0.2
done

echo ""
echo "Started: $started platform service(s)  |  Skipped: $skipped (binary not built yet)"
echo "Agent services (chat+sse/ingestion/chainlit) will be started by runtime-manager when an agent is activated."

# ── Runtime Manager (port 7050) ───────────────────────────────────────────────
# Starts after platform Flogo services. Manages per-agent process groups:
# each deployed (active) agent gets its own chat+sse+ingestion+chainlit stack.
# sse-stream-service is merged into agent-chat-service (single binary, 3 ports).
if [[ -f "$SCRIPT_DIR/deployment.py" ]]; then
  python3 "$SCRIPT_DIR/deployment.py" > logs/runtime-manager.log 2>&1 &
  echo "START runtime-manager  (port 7050, pid $!)"
else
  echo "SKIP  runtime-manager  (deployment/deployment.py not found)"
fi

echo ""
echo "Waiting 5s for services to initialise..."
sleep 5

# ── Flogo service health check (mandatory — script fails here if any are down) ─
if (( started > 0 )); then
  echo ""
  echo "── Checking Flogo service health ──────────────────────────────────────"
  FLOGO_FAILED=()
  for entry in "${SERVICES[@]}"; do
    IFS=: read -r exe svc_name svc_port <<< "$entry"
    # Skip services whose binary wasn't present (already skipped at start)
    [[ ! -f "$exe" ]] && continue
    if ! check_port "$svc_name" "$svc_port" 25; then
      FLOGO_FAILED+=("$svc_name:$svc_port")
    fi
  done

  if (( ${#FLOGO_FAILED[@]} > 0 )); then
    echo ""
    echo "ERROR ─ The following Flogo services did not become ready:"
    for entry in "${FLOGO_FAILED[@]}"; do
      IFS=: read -r svc_name svc_port <<< "$entry"
      echo "  ✗  ${svc_name} (port ${svc_port})"
      local_log="logs/${svc_name}.log"
      if [[ -f "$local_log" ]]; then
        echo "     ── Last 5 lines of logs/${svc_name}.log ──"
        tail -5 "$local_log" | sed 's/^/     /'
      fi
    done
    echo ""
    echo "Fix the errors above and re-run start-all.sh."
    exit 1
  fi

  echo ""
  echo "All Flogo services are healthy."
fi

echo ""
echo "Ready."
