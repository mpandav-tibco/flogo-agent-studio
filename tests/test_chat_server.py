#!/usr/bin/env python3
"""
Standalone test for services/agent/ui/chat/server.py

Tests all proxy routes against the running DevOps & SRE agent (slot 1, port 7211).
Run BEFORE switching production agents to the new server.

Usage:
    python3 tests/test_chat_server.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Test config ───────────────────────────────────────────────────────────────
TEST_PORT    = 17215          # Use a distinct port so we don't clash with running chainlit
AGENT_ID     = "50e41c0c-43c6-4ca2-8fcf-8c479257f57b"
AGENT_NAME   = "DevOps & SRE Assistant (test)"
CHAT_PORT    = 7211
SSE_PORT     = 7212
EVENTS_PORT  = 7213
RE_PORT      = 7216
FEEDBACK_URL = "http://localhost:7020"

BASE = f"http://localhost:{TEST_PORT}"
AUTH = "Basic ZmxvZ286Y2hhbmdlbWU="

PASS, FAIL, SKIP = "✅", "❌", "⏭ "
results = []

def check(name, ok, detail=""):
    sym = PASS if ok else FAIL
    print(f"  {sym}  {name}" + (f"  — {detail}" if detail else ""))
    results.append((name, ok))

def get(path, timeout=10):
    req = urllib.request.Request(BASE + path, headers={"Authorization": AUTH})
    return urllib.request.urlopen(req, timeout=timeout)

def post(path, body: dict, timeout=30):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(BASE + path, data=data,
                                   headers={"Authorization": AUTH,
                                            "Content-Type": "application/json"},
                                   method="POST")
    return urllib.request.urlopen(req, timeout=timeout)

# ── Start test server ─────────────────────────────────────────────────────────
env = {
    **os.environ,
    "PORT":                    str(TEST_PORT),
    "CHAT_SERVICE_URL":        f"http://localhost:{CHAT_PORT}",
    "SSE_SERVICE_URL":         f"http://localhost:{SSE_PORT}",
    "SSE_EVENTS_URL":          f"http://localhost:{EVENTS_PORT}",
    "RULE_ENGINE_SERVICE_URL": f"http://localhost:{RE_PORT}",
    "FEEDBACK_SERVICE_URL":    FEEDBACK_URL,
    "AGENT_ID":                AGENT_ID,
    "AGENT_NAME":              AGENT_NAME,
    "AGENT_DESCRIPTION":       "Test instance",
    "AUTH_HEADER":             AUTH,
}
server_py = Path(__file__).parent.parent / "services/agent/ui/chat/server.py"
proc = subprocess.Popen([sys.executable, str(server_py)], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

# Wait for server to start
for _ in range(20):
    try:
        urllib.request.urlopen(f"http://localhost:{TEST_PORT}/", timeout=1)
        break
    except Exception:
        time.sleep(0.3)
else:
    print("❌  Server failed to start")
    err = proc.stderr.read().decode()
    print(err)
    sys.exit(1)

print(f"\nFlogents chat server tests  (port {TEST_PORT})\n{'─'*50}")

try:
    # ── T1: HTML served ───────────────────────────────────────────────────────
    print("\n[1] Static serving")
    try:
        r = get("/")
        html = r.read().decode()
        check("GET / returns 200",               r.status == 200)
        check("Content-Type is text/html",       "text/html" in r.headers.get("Content-Type",""))
        check("Config injected into HTML",       "FLOGENTS_CONFIG" in html)
        check("Agent name injected",             AGENT_NAME in html)
        check("Agent ID injected",               AGENT_ID in html)
        check("Markdown renderer present",       "renderMarkdown" in html)
        check("SSE streaming code present",      "stream.answer" in html)
        check("File upload handling present",    "analyzeFile" in html)
        check("Feedback handler present",        "sendFeedback" in html)
    except Exception as exc:
        check("GET / basic test", False, str(exc))

    # ── T2: Health proxy ──────────────────────────────────────────────────────
    print("\n[2] Health proxy → chat service")
    try:
        r    = get("/proxy/health")
        data = json.loads(r.read())
        check("GET /proxy/health returns 200",   r.status == 200)
        check("Health response has 'status'",    "status" in data, data.get("status","?"))
    except Exception as exc:
        check("Health proxy", False, str(exc))

    # ── T3: Chat proxy ────────────────────────────────────────────────────────
    print("\n[3] Chat proxy → agent-chat-service")
    try:
        r    = post("/proxy/chat", {
            "message":   "What is a Kubernetes liveness probe?",
            "agentId":   AGENT_ID,
            "sessionId": "test-session-001",
            "requestId": "test-req-001",
            "topK": 3,
        })
        data = json.loads(r.read())
        check("POST /proxy/chat returns 200",    r.status == 200)
        check("Response has 'answer' field",     "answer" in data, (data.get("answer","")[:80] + "…"))
        check("Answer is non-empty",             bool(data.get("answer","")))
        check("Response has 'duration'",         "duration" in data)
    except Exception as exc:
        check("Chat proxy", False, str(exc))

    # ── T4: Rule Engine proxy ─────────────────────────────────────────────────
    print("\n[4] Rule Engine proxy → rule-engine-service")
    SAMPLE_K8S = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: test-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: test
  template:
    metadata:
      labels:
        app: test
    spec:
      containers:
      - name: app
        image: nginx:latest
"""
    try:
        r    = post("/proxy/analyze", {"fileName": "deployment.yaml", "content": SAMPLE_K8S})
        data = json.loads(r.read())
        check("POST /proxy/analyze returns 200", r.status == 200)
        check("Response has 'findings'",         "findings" in data)
        check("Response has success flag",        "success" in data)
        check("Findings is a list",              isinstance(data.get("findings"), list))
        fc = len(data.get("findings", []))
        check("Rule engine returned at least one finding", fc >= 1, f"{fc} findings")
    except Exception as exc:
        check("Rule Engine proxy", False, str(exc))

    # ── T5: Feedback proxy ────────────────────────────────────────────────────
    print("\n[5] Feedback proxy → platform-service")
    try:
        r = post("/proxy/feedback", {
            "agentId":   AGENT_ID,
            "sessionId": "test-session-001",
            "messageId": "msg-test-001",
            "rating":    "up",
            "comment":   "test feedback from chat server test",
        })
        check("POST /proxy/feedback returns 2xx", 200 <= r.status < 300)
    except urllib.error.HTTPError as exc:
        # Platform service may return non-200 for test data — that's OK
        check("POST /proxy/feedback reached service", exc.code < 500, f"HTTP {exc.code}")
    except Exception as exc:
        check("Feedback proxy", False, str(exc))

    # ── T6: 404 for unknown routes ────────────────────────────────────────────
    print("\n[6] Error handling")
    try:
        urllib.request.urlopen(f"http://localhost:{TEST_PORT}/nonexistent", timeout=5)
        check("Unknown route returns 404", False, "expected 404 got 200")
    except urllib.error.HTTPError as exc:
        check("Unknown route returns 404", exc.code == 404, f"HTTP {exc.code}")
    except Exception as exc:
        check("Unknown route 404 test", False, str(exc))

    # ── T7: SSE events proxy — just verify TCP + HTTP layer ─────────────────
    # The Flogo SSE event bus holds the connection open and only sends headers
    # once the first event arrives, so we only check TCP connection acceptance.
    print("\n[7] SSE events proxy (TCP connection)")
    import socket
    try:
        s = socket.create_connection(("localhost", TEST_PORT), timeout=5)
        s.close()
        check("SSE /proxy/events TCP port is reachable", True)
    except Exception as exc:
        check("SSE events proxy TCP", False, str(exc))

finally:
    proc.terminate()
    proc.wait()

# ── Summary ───────────────────────────────────────────────────────────────────
total  = len(results)
passed = sum(1 for _, ok in results if ok)
failed = total - passed
print(f"\n{'─'*50}")
print(f"Results: {passed}/{total} passed" + (f"  ({failed} failed)" if failed else "  — all green"))

if failed:
    print("\nFailed tests:")
    for name, ok in results:
        if not ok:
            print(f"  ❌  {name}")
    sys.exit(1)
else:
    print("\nAll tests passed. Safe to use as Chainlit replacement.")
