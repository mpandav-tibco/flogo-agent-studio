# ── flogo-agent-studio: agent-chat-service ────────────────────────────────────
# Single-stage image — accepts a pre-compiled Linux amd64 binary in the build
# context (built by deployment.py using tools/flogobuild/linux_amd64/flogobuild).
#
# The binary already embeds the full app definition; per-agent configuration
# is injected purely via environment variables at container start (no -app flag
# needed — FLOGO_APP_PROPS_ENV=auto reads all env vars as property overrides).
#
# Exposed internal ports (fixed — Docker compose maps these to host ports):
#   7001  — AgentChatRESTTrigger  (chat API)
#   7005  — SSEStreamRESTTrigger  (SSE REST gateway)
#   7099  — SSEEventBus           (SSE event push)
# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-built linux_amd64 binary placed in the build context by deployment.py
COPY agent-chat-service /app/agent-chat-service
RUN chmod +x /app/agent-chat-service

# Flogo reads all env vars prefixed FLOGO_APP_PROPS_ENV=auto as property overrides
ENV FLOGO_APP_PROPS_ENV=auto
ENV FLOGO_LOG_FORMAT=JSON
ENV FLOGO_LOG_LEVEL=INFO

# These are the default values; override via env vars in docker-compose
ENV COLLECTION_NAME=AgentDefault
ENV SYSTEM_PROMPT="You are a helpful assistant."
ENV LLM_MODEL=llama3.2:3b
ENV LLM_PROVIDER=Ollama
ENV LLM_BASE_URL=http://ollama:11434/v1
ENV WEAVIATE_HOST=weaviate
ENV WEAVIATE_PORT=8080

EXPOSE 7001 7005 7099

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://localhost:7001/api/health || exit 1

CMD ["/app/agent-chat-service"]
