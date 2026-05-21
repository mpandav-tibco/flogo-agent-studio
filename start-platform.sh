#!/bin/sh
# Start platform-service with updated env (FLOGO_APP_PROPS_ENV=auto)
cd /Users/milindpandav/git/flogo-agent-studio
export OTEL_SERVICE_NAME=platform
exec python3 services/launch.py \
  services/platform/env/platform-service.env \
  services/bin/platform-service \
  -app services/platform/flogo/platform-service.flogo
