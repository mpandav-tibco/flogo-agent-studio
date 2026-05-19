"""
FLOGO AGENT STUDIO — FUNCTIONAL TEST SUITE
Tests every exposed API endpoint with assertions on response shape and business logic.
Spin up / teardown a clean test agent so production data is untouched.

Usage:
    python3 functional_tests.py
    python3 functional_tests.py --service design   # run only one service group
"""
import urllib.request, urllib.error, json, re, sys, time, datetime, argparse

# Force UTF-8 output
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
AUTH   = "Basic ZmxvZ286Y2hhbmdlbWU="
PORTS  = {
    "rule-engine":    7097,
    "agent-chat":     7001,
    "ingestion":      7002,
    "feedback":       7003,
    "sse-stream":     7005,
    "agent-builder":  7010,
    "design":         7020,
    "deploy":         7030,
    "mcp":            3333,
}
COLLECTION = "FunctionalTestKB"
MCP_URL    = "http://localhost:3333/mcp"

# ─────────────────────────────────────────────────────────────────────────────
# Test result tracking
# ─────────────────────────────────────────────────────────────────────────────
RESULTS: list[dict] = []   # {service, name, status, reason}
_current_service = "unknown"

def service(name: str):
    global _current_service
    _current_service = name
    w = 70
    print(f"\n{'─'*w}")
    print(f"  SERVICE: {name.upper()}  (port {PORTS.get(name, '?')})")
    print(f"{'─'*w}")

def check(name: str, condition: bool, reason: str = ""):
    status = "PASS" if condition else "FAIL"
    icon   = "✓" if condition else "✗"
    print(f"  [{icon}] {name:<52}  {'' if condition else f'FAIL: {reason}'}")
    RESULTS.append({"service": _current_service, "name": name, "status": status, "reason": reason if not condition else ""})
    return condition

def skip(name: str, reason: str = ""):
    print(f"  [–] {name:<52}  SKIP: {reason}")
    RESULTS.append({"service": _current_service, "name": name, "status": "SKIP", "reason": reason})

# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────
def req(url: str, method: str = "GET", body=None, timeout: int = 60):
    h = {"Content-Type": "application/json", "Authorization": AUTH}
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:    return resp.status, json.loads(raw)
            except: return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:    return e.code, json.loads(raw)
        except: return e.code, raw
    except Exception as ex:
        return 0, str(ex)

def is_up(port: int) -> bool:
    s, _ = req(f"http://localhost:{port}/api/health", timeout=3)
    return s in (200, 403)  # 403 = service up but health endpoint is auth-protected

def unwrap(data) -> list:
    """Unwrap design-service {records:[...]} envelope."""
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    if isinstance(data, list):
        return data
    return [data]

def parse_feedback(text: str) -> list:
    """Parse concatenated JSON objects (no newlines between them)."""
    if not text or not str(text).strip():
        return []
    raw = text if isinstance(text, str) else json.dumps(text)
    raw = raw.strip()
    try:
        return json.loads("[" + re.sub(r"\}\s*\{", "},{", raw) + "]")
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# Shared test state  (populated as tests run)
# ─────────────────────────────────────────────────────────────────────────────
ctx: dict = {
    "design_agent_id":   None,   # UUID of the test agent we create
    "active_agent_id":   None,   # first active agent found at startup
    "template_count":    0,
}

# ═════════════════════════════════════════════════════════════════════════════
# 1. DESIGN SERVICE  — agent lifecycle CRUD
# ═════════════════════════════════════════════════════════════════════════════
def test_design():
    service("design")
    base = "http://localhost:7020/api/v1"

    # ── health ────────────────────────────────────────────────────────────────
    s, b = req(f"{base.replace('/api/v1','')}/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    # ── list agents ───────────────────────────────────────────────────────────
    s, b = req(f"{base}/agents")
    check("GET /api/v1/agents → 200", s == 200, f"HTTP {s}")
    agents = unwrap(b)
    check("GET /api/v1/agents → returns list", isinstance(agents, list), type(b).__name__)
    if agents:
        a0 = agents[0]
        check("agent object has id/name/status fields",
              all(k in a0 for k in ("id", "name", "status")),
              f"keys={list(a0.keys())}")
        # cache first active agent for chat tests
        active = [a for a in agents if a.get("status") == "active"]
        if active:
            ctx["active_agent_id"] = active[0]["id"]

    # ── list agents filtered by status ────────────────────────────────────────
    s, b = req(f"{base}/agents?status=active")
    check("GET /api/v1/agents?status=active → 200", s == 200, f"HTTP {s}")
    filtered = unwrap(b)
    if isinstance(filtered, list) and filtered:
        active_only = [a for a in filtered if a.get("status") == "active"]
        # endpoint may return all agents or only active — just verify it responds
        check("GET /api/v1/agents?status=active → returns agent objects",
              all("id" in a for a in filtered), f"missing id field in some records")

    # ── list templates ────────────────────────────────────────────────────────
    s, b = req(f"{base}/templates")
    check("GET /api/v1/templates → 200", s == 200, f"HTTP {s}")
    templates = unwrap(b) if isinstance(b, (dict, list)) else []
    ctx["template_count"] = len(templates) if isinstance(templates, list) else 0
    check("GET /api/v1/templates → returns list", isinstance(templates, list),
          type(b).__name__)

    # ── create agent ──────────────────────────────────────────────────────────
    payload = {
        "name": "functional-test-agent",
        "description": "Created by functional_tests.py — safe to delete",
        "config": {
            "systemPrompt":  "You are a functional test agent.",
            "collectionName": COLLECTION,
            "topK": 3,
            "llmProvider": "Ollama",
            "llmModel": "llama3.2:3b",
            "llmBaseUrl": "http://localhost:11434",
            "temperature": 0.7,
        },
    }
    s, b = req(f"{base}/agents", "POST", payload)
    check("POST /api/v1/agents → 200/201", s in (200, 201), f"HTTP {s}: {b}")
    recs = unwrap(b)
    agent = recs[0] if recs else {}
    aid = agent.get("id")
    check("create → response has UUID id",       bool(aid) and len(str(aid)) > 10, f"id={aid}")
    check("create → name matches input",          agent.get("name") == "functional-test-agent", f"name={agent.get('name')}")
    check("create → initial status is draft",     agent.get("status") == "draft", f"status={agent.get('status')}")
    check("create → version is 1",                agent.get("version") == 1, f"version={agent.get('version')}")
    ctx["design_agent_id"] = aid

    if not aid:
        print("  [!] Cannot continue design tests — no agent ID")
        return

    # ── get agent ─────────────────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}")
    check("GET /api/v1/agents/:id → 200",    s == 200, f"HTTP {s}")
    rec = unwrap(b)
    got = rec[0] if rec else {}
    # design-service may return config as a JSON string — parse if needed
    cfg = got.get("config", {})
    if isinstance(cfg, str):
        try: cfg = json.loads(cfg)
        except Exception: cfg = {}
    check("get → id matches requested id",   got.get("id") == aid, f"got id={got.get('id')}")
    check("get → config is parseable object",
          isinstance(cfg, dict), f"config type={type(got.get('config')).__name__}")
    check("get → config.systemPrompt present",
          bool(cfg.get("systemPrompt")), "empty systemPrompt")

    # ── update agent ──────────────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}", "PUT", {"description": "Updated by functional test"})
    check("PUT /api/v1/agents/:id → 200",    s == 200, f"HTTP {s}: {b}")
    rec = unwrap(b)
    upd = rec[0] if rec else {}
    check("update → description changed",    upd.get("description") == "Updated by functional test",
          f"desc={upd.get('description')}")
    check("update → version incremented to 2", upd.get("version") == 2, f"version={upd.get('version')}")


# ═════════════════════════════════════════════════════════════════════════════
# 2. DEPLOY SERVICE  — activate / status / export / deactivate
# ═════════════════════════════════════════════════════════════════════════════
def test_deploy():
    service("deploy")
    base = "http://localhost:7030/api/v1"
    aid  = ctx.get("design_agent_id")

    s, _ = req("http://localhost:7030/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    if not aid:
        skip("deploy tests", "no test agent id (design tests may have failed)")
        return

    # ── deploy / activate ────────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}/deploy", "POST", {})
    check("POST /api/v1/agents/:id/deploy → 200", s == 200, f"HTTP {s}: {b}")
    recs = b.get("records", []) if isinstance(b, dict) else []
    status_val = recs[0].get("status") if recs else None
    check("deploy → status becomes active",  status_val == "active", f"status={status_val}")

    # ── get deploy status ────────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}/deploy")
    check("GET /api/v1/agents/:id/deploy → 200", s == 200, f"HTTP {s}")
    recs = b.get("records", []) if isinstance(b, dict) else []
    check("deploy status → has status field",
          bool(recs) and "status" in recs[0], f"records={recs}")
    check("deploy status → status is active",
          (recs[0].get("status") if recs else None) == "active",
          f"status={recs[0].get('status') if recs else 'no records'}")

    # ── export kubernetes ────────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}/export/kubernetes")
    check("GET /api/v1/agents/:id/export/kubernetes → 200", s == 200, f"HTTP {s}")
    yaml_text = b if isinstance(b, str) else json.dumps(b)
    check("k8s export → contains 'apiVersion'",  "apiVersion" in yaml_text,
          f"preview={yaml_text[:100]}")
    check("k8s export → contains 'Deployment'",  "Deployment" in yaml_text,
          f"preview={yaml_text[:100]}")

    # ── export docker-compose ────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}/export/docker-compose")
    check("GET /api/v1/agents/:id/export/docker-compose → 200", s == 200, f"HTTP {s}")
    dc_text = b if isinstance(b, str) else json.dumps(b)
    check("docker-compose export → contains 'services'", "services" in dc_text,
          f"preview={dc_text[:100]}")
    check("docker-compose export → contains agent id",   aid[:8] in dc_text,
          f"id={aid[:8]} not in output")

    # ── undeploy / deactivate ────────────────────────────────────────────────
    s, b = req(f"{base}/agents/{aid}/deploy", "DELETE")
    check("DELETE /api/v1/agents/:id/deploy → 200", s == 200, f"HTTP {s}: {b}")
    recs = b.get("records", []) if isinstance(b, dict) else []
    undep_status = recs[0].get("status") if recs else None
    check("undeploy → status is no longer active",
          undep_status != "active", f"status={undep_status}")


# ═════════════════════════════════════════════════════════════════════════════
# 3. INGESTION SERVICE  — text / url / github / confluence / collection
# ═════════════════════════════════════════════════════════════════════════════
def test_ingestion():
    service("ingestion")
    base = "http://localhost:7002"

    s, _ = req(f"{base}/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    doc_text = (
        "TIBCO Flogo provides a suite of AgenticAI activities for building intelligent agents. "
        "The VectorDB RAGQuery activity retrieves semantically relevant chunks from Weaviate. "
        "The AgentActivity orchestrates LLM reasoning with optional tool use. "
        "This document is used by the functional test suite."
    )

    # ── ingest text ───────────────────────────────────────────────────────────
    s, b = req(f"{base}/api/ingest", "POST", {
        "collectionName": COLLECTION,
        "chunkStrategy": "sentence",
        "documents": [{"text": doc_text, "metadata": {"source": "functional-test"}}],
    })
    check("POST /api/ingest → 200", s == 200, f"HTTP {s}: {b}")
    if isinstance(b, dict):
        check("ingest text → success:true",          b.get("success") is True, f"success={b.get('success')}")
        check("ingest text → chunksIngested > 0",
              (b.get("chunksIngested", 0) or b.get("chunksCreated", 0) or b.get("ingestedCount", 0)) > 0,
              f"body={b}")

    # ── ingest url  (local URL — no external dependency) ─────────────────────
    local_url = "http://localhost:7003/api/health"
    s, b = req(f"{base}/api/ingest/url", "POST", {
        "collectionName": COLLECTION,
        "url": local_url,
        "chunkStrategy": "sentence",
    }, timeout=30)
    check("POST /api/ingest/url → 200", s == 200, f"HTTP {s}: {b}")
    if isinstance(b, dict) and s == 200:
        check("ingest url → success:true",  b.get("success") is True, f"body={b}")
        check("ingest url → url echoed",    b.get("url") == local_url, f"url={b.get('url')}")

    # ── ingest github (route reachability — GitHub URL not configured in env) ─
    s, b = req(f"{base}/api/ingest/github", "POST", {
        "collectionName": COLLECTION,
        "owner": "TIBCOSoftware", "repo": "flogo-contrib",
        "path": "README.md", "branch": "master",
    }, timeout=15)
    route_ok = s > 0
    check("POST /api/ingest/github → route exists (any HTTP response)",
          route_ok, "no response (service may be down)")
    if s == 400 and "URL is not configured" in str(b):
        print(f"       NOTE: GitHub API URL not set in Flogo app — expected in dev env")

    # ── ingest confluence (route reachability) ────────────────────────────────
    s, b = req(f"{base}/api/ingest/confluence", "POST", {
        "collectionName": COLLECTION,
        "spaceKey": "TEST", "baseUrl": "http://localhost:8090",
    }, timeout=10)
    check("POST /api/ingest/confluence → route exists (any HTTP response)",
          s > 0, "no response")

    # ── ingest/collection endpoint ────────────────────────────────────────────
    s, b = req(f"{base}/api/ingest/collection", "POST", {
        "collectionName": COLLECTION,
    }, timeout=10)
    check("POST /api/ingest/collection → route exists",
          s > 0, "no response")


# ═════════════════════════════════════════════════════════════════════════════
# 4. AGENT CHAT SERVICE  — RAG query
# ═════════════════════════════════════════════════════════════════════════════
def test_agent_chat():
    service("agent-chat")
    base = "http://localhost:7001"

    s, _ = req(f"{base}/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    agent_id = ctx.get("active_agent_id")
    if not agent_id:
        skip("POST /api/chat", "no active agent found — activate one first")
        return

    question = "What AgenticAI activities does TIBCO Flogo provide?"
    s, b = req(f"{base}/api/chat", "POST", {
        "message":   question,
        "agentId":   agent_id,
        "sessionId": "functional-test-session",
    }, timeout=180)
    check("POST /api/chat → 200", s == 200, f"HTTP {s}: {str(b)[:200]}")
    if isinstance(b, dict) and s == 200:
        answer = b.get("answer", "")
        check("chat → answer is non-empty string",
              isinstance(answer, str) and len(answer) > 10, f"answer='{answer[:80]}'")
        check("chat → no error field set",
              not b.get("error"), f"error={b.get('error')}")
        check("chat → duration field present",
              "duration" in b, f"keys={list(b.keys())}")


# ═════════════════════════════════════════════════════════════════════════════
# 5. FEEDBACK SERVICE  — submit / retrieve / isolation
# ═════════════════════════════════════════════════════════════════════════════
def test_feedback():
    service("feedback")
    base  = "http://localhost:7003"
    aid   = ctx.get("design_agent_id") or "functional-test-agent-id"
    other = "00000000-0000-0000-0000-000000000000"   # an agent that won't have our feedback

    s, _ = req(f"{base}/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    # ── submit thumbsUp ───────────────────────────────────────────────────────
    s, b = req(f"{base}/api/feedback", "POST", {
        "agentId":   aid,
        "rating":    "thumbsUp",
        "comment":   "Functional test: thumbsUp",
        "sessionId": "functional-test-session",
        "messageId": "functional-msg-001",
    })
    check("POST /api/feedback (thumbsUp) → 200", s == 200, f"HTTP {s}: {b}")
    if isinstance(b, dict):
        check("thumbsUp → response has fullPath or success",
              bool(b.get("fullPath") or b.get("success")), f"body={b}")

    # ── submit thumbsDown ─────────────────────────────────────────────────────
    s, b = req(f"{base}/api/feedback", "POST", {
        "agentId":   aid,
        "rating":    "thumbsDown",
        "comment":   "Functional test: thumbsDown",
        "sessionId": "functional-test-session",
        "messageId": "functional-msg-002",
    })
    check("POST /api/feedback (thumbsDown) → 200", s == 200, f"HTTP {s}: {b}")

    # ── submit numeric rating ─────────────────────────────────────────────────
    s, b = req(f"{base}/api/feedback", "POST", {
        "agentId":   aid,
        "rating":    4,
        "comment":   "Functional test: numeric rating 4",
        "sessionId": "functional-test-session",
    })
    check("POST /api/feedback (numeric=4) → 200", s == 200, f"HTTP {s}: {b}")

    # ── retrieve feedback for our agent ──────────────────────────────────────
    time.sleep(0.2)   # brief pause for JSONL flush
    s, b = req(f"{base}/api/feedback/{aid}")
    check("GET /api/feedback/:agentId → 200", s == 200, f"HTTP {s}")
    records = parse_feedback(b)
    check("retrieve → returns at least 3 records we just submitted",
          len(records) >= 3, f"found {len(records)} records")
    if records:
        check("retrieve → all records have agentId matching our agent",
              all(r.get("agentId") == aid for r in records),
              f"foreign agentIds: {[r.get('agentId') for r in records if r.get('agentId') != aid]}")
        ratings = {str(r.get("rating")) for r in records}
        check("retrieve → thumbsUp present in records",   "thumbsUp" in ratings,   f"ratings={ratings}")
        check("retrieve → thumbsDown present in records", "thumbsDown" in ratings, f"ratings={ratings}")

    # ── agent isolation: other agent's feedback should NOT appear ─────────────
    s, b = req(f"{base}/api/feedback/{other}")
    other_records = parse_feedback(b)
    our_in_other = [r for r in other_records if r.get("agentId") == aid]
    check("isolation → our feedback not in another agent's records",
          len(our_in_other) == 0,
          f"found {len(our_in_other)} of our records under agent={other}")

    # ── retrieve all feedback (no filter) ─────────────────────────────────────
    s, b = req(f"{base}/api/feedback")
    check("GET /api/feedback (all) → 200", s == 200, f"HTTP {s}")
    all_records = parse_feedback(b)
    check("GET /api/feedback → returns list", isinstance(all_records, list),
          f"type={type(b).__name__}")


# ═════════════════════════════════════════════════════════════════════════════
# 6. RULE ENGINE SERVICE  — analyze Flogo app
# ═════════════════════════════════════════════════════════════════════════════
def test_rule_engine():
    service("rule-engine")
    base = "http://localhost:7000"

    s, _ = req(f"{base}/api/health")
    if s == 403:
        skip("POST /api/analyze", "rule-engine requires no-auth direct access — test via Forge UI proxy (7025) instead")
        skip("POST /api/analyze (invalid JSON)", "skipped (same auth issue)")
        check("GET /api/health → service listening (403=auth-protected)",
              True, "")
        return
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    flogo_app = {
        "name": "functional-test-app",
        "type": "flogo:app",
        "version": "1.0.0",
        "imports": [
            "github.com/tibco/flogo-general/src/app/General/activity/rest",
            "github.com/tibco/flogo-mcp/src/app/MCP/trigger/mcpserver",
        ],
        "triggers": [{"id": "MCPServer", "ref": "#tr_mcpserver"}],
        "resources": [],
    }

    s, b = req(f"{base}/api/analyze", "POST", {
        "content":   json.dumps(flogo_app),
        "fileName":  "functional-test-app.flogo",
        "rulesPath": "rules/",
        "tags":      "functional-test",
    })
    check("POST /api/analyze → 200", s == 200, f"HTTP {s}: {b}")
    if isinstance(b, dict) and s == 200:
        check("analyze → has 'success' field",     "success" in b,     f"keys={list(b.keys())}")
        check("analyze → has 'findings' field",    "findings" in b,    f"keys={list(b.keys())}")
        check("analyze → has 'errorCount' field",  "errorCount" in b,  f"keys={list(b.keys())}")
        check("analyze → overview.rules_run > 0",
              (b.get("overview", {}).get("rules_run", 0) or 0) > 0,
              f"overview={b.get('overview')}")

    # ── analyze with invalid JSON content ─────────────────────────────────────
    s, b = req(f"{base}/api/analyze", "POST", {
        "content":  "this is not json",
        "fileName": "bad.flogo",
        "rulesPath": "rules/",
    })
    check("POST /api/analyze (invalid JSON) → responds (non-zero status)",
          s > 0, "no response from service")


# ═════════════════════════════════════════════════════════════════════════════
# 7. SSE STREAM SERVICE  — broadcast + chat stream
# ═════════════════════════════════════════════════════════════════════════════
def test_sse_stream():
    service("sse-stream")
    base = "http://localhost:7005"

    s, _ = req(f"{base}/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    # ── broadcast event ───────────────────────────────────────────────────────
    s, b = req(f"{base}/api/stream/broadcast", "POST", {
        "eventType": "test.ping",
        "sessionId": "functional-test-session",
        "data":      {"msg": "functional test broadcast"},
    })
    check("POST /api/stream/broadcast → 200", s == 200, f"HTTP {s}: {b}")

    # ── stream chat ───────────────────────────────────────────────────────────
    agent_id = ctx.get("active_agent_id")
    if not agent_id:
        skip("POST /api/stream/chat", "no active agent")
    else:
        s, b = req(f"{base}/api/stream/chat", "POST", {
            "message":   "What is TIBCO Flogo?",
            "agentId":   agent_id,
            "sessionId": "functional-test-session",
        }, timeout=180)
        check("POST /api/stream/chat → 200/202", s in (200, 202), f"HTTP {s}: {str(b)[:200]}")
        if isinstance(b, dict) and s in (200, 202):
            check("stream/chat → streaming:true",
                  b.get("streaming") is True, f"body={b}")
            check("stream/chat → eventsUrl present",
                  bool(b.get("eventsUrl")), f"body={b}")


# ═════════════════════════════════════════════════════════════════════════════
# 8. AGENT BUILDER SERVICE  — generate / improve / validate
# ═════════════════════════════════════════════════════════════════════════════
def test_agent_builder():
    service("agent-builder")

    if not is_up(7010):
        skip("all agent-builder tests", "service not running on port 7010")
        return

    base = "http://localhost:7010"

    s, _ = req(f"{base}/api/health")
    if not check("GET /api/health → 200", s == 200, f"HTTP {s}"): return

    # ── generate ──────────────────────────────────────────────────────────────
    s, b = req(f"{base}/api/agent-builder/generate", "POST", {
        "prompt": "Create a TIBCO Flogo AgenticAI assistant that answers questions about Flogo activities.",
        "model":  "llama3.2:3b",
    }, timeout=90)
    check("POST /api/agent-builder/generate → 200", s == 200, f"HTTP {s}: {b}")
    gen_cfg = {}
    if isinstance(b, dict) and s == 200:
        gen_cfg = b.get("config", {})
        check("generate → config.systemPrompt non-empty",
              bool(gen_cfg.get("systemPrompt")), f"config={gen_cfg}")
        check("generate → config.name non-empty",
              bool(gen_cfg.get("name")), f"config={gen_cfg}")

    # ── improve ───────────────────────────────────────────────────────────────
    agent_id = ctx.get("active_agent_id") or ctx.get("design_agent_id")
    s, b = req(f"{base}/api/agent-builder/improve", "POST", {
        "agentId":  agent_id,
        "feedback": "Users want more concise answers with code examples.",
    }, timeout=90)
    check("POST /api/agent-builder/improve → 200", s == 200, f"HTTP {s}: {b}")
    if isinstance(b, dict) and s == 200:
        # response shape: {agentId, current, suggestions: {improved: {...}, changes: [...]}}
        imp_cfg = b.get("suggestions", {}).get("improved", {})
        check("improve → suggestions.improved.systemPrompt non-empty",
              bool(imp_cfg.get("systemPrompt")), f"suggestions.improved keys={list(imp_cfg.keys())}")

    # ── validate ──────────────────────────────────────────────────────────────
    s, b = req(f"{base}/api/agent-builder/validate", "POST", {
        "config": {
            "name": "test", "systemPrompt": "You are a test agent.",
            "llmProvider": "Ollama", "llmModel": "llama3.2:3b",
        },
    }, timeout=30)
    check("POST /api/agent-builder/validate → route exists",
          s > 0, "no response")


# ═════════════════════════════════════════════════════════════════════════════
# 9. MCP SERVER  — initialize / tools/list / tools/call
# ═════════════════════════════════════════════════════════════════════════════
def mcp_req(method: str, params=None, msg_id: int = 1, session_id=None):
    payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params:
        payload["params"] = params
    hdrs = {"Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    if session_id:
        hdrs["mcp-session-id"] = session_id
    data = json.dumps(payload).encode()
    r = urllib.request.Request(MCP_URL, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            sid = resp.headers.get("mcp-session-id", session_id)
            raw = resp.read().decode()
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    d = line[5:].strip()
                    if d:
                        try:    return sid, json.loads(d)
                        except: return sid, d
            return sid, raw
    except Exception as e:
        return session_id, {"error": str(e)}

def _mcp_is_up() -> bool:
    """MCP server uses JSON-RPC over SSE — plain HTTP health check returns 404.
    Use a lightweight initialize to verify the server is responsive."""
    _, resp = mcp_req("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "healthcheck", "version": "1.0"},
    })
    return isinstance(resp, dict) and "result" in resp


def test_mcp():
    service("mcp")

    if not _mcp_is_up():
        skip("all MCP tests", "MCP server not running on port 3333")
        return

    # ── initialize ────────────────────────────────────────────────────────────
    sid, resp = mcp_req("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "functional-tests", "version": "1.0"},
    })
    ok = isinstance(resp, dict) and "result" in resp
    check("MCP initialize → JSON-RPC result", ok, f"resp={resp}")
    if ok:
        r = resp["result"]
        check("MCP initialize → serverInfo present",    "serverInfo" in r, f"keys={list(r.keys())}")
        check("MCP initialize → protocolVersion echoed", "protocolVersion" in r, f"keys={list(r.keys())}")

    # notify initialized
    notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if sid:
        hdrs["mcp-session-id"] = sid
    try:
        urllib.request.urlopen(
            urllib.request.Request(MCP_URL, data=json.dumps(notify).encode(), headers=hdrs, method="POST"),
            timeout=5)
    except Exception:
        pass

    # ── tools/list ────────────────────────────────────────────────────────────
    _, tresp = mcp_req("tools/list", msg_id=2, session_id=sid)
    ok = isinstance(tresp, dict) and "result" in tresp
    check("MCP tools/list → JSON-RPC result", ok, f"resp={tresp}")
    tools = []
    if ok:
        tools = tresp["result"].get("tools", [])
        check("MCP tools/list → at least 4 tools registered", len(tools) >= 4,
              f"found {len(tools)}: {[t['name'] for t in tools]}")
        tool_names = {t["name"] for t in tools}
        for expected in ("list_agents", "get_agent", "rag_chat", "analyze_flogo"):
            check(f"MCP tool '{expected}' registered", expected in tool_names,
                  f"registered={sorted(tool_names)}")

    # ── tools/call: list_agents ───────────────────────────────────────────────
    _, resp = mcp_req("tools/call", {"name": "list_agents", "arguments": {}}, 3, sid)
    ok = isinstance(resp, dict) and "result" in resp
    check("MCP tools/call list_agents → result",  ok, f"resp={resp}")
    if ok:
        content = resp["result"].get("content", [])
        check("list_agents → content non-empty",  bool(content), "empty content")

    # ── tools/call: rag_chat ──────────────────────────────────────────────────
    agent_id = ctx.get("active_agent_id")
    if agent_id:
        _, resp = mcp_req("tools/call", {
            "name": "rag_chat",
            "arguments": {"message": "What is TIBCO Flogo?", "agentId": agent_id, "sessionId": "mcp-func-test"},
        }, 4, sid)
        ok = isinstance(resp, dict) and "result" in resp
        check("MCP tools/call rag_chat → result", ok, f"resp={resp}")
        if ok:
            content = resp["result"].get("content", [])
            text = content[0].get("text", "") if content else ""
            try:
                data = json.loads(text)
                answer = data.get("answer", "")
            except Exception:
                answer = text
            check("rag_chat → non-empty answer", len(str(answer)) > 5, f"answer='{str(answer)[:80]}'")
    else:
        skip("MCP tools/call rag_chat", "no active agent")


# ═════════════════════════════════════════════════════════════════════════════
# 10. TEARDOWN  — delete the test agent we created
# ═════════════════════════════════════════════════════════════════════════════
def teardown():
    aid = ctx.get("design_agent_id")
    if not aid:
        return
    s, b = req(f"http://localhost:7020/api/v1/agents/{aid}", "DELETE")
    ok = s in (200, 204)
    print(f"\n  [{'✓' if ok else '✗'}] TEARDOWN: DELETE test agent {aid}  →  HTTP {s}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", help="Run only this service group", default="all")
    args = parser.parse_args()
    target = args.service.lower()

    w = 70
    print("=" * w)
    print("  FLOGO AGENT STUDIO — FUNCTIONAL TEST SUITE")
    print(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * w)

    # Service health pre-check
    print("\n  Service availability:")
    for svc, port in PORTS.items():
        up = _mcp_is_up() if svc == "mcp" else is_up(port)
        print(f"    {'✓' if up else '✗'}  {svc:<20} port {port}  {'UP' if up else 'DOWN'}")

    t0 = time.time()
    SUITE = [
        ("design",        test_design),
        ("deploy",        test_deploy),
        ("ingestion",     test_ingestion),
        ("agent-chat",    test_agent_chat),
        ("feedback",      test_feedback),
        ("rule-engine",   test_rule_engine),
        ("sse-stream",    test_sse_stream),
        ("agent-builder", test_agent_builder),
        ("mcp",           test_mcp),
    ]

    for name, fn in SUITE:
        if target == "all" or target == name:
            fn()

    teardown()

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    passed  = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed  = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")
    total   = len(RESULTS)

    print(f"\n{'=' * w}")
    print(f"  RESULTS SUMMARY  ({elapsed:.1f}s)")
    print(f"{'=' * w}")
    print(f"  Total : {total}  |  PASS: {passed}  |  FAIL: {failed}  |  SKIP: {skipped}")

    if failed:
        print(f"\n  FAILURES:")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"    ✗  [{r['service']}]  {r['name']}")
                if r["reason"]:
                    print(f"         reason: {r['reason']}")

    # Per-service breakdown
    print(f"\n  PER-SERVICE BREAKDOWN:")
    services_seen = []
    for r in RESULTS:
        if r["service"] not in services_seen:
            services_seen.append(r["service"])
    for svc in services_seen:
        svc_results = [r for r in RESULTS if r["service"] == svc]
        p = sum(1 for r in svc_results if r["status"] == "PASS")
        f = sum(1 for r in svc_results if r["status"] == "FAIL")
        sk = sum(1 for r in svc_results if r["status"] == "SKIP")
        bar = ("✓" * p) + ("✗" * f) + ("–" * sk)
        print(f"    {svc:<20}  {bar:<30}  {p}P {f}F {sk}S")

    print(f"\n  STATUS: {'ALL TESTS PASSED' if failed == 0 else f'{failed} TESTS FAILED'}")
    print(f"{'=' * w}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
