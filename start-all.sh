#!/usr/bin/env bash
# Start all Flogo Agent Studio services (macOS / Linux)
# Equivalent to start-all.ps1 for Windows

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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
if [[ -d "ui/forge" ]] && command -v npm &>/dev/null; then
  if [[ ! -d "ui/forge/node_modules" ]]; then
    echo "Installing Forge dependencies..."
    npm --prefix ui/forge install --silent
  fi
  npm --prefix ui/forge run dev > logs/forge.log 2>&1 &
  echo "START forge-ui    (port 7025, pid $!)"
else
  echo "SKIP  forge-ui    (ui/forge not found or npm unavailable)"
fi

# ── Chainlit UI (port 7080) ───────────────────────────────────────────────────
if [[ -d "ui/chainlit" ]]; then
  CHAINLIT_CMD=""
  if command -v chainlit &>/dev/null; then
    CHAINLIT_CMD="chainlit run app.py --port 7080 --headless"
  elif python3 -m chainlit --version &>/dev/null 2>&1; then
    CHAINLIT_CMD="python3 -m chainlit run app.py --port 7080 --headless"
  fi

  if [[ -n "$CHAINLIT_CMD" ]]; then
    (cd ui/chainlit && $CHAINLIT_CMD) > logs/chainlit.log 2>&1 &
    echo "START chainlit-ui (port 7080, pid $!)"
  else
    echo "SKIP  chainlit-ui (chainlit not installed — run: pip install chainlit)"
  fi
else
  echo "SKIP  chainlit-ui (ui/chainlit not found)"
fi

# ── Wait for UIs to be ready before starting Flogo services ──────────────────
echo ""
wait_for_port "forge-ui"    7025 30
wait_for_port "chainlit-ui" 7080 30
echo ""

# ── Array of: "binary:log-prefix:port"
SERVICES=(
  "services/bin/rule-engine-service:rule-engine:7097"
  "services/bin/agent-chat-service:agent-chat:7001"
  "services/bin/ingestion-service:ingestion:7002"
  "services/bin/feedback-service:feedback:7003"
  # config-service (7004) retired — superseded by design-service (7020)
  "services/bin/sse-stream-service:sse-stream:7005"
  "services/bin/agent-builder-service:agent-builder:7010"
  "services/bin/design-service:design:7020"
  "services/bin/deploy-service:deploy:7030"
  "services/bin/mcp-server:mcp-server:7333"
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
  if [[ -f "services/apps/${name}-service.flogo" ]]; then
    APP_OVERRIDE="-app services/apps/${name}-service.flogo"
  elif [[ -f "services/apps/${name}.flogo" ]]; then
    APP_OVERRIDE="-app services/apps/${name}.flogo"
  fi

  # Per-service env file — supplies all app properties as env vars so that
  # FLOGO_APP_PROPS_ENV=auto resolves them without emitting startup WARNings.
  # Launched via services/launch.py because some property names (e.g.
  # VECTORDB_VECTORDB-WEAVIATE_TIMEOUT_(SECONDS)) contain characters that bash
  # export cannot handle; Python's os.execve passes them directly to the kernel.
  SVC_ENV_FILE="services/env/${name}-service.env"
  if [[ ! -f "$SVC_ENV_FILE" ]]; then
    SVC_ENV_FILE="services/env/${name}.env"
  fi

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
echo "Started: $started service(s)  |  Skipped: $skipped (binary not built yet)"
echo "Waiting 5s for readiness..."
sleep 5
echo "Ready."
