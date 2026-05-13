"""One-shot: import agents/*.json files into design-service PostgreSQL.

Usage:
    python scripts/migrate_agents.py [--activate]

Options:
    --activate   Set imported agents to status='active' via the deploy endpoint.
"""
import argparse
import glob
import json
import os
import sys

import httpx

DESIGN_URL = os.getenv("DESIGN_URL", "http://localhost:7020")
AUTH = "Basic ZmxvZ286Y2hhbmdlbWU="
HEADERS = {"Authorization": AUTH, "Content-Type": "application/json"}
AGENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "agents")

# Fields that belong to the identity/metadata layer, not the agent config
IDENTITY_KEYS = {"id", "name", "description", "created", "version", "active", "tags"}


def migrate(activate: bool = False):
    # Only migrate named (non-UUID) agent files
    all_files = glob.glob(os.path.join(AGENTS_DIR, "*.json"))
    files = [f for f in all_files if "-" not in os.path.basename(f)]
    print(f"Found {len(files)} named agent file(s) to migrate (skipping UUID-named files)")

    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        agent_id = data.get("id", os.path.splitext(os.path.basename(path))[0])

        # Check if already exists by ID (design-service uses UUID IDs, not named ones)
        # Search by name instead
        name = data.get("name", agent_id)
        try:
            list_r = httpx.get(f"{DESIGN_URL}/api/v1/agents", headers=HEADERS, timeout=10)
            existing = list_r.json() if list_r.status_code == 200 else []
            if isinstance(existing, list):
                match = next((a for a in existing if a.get("name") == name), None)
            else:
                match = None
        except Exception as e:
            print(f"  WARN  could not check existing: {e}")
            match = None

        if match:
            new_id = match.get("id", "?")
            print(f"  SKIP  {agent_id} (already exists as {new_id} with name='{name}')")
            continue

        # Build config by stripping identity/metadata fields
        config = {k: v for k, v in data.items() if k not in IDENTITY_KEYS}

        payload = {
            "name": name,
            "description": data.get("description", ""),
            "config": config,
        }

        try:
            r = httpx.post(f"{DESIGN_URL}/api/v1/agents", json=payload, headers=HEADERS, timeout=10)
        except Exception as e:
            print(f"  FAIL  {agent_id}: connection error: {e}")
            continue

        if r.status_code in (200, 201):
            body = r.json()
            # design-service now returns single agent (after our fix)
            if isinstance(body, dict) and body.get("id"):
                new_id = body["id"]
            elif isinstance(body, dict):
                records = body.get("records", [body])
                new_id = records[0].get("id", "?") if records else "?"
            else:
                new_id = "?"
            print(f"  OK    {agent_id} -> {new_id}")

            if activate and new_id != "?":
                try:
                    dep = httpx.put(
                        f"{DESIGN_URL}/api/v1/agents/{new_id}",
                        json={"status": "active"},
                        headers=HEADERS,
                        timeout=10,
                    )
                    if dep.status_code in (200, 201):
                        print(f"  ACTIVE {new_id}")
                    else:
                        print(f"  WARN  activate failed for {new_id}: {dep.status_code}")
                except Exception as e:
                    print(f"  WARN  activate error for {new_id}: {e}")
        else:
            print(f"  FAIL  {agent_id}: {r.status_code} {r.text[:120]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate agents/*.json -> design-service PostgreSQL")
    parser.add_argument("--activate", action="store_true", help="Activate imported agents")
    args = parser.parse_args()
    migrate(activate=args.activate)
