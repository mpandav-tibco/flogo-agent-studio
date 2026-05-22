"""
Flogo Agent Studio — quick smoke test (health checks only, no LLM).
Run after any flogobuild rebuild to verify no regressions.
Exit 0 = all expected services up. Exit 1 = at least one expected service down.

Architecture notes:
  - feedback-service (7003) is MERGED into platform-service (7020) — not a separate process.
  - agent-chat-service and ingestion-service are per-agent services started by
    the runtime-manager (7050) on dynamic ports (7201–7295). They are NOT static.
  - runtime-manager reports active agent runtimes via GET /api/agents.
"""
import sys, time, json, urllib.request, urllib.error

AUTH = "Basic ZmxvZ286Y2hhbmdlbWU="

# Platform services — always running after start-all.sh
SERVICES = [
    ("rule-engine-service",    7097, "/api/health",  True),   # 7000 taken by macOS AirPlay
    ("agent-builder-service",  7010, "/api/health",  True),
    ("platform-service",       7020, "/api/health",  True),   # design + feedback merged
    ("runtime-manager",        7050, "/api/health",  True),
    ("mcp-server",             7333, "/mcp",         True),
]

def check(port, path, timeout=2):
    try:
        if path == "/mcp":
            # MCP requires POST with JSON-RPC initialize
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "smoke-test", "version": "1.0"}}}).encode()
            req = urllib.request.Request(f"http://localhost:{port}{path}", data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode().split("data: ", 1)[-1].strip())
                return "result" in body
        req = urllib.request.Request(
            f"http://localhost:{port}{path}",
            headers={"Authorization": AUTH}
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False

def check_runtime_agents():
    """Report active per-agent runtimes via runtime-manager."""
    try:
        req = urllib.request.Request(
            "http://localhost:7050/api/agents",
            headers={"Authorization": AUTH}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            agents = json.loads(r.read().decode())
            if not agents:
                print(f"  [INFO]  No active agent runtimes (deploy an agent to start per-agent services)")
            else:
                for a in agents:
                    name = a.get("name", a.get("agent_id", "?"))[:24]
                    chat = a.get("chatApiUrl", "?")
                    ingest = a.get("ingestionUrl", "?")
                    print(f"  [RUN ]  agent: {name:<24}  chat={chat}  ingest={ingest}")
    except Exception:
        pass  # runtime-manager already reported in SERVICES above

def main():
    t0 = time.time()
    print("=" * 60)
    print("  FLOGO AGENT STUDIO — SMOKE TEST")
    print("=" * 60)

    failures = []
    for name, port, path, required in SERVICES:
        up = check(port, path)
        icon = "OK  " if up else ("FAIL" if required else "SKIP")
        marker = " (required)" if required and not up else (" (optional)" if not required and not up else "")
        print(f"  [{icon}]  {name:<28} :{port}{marker}")
        if required and not up:
            failures.append(name)

    check_runtime_agents()

    elapsed = int((time.time() - t0) * 1000)
    print("=" * 60)
    if failures:
        print(f"  RESULT : FAILED — {len(failures)} required service(s) down: {', '.join(failures)}")
        print(f"  Elapsed: {elapsed}ms")
        print("=" * 60)
        sys.exit(1)
    else:
        print(f"  RESULT : PASSED — all required services healthy")
        print(f"  Elapsed: {elapsed}ms")
        print("=" * 60)
        sys.exit(0)

if __name__ == "__main__":
    main()
