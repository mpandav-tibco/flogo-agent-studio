#!/usr/bin/env bash
# Start all Flogo Agent Studio services (macOS / Linux)
# Equivalent to start-all.ps1 for Windows

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

mkdir -p logs

# Array of: "binary:log-prefix:port"
SERVICES=(
  "bin/rule-engine-service:rule-engine:7000"
  "bin/agent-chat-service:agent-chat:7001"
  "bin/ingestion-service:ingestion:7002"
  "bin/feedback-service:feedback:7003"
  # config-service (7004) retired — superseded by design-service (7020)
  "bin/sse-stream-service:sse-stream:7005"
  "bin/agent-builder-service:agent-builder:7010"
  "bin/design-service:design:7020"
  "bin/deploy-service:deploy:7030"
  "bin/mcp-server:mcp-server:3333"
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
  "$exe" > "logs/${name}.log" 2> "logs/${name}-err.log" &
  echo "START $name  (port $port, pid $!)"
  ((started++)) || true
  sleep 0.2
done

echo ""
echo "Started: $started service(s)  |  Skipped: $skipped (binary not built yet)"
echo "Waiting 5s for readiness..."
sleep 5
echo "Ready."
