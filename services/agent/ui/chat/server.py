#!/usr/bin/env python3
"""
Flogents lightweight chat server — zero-dependency replacement for Chainlit.

Serves services/agent/ui/chat/index.html and proxies /proxy/* routes to the
per-agent backend services. Reads the same environment variables as app.py so
deployment.py needs minimal changes to switch to this server.

Usage:
    python3 server.py

Environment variables:
    PORT                    Port to listen on          (default: 7215)
    CHAT_SERVICE_URL        Sync chat API              (default: http://localhost:7211)
    SSE_SERVICE_URL         SSE trigger REST API       (default: http://localhost:7212)
    SSE_EVENTS_URL          SSE event bus              (default: http://localhost:7213)
    RULE_ENGINE_SERVICE_URL Rule engine analyze API    (default: http://localhost:7216)
    FEEDBACK_SERVICE_URL    Feedback / platform svc    (default: http://localhost:7020)
    AGENT_ID                UUID injected into the UI  (default: "")
    AGENT_NAME              Display name               (default: Flogents Agent)
    AGENT_DESCRIPTION       One-line description       (default: "")
    AUTH_HEADER             Basic auth value           (default: Basic ZmxvZ286Y2hhbmdlbWU=)
"""

import http.server
import json
import os
import socketserver
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────
PORT             = int(os.getenv("PORT", "7215"))
CHAT_URL         = os.getenv("CHAT_SERVICE_URL",        "http://localhost:7211").rstrip("/")
SSE_URL          = os.getenv("SSE_SERVICE_URL",         "http://localhost:7212").rstrip("/")
EVENTS_URL       = os.getenv("SSE_EVENTS_URL",          "http://localhost:7213").rstrip("/")
RULE_ENGINE_URL  = os.getenv("RULE_ENGINE_SERVICE_URL", "http://localhost:7216").rstrip("/")
FEEDBACK_URL     = os.getenv("FEEDBACK_SERVICE_URL",    "http://localhost:7020").rstrip("/")
AGENT_ID         = os.getenv("AGENT_ID",                "")
AGENT_NAME       = os.getenv("AGENT_NAME",              "Flogents Agent")
AGENT_DESCRIPTION= os.getenv("AGENT_DESCRIPTION",       "")
AUTH_HEADER      = os.getenv("AUTH_HEADER",             "Basic ZmxvZ286Y2hhbmdlbWU=")

HTML_FILE = Path(__file__).parent / "index.html"

# Proxy route table: path → (base_url, upstream_path)
_PROXY_ROUTES = {
    "/proxy/chat":     (CHAT_URL,        "/api/chat"),
    "/proxy/stream":   (SSE_URL,         "/api/stream/chat"),
    "/proxy/analyze":  (RULE_ENGINE_URL, "/api/analyze"),
    "/proxy/feedback": (FEEDBACK_URL,    "/api/feedback"),
    "/proxy/health":   (CHAT_URL,        "/api/health"),
}

_AUTH = {"Authorization": AUTH_HEADER, "Content-Type": "application/json"}


# ── Request handler ───────────────────────────────────────────────────────────
class _Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):  # silence per-request log noise
        pass

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        if path in ("/", "/index.html"):
            self._serve_html()
        elif path == "/proxy/events":
            self._proxy_sse(parsed.query)
        elif path == "/proxy/health":
            self._proxy_json("GET", CHAT_URL + "/api/health", b"")
        else:
            self._respond(404, b'{"error":"not found"}')

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in _PROXY_ROUTES:
            self._respond(404, b'{"error":"not found"}')
            return
        base, upstream = _PROXY_ROUTES[path]
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""
        self._proxy_json("POST", base + upstream, body)

    # ── CORS preflight ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _respond(self, code, body, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self):
        """Serve index.html with the runtime config injected as a <script> block."""
        html = HTML_FILE.read_text(encoding="utf-8")
        config_js = (
            "<script>\n"
            "window.FLOGENTS_CONFIG = " +
            json.dumps({
                "agentId":          AGENT_ID,
                "agentName":        AGENT_NAME,
                "agentDescription": AGENT_DESCRIPTION,
            }) +
            ";\n</script>"
        )
        html = html.replace("</head>", config_js + "\n</head>", 1)
        body = html.encode("utf-8")
        self._respond(200, body, "text/html; charset=utf-8")

    def _proxy_json(self, method, url, body):
        """Forward a request to an upstream service and relay the response."""
        try:
            req = urllib.request.Request(url, data=body or None,
                                         headers=_AUTH, method=method)
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
                ct   = resp.headers.get("Content-Type", "application/json")
                self._respond(resp.status, data, ct)
        except urllib.error.HTTPError as exc:
            self._respond(exc.code, exc.read())
        except Exception as exc:
            self._respond(502, json.dumps({"error": str(exc)}).encode())

    def _proxy_sse(self, query_string):
        """Relay a server-sent event stream from the upstream event bus."""
        url = f"{EVENTS_URL}/events"
        if query_string:
            url += "?" + query_string
        try:
            req = urllib.request.Request(url, headers={"Authorization": AUTH_HEADER})
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self._cors_headers()
                self.end_headers()
                while True:
                    chunk = resp.read(256)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except Exception:
            pass  # client disconnected or upstream closed — normal SSE teardown


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), _Handler) as srv:
        print(f"[flogents-chat] http://localhost:{PORT}  "
              f"agent={AGENT_NAME!r}  id={AGENT_ID or '(none)'}")
        srv.serve_forever()
