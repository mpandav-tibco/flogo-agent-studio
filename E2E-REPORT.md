# Flogo Agent Studio — End-to-End Test Report

**Date:** 2026-05-13  
**Platform:** TIBCO Flogo 2.26.3  
**Build Context:** flogo-v2263-2498  
**LLM Backend:** Ollama (local)  
**Embedding Model:** `nomic-embed-text:latest` (768 dims)  
**Chat/Agent Model:** `llama3.2:3b`  
**VectorDB:** Weaviate (local)  
**Auth:** Basic Auth — `Authorization: Basic ZmxvZ286Y2hhbmdlbWU=` (`flogo:changeme`)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Flogo Agent Studio Services                   │
├─────────────────┬───────────────────────────────────────────────┤
│ Service         │ Port  │ Purpose                               │
├─────────────────┼───────┼───────────────────────────────────────┤
│ rule-engine     │ 7000  │ Code/config analysis with rules        │
│ agent-chat      │ 7001  │ RAG chat + agent interactions          │
│ ingestion       │ 7002  │ Document ingest into VectorDB          │
│ feedback        │ 7003  │ User feedback storage (JSONL files)    │
│ config          │ 7004  │ Agent config CRUD (JSON files)         │
│ sse-stream      │ 7005  │ SSE streaming chat                     │
│ agent-builder   │ 7010  │ LLM-driven agent generation/improve    │
│ design-service  │ 7020  │ Agent lifecycle mgmt (PostgreSQL)      │
│ deploy-service  │ 7030  │ Agent activation/export                │
│ mcp-server      │ 3333  │ MCP protocol server                    │
└─────────────────┴───────┴───────────────────────────────────────┘
```

### Service Interaction Diagram

```
[Client]
   │
   ├──► [design-service :7020] ──────────────────► [PostgreSQL DB]
   │         │ create/update/activate
   │         ▼
   ├──► [deploy-service :7030] ──calls──► [design-service]
   │
   ├──► [config-service :7004] ──────────────────► [agents/*.json files]
   │
   ├──► [ingestion-service :7002] ─────────────► [Weaviate VectorDB]
   │                                               └─► [Ollama Embeddings]
   │
   ├──► [agent-chat-service :7001]
   │         ├──► [config-service :7004]  (get agent config)
   │         ├──► [Weaviate VectorDB]     (RAG retrieval)
   │         └──► [Ollama LLM]            (generate answer)
   │
   ├──► [sse-stream-service :7005]
   │         ├──► [agent-chat-service :7001] (RAG)
   │         ├──► [Ollama agentactivity]     (streaming LLM)
   │         └──► [SSE clients :7099/events] (push events)
   │
   ├──► [agent-builder-service :7010]
   │         ├──► [config-service :7004]   (read current config for improve)
   │         └──► [Ollama agentactivity]   (generate/improve/validate)
   │
   ├──► [feedback-service :7003] ──────────────► [feedback/*.jsonl files]
   │
   └──► [rule-engine-service :7000] ───────────► [Custom ruleengine activity]
```

---

## Full E2E Chain (Happy Path)

**Chain:** Design → Deploy → Ingest → Chat → Feedback

| Step | Service | Action | Result |
|------|---------|--------|--------|
| 1 | design-service | Create agent | `id: ec22ce54-...`, status `draft` |
| 2 | deploy-service | Activate agent | status → `active` |
| 3 | config-service | Save chat config | `201 Created` |
| 4 | ingestion-service | Ingest 2 docs | `chunksCreated: 2` in E2ETestCollection |
| 5 | agent-chat-service | POST /api/chat | LLM answer about Flogo returned |
| 6 | feedback-service | Submit rating 5 | Appended to `feedback/e2e-chain-agent.jsonl` |

---

## Service-by-Service Test Results

---

### 1. rule-engine-service (`:7000`)

**Description:** Analyzes code files against registered rule sets. Extension-based parser selection. Custom `ruleengine` activity.

#### GET `/api/health`

```
Request:  GET http://localhost:7000/api/health
          Authorization: Basic ZmxvZ286Y2hhbmdlbWU=

Response: 200 OK
{
  "port": 7000,
  "service": "rule-engine-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/analyze` — `.flogo` file

```
Request:  POST http://localhost:7000/api/analyze
{
  "fileName": "test-agent.flogo",
  "content": "{\"name\":\"test-agent\",\"description\":\"Test Agent\",\"type\":\"flogo:app\",\"version\":\"1.0.0\"}"
}

Response: 200 OK
{
  "error": "",
  "errorCount": 0,
  "findings": [],
  "infoCount": 0,
  "markdown": "## Analysis Report - test-agent.flogo\n\n**Errors:** 0 | **Warnings:** 0 | **Info:** 0\n\n",
  "overview": {
    "extension": ".flogo",
    "file": "test-agent.flogo",
    "name": "test-agent",
    "parser": "json",
    "rules_run": 21
  },
  "positives": [],
  "success": true,
  "warningCount": 0
}
```

#### POST `/api/analyze` — `.yaml` file

```
Request:  POST http://localhost:7000/api/analyze
{
  "fileName": "deployment.yaml",
  "content": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: test-agent\nspec:\n  replicas: 1"
}

Response: 200 OK
{
  "overview": { "parser": "yaml", "extension": ".yaml" },
  "success": true,
  "errorCount": 0,
  "warningCount": 0,
  "infoCount": 0
}
```

> **Note:** The request field is `fileName` (camelCase), not `filename`. The `parserOverride` field can be used to force a specific parser by name.

---

### 2. agent-chat-service (`:7001`)

**Description:** RAG-based chat service. Fetches agent config from config-service, retrieves context from VectorDB, then optionally calls Ollama LLM. Three endpoints cover RAG-only, RAG+LLM, and OOTB agentactivity paths.

**Request field names:** `message` (not `query`), `collectionName` (not `collection`), `agentId`, `sessionId`, `topK`

#### GET `/api/health`

```
Request:  GET http://localhost:7001/api/health
Response: 200 OK
{
  "port": 7001,
  "service": "agent-chat-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/chat` — Custom RAG + LLM (RAGQuery activity)

```
Request:  POST http://localhost:7001/api/chat
{
  "message": "What is TIBCO Flogo?",
  "agentId": "default",
  "collectionName": "KnowledgeBase",
  "sessionId": "sess-001"
}

Flow:
  1. GetAgentConfig → GET http://localhost:7004/api/agents/default
  2. RAGQuery → Weaviate vector search (embed query via nomic-embed-text)
  3. Custom HTTP call to Ollama /v1/chat/completions
  4. Return formatted answer

Response: 200 OK
{
  "answer": "Based on the provided context, I can answer that:\n\nTIBCO Flogo is a platform for building AI agents and integration flows.",
  "duration": "2.418999s",
  "error": "",
  "formattedContext": "1. Agent Studio is a platform for building AI agents with TIBCO Flogo.\n\n2. ..."
}
```

#### POST `/api/chat/retrieve` — RAG Retrieval Only (no LLM)

```
Request:  POST http://localhost:7001/api/chat/retrieve
{
  "message": "What is agent activity?",
  "collectionName": "KnowledgeBase",
  "topK": 3
}

Flow:
  1. RAGQuery → Weaviate vector search
  2. Return context without LLM call

Response: 200 OK
{
  "answer": "",
  "duration": "219.7104ms",
  "error": "",
  "formattedContext": "1. Agent Studio is a platform for building AI agents with TIBCO Flogo.\n\n2. ...",
  "queryEmbedding": [-0.00926958, ...]
}
```

#### POST `/api/chat/agent` — OOTB agentactivity (Tool-using agent)

```
Request:  POST http://localhost:7001/api/chat/agent
{
  "message": "What tools are available in Flogo?",
  "agentId": "default",
  "collectionName": "KnowledgeBase",
  "sessionId": "sess-002"
}

Flow:
  1. RAGRetrieve → Weaviate vector search
  2. OOTB agentactivity → Ollama llama3.2:3b via http://localhost:11434/v1/chat/completions
  3. Return agent response

Response: 200 OK
{
  "response": "According to the provided context, some of the tools available in Flogo include:\n1. Write File\n2. Read File\n3. Log\n4. REST HTTP\n5. Return\n6. VectorDB\n7. AI activities...",
  "execution_time_ms": 12302,
  "llm_calls": 1,
  "model_used": "llama3.2:3b",
  "total_tokens": 273
}
```

---

### 3. ingestion-service (`:7002`)

**Description:** Ingests documents from multiple sources (text, URL, GitHub, Confluence, S3) into a Weaviate VectorDB collection using embedding via Ollama.

#### GET `/api/health`

```
Response: 200 OK
{
  "port": 7002,
  "service": "ingestion-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/ingest/collection` — Ensure Collection Exists

```
Request:  POST http://localhost:7002/api/ingest/collection
{ "collectionName": "E2ETestCollection" }

Flow:
  1. EnsureCollection → VectorDB CreateCollection (skips if exists)

Response: 200 OK
{
  "duration": "52.1939ms",
  "error": "",
  "success": true
}
```

#### POST `/api/ingest` — Ingest Text Documents

```
Request:  POST http://localhost:7002/api/ingest
{
  "collectionName": "E2ETestCollection",
  "documents": [
    {
      "text": "TIBCO Flogo is an ultra-light integration platform for building event-driven microservices.",
      "metadata": { "source": "test" }
    },
    {
      "text": "Flogo supports AI activities including RAG, agent, and embedding workflows.",
      "metadata": { "source": "test" }
    }
  ]
}

Flow:
  1. EnsureCollection → skip (already exists)
  2. IngestDocuments → embed via nomic-embed-text → store in Weaviate

Response: 200 OK
{
  "chunksCreated": 2,
  "dimensions": 768,
  "duration": "454.0368ms",
  "error": "",
  "ids": ["3f0b2fd9-...", "2cf4df4b-..."],
  "ingestedCount": 2,
  "sourceDocumentCount": 2,
  "success": true
}
```

> **Note:** The `documents` array items use `text` field (not `content`). Other supported sources: `/api/ingest/url`, `/api/ingest/github`, `/api/ingest/confluence`, `/api/ingest/s3`.

---

### 4. feedback-service (`:7003`)

**Description:** Stores and retrieves user feedback as JSONL files (one per agent). Each record is appended on submit.

#### GET `/api/health`

```
Response: 200 OK
{
  "service": "feedback-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/feedback` — Submit Feedback

```
Request:  POST http://localhost:7003/api/feedback
{
  "sessionId": "sess-e2e-001",
  "agentId": "default",
  "messageId": "msg-001",
  "rating": 5,
  "comment": "Great answer!",
  "query": "What is Flogo?",
  "response": "TIBCO Flogo is a platform."
}

Flow:
  1. Write record → append to feedback/{agentId}.jsonl

Response: 200 OK
{
  "fullPath": "C:\\...\\feedback\\default.jsonl",
  "name": "default.jsonl",
  "size": 1301
}
```

#### GET `/api/feedback/{agentId}` — Get Feedback by Agent

```
Request:  GET http://localhost:7003/api/feedback/default

Flow:
  1. Read file → feedback/default.jsonl

Response: 200 OK (JSONL format, multiple JSON objects concatenated)
{"agentId":"default","comment":"Great response!","messageId":"msg-001","rating":5,...}
{"agentId":"default","comment":"Very helpful!","messageId":"msg-002","rating":5,...}
```

#### GET `/api/feedback` — Get All Feedback

```
Request:  GET http://localhost:7003/api/feedback

Response: 200 OK (JSONL from global feedback log)
```

> **Note:** Response is raw JSONL (not a JSON array). Client must split on newline and parse individually.

---

### 5. config-service (`:7004`)

**Description:** Agent configuration CRUD backed by JSON files in `agents/` directory. Each agent has an `{agentId}.json` file.

#### GET `/api/health`

```
Response: 200 OK
{
  "service": "config-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### GET `/api/agents` — List Agents

```
Response: 200 OK
[
  { "name": "default.json", "size": 731, ... },
  { "name": "flogo-analyzer.json", "size": 833, ... },
  ...7 agents total
]
```

#### GET `/api/agents/{agentId}` — Get Agent Config

```
Request:  GET http://localhost:7004/api/agents/default

Response: 200 OK
{
  "id": "default",
  "name": "Default Assistant",
  "description": "General-purpose knowledge base assistant.",
  "systemPrompt": "You are a helpful assistant...",
  "collectionName": "KnowledgeBase",
  "llmProvider": "Ollama",
  "llmBaseUrl": "http://localhost:11434",
  "model": "nemotron-3-nano:30b",
  "temperature": 0.7,
  "maxTokens": 2048,
  "topK": 5,
  "tools": ["ragQuery"],
  "active": true
}
```

#### POST `/api/agents` — Create Agent

```
Request:  POST http://localhost:7004/api/agents
{
  "id": "e2e-test-agent",
  "name": "E2E Test Agent",
  "systemPrompt": "You are a test agent.",
  "collectionName": "E2ETestCollection",
  "model": "llama3.2:3b",
  "active": true
}

Response: 201 Created
{ "fullPath": "...agents/e2e-test-agent.json", "name": "e2e-test-agent.json", ... }
```

#### DELETE `/api/agents/{agentId}` — Delete Agent

```
Request:  DELETE http://localhost:7004/api/agents/e2e-test-agent

Response: 200 OK
{ "fullPath": "...agents/e2e-test-agent.json", "name": "e2e-test-agent.json", ... }
```

---

### 6. sse-stream-service (`:7005`)

**Description:** Server-Sent Events streaming service. Calls agent-chat-service for RAG context, then runs OOTB agentactivity to generate streaming LLM response pushed via SSE.

**SSE endpoint:** `GET http://localhost:7099/events` (separate port for SSE connections)

#### GET `/api/health`

```
Response: 200 OK
{
  "port": 7005,
  "service": "sse-stream-service",
  "sseEndpoint": "/events",
  "status": "ok",
  "streamEndpoint": "/api/stream/chat",
  "version": "1.0.0"
}
```

#### POST `/api/stream/chat` — Start Streaming Chat

```
Request:  POST http://localhost:7005/api/stream/chat
{
  "message": "What is TIBCO Flogo in one sentence?",
  "agentId": "default",
  "collectionName": "KnowledgeBase",
  "sessionId": "stream-sess-001"
}

Flow:
  1. SSESend → push "start" event to SSE clients
  2. CallRAG → POST http://localhost:7001/api/chat (get RAG context)
  3. agentactivity → Ollama llama3.2:3b for LLM response
  4. SSESend → push "complete" event with response
  5. Return 202 Accepted (async)

Response: 202 Accepted
{
  "eventsUrl": "/events",
  "streaming": true
}
```

> **Note:** Response is immediate 202. Full answer is pushed asynchronously via SSE at `http://localhost:7099/events`. Connect to SSE endpoint before calling `/api/stream/chat` to receive streamed events.

#### POST `/api/stream/broadcast` — Broadcast Event

```
Request:  POST http://localhost:7005/api/stream/broadcast
{
  "event": "test",
  "data": { "message": "E2E broadcast test" },
  "sessionId": "all"
}

Response: 200 OK
{ "broadcasted": true }
```

---

### 7. agent-builder-service (`:7010`)

**Description:** LLM-driven agent generation and improvement. Uses OOTB agentactivity (Ollama nemotron-3-nano:30b) to generate configurations and improvement suggestions.

#### GET `/api/health`

```
Response: 200 OK
{
  "llmModel": "nemotron-3-nano:30b",
  "port": 7010,
  "service": "agent-builder-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/agent-builder/generate` — Generate Agent Config

```
Request:  POST http://localhost:7010/api/agent-builder/generate
{
  "name": "Customer Support Agent",
  "description": "Handles customer inquiries about products and services",
  "domain": "customer-support",
  "capabilities": ["answer-faq", "escalate-tickets"],
  "llmModel": "llama3.2:3b"
}

Flow:
  1. agentactivity → Ollama LLM generates structured agent config JSON

Response: 200 OK
{
  "config": {
    "active": true,
    "collectionName": "CustomerInquiry",
    "description": "Handles customer inquiries about products and services",
    "id": "customer-inquiry-handler",
    "llmProvider": "openai",
    "maxTokens": 500,
    "model": "gpt-3.5-turbo",
    "name": "Customer Inquiry Handler",
    "systemPrompt": "You are a helpful assistant that answers customer questions..."
  }
}
```

#### POST `/api/agent-builder/validate` — Validate Agent Config

```
Request:  POST http://localhost:7010/api/agent-builder/validate
{
  "config": {
    "id": "test-agent",
    "name": "Test",
    "systemPrompt": "You are helpful.",
    "collectionName": "TestCol",
    "model": "llama3.2:3b",
    "active": true
  }
}

Response: 200 OK
{
  "errors": [
    "Missing required field: description",
    "Missing required field: llmProvider",
    "Missing required field: temperature",
    "Missing required field: maxTokens",
    "Missing required field: topK",
    "Missing required field: tools",
    "Missing required field: version"
  ],
  "valid": false
}
```

#### POST `/api/agent-builder/improve` — Improve Existing Agent

```
Request:  POST http://localhost:7010/api/agent-builder/improve
{
  "agentId": "test-improve-agent",
  "feedback": "The agent does not handle technical questions well. Needs more domain knowledge.",
  "improvements": ["add-technical-knowledge", "improve-accuracy"]
}

Flow:
  1. ReadCurrentConfig → GET http://localhost:7004/api/agents/test-improve-agent
     (with Authorization: Basic ZmxvZ286Y2hhbmdlbWU=)
  2. agentactivity → Ollama LLM analyzes config and feedback, generates suggestions

Response: 200 OK
{
  "agentId": "test-improve-agent",
  "current": {
    "collectionName": "KnowledgeBase",
    "description": "Test agent for improvement",
    "model": "llama3.2:3b",
    "systemPrompt": "You are a helpful assistant.",
    "temperature": 0.7,
    "topK": 5
  },
  "suggestions": {
    "changes": [
      "Enhanced systemPrompt to explicitly mention software development domain.",
      "Reduced temperature from 0.7 to 0.2 for more deterministic, accurate technical answers."
    ]
  }
}
```

---

### 8. design-service (`:7020`)

**Description:** Full agent lifecycle management backed by PostgreSQL. Supports versioning (each update increments version). Archive/soft-delete. Includes agent templates.

**API base:** `/api/v1/agents`

#### GET `/api/health`

```
Response: 200 OK
{
  "database": "connected",
  "port": 7020,
  "service": "design-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/v1/agents` — Create Agent

```
Request:  POST http://localhost:7020/api/v1/agents
{
  "name": "E2E Agent",
  "description": "E2E test agent via design-service",
  "config": {
    "systemPrompt": "You are helpful.",
    "collectionName": "E2ETestCollection",
    "model": "llama3.2:3b",
    "topK": 3
  }
}

Response: 201 Created
{
  "records": [{
    "id": "9d30f5b8-17e3-4698-9265-2d2d931d68a0",
    "name": "E2E Agent",
    "description": "E2E test agent via design-service",
    "status": "draft",
    "config": "{\"topK\": 3, \"systemPrompt\": \"You are helpful.\", ...}",
    "created_at": "2026-05-13 06:34:21.049978+00",
    "version": 1
  }]
}
```

#### GET `/api/v1/agents/{agentId}` — Get Agent

```
Response: 200 OK
{ "records": [{ ...agent data... }] }
```

#### PUT `/api/v1/agents/{agentId}` — Update Agent

```
Request:  PUT http://localhost:7020/api/v1/agents/9d30f5b8-...
{
  "name": "E2E Agent Updated",
  "description": "Updated E2E test",
  "config": { "topK": 5, "temperature": 0.5 }
}

Response: 200 OK (version incremented to 2)
{
  "records": [{
    "id": "9d30f5b8-...",
    "name": "E2E Agent Updated",
    "status": "draft",
    "version": 2,
    "updated_at": "..."
  }]
}
```

#### DELETE `/api/v1/agents/{agentId}` — Archive Agent (Soft Delete)

```
Response: 200 OK
{ "agentId": "9d30f5b8-...", "archived": true }
```

#### GET `/api/v1/templates` — List Templates

```
Response: 200 OK
[
  {
    "id": "tpl-general-rag",
    "description": "General-purpose RAG agent",
    "config": { "llmModel": "nemotron-mini:4b", "temperature": 0.7, "topK": 5 }
  },
  ... (3 templates total)
]
```

---

### 9. deploy-service (`:7030`)

**Description:** Manages agent deployment lifecycle by updating agent status in design-service's PostgreSQL DB. Activate sets status to `active`, deactivate reverts to `draft`. Export endpoints return agent config for Kubernetes/Docker-Compose manifests.

**API base:** `/api/v1/agents/{agentId}/deploy`

#### GET `/api/health`

```
Response: 200 OK
{
  "service": "deploy-service",
  "status": "ok",
  "version": "1.0.0"
}
```

#### POST `/api/v1/agents/{agentId}/deploy` — Activate Agent

```
Request:  POST http://localhost:7030/api/v1/agents/cdc6f821-.../deploy
          Body: {}

Flow:
  1. GetAgentConfig → GET design-service /api/v1/agents/{agentId}
  2. ActivateAgent → PUT design-service /api/v1/agents/{agentId} { "status": "active" }

Response: 200 OK
{
  "records": [{
    "id": "cdc6f821-...",
    "name": "Deploy Test Agent",
    "status": "active",
    "version": 2
  }]
}
```

#### GET `/api/v1/agents/{agentId}/deploy` — Get Deployment Status

```
Response: 200 OK
{ "records": [{ "id": "...", "status": "active", ... }] }
```

#### GET `/api/v1/agents/{agentId}/export/kubernetes` — Export Kubernetes Manifest

```
Response: 200 OK
{ "records": [{ ...agent config for generating K8s manifest... }] }
```

#### GET `/api/v1/agents/{agentId}/export/docker-compose` — Export Docker Compose

```
Response: 200 OK
{ "records": [{ ...agent config for generating docker-compose.yml... }] }
```

#### DELETE `/api/v1/agents/{agentId}/deploy` — Deactivate Agent

```
Response: 200 OK
{
  "records": [{
    "id": "cdc6f821-...",
    "status": "draft",
    "version": 3
  }]
}
```

> **Note:** deploy-service and design-service share the same PostgreSQL database. The deploy-service requires agents to exist in the design-service DB (created via POST `/api/v1/agents`). Agents in the JSON-file config-service (`:7004`) are a separate store.

---

### 10. mcp-server (`:3333`)

**Description:** Model Context Protocol server exposing Flogo agent tools/resources via HTTP Streamable transport. Used by AI assistants (e.g., Claude) to call tools on registered Flogo flows.

**Endpoint:** `http://localhost:3333/mcp`  
**Protocol:** MCP HTTP Streamable Transport  
**Status:** Port listening confirmed (TCP check). Full MCP session flow (SSE + POST) requires an MCP client.

---

## Bugs Fixed (This Session and Prior)

| # | Service | Issue | Root Cause | Fix |
|---|---------|-------|------------|-----|
| 1 | agent-chat | `embeddingBaseURL` 404 | Previous session replaced `/v1` suffix; VectorDB connector appends `/embeddings` not `/v1/embeddings` | Restored `embeddingBaseURL: http://localhost:11434/v1` |
| 2 | agent-chat | `agentactivity` 404 | `llmProviderUrl: http://localhost:11434` — agentactivity appends `/chat/completions` (not `/v1/chat/completions`) | Changed to `http://localhost:11434/v1` |
| 3 | sse-stream | `agentactivity` 404 | Same issue as #2 plus URL was `https://ollama.com/v1` (wrong host) | Changed to `http://localhost:11434/v1` |
| 4 | sse-stream | Model `nemotron-3-nano:30b` not found | LLM_MODEL property set to unavailable model | Changed property to `llama3.2:3b` |
| 5 | sse-stream | `CallRAG` 401 Unauthorized | No `Authorization` header in the REST call to agent-chat | Added `Authorization: Basic ZmxvZ286Y2hhbmdlbWU=` to headers + added schema |
| 6 | agent-builder | `ReadCurrentConfig` 401 Unauthorized | No `Authorization` header when calling config-service | Added `Authorization` to `headers.mapping` in OOTB `#rest` activity |
| 7 | deploy-service | Return activities returned literal strings | Nested expressions in MAP values not evaluated by Flogo | Changed to top-level expressions: `"data": "=$activity[Name].responseBody"` |
| 8 | all services | `"version": "=$property[\"SERVICE_VERSION\"]"` in health returns literal | Nested expression in MAP not evaluated | Changed to static `"version": "1.0.0"` |
| 9 | design-service | Same version expression issue | Same root cause | Changed to static `"version": "1.0.0"` |
| 10 | ingestion | `ensure_collection` returned nested literal | Nested expression not evaluated | Changed to `"data": "=$activity[EnsureCollection]"` (top-level) |

### Critical Flogo Behavior: Nested Expression Evaluation

In Flogo Return activity mappings, **expressions in nested MAP values are NOT evaluated**:

```json
// WRONG — "=$flow.body.agentId" returned as literal string
"data": {
  "agentId": "=$flow.body.agentId"
}

// CORRECT — expression evaluated, full output returned
"data": "=$activity[SomeActivity].responseBody"
```

Only **top-level** mapping values are evaluated as expressions.

---

## Execution Paths Tested

| Service | Endpoint | Path Description | Status |
|---------|----------|-----------------|--------|
| rule-engine | GET /api/health | Health check | ✅ |
| rule-engine | POST /api/analyze | `.flogo` file → json parser | ✅ |
| rule-engine | POST /api/analyze | `.yaml` file → yaml parser | ✅ |
| agent-chat | GET /api/health | Health check | ✅ |
| agent-chat | POST /api/chat | RAG + custom LLM (RAGQuery) | ✅ |
| agent-chat | POST /api/chat/retrieve | RAG only (no LLM) | ✅ |
| agent-chat | POST /api/chat/agent | OOTB agentactivity | ✅ |
| ingestion | GET /api/health | Health check | ✅ |
| ingestion | POST /api/ingest/collection | Ensure collection | ✅ |
| ingestion | POST /api/ingest | Ingest text documents | ✅ |
| feedback | GET /api/health | Health check | ✅ |
| feedback | POST /api/feedback | Submit feedback | ✅ |
| feedback | GET /api/feedback/{agentId} | Get by agent | ✅ |
| feedback | GET /api/feedback | Get all feedback | ✅ |
| config | GET /api/health | Health check | ✅ |
| config | GET /api/agents | List agents | ✅ |
| config | GET /api/agents/{id} | Get agent config | ✅ |
| config | POST /api/agents | Create agent | ✅ |
| config | DELETE /api/agents/{id} | Delete agent | ✅ |
| sse-stream | GET /api/health | Health check | ✅ |
| sse-stream | POST /api/stream/chat | Start streaming chat | ✅ (202 Accepted) |
| sse-stream | POST /api/stream/broadcast | Broadcast event | ✅ |
| agent-builder | GET /api/health | Health check | ✅ |
| agent-builder | POST /api/agent-builder/generate | Generate agent config | ✅ |
| agent-builder | POST /api/agent-builder/validate | Validate agent config | ✅ |
| agent-builder | POST /api/agent-builder/improve | Improve with feedback | ✅ |
| design | GET /api/health | Health check | ✅ |
| design | GET /api/v1/agents | List agents | ✅ |
| design | POST /api/v1/agents | Create agent | ✅ |
| design | GET /api/v1/agents/{id} | Get agent | ✅ |
| design | PUT /api/v1/agents/{id} | Update agent | ✅ |
| design | DELETE /api/v1/agents/{id} | Archive agent | ✅ |
| design | GET /api/v1/templates | List templates | ✅ |
| deploy | GET /api/health | Health check | ✅ |
| deploy | POST /api/v1/agents/{id}/deploy | Activate agent | ✅ |
| deploy | GET /api/v1/agents/{id}/deploy | Get deploy status | ✅ |
| deploy | GET /api/v1/agents/{id}/export/kubernetes | K8s export | ✅ |
| deploy | GET /api/v1/agents/{id}/export/docker-compose | Compose export | ✅ |
| deploy | DELETE /api/v1/agents/{id}/deploy | Deactivate agent | ✅ |
| mcp | Port :3333 | TCP connectivity | ✅ (port open) |

**Not tested (require external credentials):**
- `POST /api/ingest/url` — external URL ingest
- `POST /api/ingest/github` — GitHub repository ingest
- `POST /api/ingest/confluence` — Confluence page ingest
- `POST /api/ingest/s3` — S3 object ingest
- MCP full session (requires MCP client)

---

## Service Ports Reference

| Service | Port | Health Endpoint |
|---------|------|-----------------|
| rule-engine-service | 7000 | `/api/health` |
| agent-chat-service | 7001 | `/api/health` |
| ingestion-service | 7002 | `/api/health` |
| feedback-service | 7003 | `/api/health` |
| config-service | 7004 | `/api/health` |
| sse-stream-service | 7005 | `/api/health` |
| SSE event bus | 7099 | `/events` (SSE) |
| agent-builder-service | 7010 | `/api/health` |
| design-service | 7020 | `/api/health` |
| deploy-service | 7030 | `/api/health` |
| mcp-server | 3333 | `/mcp` (MCP protocol) |

---

## Infrastructure Requirements

| Component | Purpose | URL |
|-----------|---------|-----|
| Ollama | LLM inference | `http://localhost:11434` |
| Weaviate | VectorDB | `http://localhost:8080` |
| PostgreSQL | design/deploy service DB | `localhost:5432` |
| Models required | `llama3.2:3b`, `nomic-embed-text:latest` | — |
