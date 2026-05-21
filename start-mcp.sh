#!/bin/sh
# Start mcp-server with updated env (FLOGO_APP_PROPS_ENV=auto, feedback on 7020)
cd /Users/milindpandav/git/flogo-agent-studio
export OTEL_SERVICE_NAME=mcp
exec python3 services/launch.py \
  services/platform/env/mcp-server.env \
  services/bin/mcp-server \
  -app services/platform/flogo/mcp-server.flogo
