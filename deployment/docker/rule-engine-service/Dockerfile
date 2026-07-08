# ── flogo-agent-studio: rule-engine-service ──────────────────────────────────
# Single-stage image — accepts a pre-compiled Linux amd64 binary in the build
# context (built by deployment.py using tools/flogobuild/linux_amd64/flogobuild).
#
# Exposed internal port (fixed):
#   7097  — RuleEngineRESTTrigger  (rule evaluation API)
#
# Required env vars at runtime:
#   RULES_PATH   — path inside the container where rule files are mounted
#                  (mounted from config/rules/ on the host via docker-compose)
# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-built linux_amd64 binary placed in the build context by deployment.py
COPY rule-engine-service /app/rule-engine-service
RUN chmod +x /app/rule-engine-service

ENV FLOGO_APP_PROPS_ENV=auto
ENV FLOGO_LOG_FORMAT=JSON
ENV FLOGO_LOG_LEVEL=INFO

ENV RULES_PATH=/rules
ENV SERVICE_VERSION=1.0.0
ENV API_KEY=changeme

EXPOSE 7097

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://localhost:7097/api/health || exit 1

CMD ["/app/rule-engine-service"]
