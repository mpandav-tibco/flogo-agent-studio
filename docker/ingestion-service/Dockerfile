# ── flogo-agent-studio: ingestion-service ────────────────────────────────────
# Single-stage image — accepts a pre-compiled Linux amd64 binary in the build
# context (built by deployment.py using tools/flogobuild/linux_amd64/flogobuild).
#
# Exposed internal port (fixed):
#   7002  — IngestionRESTTrigger  (document ingestion API)
# ─────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-built linux_amd64 binary placed in the build context by deployment.py
COPY ingestion-service /app/ingestion-service
RUN chmod +x /app/ingestion-service

ENV FLOGO_APP_PROPS_ENV=auto
ENV FLOGO_LOG_FORMAT=JSON
ENV FLOGO_LOG_LEVEL=INFO

ENV COLLECTION_NAME=AgentDefault
ENV WEAVIATE_HOST=weaviate
ENV WEAVIATE_PORT=8080
ENV EMBEDDING_MODEL=nomic-embed-text
ENV EMBEDDING_PROVIDER=Ollama
ENV EMBEDDING_BASE_URL=http://ollama:11434/v1
ENV CHUNK_STRATEGY=sentence

EXPOSE 7002

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD wget -qO- http://localhost:7002/api/health || exit 1

CMD ["/app/ingestion-service"]
