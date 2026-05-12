# Flogo Agent Studio

A production-grade, multi-agent AI platform built entirely on **TIBCO Flogo 2.26.3**. Every service — from RAG retrieval to SSE streaming to MCP tool exposure — is a compiled Flogo application running as a standalone binary with no runtime dependencies.

---

## Use Case

Enterprises need AI agents that can answer questions from proprietary knowledge bases, analyze application artifacts, and integrate with modern AI tooling (Claude, Cursor, VS Code Copilot). Flogo Agent Studio provides:

- **Knowledge-grounded Q&A** — ingest documents into a vector database, retrieve the most relevant chunks, and generate accurate answers using a local LLM (Ollama) or cloud LLM.
- **Flogo app quality analysis** — submit a `.flogo` file and receive a structured rule-engine report (errors, warnings, info) in milliseconds.
- **Multi-agent configuration** — register multiple specialized agents (FAQ, support, analyzer) and route user queries to the right one via a shared UI or API.
- **Real-time streaming** — stream LLM responses token-by-token to any browser via Server-Sent Events (SSE).
- **AI IDE integration** — expose the full capability surface as MCP tools so Claude Code, Cursor, and other AI assistants can call them natively.
- **User feedback loop** — collect and store per-agent ratings and comments in JSONL format for offline analysis and agent improvement.

---

## Architecture Overview

```
  +------------------+          +------------------------+
  |  Chainlit UI     |          |  Claude / Cursor / IDE |
  |  (port 7080)     |          |  (MCP client)          |
  +--------+---------+          +----------+-------------+
           |  REST                         |  JSON-RPC 2.0 (SSE)
           v                               v
  +--------+---------------------------------------------------+
  |                     API  GATEWAY  LAYER                     |
  |                                                             |
  |   config-service :7004       mcp-server :3333               |
  |   (agent registry)           (MCP protocol gateway)         |
  +--------+----------------------------+------------------------+
           |                            |
           v                            v (proxies to internal REST)
  +--------+----------------------------+------------------------+
  |                  SERVICE  MESH  (Flogo binaries)            |
  |                                                             |
  |   agent-chat-service  :7001    ingestion-service  :7002     |
  |   (RAG + LLM answer)           (document loading)          |
  |                                                             |
  |   rule-engine-service :7000    feedback-service   :7003     |
  |   (YAML rule analysis)         (ratings + comments)        |
  |                                                             |
  |   agent-builder-service :7010  sse-stream-service :7005     |
  |   (LLM config generation)      (SSE streaming pipeline)    |
  +--------+----------------------------+------------------------+
           |                            |
           v                            v
  +--------+----+          +-----------+---------+
  |  Weaviate   |          |  Ollama              |
  |  VectorDB   |          |  (local LLM runtime) |
  |  :8080      |          |  :11434              |
  +-------------+          +---------------------+
```

---

## Technical Architecture

### Runtime

All Flogo services are compiled to native Go binaries via `flogobuild`. Each service is a self-contained Flogo engine that:
- Starts in ~20–44ms
- Exposes a REST trigger (TIBCO Flogo REST trigger)
- Executes flows in response to HTTP requests
- Uses OOTB activities (REST, File, Log, VectorDB, AgentActivity, SSE)
- Produces structured logs in JSON (stderr) and writes output to stdout

### Data Flow

#### RAG Chat Pipeline

```
User question
    |
    v
agent-chat-service :7001
    |-- [Log] request metadata
    |-- [VectorDB RAGQuery] embed question (nomic-embed-text, 768-dim)
    |                       --> Weaviate :8080 (BM25 + vector hybrid search)
    |                       <-- top-K chunks
    |-- [AgentActivity] build prompt + call LLM
    |                   --> Ollama :11434 (nemotron-3-nano:30b or user-chosen model)
    |                   <-- generated answer
    |-- [actreturn] HTTP 200 {answer, duration, error}
```

#### SSE Streaming Pipeline

```
POST /api/stream/chat  (sse-stream-service :7005)
    |-- [Log] session/collection metadata
    |-- [SSE Send] emit "stream.start" event --> :7099/events
    |-- [REST] POST agent-chat-service :7001/api/chat  (RAG answer)
    |-- [AgentActivity] RunLLM: context-aware response (nemotron-3-nano:30b)
    |                   Agent SDK: input=112 tok, output=98 tok, 1074ms
    |-- [SSE Send] emit "stream.answer" event --> :7099/events
    |-- [SSE Send] emit "stream.done"   event --> :7099/events
    |-- [actreturn] HTTP 202 {streaming:true, eventsUrl:"/events"}

Browser/client connects to GET :7099/events  (SSE trigger)
    --> receives stream.start, stream.answer, stream.done events
```

#### MCP Tool Invocation

```
MCP Client (Claude, Cursor)
    |-- POST :3333/mcp  JSON-RPC initialize
    |       <-- mcp-session-id header
    |-- POST :3333/mcp  notifications/initialized
    |-- POST :3333/mcp  tools/list
    |       <-- 6 tools: analyze_flogo, rag_chat, list_agents,
    |                    get_agent, submit_feedback, get_feedback
    |-- POST :3333/mcp  tools/call {name, arguments}
            |
            v  (mcp-server proxies to internal REST services)
            +-- analyze_flogo   --> rule-engine-service :7000/api/analyze
            +-- rag_chat        --> agent-chat-service  :7001/api/chat
            +-- list_agents     --> config-service      :7004/api/agents
            +-- get_agent       --> config-service      :7004/api/agents/{id}
            +-- submit_feedback --> feedback-service    :7003/api/feedback
            +-- get_feedback    --> feedback-service    :7003/api/feedback
```

---

## Services

| Service | Port | Binary | Purpose |
|---|---|---|---|
| rule-engine-service | 7000 | `bin/apps/rule-engine-service.exe` | YAML rule evaluation against Flogo app JSON |
| agent-chat-service | 7001 | `bin/apps/agent-chat-service.exe` | RAG query: embed → Weaviate → LLM answer |
| ingestion-service | 7002 | `bin/apps/ingestion-service.exe` | Document ingestion into Weaviate |
| feedback-service | 7003 | `bin/apps/feedback-service.exe` | Per-agent feedback storage (JSONL) |
| config-service | 7004 | `bin/apps/config-service.exe` | Multi-agent JSON configuration registry |
| sse-stream-service | 7005 | `bin/apps/sse-stream-service.exe` | SSE broadcast + RAG+LLM streaming pipeline |
| agent-builder-service | 7010 | `bin/apps/agent-builder-service.exe` | LLM-generated agent configurations |
| mcp-server | 3333 | `bin/apps/mcp-server.exe` | MCP gateway (JSON-RPC 2.0 over SSE transport) |
| chainlit-ui | 7080 | `chainlit/app.py` | Browser chat UI (Python, Chainlit) |

### External Dependencies

| Dependency | Port | Purpose |
|---|---|---|
| Weaviate | 8080 / 50051 | Vector database (HTTP + gRPC) |
| Ollama | 11434 | Local LLM and embedding runtime |

### Embedding and LLM Models

| Use | Model | Notes |
|---|---|---|
| Document/query embedding | `nomic-embed-text` (Ollama) | 768-dimension vectors |
| RAG answer generation | configured per-agent | `gemma3:4b-cloud` default in agent-chat |
| Agent config generation | `nemotron-3-nano:30b` | Best for structured JSON output |
| SSE stream LLM | `nemotron-3-nano:30b` | Context-aware streaming responses |

---

## API Reference

### rule-engine-service (port 7000)

```
POST /api/analyze
{
  "content": "<flogo app JSON as string>",
  "fileName": "my-app.flogo",
  "rulesPath": "rules/",
  "tags": "production,ai-agent"
}
--> { "success": true, "errorCount": 0, "warningCount": 0, "infoCount": 0,
      "overview": { "rules_run": 2, "parser": "json" } }

GET  /api/health
```

### agent-chat-service (port 7001)

```
POST /api/chat
{ "message": "What is TIBCO Flogo?", "collectionName": "KnowledgeBase" }
--> { "answer": "...", "duration": "225ms", "error": "" }

POST /api/chat/retrieve      (retrieve chunks only, no LLM)
POST /api/chat/agent         (full AgentActivity with tools)
GET  /api/health
```

### ingestion-service (port 7002)

```
POST /api/ingest
{
  "collectionName": "KnowledgeBase",
  "documents": [
    { "text": "...", "metadata": { "source": "my-doc", "page": 1 } }
  ]
}
--> { "chunksCreated": 1, "ingestedCount": 1, "dimensions": 768,
      "duration": "2.22s", "vectorId": "5a6c220b-..." }

POST /api/ingest/collection  (create collection only)
GET  /api/health
```

### feedback-service (port 7003)

```
POST /api/feedback
{ "agentId": "default", "rating": 5, "comment": "...", "sessionId": "..." }
--> { "fullPath": "feedback/default.jsonl" }

GET  /api/feedback/{agentId}   --> last feedback record for this agent
GET  /api/feedback             --> all feedback records
GET  /api/health
```

### config-service (port 7004)

```
GET  /api/agents               --> { "agents": [{ "fileName": "default.json", ... }] }
GET  /api/agents/{agentId}     --> { "config": "{...JSON string...}" }
POST /api/agents               --> create new agent config
PUT  /api/agents/{agentId}     --> update existing agent config
DEL  /api/agents/{agentId}     --> remove agent config
GET  /api/health
```

### sse-stream-service (port 7005 + SSE on 7099)

```
POST /api/stream/chat
{ "message": "...", "collectionName": "KnowledgeBase", "sessionId": "..." }
--> HTTP 202 { "streaming": true, "eventsUrl": "/events" }
    (SSE events emitted on :7099/events: stream.start, stream.answer, stream.done)

POST /api/stream/broadcast
{ "eventType": "session.start", "sessionId": "...", "data": {...} }
--> HTTP 200 { "broadcasted": true }

GET  :7099/events    (SSE stream endpoint for browser EventSource)
GET  /api/health
```

### agent-builder-service (port 7010)

```
POST /api/agent-builder/generate
{ "prompt": "Create a FAQ agent for ...", "model": "nemotron-mini-4b-instruct" }
--> { "config": { "id": "...", "name": "...", "collectionName": "...", ... } }

POST /api/agent-builder/improve   (refine existing config with LLM)
POST /api/agent-builder/validate  (validate config structure)
GET  /api/health
```

### mcp-server (port 3333)

MCP Streamable HTTP transport. Session established via `initialize` → `notifications/initialized`, then `tools/call`:

| Tool | Arguments | Proxies to |
|---|---|---|
| `list_agents` | _(none)_ | `config-service GET /api/agents` |
| `get_agent` | `agentId` | `config-service GET /api/agents/{id}` |
| `submit_feedback` | `agentId, rating, comment, sessionId` | `feedback-service POST /api/feedback` |
| `get_feedback` | `agentId` | `feedback-service GET /api/feedback` |
| `rag_chat` | `message, collectionName` | `agent-chat-service POST /api/chat` |
| `analyze_flogo` | `content, fileName, rulesPath, tags` | `rule-engine-service POST /api/analyze` |

---

## File Structure

```
flogo-agent-studio/
├── apps/                        # Flogo source files (.flogo)
│   ├── rule-engine-service.flogo
│   ├── agent-chat-service.flogo
│   ├── ingestion-service.flogo
│   ├── feedback-service.flogo
│   ├── config-service.flogo
│   ├── sse-stream-service.flogo
│   ├── agent-builder-service.flogo
│   └── mcp-server.flogo
├── bin/
│   └── apps/                    # Compiled binaries (flogobuild output)
│       ├── rule-engine-service.exe
│       ├── agent-chat-service.exe
│       └── ...
├── agents/                      # Agent configuration registry (JSON)
│   ├── default.json
│   ├── flogo-analyzer.json
│   ├── support-agent-v1.json
│   └── test-e2e-agent.json
├── rules/                       # YAML rule definitions for rule-engine
│   ├── flogo/
│   ├── custom/
│   └── ...
├── feedback/                    # Feedback storage (JSONL, per-agent)
│   └── default.jsonl
├── chainlit/                    # Browser UI
│   ├── app.py                   # Chainlit proxy (Python)
│   ├── chainlit.md              # Welcome page
│   └── Dockerfile
├── logs/                        # Runtime logs (created on start)
│   ├── rule-engine.log / rule-engine-err.log
│   ├── agent-chat.log  / agent-chat-err.log
│   └── ...
├── docker-compose.yml           # Full-stack container orchestration
├── ports.yaml                   # Canonical port registry
├── start-all.ps1                # Windows: start all services with log capture
├── e2e_test.py                  # Integration tests (30 tests)
├── e2e_journey.py               # Single-scenario E2E journey test (19 steps)
└── show_results.py              # Live service test log runner
```

---

## Quick Start

### Prerequisites

- Weaviate running on `localhost:8080` (via Docker: `docker compose up weaviate -d`)
- Ollama running on `localhost:11434` with models pulled:
  ```
  ollama pull nomic-embed-text
  ollama pull nemotron-3-nano:30b
  ```
- Flogo binaries built with `flogobuild` (see Building section)

### Start all services (Windows)

```powershell
cd flogo-agent-studio
.\start-all.ps1
```

This starts all 8 Flogo services with stdout/stderr redirected to `logs/`.

### Start all services (Docker Compose)

```bash
docker compose up -d
```

Brings up Weaviate, Ollama, all 8 Flogo services, and the Chainlit UI.

### Verify everything is healthy

```bash
python show_results.py
```

### Run the integration test suite

```bash
python e2e_test.py        # 30-test suite covering all services and MCP tools
python e2e_journey.py     # Single end-to-end scenario across all 8 services
```

---

## Building from Source

Flogo services are built using `flogobuild`:

```bash
# Build a single service
flogobuild build-exe -f apps/rule-engine-service.flogo -c flogo-v2263-2498 -o ./bin

# Build all services
for svc in rule-engine-service agent-chat-service ingestion-service \
           feedback-service config-service sse-stream-service \
           agent-builder-service mcp-server; do
  flogobuild build-exe -f apps/${svc}.flogo -c flogo-v2263-2498 -o ./bin
done
```

Build context `flogo-v2263-2498` corresponds to TIBCO Flogo 2.26.3 build 2498.

---

## MCP Integration (Claude Code / Cursor)

Add the MCP server to your `.mcp.json`:

```json
{
  "mcpServers": {
    "flogo-dev-assist": {
      "type": "http",
      "url": "http://localhost:3333/mcp"
    }
  }
}
```

Claude and Cursor will then have access to all 6 tools: `analyze_flogo`, `rag_chat`, `list_agents`, `get_agent`, `submit_feedback`, `get_feedback`.

---

## Agent Configuration Format

Agents are stored as JSON files in `agents/`. Example:

```json
{
  "id": "default",
  "name": "Default Assistant",
  "description": "General-purpose knowledge base assistant.",
  "collectionName": "KnowledgeBase",
  "model": "gemma3:4b-cloud",
  "maxTokens": 1024,
  "active": true,
  "chunkStrategy": "heading",
  "systemPrompt": "You are a helpful assistant that answers questions from a knowledge base."
}
```

---

## E2E Test Results

Verified 2026-05-12 with all 8 services running on `localhost`.

```
e2e_test.py     : 30/30 tests PASSED
e2e_journey.py  : 19/19 steps PASSED  (9.9 seconds wall clock)
```

### Journey steps and timings

| Phase | Step | Service | Duration |
|---|---|---|---|
| 1 | Health check ×8 | All services | ~1–39ms each |
| 2 | List agents | config-service | 1.5ms |
| 2 | Get default agent config | config-service | 1.5ms |
| 3 | Ingest AgenticAI doc → Weaviate | ingestion-service | 2,237ms |
| 4 | Run quality rules (2 rules) | rule-engine-service | 4.8ms |
| 5 | RAG query → Weaviate → LLM answer | agent-chat-service | 1,072ms |
| 6 | Submit feedback (rating=5) | feedback-service | 6.2ms |
| 6 | Retrieve feedback records | feedback-service | 10.8ms |
| 7 | LLM-generate agent config (937 tokens) | agent-builder-service | 3,022ms |
| 8 | Broadcast SSE event | sse-stream-service | 1.5ms |
| 8 | Stream chat: RAG+LLM pipeline | sse-stream-service | 2,174ms |
| 9 | MCP initialize + 6 tool calls | mcp-server | ~1–1,061ms each |

### LLM token usage (from agent-builder-service and sse-stream-service logs)

| Call | Model | Input tokens | Output tokens | Total | Time |
|---|---|---|---|---|---|
| Agent config generation | nemotron-3-nano:30b | 256 | 681 | 937 | 3,002ms |
| Stream chat LLM stage | nemotron-3-nano:30b | 112 | 98 | 210 | 1,074ms |

---

## Key Design Decisions

**Why TIBCO Flogo for all services?**
Each service is a visual flow — trigger → activities → return — compiled to a Go binary. No Node.js, no Python runtime, no JVM. Every service starts in under 50ms and runs at native Go speed.

**Why Weaviate + nomic-embed-text?**
Weaviate provides hybrid search (BM25 + vector) out of the box. `nomic-embed-text` runs locally via Ollama and produces 768-dimension embeddings — no external API calls needed for knowledge retrieval.

**Why nemotron-3-nano:30b for structured generation?**
The agent-builder-service requires the LLM to output valid JSON. `nemotron-3-nano:30b` reliably produces bare JSON without wrapping it in markdown code fences, which smaller models (e.g. `gemma3:4b`) do incorrectly.

**Why MCP over direct REST for AI IDE integration?**
MCP is the standard protocol for AI tool use. By wrapping the REST services behind a single MCP server, any MCP-capable client (Claude Code, Cursor, VS Code Copilot) gains access to all capabilities through native tool calls — no custom plugin required.

**Flogo expression evaluation caveat:**
The Flogo runtime does not evaluate expressions nested inside object values. The correct pattern for passing a request body through a REST activity is a single top-level expression: `"body": "=$flow.body"`. REST activity output is accessed as `$activity[Name].responseBody` (not `.output.responseBody`).
