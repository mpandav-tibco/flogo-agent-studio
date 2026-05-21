# Flogo Agent Studio — Linux Builder Devcontainer
#
# Purpose: Provides a linux/amd64 environment for building Flogo service
#          binaries natively (flogobuild does not support cross-compilation).
#
# What's provided here:
#   - Go 1.25  (required by flogo-runtime go.mod)
#   - Docker CLI  (to build and push images from inside the container)
#
# What's bind-mounted at runtime (see devcontainer.json):
#   - /usr/local/bin/flogobuild     ← linux_amd64 flogobuild binary
#   - /opt/flogo/flogo.vsix         ← flogo-vscode-linux-x64-2.26.3-2498.vsix
#   - /opt/flogo-custom-extensions  ← flogo-custom-extensions repo
#   - /var/run/docker.sock          ← host Docker daemon socket

FROM golang:1.25-bookworm

# Install Docker CLI (client only — daemon runs on the host via socket mount)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    unzip \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Working directory matches the devcontainer workspace mount
WORKDIR /workspaces/flogo-agent-studio
