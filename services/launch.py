#!/usr/bin/env python3
"""
Flogo service launcher — injects per-service properties as environment variables.

Usage: python3 services/launch.py <env_file> <binary> [binary_args...]

Reads <env_file> (KEY=VALUE lines, # comments ignored) and merges them into the
current environment, then exec-replaces itself with <binary>.  This allows
setting env var names that contain hyphens or parentheses (e.g.
VECTORDB_VECTORDB-WEAVIATE_TIMEOUT_(SECONDS)) which bash export cannot handle.

When FLOGO_APP_PROPS_ENV=auto is set, Flogo resolves each app property from the
matching env var — if found, no startup WARNING is emitted.
"""
import os
import sys


def load_env_file(path: str) -> dict:
    env = {}
    with open(path) as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            # Skip blank lines and comments
            if not line or line.lstrip().startswith("#"):
                continue
            # Split on first '=' only (values may contain '=' e.g. base64/SECRET:)
            key, sep, value = line.partition("=")
            if not sep:
                continue
            env[key] = value
    return env


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <env_file> <binary> [args...]", file=sys.stderr)
        sys.exit(1)

    env_file = sys.argv[1]
    binary   = sys.argv[2]
    args     = sys.argv[2:]   # binary + its args (argv[0] for the exec)

    # Build merged environment: file provides defaults, current env takes precedence.
    # (e.g. FEEDBACK_DIR / RULES_PATH set explicitly in start-all.sh must not be
    #  overridden by the per-service .env file defaults.)
    merged = {}

    if os.path.isfile(env_file):
        merged.update(load_env_file(env_file))

    # Current environment wins (OTel vars, FLOGO_* vars, explicit overrides)
    merged.update(os.environ)

    os.execve(binary, args, merged)


if __name__ == "__main__":
    main()
