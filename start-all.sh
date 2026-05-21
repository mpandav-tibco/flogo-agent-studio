#!/usr/bin/env bash
# Convenience wrapper — delegates to deployment/start-all.sh
exec "$(dirname "${BASH_SOURCE[0]}")/deployment/start-all.sh" "$@"
