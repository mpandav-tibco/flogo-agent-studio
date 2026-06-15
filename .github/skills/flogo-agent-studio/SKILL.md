---
name: flogo-agent-studio
description: >
  Work with the Flogents (Flogo Agent Studio) project — a production-grade multi-agent AI
  platform built on TIBCO Flogo 2.26.3. Use for: understanding service architecture; making
  flow changes via the Flogo Design Assistant (FDA); building, starting, and testing Flogo
  services; running integration tests; debugging flow mappings and SSE streaming;
  adding/modifying activities, triggers, connections, and properties in .flogo apps.
---

# Flogents (Flogo Agent Studio) — Working Guide

## Project Location
`/Users/milindpandav/git/flogo-agent-studio`

## Architecture: Platform Services + Per-Agent Runtime

### Platform Services (static ports, always running)

| Service | Port | Flogo file | Description |
|---|---|---|---|
| platform-service | 7020 | `services/platform/flogo/platform-service.flogo` | Agent CRUD, templates, feedback — `/api/v1/agents/*`, `/api/feedback/*` |
| agent-builder | 7010 | `services/platform/flogo/agent-builder-service.flogo` | LLM-generated agent configs — POST `/api/agent-builder/generate\|improve\|validate` |
| mcp-server | 7333 | `services/platform/flogo/mcp-server.flogo` | MCP server at `/mcp` — `analyze_flogo`, `rag_chat`, `list_agents`, `get_agent`, `submit_feedback`, `get_feedback`, `create_agent`, `list_templates`, `deploy_agent` |
| forge-ui | 7025 | `services/platform/ui/forge` | Flogents React UI |
| runtime-manager | 7050 | `deployment/deployment.py` | Manages per-agent process groups — Python aiohttp |

### Per-Agent Services (dynamic ports, one set per deployed agent)

Each deployed (active) agent gets a slot N → base port `7200 + N×10`. Up to 10 concurrent agents.

| Role | Offset | Service binary | Description |
|---|---|---|---|
| chat | base+1 | `bin/agent-chat-service` | RAG chat — POST `/api/chat` |
| sse-rest | base+2 | `bin/agent-chat-service` | SSE REST trigger (same binary, different port) |
| sse-events | base+3 | `bin/agent-chat-service` | SSE event bus — GET `/events` (merged binary) |
| ingestion | base+4 | `bin/ingestion-service` | KB ingest — POST `/api/ingest` |
| chainlit | base+5 | `chainlit run` | Per-agent Chainlit UI with baked-in AGENT_ID |
| rule-engine | base+6 | `bin/rule-engine-service` | YAML rule evaluation — POST `/api/analyze` |

**Slot 0 example**: chat=7201, sse-rest=7202, sse-events=7203, ingest=7204, chainlit=7205, rule-engine=7206

Resolve actual ports: `GET http://localhost:7050/api/agents/{id}` → fields `chatApiUrl`, `sseUrl`, `ingestionUrl`, `ruleEngineUrl`

## Key Paths

```bash
PROJECT=/Users/milindpandav/git/flogo-agent-studio
EXT=/Users/milindpandav/.vscode/extensions/tibco.flogo-2.26.3-2442
FLOGOBUILD=$PROJECT/tools/flogobuild/darwin_arm64/flogobuild
UEXT=/Users/milindpandav/git/flogo-custom-extensions
```

## Auth
- All Flogo services: Basic auth `flogo:changeme` → `Basic ZmxvZ286Y2hhbmdlbWU=`
- Header: `-u flogo:changeme` or `-H "Authorization: Basic ZmxvZ286Y2hhbmdlbWU="`
- SSE event bus (base+3): **no auth** — public SSE stream
- Runtime manager (7050): no auth

## Infrastructure Dependencies

| Service | Port | Notes |
|---|---|---|
| Weaviate | 18080 | VectorDB, Ollama embeddings (nomic-embed-text 768-dim) |
| Ollama | 11434 | Models: `llama3.1:8b`, `nomic-embed-text`; start with `ollama serve` |
| PostgreSQL | 5432 | Container `flogo-studio-postgres`, DB `flogo_agent_studio`, user `flogo`/`changeme` |

## Build a Service (flogobuild)

```bash
cd /Users/milindpandav/git/flogo-agent-studio

# Build any platform service
PATH="tools/go-wrapper:$PATH" ./tools/flogobuild/darwin_arm64/flogobuild build-exe \
  -f services/platform/flogo/<service-name>.flogo \
  -c flogo-studio \
  -n <service-name> \
  -o bin/

# Build any per-agent service
PATH="tools/go-wrapper:$PATH" ./tools/flogobuild/darwin_arm64/flogobuild build-exe \
  -f services/agent/flogo/<service-name>.flogo \
  -c flogo-studio \
  -n <service-name> \
  -o bin/
```

**IMPORTANT**: The `tools/go-wrapper/go` shim is required — without it, `go mod tidy` fails on private TIBCO dependencies. Always prepend `tools/go-wrapper` to `PATH`.

**After building**: If macOS shows "killed" (exit 137) or `codesign -v` reports "invalid signature", re-sign:
```bash
codesign --remove-signature bin/<service-name>
codesign --sign - bin/<service-name>
```

## Start/Restart All Services

```bash
cd /Users/milindpandav/git/flogo-agent-studio
bash deployment/start-all.sh
```

This starts: Docker infra (Weaviate, PostgreSQL), platform Flogo services, forge-ui, and the runtime manager. Logs go to `logs/`.

**Start runtime manager only** (after it dies):
```bash
pkill -f "deployment.py"
python3 deployment/deployment.py --port 7050 >> logs/runtime-manager.log 2>&1 &
```

## Runtime Manager API (port 7050)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Runtime manager health |
| GET | `/api/agents` | List all agent runtimes with ports and readiness |
| GET | `/api/agents/{id}` | Get single agent runtime — includes `chatApiUrl`, `sseUrl`, `ingestionUrl`, `ruleEngineUrl` |
| POST | `/api/agents/{id}/start` | Start per-agent services |
| DELETE | `/api/agents/{id}/stop` | Stop per-agent services |
| GET | `/api/runtime/agents/{id}/logs/{service}` | Stream logs for a per-agent service (`chat`, `ingestion`, `rule-engine`, `chainlit`) |
| GET | `/api/runtime/platform-logs/{service}` | Stream logs for a platform service (`platform-service`, `agent-builder`, `mcp-server`) |
| GET | `/api/admin/services` | List platform service status |
| POST | `/api/admin/services/{name}/start` | Start a platform service |
| POST | `/api/admin/services/{name}/restart` | Restart a platform service |
| DELETE | `/api/admin/services/{name}/stop` | Stop a platform service |

Agent `readiness` field: `pending` → `starting` → `ready` (poll until `ready` before calling per-agent APIs).

## Check All Services Are Healthy

```bash
# Platform services
for port in 7020 7010 7333; do
  echo -n "Port $port: "
  curl -s -u flogo:changeme http://localhost:$port/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "DOWN"
done

# Per-agent services (slot 0)
for port in 7201 7204 7206; do
  echo -n "Port $port: "
  curl -s -u flogo:changeme http://localhost:$port/api/health 2>/dev/null || echo "DOWN"
done

# Runtime manager
curl -s http://localhost:7050/api/agents | python3 -c "import json,sys; [print(a['agentName'], a['readiness']) for a in json.load(sys.stdin)]"
```

## FDA (Flogo Design Assistant) — MCP Server

FDA runs as an MCP server. It provides tools for reading and modifying `.flogo` files.

**Starting FDA:**
```bash
cd /Users/milindpandav/git/flogo-agent-studio/services
flogodesign-cli mcp http 3333 --multiFileMode true
```

The FDA server at `http://localhost:3333/mcp` supports multi-file mode covering all `.flogo` files under `services/`.

**All .flogo app changes MUST go through FDA tools** — never direct JSON edits for flow logic.

### Key FDA Patterns

**Change an activity's input field:**
```
mcp_flogo-assista_set-attribute(
  flogoFile: "agent-chat-service.flogo",
  itemType: "activity",
  itemSelector: "chat.ActivityName.input.fieldName",
  configValue: "=$activity[Other].response",
  configType: "string"
)
```

**Change a property value:**
```
mcp_flogo-assista_set-attribute(
  flogoFile: "agent-builder-service.flogo",
  itemType: "property",
  itemSelector: "PROPERTY_NAME.value",
  configValue: "http://localhost:7020",
  configType: "string"
)
```

**Add an activity:**
```
mcp_flogo-assista_create-activity(
  flogoFile: "agent-chat-service.flogo",
  flowName: "chat",
  activityName: "MapResult",
  activityDescription: "Map output fields",
  activityType: "#mapper"
)
```

**Add/remove links:**
```
mcp_flogo-assista_create-link(flogoFile, flowName, fromActivityName, toActivityName)
mcp_flogo-assista_remove-link(flogoFile, flowName, fromActivityName, toActivityName)
```

### FDA set-attribute — JSON String Caveat

FDA `set-attribute` always stores complex JSON values as **quoted strings** (double-serialized). After any FDA set-attribute call that sets a JSON object/array, verify and fix with Python:

```python
import json
with open('path/to/service.flogo') as f:
    data = json.load(f)

# Navigate to the field, then:
for res in data['resources']:
    for task in res['data']['tasks']:
        v = task['activity']['input'].get('someField')
        if isinstance(v, str):
            task['activity']['input']['someField'] = json.loads(v)

with open('path/to/service.flogo', 'w') as f:
    json.dump(data, f, indent=2)
```

**After any direct JSON unquoting, must still use FDA check tool to validate.**

## Activity Reference Types

| Ref | Output path | Notes |
|---|---|---|
| `#rest` | `$activity[X].responseBody` | TIBCO REST; also `.status` for HTTP code |
| `#agentactivity` | `$activity[X].response` | Direct `.response` — NOT `.output.response` |
| `#mapper` | `$activity[X].output` | Mapper; access fields via `$activity[X].output.fieldName` |
| `#actreturn` | — | Return activity; settings has `code` (HTTP status) and `data` |
| `#log` | — | Log activity |
| `#query` | `$activity[X].Output.records` | PostgreSQL query activity |

## Common Debugging

**Check flow mappings:**
```
mcp_flogo-assista_check(flogoFile: "service-name.flogo")
```
This returns all mapping errors across all flows. All should be 0 errors before building.

**Tail a service log:**
```bash
tail -20 /tmp/flogo-studio-logs/<service>.log
```

**Check if a port is listening:**
```bash
lsof -i :<port> | grep LISTEN
```

## Agent Config Format

Agents are stored in PostgreSQL (platform-service). The `config` column holds a JSONB object:
```json
{
  "id": "my-agent",
  "name": "My Agent",
  "description": "...",
  "systemPrompt": "You are...",
  "collectionName": "KnowledgeBase",
  "llmProvider": "Ollama",
  "llmModel": "llama3.1:8b",
  "llmBaseUrl": "http://localhost:11434/v1",
  "embeddingModel": "nomic-embed-text",
  "embeddingProvider": "Ollama",
  "embeddingBaseUrl": "http://localhost:11434/v1",
  "temperature": 0.7,
  "maxTokens": 2048,
  "topK": 5,
  "chunkStrategy": "sentence",
  "tools": ["ragQuery"],
  "tags": ["general"],
  "version": "1.0.0",
  "active": true
}
```

POST `/api/v1/agents` (platform-service) expects: `{ "name": "...", "description": "...", "config": {...} }`

Activate a deployed agent: `PUT /api/v1/agents/{id}` with `{ "status": "active" }` — this triggers the runtime manager to start per-agent services.

## Custom Extensions Location
`/Users/milindpandav/git/flogo-custom-extensions`

Key extensions:
- `connectors/SSE/trigger/` — SSE event bus trigger (merged into agent-chat-service, port base+3)
- `connectors/SSE/activity/` — emit SSE events; valid types: `message, notification, update, alert, status, data, event, error, warning, info, heartbeat, custom`
- `connectors/VectorDB/` — Weaviate VectorDB connection (vectordb-weaviate)
- `activity/schema-transform/` — JSON schema transformation
- `activity/templateengine/` — Mustache-style templates

## Known Caveats

1. **Flogo expressions in nested objects**: The runtime does NOT evaluate expressions nested inside object values. Use a single top-level expression: `"body": "=$flow.body"` not `"body": {"key": "=$flow.body.key"}`.

2. **#rest vs #agentactivity output**: `#rest` → `$activity[X].responseBody`; `#agentactivity` → `$activity[X].response` (no `.output.` wrapper).

3. **SSE `stream.*` event types** (stream.start, stream.answer, stream.done): Not in the default valid-types enum. Requires `enableValidation: false` on the emit activity.

4. **PostgreSQL activity**: Output is `$activity[X].Output.records` (capital O in Output).

5. **FDA multiFileMode**: FDA must be started with `--multiFileMode true` when working with apps in a directory. Without it, only a single file can be loaded.

6. **`wire-trigger-handler` DANGER — whole-file corruption**: `wire-trigger-handler` triggers whole-file normalization on EVERY call and has known side effects: reorders tasks across ALL flows (not just the target), restructures links, mangles UTF-8 in descriptions, adds `reply.data = {type:json,value:{}}` scaffold to the handler. **DO NOT use `wire-trigger-handler` to fix `metadata.output` mismatches.** It can swap Return/ErrorReturn task content in sibling flows, causing silent runtime failures.

7. **Correct fix for `metadata.output` mismatch** (FDA check-mapping error: `$.data` not in flow outputs): Use `flogodesign-cli set-attribute flow --jsonValue` via terminal — NOT MCP `set-attribute` and NOT `wire-trigger-handler`:
   ```bash
   FDA=/Users/milindpandav/.vscode/extensions/tibco.flogo-2.26.3-2442/bin/flogodesign-cli
   "$FDA" -f services/<path>/service.flogo set-attribute flow \
     "<FlowName>.metadata.output" "" \
     --jsonValue '[{"name":"code","type":"integer"},{"name":"data","type":"object"}]'
   ```
   Unlock (`chmod 644`) before running, relock (`chmod 444`) after for platform-service and agent-builder-service.

8. **MCP `set-attribute` + JSON arrays/objects**: MCP `set-attribute` requires `configType` (string/number/boolean). Using `configType: "string"` for a JSON array stores it as a **quoted string literal** — breaking fields like `metadata.output` or `metadata.input`. For JSON values, always use the FDA CLI with `--jsonValue`.

9. **FDA whole-file normalization**: Every FDA call (MCP or CLI) rewrites the whole file: UTF-8 em-dashes may re-encode, link labels added, task JSON order may change, `{"mapping":"=expr"}` body wrappers unwrap to `"=expr"`. These are cosmetic/structural — runtime behavior is unaffected. Accept them as part of FDA file ownership.

10. **`#agentactivity` output path**: `$activity[X].response` — NOT `$activity[X].output.response`. FDA does not flag this error (null output schema) but it IS a runtime bug. Always use the short path.
