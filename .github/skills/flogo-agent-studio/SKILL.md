---
name: flogo-agent-studio
description: >
  Work with the Flogo Agent Studio project — a production-grade multi-agent AI platform built
  on TIBCO Flogo 2.26.3. Use for: understanding service architecture; making flow changes via
  the Flogo Design Assistant (FDA); building, starting, and testing Flogo services; running
  integration tests; debugging flow mappings and SSE streaming; adding/modifying activities,
  triggers, connections, and properties in .flogo apps.
---

# Flogo Agent Studio — Working Guide

## Project Location
`/Users/milindpandav/git/flogo-agent-studio`

## Architecture: 10 Flogo Services

| Service | Port | App file | Description |
|---|---|---|---|
| rule-engine | 7000 | `apps/rule-engine-service.flogo` | YAML rule evaluation — POST /api/analyze |
| agent-chat | 7001 | `apps/agent-chat-service.flogo` | RAG + LLM chat — POST /api/chat |
| ingestion | 7002 | `apps/ingestion-service.flogo` | KB ingest (text, URL, GitHub, Confluence) |
| feedback | 7003 | `apps/feedback-service.flogo` | JSONL feedback — POST/GET /api/feedback |
| ~~config~~ | ~~7004~~ | ~~deprecated~~ | **Removed** — superseded by design-service |
| sse-stream | 7005 | `apps/sse-stream-service.flogo` | SSE streaming — POST /api/stream/chat |
| agent-builder | 7010 | `apps/agent-builder-service.flogo` | LLM-generate agent configs |
| design | 7020 | `apps/design-service.flogo` | PostgreSQL-backed agent CRUD |
| deploy | 7030 | `apps/deploy-service.flogo` | K8s/Docker-Compose manifest generation |
| mcp-server | 3333 | `apps/mcp-server.flogo` | MCP server for AI IDE integration |
| sse-eventbus | 7099 | (part of sse-stream) | SSE event bus — GET /events (no auth) |

## Key Environment Variables

```bash
EXT=/Users/milindpandav/.vscode/extensions/tibco.flogo-2.26.3-2442
APPS=/Users/milindpandav/git/flogo-agent-studio/apps
BIN=/Users/milindpandav/git/flogo-agent-studio/bin
UEXT=/Users/milindpandav/git/flogo-custom-extensions
LOGDIR=/tmp/flogo-studio-logs
```

## Auth
- All services: Basic auth `flogo:changeme` → `Basic ZmxvZ286Y2hhbmdlbWU=`
- Header: `-u flogo:changeme` or `-H "Authorization: Basic ZmxvZ286Y2hhbmdlbWU="`
- SSE event bus (7099): **no auth** — public SSE stream

## Infrastructure Dependencies

| Service | Port | Notes |
|---|---|---|
| Weaviate | 18080 | VectorDB, class `KnowledgeBase`, Ollama embeddings |
| Ollama | 11434 | Models: `llama3.1:8b`, `nomic-embed-text` |
| PostgreSQL | 5432 | Container `flogo-studio-postgres`, DB `flogo_agent_studio`, user `flogo` / `changeme` |

## Build a Service

```bash
EXT=/Users/milindpandav/.vscode/extensions/tibco.flogo-2.26.3-2442
APPS=/Users/milindpandav/git/flogo-agent-studio/apps
BIN=/Users/milindpandav/git/flogo-agent-studio/bin
UEXT=/Users/milindpandav/git/flogo-custom-extensions

"$EXT/bin/flogo-vscode-cli" app build \
  -b "$EXT/media/flogo-runtime" \
  -c "$EXT/media/flogo-contributions/wistudio/v1/contributions" \
  -e "$UEXT" \
  -f "$APPS/<service-name>.flogo" \
  -o "$BIN"
```

## Start/Restart All Services

```bash
bash /tmp/start-svcs.sh
```

If `/tmp/start-svcs.sh` is missing, start services manually:

```bash
LOGDIR=/tmp/flogo-studio-logs && mkdir -p $LOGDIR && BIN=/Users/milindpandav/git/flogo-agent-studio/bin

# Kill existing
kill $(lsof -ti :7000 -ti :7001 -ti :7002 -ti :7003 -ti :7005 -ti :7010 -ti :7020 -ti :7030 2>/dev/null) 2>/dev/null

# Start each service
for svc in rule-engine-service agent-chat-service ingestion-service feedback-service sse-stream-service agent-builder-service design-service deploy-service; do
  "$BIN/$svc" > "$LOGDIR/${svc}.log" 2>&1 &
  echo "$svc PID=$!"
done
sleep 2
```

## Check All Services Are Healthy

```bash
for port in 7000 7001 7002 7003 7005 7010 7020 7030; do
  echo -n "Port $port: "
  curl -s -u flogo:changeme http://localhost:$port/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))"
done
```

## FDA (Flogo Design Assistant) — MCP Server

FDA runs as an MCP server. It provides tools for reading and modifying `.flogo` files.

**Starting FDA:**
```bash
cd /Users/milindpandav/git/flogo-agent-studio/apps
flogodesign-cli mcp http 3333 --multiFileMode true
```

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

Agents are stored in PostgreSQL (design-service). The `config` column holds a JSONB object:
```json
{
  "id": "my-agent",
  "name": "My Agent",
  "description": "...",
  "systemPrompt": "You are...",
  "collectionName": "KnowledgeBase",
  "llmProvider": "ollama",
  "model": "llama3.1:8b",
  "temperature": 0.7,
  "maxTokens": 2048,
  "topK": 5,
  "tools": ["ragQuery"],
  "tags": ["general"],
  "version": "1.0.0",
  "active": true
}
```

POST /api/v1/agents (design-service) expects: `{ "name": "...", "description": "...", "config": {...} }`

## Test Agent ID
`0c82e85b-708f-43a8-9b79-acd618474b51` — default test agent in design-service (PostgreSQL)

## Custom Extensions Location
`/Users/milindpandav/git/flogo-custom-extensions`

Key extensions:
- `connectors/SSE/trigger/` — SSE event bus trigger (port 7099)
- `connectors/SSE/activity/` — emit SSE events; valid types: `message, notification, update, alert, status, data, event, error, warning, info, heartbeat, custom`
- `activity/schema-transform/` — JSON schema transformation
- `activity/templateengine/` — Mustache-style templates

## Known Caveats

1. **Flogo expressions in nested objects**: The runtime does NOT evaluate expressions nested inside object values. Use a single top-level expression: `"body": "=$flow.body"` not `"body": {"key": "=$flow.body.key"}`.

2. **#rest vs #agentactivity output**: `#rest` → `$activity[X].responseBody`; `#agentactivity` → `$activity[X].response` (no `.output.` wrapper).

3. **SSE `stream.*` event types** (stream.start, stream.answer, stream.done): Not in the default valid-types enum. Requires `enableValidation: false` on the emit activity.

4. **PostgreSQL activity**: Output is `$activity[X].Output.records` (capital O in Output).

5. **FDA multiFileMode**: FDA must be started with `--multiFileMode true` when working with apps in a directory. Without it, only a single file can be loaded.
