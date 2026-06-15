# Flogents Studio

**Flogents Studio** is a production-grade multi-agent AI platform built on **TIBCO Flogo**. It follows a two-tier architecture: a **Platform Layer** of always-on services (agent registry, LLM config builder, MCP gateway) and an **Agent Layer** of isolated per-agent process groups spawned on demand by the **Runtime Manager**. Each agent gets its own RAG pipeline, ingestion service, and **Rule Engine** — a YAML-driven static analysis service that continuously validates agent behaviour, configuration, and the artefacts agents work with. The **Flogents** React portal and per-agent **Chainlit** chat UIs round out the full-stack experience.

The **Rule Engine** is a first-class citizen of every agent runtime. Powered by a configurable YAML rule set, it analyses Flogo apps, Kubernetes manifests, integration configs, and any structured file your agents handle. It surfaces errors, warnings, and policy violations as structured findings — giving every agent the ability to reason over its own work products and enforce governance without calling an external LLM.

---

## Why Flogents?

Most AI agent frameworks give you a chat interface and an LLM call. Flogents goes further — it treats each agent as a **managed, observable microservice** with its own knowledge base, ingestion pipeline, governance rules, and deployment lifecycle.

| Challenge | How Flogents solves it |
|-----------|--------------------------|
| **Multiple agents, multiple knowledge bases** | Each agent owns an isolated Weaviate collection. Knowledge never leaks between agents. |
| **Agents need domain knowledge, not just LLM reasoning** | Ingestion pipeline (URL, GitHub, file, text) chunks and embeds documents into the agent's collection before the agent answers anything. |
| **Answers must be grounded, not hallucinated** | Every chat response goes through a hybrid BM25 + vector RAG retrieval step before reaching the LLM. |
| **Governance and compliance validation** | The Rule Engine evaluates structured files against YAML rule sets — catching misconfigurations, deprecated patterns, and policy violations without an LLM call. |
| **AI IDE integration (Copilot, Claude, Cursor)** | The MCP server exposes 9 tools over Streamable HTTP so any MCP-compatible IDE can query agents, ingest docs, run analysis, and manage the agent lifecycle directly from the editor. |
| **Deployment flexibility** | Agents start as lightweight native processes (fast iteration) or as fully isolated Docker containers (production handoff) — switched with a single click. |
| **Observability from day one** | Every Flogo service emits OpenTelemetry traces and structured JSON logs. No instrumentation work required. |

### Who is it for?

- **Integration engineers** modernising TIBCO BW5/BW6 assets who need an advisor with deep knowledge of their specific codebase
- **Platform / DevOps teams** that need a self-service agent per product domain (security, SRE, HR, legal) without spinning up separate AI infrastructure
- **AI IDE power users** who want a local, governed RAG backend their coding assistants can query over MCP
- **Enterprises** that need auditable agent behaviour — every analysis finding is structured, every chat turn is traceable

### Representative use cases

| Domain | Example agent | How Flogents helps |
|--------|---------------|----------------------|
| TIBCO / Integration | BW6 → Cloud migration advisor; Flogo app analyser | Ingest your BW5/BW6 process archives, EMS configs, and migration guides. The Rule Engine flags deprecated activities, unsupported patterns, and hard migration blockers before you touch a single line of code. The agent then answers "can this be automated?" and outputs an ordered migration sequence. |
| DevOps / SRE | Incident responder; K8s config validator; release notes generator | Feed it your runbooks, known-error database, and Kubernetes manifests. The Rule Engine validates manifests against security and reliability rules (missing resource limits, privileged containers, etc.) and the RAG chat matches live incident symptoms to runbook remediation steps — structured output, not freeform prose. |
| Engineering | Code review assistant; API documentation generator | Ingest your team's coding standards, security rules, and OpenAPI specs. The agent reviews submitted code against those exact standards (not generic LLM opinions), returns findings by severity, and answers developer questions about endpoints with working code examples grounded in your actual API docs. |
| Finance | Financial insights analyst; procurement assistant | Ingest quarterly reports, budgets, and vendor proposals. The agent retrieves exact figures with source citations (no rounding, no hallucination) and flags deviations from procurement policy. Deterministic temperature settings ensure consistent, auditable analysis every time. |
| HR / Legal | HR policy advisor; legal contract reviewer; onboarding guide | Ground every answer strictly in your uploaded policy and contract documents. The HR agent directs employees to the relevant policy clause; the legal agent flags risky or missing clauses against your standard templates and recommends accept / negotiate / reject — without inventing obligations not in the source. |
| Security | Compliance auditor; CVE triage; security posture reviewer | Ingest your security frameworks (SOC 2, ISO 27001, NIST, internal controls). The Rule Engine evaluates configurations against control requirements; the RAG agent maps findings to specific control IDs, lists missing evidence, and outputs an audit verdict with remediation priorities and effort estimates. |
| Knowledge management | Research synthesiser; meeting intelligence; data quality inspector | Ingest research papers, meeting transcripts, or data schemas. The agent synthesises consensus and contradictions across sources, converts raw meeting notes into structured action-item tables with owner and due date, and scores datasets against your governance rules — surfacing PII exposure, missing fields, and format inconsistencies. |

---

## Architecture

```mermaid
graph TD
  subgraph UI["🖥️  UI Layer"]
    FORGE["Flogents UI · port 7025\nReact + Vite design portal"]
    CHAINLIT["Chainlit Chat · port 72xx\nPython — one instance per active agent"]
  end

  subgraph PLATFORM["🏗️  Platform Layer — always-on"]
    PS["platform-service · :7020\nAgent CRUD · feedback · templates\n(design + feedback merged)"]
    AB["agent-builder-service · :7010\nLLM config generation\n& feedback-driven improvement"]
    MCP["mcp-server · :7333\nMCP Streamable HTTP gateway\n9 AI-IDE tools"]
    RM["runtime-manager · :7050\ndeploy / stop / reconcile\nper-agent process groups"]
  end

  subgraph AGENTS["🤖  Agent Layer — dynamic, one slot per active agent  (pool 7200 – 7299)"]
    direction LR
    AC["agent-chat-service\n:base+1  RAG chat REST\n:base+2  SSE REST trigger\n:base+3  SSE event bus"]
    ING["ingestion-service\n:base+4\nURL · GitHub · file · text"]
    RE["rule-engine-service · :7097\nYAML rule evaluation\nstatic analysis"]
    CUI["chainlit-ui · :base+5\nDedicated chat UI\nAGENT_ID baked in"]
  end

  subgraph INFRA["⚙️  Infrastructure"]
    direction LR
    WV[("Weaviate\n:8080 / :50051\nhybrid BM25 + vector")]
    PG[("PostgreSQL\n:5432\nagent registry")]
    OL["Ollama\n:11434\nLLM + embeddings"]
    OTEL["OTel Collector\n:4317\ndistributed tracing"]
  end

  FORGE -->|REST| PS & AB & RM
  CHAINLIT -->|"REST / SSE"| AC
  PS -->|agent CRUD| PG
  PS -->|OTel| OTEL
  AB -->|LLM prompts| OL
  AB -->|OTel| OTEL
  MCP -->|REST| PS & AB
  RM -->|"spawn · stop · reconcile"| AC & ING & CUI
  RM -->|"docker compose"| AC & ING & CUI
  AC -->|hybrid search| WV
  AC -->|LLM completion| OL
  ING -->|"embed + store"| WV & OL

  classDef ui      fill:#e8f5f4,stroke:#3bbfbb,color:#111
  classDef platform fill:#eef2ff,stroke:#6366f1,color:#111
  classDef agent   fill:#fefce8,stroke:#f59e0b,color:#111
  classDef infra   fill:#f1f5f9,stroke:#64748b,color:#111

  class FORGE,CHAINLIT ui
  class PS,AB,MCP,RM platform
  class AC,ING,RE,CUI agent
  class WV,PG,OL,OTEL infra
```

---

## System Interaction Flows

```mermaid
graph LR
  User(["👤 User"])
  IDE(["🤖 AI IDE\nCopilot / Claude / Cursor"])

  subgraph UIS["User Interfaces"]
    FORGE["Flogents UI\n:7025"]
    CL["Chainlit Chat\n:72xx (per-agent)"]
  end

  subgraph PLAT["Platform Layer"]
    PS["platform-service\n:7020"]
    AB["agent-builder\n:7010"]
    RM["runtime-manager\n:7050"]
    MCP["mcp-server\n:7333"]
  end

  subgraph AGNT["Agent Layer (per-agent)"]
    AC["agent-chat\n:base+1/+2/+3"]
    ING["ingestion\n:base+4"]
    RE["rule-engine\n:7097"]
  end

  subgraph INFRA2["Infrastructure"]
    WV[("Weaviate\n:8080")]
    PG[("PostgreSQL\n:5432")]
    OL["Ollama\n:11434"]
  end

  User --> FORGE & CL
  IDE -->|"MCP / JSON-RPC"| MCP
  FORGE -->|"agent CRUD\nfeedback\ntemplates"| PS
  FORGE -->|"AI generate\nimprove config"| AB
  FORGE -->|"activate (local)\nactivate (docker)\ndeactivate"| RM
  FORGE -->|"analyze flogo\nrule checks"| RE
  FORGE -->|"ingest docs"| ING
  CL -->|"RAG chat"| AC
  CL -->|"SSE stream"| AC
  MCP -->|REST| PS & AB & AC & ING & RE
  RM -->|"spawn / manage"| AC & ING & CL
  PS --> PG
  AB --> OL
  AC --> WV & OL
  ING --> WV & OL

  style UIS fill:#e8f5f4,stroke:#3bbfbb
  style PLAT fill:#eef2ff,stroke:#6366f1
  style AGNT fill:#fefce8,stroke:#f59e0b
  style INFRA2 fill:#f1f5f9,stroke:#64748b
```

---

## Service Reference

### Platform Layer — always-on

| Service | Port | Description |
|---------|------|-------------|
| `platform-service` | 7020 | Agent CRUD (`/api/v1/agents/*`), templates, feedback — PostgreSQL-backed. Merger of former `design-service` + `feedback-service`. |
| `agent-builder-service` | 7010 | LLM-generated agent config (`/api/agent-builder/generate`), feedback-driven improvement (`/improve`), validation (`/validate`). |
| `mcp-server` | 7333 | MCP Streamable HTTP gateway at `/mcp` — exposes 9 tools to AI IDEs. |
| **Flogents UI** | 7025 | React 18 + Vite design portal. |
| **runtime-manager** | 7050 | Python process manager — activates/deactivates per-agent stacks via local process spawn or Docker Compose. Reconciles every 15 s. |

### Agent Layer — per-agent, dynamic

| Service | Slot offset | Description |
|---------|-------------|-------------|
| `agent-chat-service` | `+1/+2/+3` | RAG pipeline (embed → Weaviate hybrid search → LLM). SSE streaming merged into same binary. |
| `ingestion-service` | `+4` | Document ingestion: URL, GitHub repo, raw text, file upload → chunked embeddings → Weaviate. |
| `chainlit` | `+5` | Dedicated Chainlit chat UI with `AGENT_ID` set; spawned by runtime-manager on activation. |
| `rule-engine-service` | 7097 | YAML rule evaluation and static analysis of Flogo apps / K8s configs / arbitrary structured files. |


### Infrastructure dependencies

| Dependency | Port | Purpose |
|------------|------|---------|
| Weaviate | 8080 / 50051 | Vector database (HTTP + gRPC) — hybrid BM25 + vector search |
| PostgreSQL | 5432 | Agent registry persistence |
| Ollama or any LLM | 11434 | Local LLM runtime + `nomic-embed-text` embeddings (768-dim) |

---

## Quick Start

### Prerequisites

```bash
# Infrastructure (Docker)
docker compose up weaviate postgres -d

# Ollama models
ollama pull nomic-embed-text
ollama pull llama3.2:3b          # or any model of your choice
```

### Start everything (macOS / Linux)

```bash
./start-all.sh
```

`start-all.sh` is a thin wrapper that delegates to `deployment/start-all.sh`. It:

1. Stops any existing processes on managed ports (7025, 7010, 7020, 7050, 7333, 7200–7299)
2. Clears log files and (optionally) Elasticsearch indexes
3. **Auto-builds** any Flogo binary that is missing or older than its `.flogo` source — no manual compile step needed (uses bundled `flogobuild` in `tools/`)
4. Starts **Forge UI** (port 7025) and waits for it to be ready
5. Starts the three **Platform Layer** Flogo services (platform-service, agent-builder, mcp-server)
6. Starts the **Runtime Manager** (deployment.py, port 7050)

Agent services (chat, ingestion, chainlit) are **not** started here — the Runtime Manager starts a dedicated set when an agent is activated from the UI.

| Interface | URL |
|-----------|-----|
| Flogents Studio | http://localhost:7025 |
| Runtime Manager API | http://localhost:7050 |
| MCP Server | http://localhost:7333/mcp |


### Full stack via Docker Compose

```bash
docker compose up -d
```

---

## Agent Activation Modes

From the Flogents UI (Gallery or Editor), click **Activate** to choose how the agent's services are started:

| Mode | What happens |
|------|-------------|
| **Local Process** | Runtime Manager spawns native Flogo binaries + Chainlit directly on the host. Fast startup, no Docker required. |
| **Docker Container** | Runtime Manager calls `deployment/build-images.sh` then `docker compose up -d` for the agent's generated compose file. Fully isolated. |

Click **Deactivate** to confirm and stop all services for the agent (active chat sessions disconnected).

---

## Building from Source

Flogo binaries are auto-built on `start-all.sh` using the bundled `flogobuild` in `tools/flogobuild/`. To build manually:

```bash
# Detect the right binary for your platform
FLOGOBUILD=./tools/flogobuild/darwin_arm64/flogobuild   # macOS Apple Silicon
# FLOGOBUILD=./tools/flogobuild/linux_amd64/flogobuild  # Linux x86_64

# Platform layer
$FLOGOBUILD build-exe -f services/platform/flogo/platform-service.flogo      -c flogo-studio -o ./bin
$FLOGOBUILD build-exe -f services/platform/flogo/agent-builder-service.flogo -c flogo-studio -o ./bin
$FLOGOBUILD build-exe -f services/platform/flogo/mcp-server.flogo             -c flogo-studio -o ./bin

# Agent layer
$FLOGOBUILD build-exe -f services/agent/flogo/agent-chat-service.flogo  -c flogo-studio -o ./bin
$FLOGOBUILD build-exe -f services/agent/flogo/ingestion-service.flogo   -c flogo-studio -o ./bin
$FLOGOBUILD build-exe -f services/agent/flogo/rule-engine-service.flogo -c flogo-studio -o ./bin
```

Build context `flogo-studio` = TIBCO Flogo 2.26.3 build 2442.

Set `BUILD_BINARIES=never` to skip auto-build on startup (e.g. when binaries are pre-built).

---

## MCP Integration

The `mcp-server` exposes Flogents Studio as an MCP server named **`flogo-studio-agent-assist`**.

The `.mcp.json` at the project root auto-registers it for any compatible AI IDE running in this directory:

```json
{
  "mcpServers": {
    "flogo-studio-agent-assist": {
      "type": "sse",
      "url": "http://localhost:7333/mcp"
    }
  }
}
```

To register globally, add the same block to `~/.mcp.json` or your IDE's MCP configuration.

### Available MCP tools

| Tool | Description |
|------|-------------|
| `list_agents` | List all registered agents |
| `get_agent` | Get a specific agent config by ID |
| `create_agent` | Create a new agent |
| `update_agent` | Update an existing agent |
| `deploy_agent` | Activate / deactivate an agent |
| `rag_chat` | Ask a question against an agent's knowledge base |
| `ingest_documents` | Ingest documents into a collection |
| `submit_feedback` | Submit feedback for an agent session |
| `analyze_flogo` | Run static analysis rules on a Flogo app |

---

## Agent Templates

21 built-in agent templates in `config/agents/` give you a head start:

| Category | Templates |
|----------|-----------|
| **Integration / TIBCO** | `tibco-integration-advisor`, `businessworks-migration-advisor`, `analyzer-agent` |
| **DevOps / Engineering** | `devops-sre-assistant`, `code-review-assistant`, `release-notes-generator`, `api-documentation-assistant` |
| **Business / Finance** | `financial-insights-analyst`, `sales-intelligence-agent`, `procurement-assistant`, `product-manager-assistant` |
| **HR / Legal / Compliance** | `hr-policy-advisor`, `legal-contract-reviewer`, `security-compliance-auditor`, `employee-onboarding-guide` |
| **Knowledge / Research** | `research-synthesizer`, `meeting-intelligence-assistant`, `data-quality-inspector`, `it-incident-responder` |
| **General** | `rag-assistant`, `support-agent` |

---

## Repository Layout

```
flogo-agent-studio/
├── services/
│   ├── platform/
│   │   ├── flogo/               # Platform Flogo source files (.flogo)
│   │   │   ├── platform-service.flogo          # design + feedback merged
│   │   │   ├── agent-builder-service.flogo
│   │   │   └── mcp-server.flogo
│   │   ├── env/                 # Platform service property env files
│   │   └── ui/forge/            # Flogents React + Vite portal (port 7025)
│   ├── agent/
│   │   ├── flogo/               # Per-agent Flogo source files (.flogo)
│   │   │   ├── agent-chat-service.flogo        # RAG chat + SSE merged
│   │   │   ├── ingestion-service.flogo
│   │   │   └── rule-engine-service.flogo
│   │   ├── env/                 # Per-agent service property env files
│   │   └── ui/chainlit/         # Chainlit chat UI (app.py, port 72xx per-agent)
│   └── launch.py                # Python launcher — injects env vars via os.execve
├── deployment/
│   ├── deployment.py            # Runtime Manager (port 7050)
│   ├── start-all.sh             # Main start script (called by root start-all.sh)
│   ├── build-images.sh          # Docker image builder for agent services
│   └── Dockerfile.flogo-service # Flogo service Docker image
├── bin/                         # Compiled Flogo binaries (git-ignored)
├── tools/
│   ├── flogobuild/
│   │   ├── darwin_arm64/        # flogobuild binary — macOS Apple Silicon
│   │   └── linux_amd64/         # flogobuild binary — Linux x86_64
│   └── go-wrapper/              # go wrapper with -e flag for tidy
├── config/
│   ├── agents/                  # 21 agent template JSON files
│   └── rules/                   # YAML rule sets for rule-engine-service
├── data/
│   ├── feedback/                # JSONL feedback storage (git-ignored)
│   └── agent-runtime.json       # Runtime Manager state (git-ignored)
├── tests/
│   ├── smoke_test.py            # Health check sweep (all services)
│   ├── e2e_journey.py           # Full end-to-end journey (~32 steps)
│   ├── functional_tests.py      # Per-service API tests
│   └── e2e_functional.py        # Extended functional E2E suite
├── logs/                        # Runtime logs (git-ignored)
├── docker-compose.yml           # Full-stack Docker Compose
├── ports.yaml                   # Canonical port registry
├── start-all.sh                 # Wrapper → deployment/start-all.sh
├── start-platform.sh            # Start platform services only
├── start-mcp.sh                 # Start MCP server only
└── .mcp.json                    # MCP server registration for AI IDEs
```

---

## Testing

```bash
# Health check all services
python3 tests/smoke_test.py

# Full end-to-end journey (~32 steps across all services)
python3 tests/e2e_journey.py

# Per-service functional tests
python3 tests/functional_tests.py

# Extended functional E2E suite
python3 tests/e2e_functional.py
```

---

## Observability

All Flogo services emit **OpenTelemetry** traces (OTLP gRPC → `localhost:4317`) and structured JSON logs with injected `trace_id` / `span_id`. Set `OTEL_ENABLED=false` before running `start-all.sh` to skip if the collector is not available.

```bash
OTEL_ENABLED=false ./start-all.sh
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Service runtime | TIBCO Flogo |
| Vector database | Weaviate — hybrid BM25 + vector search |
| LLM | any of your choice |
| Embeddings | any of your choice|
| Agent persistence | PostgreSQL |
| Design portal | React 18 + Vite + Tailwind CSS v3 |
| Agent Chat UI | Chainlit 1.3+ |
| Deployment Management | Python asyncio (`deployment.py`) |
| Containerisation | Docker Compose (per-agent generated manifests) |
| MCP transport | Streamable HTTP / SSE (JSON-RPC 2.0) |
