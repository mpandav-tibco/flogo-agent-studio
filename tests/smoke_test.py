"""
Flogo Agent Studio — quick smoke test (health checks only, no LLM).
Run after any flogobuild rebuild to verify no regressions.
Exit 0 = all expected services up. Exit 1 = at least one expected service down.
"""
import sys, time, json, urllib.request, urllib.error

AUTH = "Basic ZmxvZ286Y2hhbmdlbWU="

SERVICES = [
    ("rule-engine-service",    7097, "/api/health",  True),   # 7000 taken by macOS AirPlay
    ("agent-chat-service",     7001, "/api/health",  True),
    ("ingestion-service",      7002, "/api/health",  True),
    ("feedback-service",       7003, "/api/health",  True),
    ("sse-stream-service",     7005, "/api/health",  True),
    ("agent-builder-service",  7010, "/api/health",  True),
    ("design-service",         7020, "/api/health",  True),
    ("deploy-service",         7030, "/api/health",  True),
    ("mcp-server",             3333, "/mcp",         True),
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
