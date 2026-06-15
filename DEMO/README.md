# Flogents — DevOps / SRE Demo

**Duration**: ~8 minutes end-to-end  
**What you demonstrate**:
1. Create an agent from a built-in template (no config from scratch)
2. Ingest a real runbook document into the agent's knowledge base
3. Activate the agent as a local process
4. Submit a Kubernetes YAML for rule-based static analysis → structured findings
5. Ask a RAG question matched against the ingested runbook → grounded answer
6. Submit the fixed YAML and show the all-clear result

---

## Files in this folder

```
DEMO/
  README.md                                ← this guide
  assets/
    k8s/
      payment-service-deployment.yaml      ← manifest WITH violations (step 4)
      payment-service-deployment-fixed.yaml← manifest AFTER fixes (step 6)
    runbooks/
      k8s-sre-runbook.md                   ← runbook to ingest (step 2)
```

---

## Pre-flight checks

Before starting, confirm these are running:

```bash
# From the flogo-agent-studio root
docker compose ps          # Weaviate, PostgreSQL, Ollama, OTel Collector

# Flogents UI should be up
open http://localhost:7025
```

Confirm Ollama has the model loaded:
```bash
ollama list    # should show llama3.1:8b (or your configured model)
```

---

## Step 1 — Create a DevOps & SRE Agent

1. Open **Flogents UI** → [http://localhost:7025](http://localhost:7025)
2. Click **Gallery** in the left nav
3. Find the card **"DevOps & SRE Assistant"**
4. Click **Use Template** → the agent editor opens pre-filled
5. Confirm the settings shown and click **Save Agent**
   - Collection name: `DevOpsRunbooks`
   - Tools: `ragQuery`, `ruleAnalysis`
   - Model: `llama3.1:8b`, temperature `0.2`

> **Talking point**: Every agent gets its own isolated Weaviate collection. No knowledge leaks between agents.

---

## Step 2 — Ingest the Runbook

1. In the agent editor, find the **Knowledge Base** / Ingestion section
2. Click **Add Document** → choose **File**
3. Upload:
   ```
   DEMO/assets/runbooks/k8s-sre-runbook.md
   ```
4. Wait for the ingestion confirmation (the Flogo ingestion-service chunks and embeds the document)

> **Talking point**: The ingestion pipeline supports URL, GitHub repo, Confluence, and file upload. Content is chunked by paragraph and embedded into the agent's private Weaviate collection — ready for hybrid BM25 + vector search on every query.

---

## Step 3 — Activate the Agent

1. In the editor, click **Activate**
2. Choose **Local Process** (fast startup, no Docker required)
3. The status indicator changes from grey → amber → green (**Ready**)

> **Talking point**: Flogents starts the agent as a set of Flogo microservices — chat service, ingestion service, and rule engine service. Each has its own port in the 7200–7299 pool. Switch to Docker Container for production handoff — same single click.

---

## Step 4 — Rule Analysis: Submit the Broken Manifest

Open the **Chat** for this agent (click the chat bubble icon or open the Chainlit UI).

Paste this message:

```
Analyse this Kubernetes deployment for issues:

<paste the full contents of DEMO/assets/k8s/payment-service-deployment.yaml here>
```

**Expected output** — the rule engine fires **7 findings**:

| Rule ID | Severity | Finding |
|---------|----------|---------|
| KUBE-001 | ERROR | Container `payment-service` uses image `:latest` — not reproducible |
| KUBE-002 | WARNING | No readiness probe — traffic routed before app is ready |
| KUBE-003 | WARNING | No liveness probe — dead containers won't be recycled |
| KUBE-004 | ERROR | No CPU/memory limits — runaway container can starve the node |
| KUBE-005 | WARNING | No resource requests — scheduler cannot make placement decisions |
| KUBE-006 | ERROR | No `securityContext.runAsNonRoot` — container may run as root |
| KUBE-007 | WARNING | No namespace — workload deploys to `default` |

The response includes:
- Findings grouped by **ERROR → WARNING → INFO**
- Inline **recommendation** for each finding (exact YAML fix)
- A **RISK SUMMARY** table with effort-to-fix column

> **Talking point**: This is deterministic static analysis — the same file always produces the same findings. No LLM involved in the rule matching, only in formatting the recommendations.

---

## Step 5 — RAG Chat: Incident Question

In the same chat, ask:

```
Our payment-service pod is in CrashLoopBackOff with exit code 137.
What should I check first?
```

**Expected output**:
- Severity classification: **P2 High**
- Exit code 137 identified as **OOMKilled**
- Immediate triage commands from the runbook (`kubectl describe`, `kubectl logs --previous`)
- Escalation path from the runbook's escalation matrix

> **Talking point**: The answer is grounded in the runbook you just ingested — not generic LLM knowledge. If you ask about something not in the runbook, the agent says so and falls back to SRE best practice, flagging the gap.

Follow-up question to show source grounding:

```
What is the escalation SLA for a pod that has OOMKilled twice within one hour?
```

Expected: **P2 High, 30 minutes, Platform SRE + service team** — sourced directly from the escalation matrix table in the runbook.

---

## Step 6 — Show the Fix: Submit the Clean Manifest

```
Now analyse this updated deployment — I've applied all the recommendations:

<paste the full contents of DEMO/assets/k8s/payment-service-deployment-fixed.yaml here>
```

**Expected output**: 0 ERRORs, 0 WARNINGs — the rule engine confirms the manifest is clean.

> **Talking point**: The before/after comparison closes the loop — the team can use Flogents as a PR gate, checking every manifest before it reaches the cluster.

---

## Optional — Show the MCP Integration (bonus 2 min)

If demoing to developers who use VS Code / Cursor / Claude:

1. Show `services/platform/flogo/mcp-server.flogo` (or mention port `:7333`)
2. Explain: 9 MCP tools are exposed — `query_agent`, `ingest_document`, `run_rule_analysis`, `list_agents`, etc.
3. From an AI IDE, an engineer can run rule analysis on a manifest or ask the agent a question without leaving the editor

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Agent stuck at "Starting Up" | `POST http://localhost:7050/api/agents/<id>/start` |
| Chainlit chat not loading | Wait 10–15 s after activation for all 6 services to come up |
| Rule engine returns empty findings | Confirm the YAML is pasted as plain text (no markdown fences) |
| RAG returns "I don't know" | Confirm ingestion succeeded — check the ingestion-service log |
| Ollama not responding | `ollama run llama3.1:8b` to pull and warm the model |

---

## What the demo proves

| Claim | Evidence shown |
|-------|---------------|
| Deterministic rule analysis | Same YAML → same 7 findings every time |
| Knowledge base isolation | Agent uses its own `DevOpsRunbooks` collection |
| RAG grounding | OOMKilled escalation SLA answer comes from the runbook table |
| Activation lifecycle | Grey → Ready in ~10 seconds |
| Before / after workflow | Clean manifest produces zero findings |
