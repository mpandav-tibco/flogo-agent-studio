# Flogo Agent Studio — Full Lifecycle Functional Test Report

| | |
|---|---|
| **Date** | 2026-05-18 18:57:47 |
| **Duration** | 45.7s |
| **Agent** | `E2E-Functional-Agent-2026-05-18` |
| **Agent ID** | `60550bb8-0a74-4805-9227-7834d80354b6` |
| **Collection** | `FunctionalTestKB` |
| **LLM Model** | `llama3.2:3b` |
| **Session** | `e2e-functional-2dbe5982` |
| **Result** | ✅ **PASSED** — 37/37 steps (100%) |

---

## Test Scenario

This test simulates the complete agent lifecycle as experienced through the **Forge UI** and **Chainlit UI**,
exercising every backend service with real functional calls (not health probes).

```
Forge UI  →  Template Discovery  →  Create Agent  →  AI-Generate Config
          →  Ingest Knowledge    →  Deploy Agent  →  Export Artifacts
Chainlit  →  Discover Agents     →  Chat Session (RAG×2)  →  Submit Feedback
Builder   →  Improve from Feedback
MCP       →  All 9 Tools via JSON-RPC
Forge UI  →  Undeploy  →  Archive
```

---

## Results by Phase

### ✅ Templates (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/templates — list all templates | ✅ | 15ms | 3 template(s) |

### ✅ Create Agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/v1/agents — create new agent (Forge UI) | ✅ | 15ms | id=60550bb8-0a74-4805-9227-7834d80354b6 |

### ✅ Get Agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents/{id} — verify agent exists | ✅ | 6ms | status=draft |

### ✅ Generate Config (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/agent-builder/generate — LLM config generation | ✅ | 3999ms | model=llama3.2:3b |

### ✅ Update Config (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| PUT /api/v1/agents/{id} — apply AI-generated config | ✅ | 6ms | version=2 |

### ✅ Ingest Knowledge (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/ingest — embed & store in Weaviate | ✅ | 2652ms | chunks=1 |

### ✅ Rule Analysis (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/analyze — app quality gate | ✅ | 30ms | rules_run=? |

### ✅ Deploy Agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/v1/agents/{id}/deploy — activate (Forge Deploy button) | ✅ | 11ms | status=active |

### ✅ Verify Deploy (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents/{id}/deploy — confirm active status | ✅ | 3ms | records=1, status=active |

### ✅ List Active Agents (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents — find deployed agent in Forge sidebar | ✅ | 2ms | found=True |

### ✅ Chainlit Agent Discovery (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents — Chainlit finds active agent on startup | ✅ | 2ms | found=True |

### ✅ Chainlit Chat #1 (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/chat — RAG query via Chainlit (AgenticAI question) | ✅ | 10520ms | answer_len=0 |

### ✅ Chainlit Chat #2 (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/chat — follow-up RAG query (Weaviate integration) | ✅ | 5359ms | answer_len=614 |

### ✅ Submit Feedback #1 (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/feedback — thumbs-up (Chainlit button) | ✅ | 4ms | rating=thumbsUp |

### ✅ Submit Feedback #2 (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/feedback — thumbs-down (Chainlit button) | ✅ | 2ms | rating=thumbsDown |

### ✅ Retrieve Feedback (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/feedback/{agentId} — Forge feedback panel | ✅ | 0ms | records=1 |

### ✅ Improve Agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/agent-builder/improve — AI improvement from feedback | ✅ | 14121ms | changes=5 |

### ✅ Apply Improvements (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| PUT /api/v1/agents/{id} — save improved config | ✅ | 7ms | version=4 |

### ✅ SSE Broadcast (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/stream/broadcast — session.start event | ✅ | 4ms |  |

### ✅ SSE Stream Chat (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /api/stream/chat — full RAG+LLM via SSE pipeline | ✅ | 7224ms | streaming=True |

### ✅ MCP Initialize (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /mcp — initialize JSON-RPC session | ✅ | 3ms | session=YCU4FQZ64XPHRRNMYWMUQWK4TU |

### ✅ MCP tools/list (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| POST /mcp method=tools/list — discover all 9 tools | ✅ | 2ms | 9 tools |

### ✅ MCP tool: list_agents (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'list_agents' — list all agents from design-service | ✅ | 17ms | len=6186 |

### ✅ MCP tool: get_agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'get_agent' — get specific agent config from design-service | ✅ | 6ms | len=1358 |

### ✅ MCP tool: create_agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'create_agent' — create agent via MCP tool | ✅ | 7ms | len=394 |

### ✅ MCP tool: list_templates (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'list_templates' — list all agent templates | ✅ | 2ms | len=1066 |

### ✅ MCP tool: submit_feedback (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'submit_feedback' — submit feedback via MCP tool | ✅ | 6ms | len=260 |

### ✅ MCP tool: get_feedback (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'get_feedback' — retrieve feedback via MCP tool | ✅ | 4ms | len=1723 |

### ✅ MCP tool: analyze_flogo (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'analyze_flogo' — analyze Flogo app via MCP tool | ✅ | 4ms | len=204 |

### ✅ MCP tool: rag_chat (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'rag_chat' — RAG chat via MCP tool (full pipeline) | ✅ | 1561ms | len=9785 |

### ✅ MCP tool: deploy_agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| tools/call 'deploy_agent' — activate MCP-created agent | ✅ | 12ms | len=364 |

### ✅ Export kubernetes (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents/{id}/export/kubernetes — K8s YAML | ✅ | 4ms | chars=741 |

### ✅ Export docker-compose (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents/{id}/export/docker-compose — Docker Compose YAML | ✅ | 4ms | chars=361 |

### ✅ Undeploy Agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| DELETE /api/v1/agents/{id}/deploy — deactivate (Forge button) | ✅ | 6ms | status=draft |

### ✅ Verify Undeploy (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents/{id}/deploy — confirm deactivated | ✅ | 3ms | status=draft |

### ✅ Archive Agent (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| DELETE /api/v1/agents/{id} — archive/delete (Forge Delete button) | ✅ | 8ms | status=200 |

### ✅ Verify Archive (1/1)

| Step | Result | Time | Detail |
|------|--------|------|--------|
| GET /api/v1/agents — confirm agent no longer active | ✅ | 2ms | not in active list |

---

## Service Coverage

| Service | Port | Role | Tested Via |
|---------|------|------|------------|
| design-service | 7020 | Agent registry (PostgreSQL) | Forge CRUD + MCP tools |
| deploy-service | 7030 | Activation lifecycle | Forge deploy/undeploy + export |
| ingestion-service | 7002 | Knowledge ingestion → Weaviate | Direct POST /api/ingest |
| agent-chat-service | 7001 | RAG pipeline (embed→search→answer) | Chainlit chat + MCP rag_chat |
| feedback-service | 7003 | Feedback storage (JSONL) | Chainlit thumbs + MCP tools |
| agent-builder-service | 7010 | LLM config generation + improvement | Forge AI features |
| sse-stream-service | 7005 | Async SSE streaming pipeline | Broadcast + stream/chat |
| rule-engine-service | 7000 | YAML rule quality analysis | Direct POST /api/analyze |
| mcp-server | 3333 | JSON-RPC gateway (9 tools) | All 9 tools exercised |
| config-service | 7004 | File-based agent registry (legacy) | Not tested (not in critical path) |

---

## Failures

✅ **No failures — all 37 steps passed.**

---

## Summary

| Metric | Value |
|--------|-------|
| Total steps | 37 |
| Passed | 37 |
| Failed | 0 |
| Pass rate | 100% |
| Duration | 45.7s |
| Agent lifecycle | Create → Configure → Deploy → Chat → Feedback → Improve → Export → Decommission |

_Generated by `e2e_functional.py` on 2026-05-18 18:57:47_