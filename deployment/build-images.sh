#!/usr/bin/env bash
# deployment/build-images.sh
# ============================================================
# Build Docker images for all Flogo Agent Studio services.
#
# Usage:
#   bash deployment/build-images.sh [--push] [--platform linux/amd64|linux/arm64]
#
# Options:
#   --push      Push images to the registry defined by FLOGO_IMAGE
#   --platform  Target platform (default: linux/arm64 on Apple Silicon,
#               linux/amd64 elsewhere)
#   --version   Image tag suffix (default: latest)
#
# Environment:
#   FLOGO_IMAGE   Image name prefix  (default: tibco/flogo-agent-studio)
#   VERSION       Tag suffix         (default: latest)
#
# Prerequisites:
#   - Docker Desktop (or Docker Engine) installed and running
#   - flogobuild CLI available in PATH  OR  a Flogo build container
#     configured in your local extension (tibco.flogo-2.26.3)
#   - On macOS: the binaries in services/bin/ are Mach-O arm64.
#     This script cross-compiles them to Linux inside a Docker build container.
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LINUX_BIN_DIR="$SCRIPT_DIR/linux-bin"

FLOGO_IMAGE="${FLOGO_IMAGE:-tibco/flogo-agent-studio}"
VERSION="${VERSION:-latest}"
PUSH=false
PLATFORM=""

# Detect default platform
ARCH=$(uname -m)
if [[ "$ARCH" == "arm64" ]]; then
    PLATFORM="linux/arm64"
else
    PLATFORM="linux/amd64"
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --push)     PUSH=true; shift ;;
        --platform) PLATFORM="$2"; shift 2 ;;
        --version)  VERSION="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=================================================="
echo "  Flogo Agent Studio — Docker image build"
echo "  Image prefix : $FLOGO_IMAGE"
echo "  Version tag  : $VERSION"
echo "  Platform     : $PLATFORM"
echo "  Push         : $PUSH"
echo "=================================================="

mkdir -p "$LINUX_BIN_DIR"

# ── Step 1: Cross-compile Flogo services to Linux ────────────────────────────
#
# The Flogo extension uses flogobuild (wrapper around go build with cgo disabled).
# We replicate that here using a Go builder container so we don't need a local
# Go toolchain.
#
# The .flogo files are the source of truth; flogobuild generates Go code and
# compiles it.  Since we can't easily run flogobuild cross-platform outside the
# extension, we fall back to:
#   a) If linux binaries already exist in deployment/linux-bin/ → use them
#   b) If flogobuild supports --goos flag → cross-compile
#   c) Manual: document that users must supply linux binaries
#
# For now, check for pre-existing linux binaries and error if missing.

SERVICES=(
    "agent-chat-service"
    "sse-stream-service"
    "ingestion-service"
    "feedback-service"
    "design-service"
    "deploy-service"
    "rule-engine-service"
    "agent-builder-service"
    "mcp-server"
)

echo ""
echo "── Step 1: Checking for Linux binaries ──────────────────────────────────"
MISSING=()
for svc in "${SERVICES[@]}"; do
    bin="$LINUX_BIN_DIR/$svc"
    if [[ -f "$bin" ]]; then
        echo "  ✓ $svc"
    else
        MISSING+=("$svc")
        echo "  ✗ $svc  (not found in deployment/linux-bin/)"
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo "ERROR: Linux binaries missing for: ${MISSING[*]}"
    echo ""
    echo "To produce Linux binaries, run flogobuild targeting linux/$PLATFORM:"
    echo ""
    echo "  For each service in services/apps/*.flogo:"
    echo "    GOOS=linux GOARCH=\$(echo $PLATFORM | cut -d/ -f2) \\"
    echo "      flogobuild build-exe \\"
    echo "        -f services/apps/\${SERVICE}.flogo \\"
    echo "        -c flogo-v2263-2442 \\"
    echo "        -o deployment/linux-bin/\${SERVICE}"
    echo ""
    echo "Or copy pre-built Linux binaries to deployment/linux-bin/ manually."
    exit 1
fi

# ── Step 2: Build per-service Docker images ───────────────────────────────────

echo ""
echo "── Step 2: Building Docker images ──────────────────────────────────────"

declare -A SERVICE_APPS=(
    ["agent-chat-service"]="agent-chat-service.flogo"
    ["sse-stream-service"]="sse-stream-service.flogo"
    ["ingestion-service"]="ingestion-service.flogo"
    ["feedback-service"]="feedback-service.flogo"
    ["design-service"]="design-service.flogo"
    ["deploy-service"]="deploy-service.flogo"
    ["rule-engine-service"]="rule-engine-service.flogo"
    ["agent-builder-service"]="agent-builder-service.flogo"
    ["mcp-server"]="mcp-server.flogo"
)

declare -A IMAGE_TAGS=(
    ["agent-chat-service"]="agent-chat"
    ["sse-stream-service"]="sse-stream"
    ["ingestion-service"]="ingestion"
    ["feedback-service"]="feedback"
    ["design-service"]="design-service"
    ["deploy-service"]="deploy-service"
    ["rule-engine-service"]="rule-engine"
    ["agent-builder-service"]="agent-builder"
    ["mcp-server"]="mcp-server"
)

for svc in "${SERVICES[@]}"; do
    tag="${IMAGE_TAGS[$svc]}"
    flogo_app="services/apps/${SERVICE_APPS[$svc]}"
    image="${FLOGO_IMAGE}:${tag}-${VERSION}"

    if [[ ! -f "$PROJECT_ROOT/$flogo_app" ]]; then
        echo "  SKIP $svc — $flogo_app not found"
        continue
    fi

    echo ""
    echo "  Building $image ..."
    docker build \
        --platform "$PLATFORM" \
        -f "$SCRIPT_DIR/Dockerfile.flogo-service" \
        --build-arg "SERVICE_BINARY=deployment/linux-bin/$svc" \
        --build-arg "FLOGO_APP=$flogo_app" \
        -t "$image" \
        "$PROJECT_ROOT"
    echo "  ✓ $image"
done

# ── Step 3: Build Chainlit image ──────────────────────────────────────────────
echo ""
echo "── Step 3: Building chainlit image ─────────────────────────────────────"
CHAINLIT_IMAGE="${FLOGO_IMAGE}:chainlit-${VERSION}"
docker build \
    --platform "$PLATFORM" \
    -f "$PROJECT_ROOT/ui/chainlit/Dockerfile" \
    -t "$CHAINLIT_IMAGE" \
    "$PROJECT_ROOT/ui/chainlit"
echo "  ✓ $CHAINLIT_IMAGE"

# ── Step 4: Push (optional) ───────────────────────────────────────────────────
if [[ "$PUSH" == "true" ]]; then
    echo ""
    echo "── Step 4: Pushing images to registry ───────────────────────────────────"
    for svc in "${SERVICES[@]}"; do
        tag="${IMAGE_TAGS[$svc]}"
        docker push "${FLOGO_IMAGE}:${tag}-${VERSION}"
    done
    docker push "$CHAINLIT_IMAGE"
    echo "  ✓ All images pushed"
fi

echo ""
echo "=================================================="
echo "  Build complete!"
echo "  Run a full stack:  docker compose up -d"
echo "  Deploy one agent:  Use 'Deploy with Docker' in the Forge UI"
echo "=================================================="
