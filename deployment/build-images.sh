#!/usr/bin/env bash
# deployment/build-images.sh
# ============================================================
# Build Docker images for all Flogo Agent Studio services.
#
# IMPORTANT: Run this script inside the Linux devcontainer (.devcontainer/).
# flogobuild does not support cross-compilation; binaries are built natively
# on linux/amd64 and packaged into Docker images.
#
# Workflow (two-step):
#   1. flogobuild build-exe  — compiles each .flogo to a Linux binary
#   2. docker build          — packages binary + .flogo file into an image
#
# Usage:
#   bash deployment/build-images.sh [options]
#
# Options:
#   --service <name>   Build only one service (e.g. agent-chat-service)
#   --push             Push images after building
#   --platform <plat>  Docker image platform tag (default: linux/amd64)
#   --version  <tag>   Image tag suffix (default: latest)
#   --skip-compile     Skip flogobuild step; reuse binaries in deployment/linux-bin/
#
# Environment:
#   FLOGO_IMAGE     Image prefix     (default: tibco/flogo-agent-studio)
#   BUILD_CONTEXT   flogobuild ctx   (default: flogo-linux)
#   VERSION         Tag suffix       (default: latest)
#
# Prerequisites:
#   - flogobuild in PATH  (mounted by devcontainer at /usr/local/bin/flogobuild)
#   - Docker CLI in PATH  (mounted Docker socket)
#   - flogobuild context 'flogo-linux' initialised (done by postCreateCommand)
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LINUX_BIN_DIR="$SCRIPT_DIR/linux-bin"

FLOGO_IMAGE="${FLOGO_IMAGE:-tibco/flogo-agent-studio}"
BUILD_CONTEXT="${BUILD_CONTEXT:-flogo-linux}"
VERSION="${VERSION:-latest}"
PUSH=false
SKIP_COMPILE=false
ONLY_SERVICE=""

# linux/amd64 — matches the Linux VSIX and the devcontainer architecture
PLATFORM="linux/amd64"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --push)          PUSH=true; shift ;;
        --skip-compile)  SKIP_COMPILE=true; shift ;;
        --platform)      PLATFORM="$2"; shift 2 ;;
        --version)       VERSION="$2"; shift 2 ;;
        --service)       ONLY_SERVICE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=================================================="
echo "  Flogo Agent Studio — Docker image build"
echo "  Image prefix  : $FLOGO_IMAGE"
echo "  Version tag   : $VERSION"
echo "  Platform      : $PLATFORM"
  echo "  Build context : $BUILD_CONTEXT"
echo "  Skip compile  : $SKIP_COMPILE"
echo "  Push          : $PUSH"
[[ -n "$ONLY_SERVICE" ]] && echo "  Only service  : $ONLY_SERVICE"
echo "=================================================="

# ── Validate prerequisites ────────────────────────────────────────────────────
# Look for flogobuild: tools/flogobuild/linux_amd64/ (committed) then PATH
if ! command -v flogobuild &>/dev/null; then
    _fb_tool="$PROJECT_ROOT/tools/flogobuild/linux_amd64/flogobuild"
    if [[ -x "$_fb_tool" ]]; then
        export PATH="$(dirname "$_fb_tool"):$PATH"
        echo "Using flogobuild from tools/flogobuild/linux_amd64/"
    else
        echo "ERROR: flogobuild not found in tools/flogobuild/linux_amd64/ or PATH."
        echo "Place flogobuild binary at tools/flogobuild/linux_amd64/flogobuild or run inside the devcontainer."
        exit 1
    fi
fi
if ! docker info &>/dev/null; then
    echo "ERROR: Docker is not running. Start Docker Desktop and retry."
    exit 1
fi

mkdir -p "$LINUX_BIN_DIR"

# ── Service catalogue ─────────────────────────────────────────────────────────
# Maps service binary name → flogo source file (platform or agent layer)
# deploy-service is decommissioned and excluded.
declare -A FLOGO_SRC=(
    ["agent-chat-service"]="agent-chat-service.flogo"   # includes SSE streaming (merged)
    ["ingestion-service"]="ingestion-service.flogo"
    ["feedback-service"]="feedback-service.flogo"
    ["design-service"]="design-service.flogo"
    ["rule-engine-service"]="rule-engine-service.flogo"
    ["agent-builder-service"]="agent-builder-service.flogo"
    ["mcp-server"]="mcp-server.flogo"
)

declare -A IMAGE_TAG=(
    ["agent-chat-service"]="agent-chat"
    ["ingestion-service"]="ingestion"
    ["feedback-service"]="feedback"
    ["design-service"]="design-service"
    ["rule-engine-service"]="rule-engine"
    ["agent-builder-service"]="agent-builder"
    ["mcp-server"]="mcp-server"
)

# Resolve flogo source file across platform and agent layers
_find_flogo() {
    local fname="$1"
    for _dir in "services/platform/flogo" "services/agent/flogo"; do
        [[ -f "$PROJECT_ROOT/$_dir/$fname" ]] && echo "$_dir/$fname" && return 0
    done
    return 1
}

# Filter to requested service if --service passed
SERVICES=("${!FLOGO_SRC[@]}")
if [[ -n "$ONLY_SERVICE" ]]; then
    if [[ -z "${FLOGO_SRC[$ONLY_SERVICE]:-}" ]]; then
        echo "ERROR: Unknown service '$ONLY_SERVICE'."
        echo "Valid services: ${SERVICES[*]}"
        exit 1
    fi
    SERVICES=("$ONLY_SERVICE")
fi

# ── Step 1: Compile with flogobuild build-exe (native linux/amd64) ────────────
echo ""
echo "── Step 1: Compiling Flogo services (native linux/amd64) ────────────────"

if [[ "$SKIP_COMPILE" == "true" ]]; then
    echo "  Skipping compilation (--skip-compile set)."
else
    for svc in "${SERVICES[@]}"; do
        flogo_rel=$(_find_flogo "${FLOGO_SRC[$svc]}")
        if [[ -z "$flogo_rel" ]]; then
            echo "  SKIP $svc — source not found in services/platform/flogo or services/agent/flogo"
            continue
        fi
        flogo_file="$PROJECT_ROOT/$flogo_rel"

        echo ""
        echo "  Compiling $svc ..."
        flogobuild build-exe \
            -f  "$flogo_file" \
            -c  "$BUILD_CONTEXT" \
            -n  "$svc" \
            -o  "$LINUX_BIN_DIR"
        echo "  ✓ deployment/linux-bin/$svc"
    done
fi

# ── Step 2: Verify binaries exist ────────────────────────────────────────────
echo ""
echo "── Step 2: Verifying Linux binaries ────────────────────────────────────"
MISSING=()
for svc in "${SERVICES[@]}"; do
    bin="$LINUX_BIN_DIR/$svc"
    if [[ -f "$bin" ]]; then
        echo "  ✓ deployment/linux-bin/$svc  ($(du -sh "$bin" | cut -f1))"
    else
        MISSING+=("$svc")
        echo "  ✗ deployment/linux-bin/$svc  MISSING"
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo ""
    echo "ERROR: Missing Linux binaries: ${MISSING[*]}"
    echo "Re-run without --skip-compile or place binaries manually in deployment/linux-bin/"
    exit 1
fi

# ── Step 3: Build Docker images with docker build ─────────────────────────────
# We use docker build (not flogobuild package-docker-image) because we need to
# include BOTH the compiled binary AND the .flogo app file in the image.
# The ENTRYPOINT is: /app/service -app /app/app.flogo
echo ""
echo "── Step 3: Building Docker images ──────────────────────────────────────"

for svc in "${SERVICES[@]}"; do
    flogo_rel=$(_find_flogo "${FLOGO_SRC[$svc]}")
    flogo_file="${flogo_rel:-services/agent/flogo/${FLOGO_SRC[$svc]}}"
    image="${FLOGO_IMAGE}:${IMAGE_TAG[$svc]}-${VERSION}"

    if [[ ! -f "$PROJECT_ROOT/$flogo_file" ]]; then
        echo "  SKIP $svc — $flogo_file not found"
        continue
    fi

    echo ""
    echo "  Building $image ..."
    docker build \
        --platform "$PLATFORM" \
        -f  "$SCRIPT_DIR/Dockerfile.flogo-service" \
        --build-arg "SERVICE_BINARY=deployment/linux-bin/$svc" \
        --build-arg "FLOGO_APP=$flogo_file" \
        -t  "$image" \
        "$PROJECT_ROOT"
    echo "  ✓ $image"
done

# ── Step 4: Build Chainlit image (Python — no flogobuild needed) ────────────
echo ""
echo "── Step 4: Building chainlit image ─────────────────────────────────────"
CHAINLIT_IMAGE="${FLOGO_IMAGE}:chainlit-${VERSION}"
docker build \
    --platform "$PLATFORM" \
    -f  "$PROJECT_ROOT/services/agent/ui/chainlit/Dockerfile" \
    -t  "$CHAINLIT_IMAGE" \
    "$PROJECT_ROOT/services/agent/ui/chainlit"
echo "  ✓ $CHAINLIT_IMAGE"

# ── Step 5: Push (optional) ───────────────────────────────────────────────────
if [[ "$PUSH" == "true" ]]; then
    echo ""
    echo "── Step 5: Pushing to registry ─────────────────────────────────────────"
    for svc in "${SERVICES[@]}"; do
        docker push "${FLOGO_IMAGE}:${IMAGE_TAG[$svc]}-${VERSION}"
    done
    docker push "$CHAINLIT_IMAGE"
    echo "  ✓ All images pushed to $FLOGO_IMAGE"
fi

echo ""
echo "=================================================="
echo "  Build complete!"
echo ""
echo "  Images built:"
for svc in "${SERVICES[@]}"; do
    echo "    ${FLOGO_IMAGE}:${IMAGE_TAG[$svc]}-${VERSION}"
done
echo "    $CHAINLIT_IMAGE"
echo ""
echo "  Deploy a full stack : docker compose up -d"
echo "  Deploy one agent    : 'Deploy with Docker' button in Forge UI"
echo "=================================================="
