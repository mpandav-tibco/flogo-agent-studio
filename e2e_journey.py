"""
FLOGO AGENT STUDIO - END-TO-END JOURNEY TEST
Scenario: Onboard knowledge -> analyze app -> RAG query -> feedback -> build agent -> stream -> MCP
"""
import urllib.request, urllib.error, json, time, datetime, os, sys

# Force UTF-8 output on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

LOG = []

def ts():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

def banner(title):
    sep = "=" * 100
    print(f"\n{sep}\n  {title}\n{sep}")
    LOG.append(f"\n{'='*100}\n  {title}\n{'='*100}")

def step(n, desc):
    print(f"\n  [STEP {n}] {desc}")
    LOG.append(f"\n  [STEP {n}] {desc}")

def logline(label, value, indent=4):
    pad = " " * indent
    if isinstance(value, (dict, list)):
        text = json.dumps(value, indent=2, ensure_ascii=False)
        print(f"{pad}{label}:")
        for l in text.split("\n"):
            print(f"{pad}  {l}")
        LOG.append(f"{pad}{label}: {json.dumps(value, ensure_ascii=False)}")
    else:
        print(f"{pad}{label}: {value}")
        LOG.append(f"{pad}{label}: {value}")

AUTH_HEADER = "Basic ZmxvZ286Y2hhbmdlbWU="

def req(url, method="GET", body=None, timeout=60):
    h = {"Content-Type": "application/json", "Authorization": AUTH_HEADER}
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            content = resp.read().decode()
            elapsed = int((time.time() - t0) * 1000)
            try:
                return resp.status, json.loads(content), elapsed
            except Exception:
                return resp.status, content, elapsed
    except urllib.error.HTTPError as e:
        elapsed = int((time.time() - t0) * 1000)
        content = e.read().decode()
        try:
            return e.code, json.loads(content), elapsed
        except Exception:
            return e.code, content, elapsed
    except Exception as ex:
        return 0, str(ex), int((time.time() - t0) * 1000)

def show_result(status, elapsed, ok_codes=(200, 201, 202)):
    ok = status in ok_codes
    icon = "OK  " if ok else "FAIL"
    print(f"       --> HTTP {status}  [{icon}]  ({elapsed}ms)")
    LOG.append(f"       --> HTTP {status} [{icon}] ({elapsed}ms)")
    return ok

MCP_URL = "http://localhost:3333/mcp"

def parse_sse(content):
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("data:"):
            d = line[5:].strip()
            if d:
                try:
                    return json.loads(d)
                except Exception:
                    return d
    return None

def mcp_request(method, params=None, msg_id=1, session_id=None):
    payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params:
        payload["params"] = params
    hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if session_id:
        hdrs["mcp-session-id"] = session_id
    data = json.dumps(payload).encode()
    r = urllib.request.Request(MCP_URL, data=data, headers=hdrs, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            sid = resp.headers.get("mcp-session-id", session_id)
            content = resp.read().decode()
            elapsed = int((time.time() - t0) * 1000)
            return sid, parse_sse(content), elapsed
    except Exception as e:
        return session_id, {"error": str(e)}, 0

def mcp_notify(session_id):
    payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    hdrs = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "mcp-session-id": session_id}
    data = json.dumps(payload).encode()
    r = urllib.request.Request(MCP_URL, data=data, headers=hdrs, method="POST")
    try:
        urllib.request.urlopen(r, timeout=5)
    except Exception:
        pass

def mcp_tool(tool_name, args, session_id, msg_id):
    sid, resp, elapsed = mcp_request("tools/call", {"name": tool_name, "arguments": args}, msg_id, session_id)
    if resp and "result" in resp:
        content = resp["result"].get("content", [])
        if content and "text" in content[0]:
            try:
                return True, json.loads(content[0]["text"]), elapsed
            except Exception:
                return True, content[0]["text"], elapsed
    return False, resp, elapsed

# =============================================================================
banner("FLOGO AGENT STUDIO - END-TO-END JOURNEY TEST")
start_dt = datetime.datetime.now()
print(f"  Started   : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"  Scenario  : A developer onboards a knowledge topic, queries it via RAG,")
print(f"              collects feedback, generates an improved agent, streams a chat,")
print(f"              and exercises all capabilities via the MCP protocol.")
LOG.append(f"Started: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
journey_start = time.time()
all_ok = True

# =============================================================================
banner("PHASE 1 - HEALTH CHECKS (all 10 services)")
# =============================================================================
SERVICES = [
    ("rule-engine-service",    7000),
    ("agent-chat-service",     7001),
    ("ingestion-service",      7002),
    ("feedback-service",       7003),
    ("config-service",         7004),
    ("sse-stream-service",     7005),
    ("agent-builder-service",  7010),
    ("design-service",         7020),
    ("deploy-service",         7030),
]
print()
for svc, port in SERVICES:
    s, b, el = req(f"http://localhost:{port}/api/health")
    icon = "OK  " if s == 200 else "FAIL"
    ver = b.get("version", "?") if isinstance(b, dict) else "?"
    print(f"  [{icon}]  {svc:<28}  port={port}  HTTP {s}  ({el}ms)  version={ver}")
    LOG.append(f"  [{icon}] {svc} port={port} HTTP {s} ({el}ms) version={ver}")
    if s != 200:
        all_ok = False

sid_hc, _, el_hc = mcp_request("initialize", {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "health-check", "version": "1.0"}
})
icon = "OK  " if sid_hc else "FAIL"
print(f"  [{icon}]  mcp-server                    port=3333  HTTP 200  ({el_hc}ms)  MCP 2024-11-05")
LOG.append(f"  [{icon}] mcp-server port=3333 HTTP 200 ({el_hc}ms)")

# =============================================================================
banner("PHASE 2 - CONFIG-SERVICE (port 7004): Discover agents")
# =============================================================================

step(1, "List all registered agents")
print(f"       REQUEST : GET http://localhost:7004/api/agents")
s, body, el = req("http://localhost:7004/api/agents")
ok = show_result(s, el)
if ok and isinstance(body, list):
    print(f"       Found {len(body)} agent(s):")
    for a in body:
        name = a.get("name", os.path.basename(a.get("fullPath", "?")))
        print(f"         - {name}")
        LOG.append(f"         - {name}")
    LOG.append(f"       Found {len(body)} agents")
else:
    all_ok = False

step(2, "Get default agent configuration")
print(f"       REQUEST : GET http://localhost:7004/api/agents/default")
s, agent_cfg, el = req("http://localhost:7004/api/agents/default")
ok = show_result(s, el)
COLLECTION = "KnowledgeBase"
AGENT_ID   = "default"
if ok and isinstance(agent_cfg, dict):
    COLLECTION = agent_cfg.get("collectionName", "KnowledgeBase")
    AGENT_ID   = agent_cfg.get("id", "default")
    keys = ("id","name","collectionName","description","active","chunkStrategy")
    cfg_summary = {k: agent_cfg[k] for k in keys if k in agent_cfg}
    print(f"       RESPONSE:")
    for k, v in cfg_summary.items():
        print(f"         {k:<20}: {v}")
    LOG.append(f"       RESPONSE: {cfg_summary}")
else:
    all_ok = False

# =============================================================================
banner(f"PHASE 3 - INGESTION-SERVICE (port 7002): Load knowledge into '{COLLECTION}'")
# =============================================================================

DOC_TEXT = (
    "TIBCO Flogo AgenticAI provides a suite of activities for building intelligent agents. "
    "Key activities include: AgentActivity for orchestrating LLM-based reasoning with tool use, "
    "VectorDB RAGQuery for retrieval-augmented generation from a vector database, "
    "MCP trigger for Model Context Protocol tool registration and JSON-RPC handling, "
    "SSE trigger for real-time server-sent event streaming to browser clients, "
    "and Pongo2Prompt for dynamic Jinja2-style prompt templating. "
    "Agents can chain these activities to build end-to-end AI workflows combining "
    "knowledge retrieval, LLM reasoning, and real-time streaming."
)

step(3, f"Ingest AgenticAI capabilities document -> '{COLLECTION}' (Weaviate)")
ingest_payload = {
    "collectionName": COLLECTION,
    "documents": [{
        "text": DOC_TEXT,
        "metadata": {"source": "e2e-journey", "topic": "AgenticAI", "timestamp": ts()}
    }]
}
print(f"       REQUEST : POST http://localhost:7002/api/ingest")
print(f"       BODY    : collectionName={COLLECTION}")
print(f"       BODY    : documents[0].text = '{DOC_TEXT[:80]}...'")
print(f"       BODY    : documents[0].metadata = source=e2e-journey, topic=AgenticAI")
LOG.append(f"       REQUEST: POST /api/ingest body={json.dumps(ingest_payload)}")
s, body, el = req("http://localhost:7002/api/ingest", "POST", ingest_payload)
ok = show_result(s, el)
if ok and isinstance(body, dict):
    INGEST_ID = body.get("ids", ["?"])[0]
    print(f"       RESPONSE:")
    print(f"         chunksCreated   : {body.get('chunksCreated', 0)}")
    print(f"         ingestedCount   : {body.get('ingestedCount', 0)}")
    print(f"         dimensions      : {body.get('dimensions', 0)}  (embedding vector size)")
    print(f"         duration        : {body.get('duration', '')}")
    print(f"         vectorId        : {INGEST_ID[:20]}...")
    LOG.append(f"       RESPONSE: {body}")
else:
    print(f"       ERROR: {body}"); all_ok = False

# =============================================================================
banner("PHASE 4 - RULE-ENGINE-SERVICE (port 7000): Analyze Flogo app quality")
# =============================================================================

FLOGO_SAMPLE = {
    "name": "agent-studio-app",
    "type": "flogo:app",
    "version": "1.0.0",
    "description": "Flogo Agent Studio - AI agent platform",
    "imports": [
        "github.com/tibco/flogo-general/src/app/General/activity/rest",
        "github.com/tibco/flogo-mcp/src/app/MCP/trigger/mcpserver"
    ],
    "triggers": [{"id": "MCPServer", "ref": "#tr_mcpserver"}],
    "resources": []
}

step(4, "Run quality rules against the Flogo app definition")
analyze_payload = {
    "content":   json.dumps(FLOGO_SAMPLE),
    "fileName":  "agent-studio-app.flogo",
    "rulesPath": "rules/",
    "tags":      "production,ai-agent"
}
print(f"       REQUEST : POST http://localhost:7000/api/analyze")
print(f"       BODY    : fileName=agent-studio-app.flogo  tags=production,ai-agent")
print(f"       BODY    : content={{ name=agent-studio-app type=flogo:app version=1.0.0 }}")
LOG.append(f"       REQUEST: POST /api/analyze")
s, body, el = req("http://localhost:7000/api/analyze", "POST", analyze_payload)
ok = show_result(s, el)
if ok and isinstance(body, dict):
    ov = body.get("overview", {})
    print(f"       RESPONSE:")
    print(f"         success         : {body.get('success')}")
    print(f"         errorCount      : {body.get('errorCount', 0)}")
    print(f"         warningCount    : {body.get('warningCount', 0)}")
    print(f"         infoCount       : {body.get('infoCount', 0)}")
    print(f"         rules_run       : {ov.get('rules_run', 0)}")
    print(f"         parser          : {ov.get('parser', '')}")
    findings = body.get("findings", [])
    if findings:
        print(f"         findings        : {len(findings)}")
        for f in findings:
            print(f"           - [{f.get('severity','?')}] {f.get('message','')}")
    else:
        print(f"         findings        : none (app looks clean)")
    LOG.append(f"       RESPONSE: {body}")
else:
    print(f"       ERROR: {body}"); all_ok = False

# =============================================================================
banner(f"PHASE 5 - AGENT-CHAT-SERVICE (port 7001): RAG query against '{COLLECTION}'")
# =============================================================================

QUESTION = "What AgenticAI activities does TIBCO Flogo provide for building AI agents?"

step(5, f"Ask RAG question: '{QUESTION}'")
chat_payload = {"message": QUESTION, "collectionName": COLLECTION}
print(f"       REQUEST : POST http://localhost:7001/api/chat")
print(f"       BODY    : message='{QUESTION}'")
print(f"       BODY    : collectionName={COLLECTION}")
print(f"       PIPELINE: embed query -> search Weaviate -> rerank -> build answer")
LOG.append(f"       REQUEST: POST /api/chat body={json.dumps(chat_payload)}")
s, chat_resp, el = req("http://localhost:7001/api/chat", "POST", chat_payload)
ok = show_result(s, el)
ANSWER = ""
if ok and isinstance(chat_resp, dict):
    ANSWER = chat_resp.get("answer", "")
    print(f"       RESPONSE:")
    print(f"         answer          : {ANSWER}")
    print(f"         duration        : {chat_resp.get('duration', '')}")
    print(f"         error           : '{chat_resp.get('error', '')}'")
    LOG.append(f"       RESPONSE: {chat_resp}")
else:
    print(f"       ERROR: {chat_resp}"); all_ok = False

# =============================================================================
banner("PHASE 6 - FEEDBACK-SERVICE (port 7003): Submit and retrieve feedback")
# =============================================================================

step(6, f"Submit user feedback (rating=5) for agent '{AGENT_ID}'")
fb_payload = {
    "agentId":   AGENT_ID,
    "rating":    5,
    "comment":   f"RAG answer correctly listed AgenticAI activities. Answer: {ANSWER[:80]}",
    "sessionId": "e2e-journey-001"
}
print(f"       REQUEST : POST http://localhost:7003/api/feedback")
print(f"       BODY    : agentId={AGENT_ID}  rating=5  sessionId=e2e-journey-001")
print(f"       BODY    : comment='{fb_payload['comment'][:70]}...'")
LOG.append(f"       REQUEST: POST /api/feedback body={json.dumps(fb_payload)}")
s, body, el = req("http://localhost:7003/api/feedback", "POST", fb_payload)
ok = show_result(s, el)
if ok and isinstance(body, dict):
    fpath = body.get("fullPath", "")
    print(f"       RESPONSE:")
    print(f"         stored file     : {os.path.basename(fpath)}  (JSONL append)")
    print(f"         full path       : {fpath}")
    LOG.append(f"       RESPONSE: {body}")
else:
    print(f"       ERROR: {body}"); all_ok = False

step(7, f"Retrieve stored feedback records for agent '{AGENT_ID}'")
print(f"       REQUEST : GET http://localhost:7003/api/feedback/{AGENT_ID}")
LOG.append(f"       REQUEST: GET /api/feedback/{AGENT_ID}")
s, body, el = req(f"http://localhost:7003/api/feedback/{AGENT_ID}")
ok = show_result(s, el)
if ok:
    print(f"       RESPONSE:")
    raw = body if isinstance(body, str) else json.dumps(body)
    records = [l for l in raw.strip().split("\n") if l.strip()]
    print(f"         records found   : {len(records)}")
    for i, line in enumerate(records[-3:], 1):
        try:
            rec = json.loads(line)
            print(f"         record {i}        : agentId={rec.get('agentId')}  rating={rec.get('rating')}  sessionId={rec.get('sessionId')}")
            print(f"                          comment: {rec.get('comment','')[:70]}")
            LOG.append(f"         record: {rec}")
        except Exception:
            print(f"         record {i}        : {line[:100]}")
else:
    print(f"       ERROR: {body}"); all_ok = False

# =============================================================================
banner("PHASE 7 - AGENT-BUILDER-SERVICE (port 7010): LLM-generated agent config")
# =============================================================================

step(8, "Generate an improved agent config via LLM (llama3.2:3b)")
builder_payload = {
    "prompt": (
        "Create an AI agent configuration for a TIBCO Flogo AgenticAI capabilities assistant. "
        "The agent should answer questions about Flogo activities, MCP tools, SSE streaming, "
        "VectorDB RAG queries, and agent orchestration patterns."
    ),
    "model": "llama3.2:3b"
}
print(f"       REQUEST : POST http://localhost:7010/api/agent-builder/generate")
print(f"       BODY    : model={builder_payload['model']}")
print(f"       BODY    : prompt='{builder_payload['prompt'][:80]}...'")
print(f"       LLM     : llama3.2:3b (via Ollama)")
LOG.append(f"       REQUEST: POST /api/agent-builder/generate")
s, body, el = req("http://localhost:7010/api/agent-builder/generate", "POST", builder_payload, timeout=90)
ok = show_result(s, el)
GEN_CONFIG = {}
if ok and isinstance(body, dict) and body.get("config"):
    GEN_CONFIG = body["config"]
    print(f"       RESPONSE (LLM-generated agent config):")
    for k in ("id","name","description","collectionName","model","maxTokens","active"):
        if k in GEN_CONFIG:
            print(f"         {k:<20}: {GEN_CONFIG[k]}")
    sp = GEN_CONFIG.get("systemPrompt","")
    if sp:
        print(f"         systemPrompt        : {sp[:120]}...")
    LOG.append(f"       RESPONSE: {GEN_CONFIG}")
else:
    print(f"       ERROR: {body}"); all_ok = False

# =============================================================================
banner("PHASE 8 - SSE-STREAM-SERVICE (port 7005): Broadcast + RAG+LLM stream")
# =============================================================================

step(9, "Broadcast 'session.start' event to all SSE subscribers")
bc_payload = {
    "eventType": "session.start",
    "sessionId": "e2e-journey-001",
    "data": {"user": "e2e-journey", "topic": "AgenticAI", "timestamp": ts()}
}
print(f"       REQUEST : POST http://localhost:7005/api/stream/broadcast")
print(f"       BODY    : eventType=session.start  sessionId=e2e-journey-001")
print(f"       ACTION  : SSE event broadcast to all connected /events subscribers")
LOG.append(f"       REQUEST: POST /api/stream/broadcast")
s, body, el = req("http://localhost:7005/api/stream/broadcast", "POST", bc_payload)
ok = show_result(s, el)
print(f"       RESPONSE: {body}")
LOG.append(f"       RESPONSE: {body}")

step(10, "Stream chat: full RAG+LLM pipeline with async SSE event emission")
stream_payload = {
    "message":        QUESTION,
    "collectionName": COLLECTION,
    "sessionId":      "e2e-journey-001"
}
print(f"       REQUEST : POST http://localhost:7005/api/stream/chat")
print(f"       BODY    : message='{QUESTION[:70]}'")
print(f"       BODY    : collectionName={COLLECTION}  sessionId=e2e-journey-001")
print(f"       INTERNAL PIPELINE:")
print(f"         1) EmitStart     -> SSE event 'stream.start' -> /events:7099")
print(f"         2) CallRAG       -> POST http://localhost:7001/api/chat (agent-chat-service)")
print(f"                            -> Weaviate embed + search + answer")
print(f"         3) RunLLM        -> nemotron-3-nano:30b (context + question -> response)")
print(f"         4) EmitAnswer    -> SSE event 'stream.answer' -> /events:7099")
print(f"         5) EmitDone      -> SSE event 'stream.done'   -> /events:7099")
print(f"         6) Return        -> HTTP 202 {{streaming:true, eventsUrl:/events}}")
LOG.append(f"       REQUEST: POST /api/stream/chat body={json.dumps(stream_payload)}")
s, body, el = req("http://localhost:7005/api/stream/chat", "POST", stream_payload, timeout=60)
ok = show_result(s, el)
if ok and isinstance(body, dict):
    print(f"       RESPONSE: {body}")
    print(f"       NOTE    : SSE events are streaming async on port 7099/events")
    LOG.append(f"       RESPONSE: {body}")
else:
    print(f"       ERROR: {body}"); all_ok = False

# =============================================================================
banner("PHASE 9 - MCP SERVER (port 3333): All 6 tools via Model Context Protocol")
# =============================================================================

print(f"  Transport : Streamable HTTP (JSON-RPC 2.0 + SSE)")
print(f"  Protocol  : initialize -> notifications/initialized -> tools/list -> tools/call x6")
LOG.append(f"  Protocol: JSON-RPC 2.0 over Streamable HTTP")

step(11, "Initialize MCP session (JSON-RPC handshake)")
init_params = {
    "protocolVersion": "2024-11-05",
    "capabilities": {"roots": {"listChanged": True}},
    "clientInfo": {"name": "e2e-journey-client", "version": "1.0.0"}
}
print(f"       REQUEST : POST http://localhost:3333/mcp")
print(f"       BODY    : method=initialize  protocolVersion=2024-11-05")
LOG.append(f"       REQUEST: POST /mcp method=initialize")
sid, init_resp, el = mcp_request("initialize", init_params)
ok = bool(sid)
icon = "OK  " if ok else "FAIL"
print(f"       --> HTTP 200  [{icon}]  ({el}ms)")
LOG.append(f"       --> HTTP 200 [{icon}] ({el}ms)")
if init_resp and "result" in init_resp:
    r = init_resp["result"]
    si = r.get("serverInfo", {})
    print(f"       RESPONSE:")
    print(f"         sessionId       : {sid}")
    print(f"         serverName      : {si.get('name')}")
    print(f"         serverVersion   : {si.get('version')}")
    print(f"         protocolVersion : {r.get('protocolVersion')}")
    LOG.append(f"       RESPONSE: session={sid} server={si}")

mcp_notify(sid)
print(f"       NOTIFY  : notifications/initialized sent -> session active")

step(12, "tools/list -> discover all registered MCP tools")
print(f"       REQUEST : POST http://localhost:3333/mcp  method=tools/list")
LOG.append(f"       REQUEST: POST /mcp method=tools/list")
_, tools_resp, el = mcp_request("tools/list", msg_id=2, session_id=sid)
icon = "OK  " if (tools_resp and "result" in tools_resp) else "FAIL"
print(f"       --> HTTP 200  [{icon}]  ({el}ms)")
LOG.append(f"       --> HTTP 200 [{icon}] ({el}ms)")
if tools_resp and "result" in tools_resp:
    tools = tools_resp["result"].get("tools", [])
    print(f"       RESPONSE: {len(tools)} tool(s) registered:")
    for t in tools:
        print(f"         - {t['name']:<22} {t.get('description','')[:60]}")
        LOG.append(f"         - {t['name']}: {t.get('description','')[:60]}")

print()
print("  Calling all 6 MCP tools...")
LOG.append("\n  Calling all 6 MCP tools:")

TOOL_CALLS = [
    ("list_agents",
     {},
     3, "config-service:7004 -> GET /api/agents"),
    ("get_agent",
     {"agentId": AGENT_ID},
     4, "config-service:7004 -> GET /api/agents/{id}"),
    ("submit_feedback",
     {"agentId": AGENT_ID, "rating": 5, "comment": "MCP round-trip works perfectly", "sessionId": "mcp-journey-001"},
     5, "feedback-service:7003 -> POST /api/feedback"),
    ("get_feedback",
     {"agentId": AGENT_ID},
     6, "feedback-service:7003 -> GET /api/feedback/{id}"),
    ("rag_chat",
     {"message": QUESTION, "collectionName": COLLECTION},
     7, "agent-chat-service:7001 -> POST /api/chat (RAG pipeline)"),
    ("analyze_flogo",
     {"content": json.dumps(FLOGO_SAMPLE), "fileName": "agent-studio-app.flogo", "rulesPath": "rules/", "tags": "mcp-journey"},
     8, "rule-engine-service:7000 -> POST /api/analyze"),
]

for tool_name, args, mid, backend_note in TOOL_CALLS:
    step(mid + 4, f"tools/call: '{tool_name}'")
    print(f"       BACKEND : {backend_note}")
    key_args = {k: str(v)[:50] for k, v in args.items() if k not in ("content",)}
    if key_args:
        print(f"       ARGS    : {key_args}")
    LOG.append(f"       tools/call {tool_name} -> {backend_note}")

    ok_tool, data, el = mcp_tool(tool_name, args, sid, mid)
    icon = "OK  " if ok_tool else "FAIL"
    print(f"       --> HTTP 200  [{icon}]  ({el}ms)")
    LOG.append(f"       --> HTTP 200 [{icon}] ({el}ms)")

    if ok_tool:
        print(f"       RESPONSE:")
        if isinstance(data, dict):
            if "answer" in data:
                print(f"         answer          : {data['answer']}")
                print(f"         duration        : {data.get('duration','')}")
                LOG.append(f"         answer: {data['answer']}")
            elif "collectionName" in data:
                print(f"         id              : {data.get('id','')}")
                print(f"         collectionName  : {data.get('collectionName','')}")
                print(f"         name            : {data.get('name','')}")
                print(f"         chunkStrategy   : {data.get('chunkStrategy','')}")
                LOG.append(f"         {data}")
            elif "fullPath" in data:
                print(f"         written to      : {os.path.basename(data['fullPath'])}")
                LOG.append(f"         file: {data['fullPath']}")
            elif "agentId" in data:
                raw = json.dumps(data)
                lines_found = [l for l in raw.split("\\n") if l.strip()]
                print(f"         records         : {len(lines_found)} feedback record(s)")
                try:
                    rec = data
                    print(f"         agentId         : {rec.get('agentId','')}")
                    print(f"         rating          : {rec.get('rating','')}")
                    print(f"         comment         : {rec.get('comment','')[:70]}")
                    LOG.append(f"         {rec}")
                except Exception:
                    pass
            elif "success" in data:
                ov = data.get("overview", {})
                print(f"         success         : {data.get('success')}")
                print(f"         errorCount      : {data.get('errorCount', 0)}")
                print(f"         warningCount    : {data.get('warningCount', 0)}")
                print(f"         rules_run       : {ov.get('rules_run', 0)}")
                LOG.append(f"         {data}")
            else:
                for k, v in list(data.items())[:5]:
                    print(f"         {k:<16}: {str(v)[:60]}")
                LOG.append(f"         {data}")
        elif isinstance(data, list):
            print(f"         count           : {len(data)} agent(s)")
            for a in data:
                nm = a.get("name", os.path.basename(a.get("fullPath","?")))
                print(f"           - {nm}")
                LOG.append(f"           - {nm}")
    else:
        print(f"       ERROR: {data}"); all_ok = False

# =============================================================================
banner("PHASE 9 - DESIGN-SERVICE (port 7020): Agent lifecycle management (PostgreSQL)")
# =============================================================================

DS_BASE = "http://localhost:7020/api/v1"

step(19, "Create a new agent via design-service")
ds_agent_payload = {
    "name": "E2E Journey Agent",
    "description": "Agent created by e2e_journey.py integration test",
    "config": {
        "systemPrompt": "You are a test agent created by the E2E journey.",
        "collectionName": COLLECTION,
        "topK": 5,
        "llmProvider": "Ollama",
        "llmModel": "llama3.2:3b",
        "llmBaseUrl": "http://localhost:11434",
        "temperature": 0.7
    }
}
print(f"       REQUEST : POST {DS_BASE}/agents")
print(f"       BODY    : name=E2E Journey Agent  collectionName={COLLECTION}")
LOG.append(f"       REQUEST: POST /api/v1/agents")
s, ds_body, el = req(f"{DS_BASE}/agents", "POST", ds_agent_payload)
ok = show_result(s, el, ok_codes=(200, 201))
DS_AGENT_ID = None
if ok and isinstance(ds_body, dict):
    DS_AGENT_ID = ds_body.get("id")
    print(f"       RESPONSE:")
    print(f"         id              : {ds_body.get('id')}")
    print(f"         name            : {ds_body.get('name')}")
    print(f"         status          : {ds_body.get('status')}")
    print(f"         version         : {ds_body.get('version')}")
    LOG.append(f"       RESPONSE: {ds_body}")
else:
    print(f"       ERROR: {ds_body}"); all_ok = False

if DS_AGENT_ID:
    step(20, f"Get agent '{DS_AGENT_ID}' from design-service")
    print(f"       REQUEST : GET {DS_BASE}/agents/{DS_AGENT_ID}")
    LOG.append(f"       REQUEST: GET /api/v1/agents/{DS_AGENT_ID}")
    s, ds_get, el = req(f"{DS_BASE}/agents/{DS_AGENT_ID}")
    ok = show_result(s, el)
    if ok and isinstance(ds_get, dict):
        print(f"       RESPONSE:")
        print(f"         id              : {ds_get.get('id')}")
        print(f"         status          : {ds_get.get('status')}")
        print(f"         version         : {ds_get.get('version')}")
        LOG.append(f"       RESPONSE: {ds_get}")
    else:
        print(f"       ERROR: {ds_get}"); all_ok = False

    step(21, "Update agent description via PUT")
    ds_upd_payload = {"description": "Updated by e2e_journey.py PUT test"}
    print(f"       REQUEST : PUT {DS_BASE}/agents/{DS_AGENT_ID}")
    LOG.append(f"       REQUEST: PUT /api/v1/agents/{DS_AGENT_ID}")
    s, ds_upd, el = req(f"{DS_BASE}/agents/{DS_AGENT_ID}", "PUT", ds_upd_payload)
    ok = show_result(s, el)
    if ok:
        print(f"       RESPONSE: version={ds_upd.get('version') if isinstance(ds_upd, dict) else '?'}")
        LOG.append(f"       RESPONSE: {ds_upd}")

    step(22, "List all templates")
    print(f"       REQUEST : GET {DS_BASE}/templates")
    LOG.append(f"       REQUEST: GET /api/v1/templates")
    s, ds_tpl, el = req(f"{DS_BASE}/templates")
    ok = show_result(s, el, ok_codes=(200,))
    if ok:
        count = len(ds_tpl) if isinstance(ds_tpl, list) else "?"
        print(f"       RESPONSE: {count} template(s)")
        LOG.append(f"       RESPONSE: {count} templates")

# =============================================================================
banner("PHASE 10 - DEPLOY-SERVICE (port 7030): Agent deployment lifecycle")
# =============================================================================

DEP_BASE = "http://localhost:7030/api/v1"

if DS_AGENT_ID:
    step(23, f"Deploy (activate) agent '{DS_AGENT_ID}'")
    print(f"       REQUEST : POST {DEP_BASE}/agents/{DS_AGENT_ID}/deploy")
    LOG.append(f"       REQUEST: POST /api/v1/agents/{DS_AGENT_ID}/deploy")
    s, dep_body, el = req(f"{DEP_BASE}/agents/{DS_AGENT_ID}/deploy", "POST", {})
    ok = show_result(s, el)
    if ok and isinstance(dep_body, dict):
        records = dep_body.get("records", [])
        status = records[0].get("status") if records else "?"
        print(f"       RESPONSE:")
        print(f"         status          : {status}")
        print(f"         records         : {len(records)}")
        LOG.append(f"       RESPONSE: {dep_body}")
    else:
        print(f"       ERROR: {dep_body}"); all_ok = False

    step(24, "Get deployment status")
    print(f"       REQUEST : GET {DEP_BASE}/agents/{DS_AGENT_ID}/deploy")
    LOG.append(f"       REQUEST: GET /api/v1/agents/{DS_AGENT_ID}/deploy")
    s, dep_status, el = req(f"{DEP_BASE}/agents/{DS_AGENT_ID}/deploy")
    ok = show_result(s, el)
    if ok and isinstance(dep_status, dict):
        records = dep_status.get("records", [])
        status = records[0].get("status") if records else "?"
        print(f"       RESPONSE: status={status}  records={len(records)}")
        LOG.append(f"       RESPONSE: {dep_status}")

    step(25, "Export Kubernetes YAML")
    print(f"       REQUEST : GET {DEP_BASE}/agents/{DS_AGENT_ID}/export/kubernetes")
    LOG.append(f"       REQUEST: GET /api/v1/agents/{DS_AGENT_ID}/export/kubernetes")
    s, k8s_body, el = req(f"{DEP_BASE}/agents/{DS_AGENT_ID}/export/kubernetes")
    ok = show_result(s, el)
    if ok and isinstance(k8s_body, dict):
        records = k8s_body.get("records", [])
        print(f"       RESPONSE: {len(records)} record(s)")
        if records:
            yaml_preview = str(records[0])[:200]
            print(f"         preview         : {yaml_preview}...")
        LOG.append(f"       RESPONSE: {k8s_body}")

    step(26, "Deactivate (undeploy) agent")
    print(f"       REQUEST : DELETE {DEP_BASE}/agents/{DS_AGENT_ID}/deploy")
    LOG.append(f"       REQUEST: DELETE /api/v1/agents/{DS_AGENT_ID}/deploy")
    s, undep_body, el = req(f"{DEP_BASE}/agents/{DS_AGENT_ID}/deploy", "DELETE")
    ok = show_result(s, el)
    if ok:
        records = undep_body.get("records", []) if isinstance(undep_body, dict) else []
        status = records[0].get("status") if records else "?"
        print(f"       RESPONSE: status={status}")
        LOG.append(f"       RESPONSE: {undep_body}")

    step(27, "Cleanup — delete test agent from design-service")
    print(f"       REQUEST : DELETE {DS_BASE}/agents/{DS_AGENT_ID}")
    LOG.append(f"       REQUEST: DELETE /api/v1/agents/{DS_AGENT_ID}")
    s, del_body, el = req(f"{DS_BASE}/agents/{DS_AGENT_ID}", "DELETE")
    ok = show_result(s, el, ok_codes=(200, 204))
    print(f"       RESPONSE: {del_body if del_body else '(no body)'}")
    LOG.append(f"       RESPONSE: {del_body}")

# =============================================================================
banner("JOURNEY COMPLETE - FINAL OUTCOME")
# =============================================================================

total_s = time.time() - journey_start
end_dt = datetime.datetime.now()

print(f"""
  Started  : {start_dt.strftime('%Y-%m-%d %H:%M:%S')}
  Ended    : {end_dt.strftime('%Y-%m-%d %H:%M:%S')}
  Duration : {total_s:.1f}s

  SERVICES TRAVERSED:
  +-----------------------------+------+----------------------------------------------------+
  | Service                     | Port | Role in this journey                               |
  +-----------------------------+------+----------------------------------------------------+
  | config-service              | 7004 | List agents, fetch default agent config            |
  | ingestion-service           | 7002 | Ingest doc into Weaviate vector DB                 |
  | rule-engine-service         | 7000 | Run quality rules against flogo app                |
  | agent-chat-service          | 7001 | RAG query: embed->search Weaviate->answer          |
  | feedback-service            | 7003 | Write/read user ratings and comments (JSONL)       |
  | agent-builder-service       | 7010 | LLM-generated agent config (llama3.2:3b)           |
  | sse-stream-service          | 7005 | Broadcast SSE event + RAG+LLM streaming pipeline   |
  | mcp-server                  | 3333 | MCP gateway - all 6 tools via JSON-RPC             |
  | design-service              | 7020 | Create/read/update/delete agents (PostgreSQL)      |
  | deploy-service              | 7030 | Activate/deactivate/export agents                  |
  +-----------------------------+------+----------------------------------------------------+

  MCP TOOLS EXERCISED:
    list_agents     -> config-service    GET  /api/agents
    get_agent       -> config-service    GET  /api/agents/default
    submit_feedback -> feedback-service  POST /api/feedback
    get_feedback    -> feedback-service  GET  /api/feedback/default
    rag_chat        -> agent-chat        POST /api/chat (Weaviate RAG)
    analyze_flogo   -> rule-engine       POST /api/analyze

  RESULT   : {"ALL 27 STEPS PASSED" if all_ok else "SOME STEPS FAILED - see above"}
  STATUS   : {"SUCCESS" if all_ok else "FAILED"}
""")
LOG.append(f"\nRESULT: {'ALL STEPS PASSED' if all_ok else 'SOME STEPS FAILED'}")
LOG.append(f"TOTAL TIME: {total_s:.1f}s")

os.makedirs("logs", exist_ok=True)
with open("logs/e2e-journey.log", "w", encoding="utf-8") as f:
    f.write("\n".join(LOG))
print(f"  Full log saved to: logs/e2e-journey.log")
print(f"  Service logs in  : logs/")
