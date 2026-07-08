#!/usr/bin/env python3
"""
Flogents Runtime Manager  (port 7050)
========================================
Manages per-agent process groups.  Each *deployed* (status=active) agent gets
its own isolated set of three processes:

  ┌─────────────────────────────────────────────────────────────────────┐
  │  agent-chat-service  :base+1  — RAG chat + SSE streaming (merged)  │
  │                      :base+2  — SSE REST gateway (same process)    │
  │                      :base+3  — SSE event bus   (same process)     │
  │  ingestion-service   :base+4  — doc ingestion for this agent only  │
  │  chainlit-ui         :base+5  — dedicated chat UI (AGENT_ID set)   │
  └─────────────────────────────────────────────────────────────────────┘

Port pool  (10 slots, 10 ports each):
  Slot N → base = 7200 + N*10
  Slot 0: 7201–7205   Slot 9: 7291–7295

Reconciliation loop (RECONCILE_INTERVAL, default 15 s):
  • Polls design-service for active agents.
  • Starts runtimes for newly-active agents not yet managed.
  • Stops  runtimes for deactivated agents still running.
  • Health-checks each running process and restarts crashed ones.

REST API  (all JSON):
  GET    /api/health
  GET    /api/agents                           — list managed agents + their URLs
  GET    /api/agents/{agentId}                 — single agent runtime status
  POST   /api/agents/{agentId}/start           — force-start (bypass reconciler)
  DELETE /api/agents/{agentId}/stop            — force-stop
  POST   /api/agents/{agentId}/docker-deploy   — generate compose + docker compose up -d
  GET    /api/agents/{agentId}/docker-deploy   — docker compose ps (container status)
  DELETE /api/agents/{agentId}/docker-deploy   — docker compose down

State persistence: data/agent-runtime.json
  Survives restarts — existing processes are re-adopted if their PID is
  still alive; dead ones are restarted on the next reconciliation pass.

Usage:
  python3 deployment.py
"""

import asyncio
import base64
import datetime
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import asyncpg
import httpx
from aiohttp import web

# ── Configuration ──────────────────────────────────────────────────────────────

PORT               = int(os.getenv("RUNTIME_MANAGER_PORT", "7050"))
DESIGN_URL         = os.getenv("DESIGN_SERVICE_URL",   "http://localhost:7020")
FEEDBACK_URL       = os.getenv("FEEDBACK_SERVICE_URL", "http://localhost:7020")  # merged into platform-service
RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL", "15"))

_AUTH_HEADER = os.getenv("SERVICE_AUTH_HEADER", "Basic ZmxvZ286Y2hhbmdlbWU=")

# Resolved once at startup from the location of this file
_THIS_FILE   = Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent.parent          # flogo-agent-studio/ (script is in deployment/)

SERVICES_BIN          = PROJECT_ROOT / "bin"
SERVICES_PLATFORM_APPS = PROJECT_ROOT / "services" / "platform" / "flogo"
SERVICES_AGENT_APPS    = PROJECT_ROOT / "services" / "agent"   / "flogo"
SERVICES_PLATFORM_ENV  = PROJECT_ROOT / "services" / "platform" / "env"
SERVICES_AGENT_ENV     = PROJECT_ROOT / "services" / "agent"   / "env"
DATA_DIR         = PROJECT_ROOT / "data"
RUNTIME_DIR      = DATA_DIR / "agent-runtimes"   # per-agent generated files
LOGS_AGENT_DIR   = PROJECT_ROOT / "logs" / "agents"
STATE_FILE       = DATA_DIR / "agent-runtime.json"
DOCKER_DEPLOY_DIR = DATA_DIR / "docker-deployments"   # per-agent compose files
LAUNCH_PY        = PROJECT_ROOT / "services" / "launch.py"
CHAINLIT_DIR     = PROJECT_ROOT / "services" / "agent" / "ui" / "chainlit"   # kept for docker-build compatibility
CHAT_SERVER_DIR  = PROJECT_ROOT / "services" / "agent" / "ui" / "chat"   # kept for docker-build compatibility
CHAT_SERVER_DIR  = PROJECT_ROOT / "services" / "agent" / "ui" / "chat"

# ── Port pool ──────────────────────────────────────────────────────────────────

_PORT_BASE  = 7200
_MAX_SLOTS  = 10
_PORTS_PER_SLOT = 10    # reserve 10 ports per slot for future growth

_PORT_OFFSETS = {
    "chat":        1,
    "sse_rest":    2,
    "sse_events":  3,
    "ingestion":   4,
    "chainlit":    5,
    "rule_engine": 6,
    "mcp":         7,   # per-agent MCPServer trigger (Phase 2.4)
}

# ── PostgreSQL connection pool (conversation history + KB queries) ─────────────

_DB_DSN     = os.getenv("DB_DSN",
    "postgresql://flogo:changeme@localhost:5432/flogo_agent_studio")
_WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://localhost:18080")
_db_pool: asyncpg.Pool | None = None

async def _get_db() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is None:
        try:
            _db_pool = await asyncpg.create_pool(_DB_DSN, min_size=1, max_size=5)
        except Exception as exc:
            log.warning("DB pool not available: %s", exc)
            raise
    return _db_pool


def slot_ports(slot: int) -> dict[str, int]:
    base = _PORT_BASE + slot * _PORTS_PER_SLOT
    return {name: base + offset for name, offset in _PORT_OFFSETS.items()}


# ── Flogo binary build helpers ─────────────────────────────────────────────────

_BUILD_RETRY_INTERVAL = 300   # seconds between retries after a failed build
_build_failures: dict[str, float] = {}  # svc_name → epoch of last failed attempt


def _detect_flogobuild() -> Optional[str]:
    """Find flogobuild tool: looks in tools/flogobuild/<os>_<arch>/ then PATH."""
    import platform as _platform
    os_name = _platform.system().lower()          # darwin / linux
    arch    = _platform.machine().lower()          # arm64 / x86_64 / amd64
    if arch == "x86_64":
        arch = "amd64"
    local = PROJECT_ROOT / "tools" / "flogobuild" / f"{os_name}_{arch}" / "flogobuild"
    if local.is_file() and os.access(str(local), os.X_OK):
        return str(local)
    found = shutil.which("flogobuild")
    return found  # None if not found anywhere


def _go_wrapped_env() -> dict:
    """Return the current environment with tools/go-wrapper/ prepended to PATH.

    The wrapper script at tools/go-wrapper/go intercepts 'go mod tidy' and appends
    the -e flag so that test-only transitive imports that cannot be resolved (e.g.
    github.com/tibco/wi-contrib/function/float) do not abort the build.
    """
    wrapper_dir = str(PROJECT_ROOT / "tools" / "go-wrapper")
    env = dict(os.environ)
    env["PATH"] = wrapper_dir + os.pathsep + env.get("PATH", "")
    return env


async def _ensure_binary(flogo_src: Path, svc_name: str) -> bool:
    """Rebuild the named service binary when flogo source is newer than the binary.

    Returns True when the binary is ready to use (either up-to-date or freshly built).
    Returns False only when the binary is missing AND flogobuild is unavailable.

    A failed build is not retried for _BUILD_RETRY_INTERVAL seconds so that the
    reconcile loop does not hammer flogobuild on every cycle.
    """
    binary = PROJECT_ROOT / "bin" / svc_name
    # Fast path: binary exists and is newer than (or same age as) the source
    if binary.exists() and flogo_src.stat().st_mtime <= binary.stat().st_mtime:
        return True

    # Cooldown: if a recent build attempt already failed, wait before retrying
    last_failure = _build_failures.get(svc_name, 0.0)
    if binary.exists() and time.time() - last_failure < _BUILD_RETRY_INTERVAL:
        log.debug(
            "Skipping rebuild of %s (last attempt failed %ds ago; retry in %ds)",
            svc_name,
            int(time.time() - last_failure),
            int(_BUILD_RETRY_INTERVAL - (time.time() - last_failure)),
        )
        return True

    fb = _detect_flogobuild()
    if not fb:
        if binary.exists():
            log.warning(
                "flogobuild not found — using existing binary for %s "
                "(source is newer; rebuild manually to pick up import changes)",
                svc_name,
            )
            return True
        log.error("flogobuild not found and binary %s is missing — cannot start", svc_name)
        return False

    reason = "binary missing" if not binary.exists() else "source newer than binary"
    log.info("Building %s (%s)…", svc_name, reason)
    proc = await asyncio.create_subprocess_exec(
        fb, "build-exe",
        "-f", str(flogo_src),
        "-c", "flogo-studio-2264",
        "-n", svc_name,
        "-o", str(PROJECT_ROOT / "bin"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_go_wrapped_env(),
    )
    out, _ = await proc.communicate()
    if proc.returncode == 0:
        _build_failures.pop(svc_name, None)  # clear failure record on success
        # macOS: ad-hoc re-sign the freshly built binary. Rebuilding in place can
        # leave a stale kernel code-signing cache entry that SIGKILLs the process
        # at launch ("Taskgated Invalid Signature") with zero log output, even
        # though `codesign --verify` passes on disk. A forced ad-hoc sign busts it.
        if sys.platform == "darwin":
            try:
                sign = await asyncio.create_subprocess_exec(
                    "codesign", "--force", "--sign", "-", str(binary),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await sign.wait()
            except Exception as exc:  # noqa: BLE001 — best-effort; non-fatal
                log.warning("codesign re-sign failed for %s: %s", svc_name, exc)
        log.info("Binary %s built successfully", svc_name)
        return True
    _build_failures[svc_name] = time.time()
    log.error("flogobuild failed for %s (exit %d):\n%s",
              svc_name, proc.returncode, out.decode(errors="replace"))
    return binary.exists()   # fall back to stale binary if it still exists


# ── In-memory state ────────────────────────────────────────────────────────────
# Keyed by agentId:
# {
#   "slot": int,
#   "ports": {"chat": int, "sse_rest": int, "sse_events": int, ...},
#   "pids":  {"chat": int|None, "sse_rest": int|None, ...},
#   "chatUiUrl": str,
#   "chatApiUrl": str,
#   "sseUrl": str,
#   "ingestionUrl": str,
#   "startedAt": float,
#   "agentName": str,
#   "readiness": "starting" | "ready" | "degraded",
# }

_state: dict[str, dict] = {}
_state_lock = asyncio.Lock()

log = logging.getLogger("runtime-manager")


# ── State persistence ─────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception as exc:
            log.warning("Could not load state file: %s", exc)
    return {}


async def _save_state():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with _state_lock:
        snapshot = {k: {kk: vv for kk, vv in v.items()} for k, v in _state.items()}
    try:
        STATE_FILE.write_text(json.dumps(snapshot, indent=2))
    except Exception as exc:
        log.warning("Could not save state: %s", exc)


# ── Process helpers ───────────────────────────────────────────────────────────

def _is_pid_running(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        log.debug("Process %s gone or no permission", pid)
        return False


def _kill_pid(pid: Optional[int], timeout: int = 5):
    if not pid or not _is_pid_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for graceful shutdown
        for _ in range(timeout * 10):
            if not _is_pid_running(pid):
                return
            time.sleep(0.1)
        os.kill(pid, signal.SIGKILL)
    except Exception as exc:
        log.debug("Kill pid %s: %s", pid, exc)


def _kill_port(port: int, timeout: int = 3):
    """Kill any process listening on the given TCP port (handles orphans across restarts)."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().split():
            try:
                _kill_pid(int(pid_str), timeout=timeout)
            except Exception:
                pass
    except Exception as exc:
        log.debug("_kill_port %s: %s", port, exc)


def _chainlit_cmd() -> Optional[list[str]]:
    """Retained for docker-build path; native launch now uses server.py."""
    if shutil.which("chainlit"):
        return ["chainlit", "run", "app.py", "--headless"]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "chainlit", "--version"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return [sys.executable, "-m", "chainlit", "run", "app.py", "--headless"]
    except Exception:
        pass
    return None


# ── Flogo file + env generation ───────────────────────────────────────────────

def _modify_flogo_port(src: Path, dst: Path, port_map: dict[str, int]):
    """
    Clone a .flogo JSON, replacing trigger ports according to port_map.
    port_map is matched by trigger ref suffix:
      {"#rest": 7201, "#trigger": 7203, "#rest_1": 7202}  etc.
    Fallback: if only one entry in port_map and trigger ref not matched,
    use the first value.
    """
    with open(src) as f:
        app = json.load(f)

    for trigger in app.get("triggers", []):
        ref = trigger.get("ref", "")
        trigger_id = trigger.get("id", "")
        if "settings" not in trigger or "port" not in trigger["settings"]:
            continue
        # Try to match by trigger ref suffix — sort longest key first to avoid
        # "#rest" matching before "#rest_1" (substring collision)
        matched_port = None
        for key, p in sorted(port_map.items(), key=lambda kv: -len(kv[0])):
            if key.lower() in ref.lower() or key.lower() in trigger_id.lower():
                matched_port = p
                break
        if matched_port is None and len(port_map) == 1:
            matched_port = next(iter(port_map.values()))
        if matched_port is not None:
            trigger["settings"]["port"] = matched_port

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w") as f:
        json.dump(app, f, indent=2)


STANDALONE_INGESTION_PORT = int(os.getenv("STANDALONE_INGESTION_PORT", "7002"))


def _read_env_dict(path: Path) -> dict[str, str]:
    """Parse a .env file into a {KEY: value} dict (skips comments and blanks)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _generate_env_file(base_env: Path, dst: Path, overrides: dict[str, str]):
    """
    Copy base_env, replacing lines whose key appears in overrides,
    then append any remaining override keys at the end.
    """
    lines: list[str] = []
    written_keys: set[str] = set()

    if base_env.exists():
        with open(base_env) as fh:
            for raw in fh:
                line = raw.rstrip("\n")
                if not line or line.lstrip().startswith("#"):
                    lines.append(line)
                    continue
                key = line.split("=", 1)[0]
                if key in overrides:
                    lines.append(f"{key}={overrides[key]}")
                    written_keys.add(key)
                else:
                    lines.append(line)

    for k, v in overrides.items():
        if k not in written_keys:
            lines.append(f"{k}={v}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n")


def _env_bool(value, default: bool = True) -> str:
    """Coerce an agent-config value to a Flogo-friendly 'true'/'false' env string."""
    if isinstance(value, bool):
        b = value
    elif isinstance(value, str):
        b = value.strip().lower() in ("true", "1", "yes", "on")
    elif value is None:
        b = default
    else:
        b = bool(value)
    return "true" if b else "false"


def _env_int(value, default: int = 0) -> str:
    """Coerce an agent-config value to an integer env string, falling back to default."""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(default)


# ── Design-service integration ────────────────────────────────────────────────

async def _fetch_all_agents() -> Optional[list[dict]]:
    """Return the agent registry from design-service.

    Returns a list (possibly empty) when the registry is reachable, or None when
    the registry is unreachable. Callers MUST treat None ("can't read desired
    state") differently from [] ("registry is genuinely empty") so a transient
    registry outage never causes running agents to be torn down.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{DESIGN_URL}/api/v1/agents",
                headers={"Authorization": _AUTH_HEADER},
            )
            resp.raise_for_status()
            body = resp.json()
            records = body if isinstance(body, list) else body.get("records", [])
            result = []
            for a in records:
                cfg = a.get("config", {})
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except Exception:
                        log.debug("Invalid JSON config for agent record, using {}")
                        cfg = {}
                a["_cfg"] = cfg
                result.append(a)
            return result
    except Exception as exc:
        log.warning("fetch_all_agents failed (registry unreachable): %s", exc)
        return None


async def _patch_agent_urls(agent_id: str, record: dict):
    """Best-effort: merge chatUiUrl / chatApiUrl into agent config via GET-then-PUT.

    design-service only exposes PUT (not PATCH) and stores everything in a JSONB
    config column, so we must read the current record first to avoid overwriting
    existing config fields.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1 — read current agent record
            r = await client.get(
                f"{DESIGN_URL}/api/v1/agents/{agent_id}",
                headers={"Authorization": _AUTH_HEADER},
            )
            r.raise_for_status()
            body = r.json()
            # design-service wraps single records in {"records": [...]}
            agent = body if isinstance(body, dict) and "id" in body else \
                    (body.get("records") or [{}])[0]

            cfg = agent.get("config", {})
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    log.debug("Invalid JSON config for agent [%s], using {}", agent_id[:8])
                    cfg = {}

            # Step 2 — merge runtime URL fields into config
            cfg.update({
                "chatUiUrl":    record.get("chatUiUrl", ""),
                "chatApiUrl":   record.get("chatApiUrl", ""),
                "sseUrl":       record.get("sseUrl", ""),
                "ingestionUrl": record.get("ingestionUrl", ""),
            })

            # Step 3 — PUT back (only config; name/description/status COALESCE to existing)
            await client.put(
                f"{DESIGN_URL}/api/v1/agents/{agent_id}",
                json={"config": cfg},
                headers={"Authorization": _AUTH_HEADER, "Content-Type": "application/json"},
            )
    except Exception as exc:
        log.warning("patch_agent_urls %s: %s", agent_id[:8], exc)


# ── Config-change detection ───────────────────────────────────────────────────

# Fields baked into env files at startup — changing them requires a process restart.
_CONFIG_HASH_KEYS = (
    "llmModel", "llmProvider", "llmBaseUrl",
    "embeddingModel", "embeddingProvider", "embeddingBaseUrl",
    "systemPrompt", "collectionName", "chunkStrategy",
    "enableGuardrails", "redactSensitiveData",
    "rateLimit", "tokenLimit", "reasoningEffort",
    "specialistAgentUrl",
)

def _config_hash(cfg: dict) -> str:
    """Stable MD5 fingerprint of config fields that affect running processes."""
    snapshot = {k: cfg.get(k, "") for k in _CONFIG_HASH_KEYS}
    return hashlib.md5(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


# ── Agent runtime lifecycle ───────────────────────────────────────────────────

async def _start_runtime(agent: dict) -> dict:
    agent_id   = agent["id"]
    agent_name = agent.get("name", agent_id)
    cfg        = agent.get("_cfg", {})

    collection_name = cfg.get("collectionName") or f"Agent_{agent_id.replace('-', '')[:16]}"
    system_prompt   = cfg.get("systemPrompt", "You are a helpful assistant.")
    llm_model       = cfg.get("llmModel", "llama3.2:3b")
    llm_provider    = cfg.get("llmProvider", "Ollama")
    llm_base_url    = cfg.get("llmBaseUrl", "http://localhost:11434/v1")
    embedding_model    = cfg.get("embeddingModel", "nomic-embed-text")
    embedding_provider = cfg.get("embeddingProvider", "Ollama")
    embedding_base_url = cfg.get("embeddingBaseUrl", "") or "http://localhost:11434/v1"
    chunk_strategy     = cfg.get("chunkStrategy", "sentence")
    # Per-agent governance (2.26.4 guardrails). Defaults match the app-property defaults
    # (guardrails + PII redaction ON; rate/token limits OFF). Overridable via agent config.
    enable_guardrails     = _env_bool(cfg.get("enableGuardrails"), True)
    redact_sensitive_data = _env_bool(cfg.get("redactSensitiveData"), True)
    rate_limit            = _env_int(cfg.get("rateLimit"), 0)
    token_limit           = _env_int(cfg.get("tokenLimit"), 0)
    # Per-agent LLM reasoning effort (2.26.4). Empty = no reasoning param sent.
    reasoning_effort      = str(cfg.get("reasoningEffort", "") or "")
    # Optional agent-to-agent handoff target. When set, the agent's ask_specialist
    # tool delegates questions to this remote agent's REST chat endpoint; empty = off.
    specialist_agent_url  = str(cfg.get("specialistAgentUrl", "") or "")

    # Allocate a free slot
    async with _state_lock:
        used_slots = {v["slot"] for v in _state.values() if "slot" in v}
    slot = next((s for s in range(_MAX_SLOTS) if s not in used_slots), None)
    if slot is None:
        raise RuntimeError(f"No free agent runtime slots (max {_MAX_SLOTS} concurrent agents)")

    ports = slot_ports(slot)
    rt_dir    = RUNTIME_DIR / agent_id
    rt_dir.mkdir(parents=True, exist_ok=True)
    LOGS_AGENT_DIR.mkdir(parents=True, exist_ok=True)

    # Evict any orphaned processes still occupying these ports from a previous run
    for port in ports.values():
        _kill_port(port)

    pids: dict[str, Optional[int]] = {}
    short = agent_id[:8]

    base_env = {
        **os.environ,
        "FLOGO_APP_PROPS_ENV": "auto",
        "FLOGO_LOG_FORMAT": "JSON",
        "FLOGO_LOG_CTX": "TRUE",
        "FLOGO_ENV": "dev",
        "FLOGO_LOG_CTX_FIELDS": "service.namespace=flogo-agent-studio,service.environment=dev",
    }

    def _open_log(name: str):
        path = LOGS_AGENT_DIR / f"{short}-{name}.log"
        fh = open(path, "w")
        fh.write(f"=== STARTED {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} [{short}] {name} ===\n")
        fh.flush()
        return fh

    # ── agent-chat-service (includes SSE streaming — merged) ──────────────────
    chat_src    = SERVICES_AGENT_APPS / "agent-chat-service.flogo"
    chat_flogo  = rt_dir / "agent-chat-service.flogo"
    chat_env    = rt_dir / "agent-chat-service.env"

    if not chat_src.exists():
        raise FileNotFoundError(f"Missing source: {chat_src}")
    if not await _ensure_binary(chat_src, "agent-chat-service"):
        raise RuntimeError("agent-chat-service binary unavailable and could not be built")

    # One flogo file now carries three triggers: AgentChatRESTTrigger (chat),
    # SSEStreamRESTTrigger (SSE REST), SSEEventBus (SSE events).
    # Both REST triggers share ref #rest_1, so we match by trigger id.
    _modify_flogo_port(chat_src, chat_flogo, {
        "AgentChatRESTTrigger": ports["chat"],
        "SSEStreamRESTTrigger": ports["sse_rest"],
        "SSEEventBus":          ports["sse_events"],
        # AgentMCPTrigger removed (Phase 2.4 deferred — module conflict with flogo-agentic-ai)
    })
    _generate_env_file(
        SERVICES_AGENT_ENV / "agent-chat-service.env",
        chat_env,
        {
            "COLLECTION_NAME":   collection_name,
            "SYSTEM_PROMPT":     system_prompt,
            "LLM_MODEL":         llm_model,
            "LLM_PROVIDER":      llm_provider,
            "LLM_BASE_URL":      llm_base_url,
            "LLM_API_KEY":       os.getenv("LLM_API_KEY", ""),
            "ENABLE_GUARDRAILS":     enable_guardrails,
            "REDACT_SENSITIVE_DATA": redact_sensitive_data,
            "RATE_LIMIT":            rate_limit,
            "TOKEN_LIMIT":           token_limit,
            "REASONING_EFFORT":      reasoning_effort,
            "AGENT_ID":              agent_id,
            "SPECIALIST_AGENT_URL":  specialist_agent_url,
            # CHAT_SERVICE_URL is used by the SSE stream_chat flow (loopback within same process)
            "CHAT_SERVICE_URL":  f"http://localhost:{ports['chat']}/api/chat",
            "HISTORY_SERVICE_URL": f"http://localhost:{PORT}/api/sessions",
        },
    )
    chat_proc = subprocess.Popen(
        [sys.executable, str(LAUNCH_PY), str(chat_env),
         str(SERVICES_BIN / "agent-chat-service"), "-app", str(chat_flogo)],
        stdin=subprocess.DEVNULL, stdout=_open_log("chat"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"agent-chat-{short}"},
        start_new_session=True,
    )
    pids["chat"]       = chat_proc.pid
    pids["sse_rest"]   = chat_proc.pid   # same process — SSE REST trigger on port sse_rest
    pids["sse_events"] = chat_proc.pid   # same process — SSE events trigger on port sse_events
    # pids["mcp"] reserved for Phase 2.4 (per-agent MCPServer trigger, deferred)
    log.info("  [%s] agent-chat+sse  pid=%-7s ports=%s/%s/%s",
             short, chat_proc.pid, ports["chat"], ports["sse_rest"], ports["sse_events"])

    # ── ingestion-service ──────────────────────────────────────────────────────
    ing_src   = SERVICES_AGENT_APPS / "ingestion-service.flogo"
    ing_flogo = rt_dir / "ingestion-service.flogo"
    ing_env   = rt_dir / "ingestion-service.env"

    if not ing_src.exists():
        raise FileNotFoundError(f"Missing source: {ing_src}")
    if not await _ensure_binary(ing_src, "ingestion-service"):
        raise RuntimeError("ingestion-service binary unavailable and could not be built")

    _modify_flogo_port(ing_src, ing_flogo, {"#rest": ports["ingestion"]})
    _generate_env_file(
        SERVICES_AGENT_ENV / "ingestion-service.env",
        ing_env,
        {
            "COLLECTION_NAME":    collection_name,
            "EMBEDDING_MODEL":    embedding_model,
            "EMBEDDING_PROVIDER": embedding_provider,
            "EMBEDDING_BASE_URL": embedding_base_url,
            "CHUNK_STRATEGY":     chunk_strategy,
        },
    )
    ing_proc = subprocess.Popen(
        [sys.executable, str(LAUNCH_PY), str(ing_env),
         str(SERVICES_BIN / "ingestion-service"), "-app", str(ing_flogo)],
        stdin=subprocess.DEVNULL, stdout=_open_log("ingestion"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"ingestion-{short}"},
        start_new_session=True,
    )
    pids["ingestion"] = ing_proc.pid
    log.info("  [%s] ingestion   pid=%-7s port=%s", short, ing_proc.pid, ports["ingestion"])

    # ── rule-engine-service (per-agent) ───────────────────────────────────────
    re_src   = SERVICES_AGENT_APPS / "rule-engine-service.flogo"
    re_flogo = rt_dir / "rule-engine-service.flogo"
    re_env   = rt_dir / "rule-engine-service.env"

    if not re_src.exists():
        raise FileNotFoundError(f"Missing source: {re_src}")
    if not await _ensure_binary(re_src, "rule-engine-service"):
        raise RuntimeError("rule-engine-service binary unavailable and could not be built")

    rules_path = str(PROJECT_ROOT / "config" / "rules")
    _modify_flogo_port(re_src, re_flogo, {"RuleEngineRESTTrigger": ports["rule_engine"]})
    _generate_env_file(
        SERVICES_AGENT_ENV / "rule-engine-service.env",
        re_env,
        {"RULES_PATH": rules_path},
    )
    re_proc = subprocess.Popen(
        [sys.executable, str(LAUNCH_PY), str(re_env),
         str(SERVICES_BIN / "rule-engine-service"), "-app", str(re_flogo)],
        stdin=subprocess.DEVNULL, stdout=_open_log("rule-engine"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"rule-engine-{short}"},
        start_new_session=True,
    )
    pids["rule_engine"] = re_proc.pid
    log.info("  [%s] rule-engine  pid=%-7s port=%s", short, re_proc.pid, ports["rule_engine"])

    # ── Chat UI (lightweight server.py — replaces Chainlit) ────────────────────
    chat_server_py = CHAT_SERVER_DIR / "server.py"
    if chat_server_py.exists():
        agent_desc = cfg.get("description", cfg.get("desc", ""))
        chat_srv_env = {
            **os.environ,
            "PORT":                    str(ports["chainlit"]),
            "AGENT_ID":                agent_id,
            "AGENT_NAME":              agent_name,
            "AGENT_DESCRIPTION":       agent_desc,
            "CHAT_SERVICE_URL":        f"http://localhost:{ports['chat']}",
            "SSE_SERVICE_URL":         f"http://localhost:{ports['sse_rest']}",
            "SSE_EVENTS_URL":          f"http://localhost:{ports['sse_events']}",
            "RULE_ENGINE_SERVICE_URL": f"http://localhost:{ports['rule_engine']}",
            "FEEDBACK_SERVICE_URL":    FEEDBACK_URL,
        }
        chat_srv_proc = subprocess.Popen(
            [sys.executable, str(chat_server_py)],
            stdin=subprocess.DEVNULL, stdout=_open_log("chainlit"), stderr=subprocess.STDOUT,
            env=chat_srv_env,
            start_new_session=True,
        )
        pids["chainlit"] = chat_srv_proc.pid
        log.info("  [%s] chat-server  pid=%-7s port=%s", short, chat_srv_proc.pid, ports["chainlit"])
    else:
        pids["chainlit"] = None
        log.warning("  [%s] chat-server skipped (services/agent/ui/chat/server.py not found)", short)

    chat_ui_url = f"http://localhost:{ports['chainlit']}" if pids.get("chainlit") else ""

    record: dict = {
        "agentId":      agent_id,
        "agentName":    agent_name,
        "slot":         slot,
        "ports":        ports,
        "pids":         pids,
        "chatUiUrl":    chat_ui_url,
        "chatApiUrl":   f"http://localhost:{ports['chat']}",
        "sseUrl":       f"http://localhost:{ports['sse_rest']}",
        "ingestionUrl": f"http://localhost:{ports['ingestion']}",
        "ruleEngineUrl": f"http://localhost:{ports['rule_engine']}",
        "historyUrl":   f"http://localhost:{PORT}/api/sessions",
        "startedAt":    time.time(),
        "readiness":    "starting",
        "configHash":   _config_hash(cfg),
    }

    async with _state_lock:
        _state[agent_id] = record

    await _save_state()
    # _patch_agent_urls is deferred — called by _wait_until_ready once all
    # services are confirmed accepting connections (avoids 404s in the UI).
    asyncio.ensure_future(_wait_until_ready(agent_id))

    log.info("Started runtime for [%s] (%s) — waiting for services to be ready…", short, agent_name)
    return record


async def _stop_runtime(agent_id: str):
    async with _state_lock:
        record = _state.pop(agent_id, None)

    if not record:
        log.warning("stop_runtime: agent %s not in state", agent_id[:8])
        return

    pids = record.get("pids", {})
    for role, pid in pids.items():
        if pid:
            log.info("  Stopping %s pid=%s", role, pid)
            _kill_pid(pid)

    # Clean up generated files
    rt_dir = RUNTIME_DIR / agent_id
    if rt_dir.exists():
        try:
            import shutil as _shutil
            _shutil.rmtree(rt_dir)
        except Exception as exc:
            log.debug("cleanup rt_dir %s: %s", rt_dir, exc)

    await _save_state()
    log.info("Stopped runtime for [%s] (%s)", agent_id[:8], record.get("agentName", "?"))


async def _wait_until_ready(agent_id: str, timeout: int = 60):
    """Poll per-agent service ports until all are accepting connections.

    Marks ``readiness = "ready"`` in state and then patches the agent's
    runtime URLs into design-service once everything is confirmed up.
    If the timeout expires before all ports respond, marks ``"degraded"``.
    """
    deadline = time.time() + timeout

    async with _state_lock:
        record = _state.get(agent_id)
    if not record:
        return

    ports = record["ports"]
    pids  = record["pids"]

    # Only check ports whose process was actually spawned (PID is not None)
    checks: dict[str, int] = {
        "chat":      ports["chat"],
        "sse_rest":  ports["sse_rest"],
        "ingestion": ports["ingestion"],
    }
    if pids.get("chainlit"):
        checks["chainlit"] = ports["chainlit"]

    log.info("Readiness watch [%s]: polling %d port(s) %s (timeout=%ss)",
             agent_id[:8], len(checks), list(checks.values()), timeout)

    while time.time() < deadline:
        await asyncio.sleep(2)
        ready: list[str] = []
        for role, port in checks.items():
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=1.0
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                ready.append(role)
            except Exception:
                pass  # port not yet accepting connections

        pending = [r for r in checks if r not in ready]
        if not pending:
            async with _state_lock:
                if agent_id in _state:
                    _state[agent_id]["readiness"] = "ready"
            await _save_state()
            # Now that all services are confirmed up, write URLs into design-service
            async with _state_lock:
                rec = _state.get(agent_id)
            if rec:
                await _patch_agent_urls(agent_id, rec)
            log.info("Agent [%s] (%s) READY — all services accepting connections",
                     agent_id[:8], record.get("agentName", "?"))
            return

        log.debug("Agent [%s] readiness: %d/%d up, still waiting on %s",
                  agent_id[:8], len(ready), len(checks), pending)

    # Timeout — mark degraded so the UI can surface the failure
    async with _state_lock:
        if agent_id in _state:
            _state[agent_id]["readiness"] = "degraded"
    await _save_state()
    log.warning("Agent [%s] readiness timeout after %ss — marked degraded",
                agent_id[:8], timeout)


def _health_check_record(record: dict) -> dict[str, str]:
    """Return a {role: "running"|"dead"} dict for the record's pids."""
    return {
        role: ("running" if _is_pid_running(pid) else "dead")
        for role, pid in record.get("pids", {}).items()
    }


async def _restart_dead_processes(agent_id: str):
    """If any process in an agent's runtime has died, restart the whole runtime."""
    async with _state_lock:
        record = _state.get(agent_id)
    if not record:
        return

    # Docker-mode agents are managed by compose — don't touch their pids
    if record.get("deploymentMode") == "docker":
        return

    health = _health_check_record(record)
    dead = [r for r, s in health.items() if s == "dead" and record["pids"].get(r) is not None]
    if not dead:
        return

    log.warning("Agent [%s] has dead processes %s — restarting runtime", agent_id[:8], dead)
    # Fetch current agent config and restart
    agents = await _fetch_all_agents() or []
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if agent and agent.get("status") == "active":
        await _stop_runtime(agent_id)
        await _start_runtime(agent)


async def _reconcile_docker_agent(agent_id: str, compose_file: Path):
    """
    Called by the reconciler for docker-deployed agents not yet in _state.
    Checks if their containers are running; if not, does 'docker compose up -d'.
    Adds a lightweight entry to _state so the admin console shows the agent.
    """
    if not _docker_available():
        log.debug("Reconcile docker [%s]: docker not available, skipping", agent_id[:8])
        return

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"],
                capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
            ),
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            try:
                containers.append(json.loads(line))
            except Exception:
                pass

        all_running = containers and all(c.get("State") == "running" for c in containers)
        any_running = any(c.get("State") == "running" for c in containers)

        if not any_running:
            log.info("Reconcile docker [%s]: containers stopped — running docker compose up -d", agent_id[:8])
            up_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                    capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                ),
            )
            if up_result.returncode != 0:
                log.error("Reconcile docker [%s]: compose up failed: %s",
                          agent_id[:8], up_result.stderr[-500:])
                return
            log.info("Reconcile docker [%s]: compose up succeeded", agent_id[:8])
        elif not all_running:
            log.info("Reconcile docker [%s]: some containers not running — docker compose up -d", agent_id[:8])
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                    capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                ),
            )
        else:
            log.debug("Reconcile docker [%s]: all containers running", agent_id[:8])

        # Register a lightweight docker-mode record in _state so the UI shows it
        async with _state_lock:
            if agent_id not in _state:
                _state[agent_id] = {
                    "agentId":        agent_id,
                    "deploymentMode": "docker",
                    "composeFile":    str(compose_file),
                    "pids":           {},
                    "ports":          {},
                    "readiness":      "docker" if any_running else "starting",
                    "startedAt":      time.time(),
                }
        await _save_state()

    except Exception as exc:
        log.warning("Reconcile docker [%s]: %s", agent_id[:8], exc)


# ── Reconciliation loop ───────────────────────────────────────────────────────

async def reconcile_loop():
    log.info("Reconciliation loop started (interval=%ss)", RECONCILE_INTERVAL)
    while True:
        try:
            await _reconcile_once()
        except Exception as exc:
            log.error("Reconcile error: %s", exc)
        await asyncio.sleep(RECONCILE_INTERVAL)


async def _reconcile_once():
    # Heal the always-on platform services first. A dead platform-service makes the
    # agent registry appear empty to the UI/orchestrator while agent runtimes keep
    # running — the registry/runtime drift this guards against.
    await _supervise_platform_services()

    agents = await _fetch_all_agents()
    if agents is None:
        # Registry unreachable — do NOT converge agents. Never tear down running
        # agents because we momentarily can't read desired state; the supervisor
        # above will bring the registry back on a subsequent cycle.
        return

    active_ids  = {a["id"] for a in agents if a.get("status") == "active"}
    running_ids = set(_state.keys())

    # Start runtimes for newly-active agents
    for agent in agents:
        aid = agent["id"]
        if agent.get("status") == "active" and aid not in running_ids:
            compose_file = DOCKER_DEPLOY_DIR / aid / "docker-compose.yml"
            if compose_file.exists():
                # Docker-deployed agent: check containers and restart if stopped
                asyncio.ensure_future(_reconcile_docker_agent(aid, compose_file))
                continue
            log.info("Reconcile: starting runtime for [%s] (%s)",
                     aid[:8], agent.get("name"))
            try:
                await _start_runtime(agent)
            except Exception as exc:
                log.error("Failed to start runtime for [%s]: %s", aid[:8], exc)

    # Stop runtimes for deactivated agents
    for agent_id in list(running_ids):
        if agent_id not in active_ids:
            log.info("Reconcile: stopping runtime for [%s] (no longer active)", agent_id[:8])
            try:
                await _stop_runtime(agent_id)
            except Exception as exc:
                log.error("Failed to stop runtime for [%s]: %s", agent_id[:8], exc)

    # Health-check running agents and restart crashed processes
    for agent_id in list(_state.keys()):
        await _restart_dead_processes(agent_id)

    # Detect config changes for running agents and restart them
    # (env files are written once at startup; changes in the UI won't take
    #  effect until the Flogo process is restarted with the new config).
    for agent in agents:
        aid = agent["id"]
        if aid not in _state:
            continue
        if _state[aid].get("readiness") != "ready":
            continue   # still starting — check next cycle
        current_hash = _config_hash(agent.get("_cfg", {}))
        stored_hash  = _state[aid].get("configHash", "")
        if stored_hash and current_hash != stored_hash:
            log.info("Reconcile: config changed for [%s] (%s) — restarting",
                     aid[:8], agent.get("name"))
            try:
                await _stop_runtime(aid)
                await _start_runtime(agent)
            except Exception as exc:
                log.error("Failed to restart [%s] on config change: %s", aid[:8], exc)


# ── Docker Compose deployment ────────────────────────────────────────────────


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _compose_safe_name(name: str) -> str:
    """Produce a docker-compose-safe project name from an agent name."""
    import re
    safe = re.sub(r"[^a-z0-9_-]", "-", name.lower()).strip("-")
    return safe[:40] or "agent"


def _pick_docker_slot(agent_id: str) -> int:
    """Deterministic slot 0-9 from agent_id hash, in range 7400-7490."""
    import hashlib
    return int(hashlib.md5(agent_id.encode()).hexdigest()[:2], 16) % 10


def _docker_slot_ports(agent_id: str) -> dict[str, int]:
    """Docker host port assignments for an agent (7400-7490 range)."""
    base = 7400 + _pick_docker_slot(agent_id) * 10
    return {
        "chat":       base + 1,   # internal only (not exposed to host)
        "sse_rest":   base + 2,   # SSE REST — mapped to host for streaming
        "sse_events": base + 3,   # SSE event bus
        "ingestion":  base + 4,   # ingestion API — mapped to host for doc upload
        "chainlit":   base + 5,   # chat UI — mapped to host
    }


# ── Docker image build pipeline ───────────────────────────────────────────────

_DOCKER_BUILD_CACHE_FILE = DATA_DIR / "docker-build-cache.json"

# In-progress / recent deploy jobs keyed by agentId.
# Each entry: {"status": "deploying"|"done"|"error", "startedAt": float,
#              "error": str|None, "buildResults": dict, "composeFile": str,
#              "stdout": str, "stderr": str}
_docker_jobs: dict[str, dict] = {}

# Services to build: (service_name, flogo_source, dockerfile_dir, image_tag)
_DOCKER_AGENT_SERVICES = [
    ("agent-chat-service", SERVICES_AGENT_APPS / "agent-chat-service.flogo",
     PROJECT_ROOT / "docker" / "agent-chat-service"),
    ("ingestion-service",  SERVICES_AGENT_APPS / "ingestion-service.flogo",
     PROJECT_ROOT / "docker" / "ingestion-service"),
    ("rule-engine-service", SERVICES_AGENT_APPS / "rule-engine-service.flogo",
     PROJECT_ROOT / "docker" / "rule-engine-service"),
]
_DOCKER_CHAINLIT_SERVICE = (
    "chainlit",
    PROJECT_ROOT / "services" / "agent" / "ui" / "chainlit",
)


def _flogo_sha256(flogo_path: Path) -> str:
    import hashlib
    return hashlib.sha256(flogo_path.read_bytes()).hexdigest()[:16]


def _docker_image_exists(tag: str) -> bool:
    """Return True if a local Docker image with the given tag exists."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _detect_flogobuild_linux() -> Optional[str]:
    """Return path to linux_amd64 flogobuild binary, or None."""
    candidate = PROJECT_ROOT / "tools" / "flogobuild" / "linux_amd64" / "flogobuild"
    if candidate.is_file() and os.access(str(candidate), os.X_OK):
        return str(candidate)
    return None


def _load_build_cache() -> dict:
    try:
        if _DOCKER_BUILD_CACHE_FILE.exists():
            return json.loads(_DOCKER_BUILD_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_build_cache(cache: dict) -> None:
    _DOCKER_BUILD_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DOCKER_BUILD_CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _build_flogo_service_image(
    service_name: str,
    flogo_src: Path,
    dockerfile_dir: Path,
    image_tag: str,
) -> None:
    """
    Build a Docker image for a Flogo service:
      1. Run flogobuild build-exe (native darwin/arm64) with the go-wrapper
         injected via PATH.  The wrapper:
           a) adds -e to 'go mod tidy' so test-only missing deps don't abort,
           b) cross-compiles a linux/amd64 binary alongside the native one via
              FLOGO_LINUX_OUTPUT_DIR (avoids -p linux/amd64 which requires the
              private github.com/tibco/license-enforcement module).
      2. docker build --platform linux/amd64 with the linux binary.
    """
    import tempfile, shutil as _shutil

    log.info("Building image %s from %s ...", image_tag, flogo_src.name)

    fb = _detect_flogobuild()
    if not fb:
        raise RuntimeError("flogobuild not found — cannot build Docker image")

    with tempfile.TemporaryDirectory(prefix="flogo-docker-build-") as tmpdir:
        tmp = Path(tmpdir)
        darwin_out = tmp / "darwin-out"
        linux_out = tmp / "linux-out"
        darwin_out.mkdir()
        linux_out.mkdir()

        # Step 1: build native binary; go-wrapper simultaneously cross-compiles
        # linux/amd64 into linux_out via FLOGO_LINUX_OUTPUT_DIR.
        env = _go_wrapped_env()
        env["FLOGO_LINUX_OUTPUT_DIR"] = str(linux_out)
        result = subprocess.run(
            [
                fb, "build-exe",
                "-f", str(flogo_src),
                "-c", "flogo-studio-2264",
                "-n", service_name,
                "-o", str(darwin_out),
            ],
            capture_output=True, text=True, timeout=600,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"flogobuild failed for {service_name}:\n"
                f"{result.stdout[-2000:]}"
            )
        binary_path = linux_out / service_name
        if not binary_path.exists():
            raise RuntimeError(
                f"go-wrapper did not produce linux/amd64 binary for {service_name}; "
                f"check /tmp/flogo-linux-build.log"
            )
        binary_path.chmod(0o755)
        log.info("  flogobuild OK → %s (%d bytes)", service_name, binary_path.stat().st_size)

        # Step 2: assemble build context
        _shutil.copy(str(dockerfile_dir / "Dockerfile"), str(tmp / "Dockerfile"))
        _shutil.copy(str(binary_path), str(tmp / service_name))

        # Step 3: docker build --platform linux/amd64
        result = subprocess.run(
            ["docker", "build", "--platform", "linux/amd64", "-t", image_tag, str(tmp)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker build failed for {image_tag}:\n"
                f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
            )
        log.info("  docker build OK → %s", image_tag)


def _build_chainlit_image(chainlit_dir: Path, image_tag: str) -> None:
    """Build the chainlit Docker image from its own Dockerfile."""
    log.info("Building chainlit image %s ...", image_tag)
    result = subprocess.run(
        ["docker", "build", "--platform", "linux/amd64", "-t", image_tag, str(chainlit_dir)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {image_tag}:\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )
    log.info("  docker build OK → %s", image_tag)


def build_docker_images(force: bool = False) -> dict:
    """
    Build Docker images for all agent services if they are missing or stale.
    Returns a dict: {service_name: {"image": str, "built": bool, "cached": bool}}.

    Caches builds by SHA256 of the source .flogo file — rebuilds only when the
    source changes (or when force=True, or the image was removed).
    """
    if not _docker_available():
        raise RuntimeError("docker not found on PATH — install Docker Desktop or Docker Engine")

    cache = _load_build_cache()
    results = {}

    # ── Flogo services ────────────────────────────────────────────────────────
    for service_name, flogo_src, dockerfile_dir in _DOCKER_AGENT_SERVICES:
        image_tag = f"flogo-agent-studio/{service_name}:latest"
        flogo_hash = _flogo_sha256(flogo_src)
        cached_hash = cache.get(service_name, {}).get("flogo_sha256")
        image_exists = _docker_image_exists(image_tag)

        if not force and image_exists and cached_hash == flogo_hash:
            log.info("  %s — up to date (cached)", image_tag)
            results[service_name] = {"image": image_tag, "built": False, "cached": True}
            continue

        reason = "forced" if force else ("new/changed source" if cached_hash != flogo_hash else "image missing")
        log.info("  %s — building (%s) ...", image_tag, reason)
        _build_flogo_service_image(service_name, flogo_src, dockerfile_dir, image_tag)

        cache[service_name] = {"flogo_sha256": flogo_hash, "image": image_tag}
        _save_build_cache(cache)
        results[service_name] = {"image": image_tag, "built": True, "cached": False}

    # ── Chainlit ──────────────────────────────────────────────────────────────
    chainlit_name, chainlit_dir = _DOCKER_CHAINLIT_SERVICE
    chainlit_tag = f"flogo-agent-studio/{chainlit_name}:latest"

    if not force and _docker_image_exists(chainlit_tag):
        log.info("  %s — up to date (cached)", chainlit_tag)
        results[chainlit_name] = {"image": chainlit_tag, "built": False, "cached": True}
    else:
        log.info("  %s — building ...", chainlit_tag)
        _build_chainlit_image(chainlit_dir, chainlit_tag)
        results[chainlit_name] = {"image": chainlit_tag, "built": True, "cached": False}

    return results


def _generate_compose_yaml(agent: dict) -> str:
    """
    Generate a per-agent docker-compose.yml using locally-built images.

    Services:
      weaviate      — dedicated vector DB (public image, no build needed)
      ollama        — LLM backend, shares ~/.ollama model cache with host
      agent-chat    — agent-chat-service binary (chat + SSE, 3 triggers merged)
      ingestion     — ingestion-service binary
      chainlit      — chat UI (Python)

    Images for Flogo services are built locally by build_docker_images() and
    tagged flogo-agent-studio/<service>:latest — no registry pull needed.

    Host ports: deterministic 7400-7490 range per agentId hash.
    Internal ports are fixed (matching .flogo trigger defaults):
      7001 chat  7005 sse-rest  7099 sse-events  7002 ingestion  7080 chainlit
    """
    agent_id    = agent["id"]
    agent_name  = agent.get("name", agent_id)
    cfg         = agent.get("_cfg") or agent.get("config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            log.debug("Invalid JSON config for [%s], using {}", agent_id[:8])
            cfg = {}

    collection  = cfg.get("collectionName") or f"Agent_{agent_id.replace('-','')[:16]}"
    prompt      = cfg.get("systemPrompt", "You are a helpful assistant.")
    llm_model   = cfg.get("llmModel", "llama3.2:3b")
    llm_provider = cfg.get("llmProvider", "Ollama")
    embedding_model    = cfg.get("embeddingModel", "nomic-embed-text")
    embedding_provider = cfg.get("embeddingProvider", "Ollama")
    embedding_base_url = cfg.get("embeddingBaseUrl", "") or "http://ollama:11434/v1"
    chunk_strategy     = cfg.get("chunkStrategy", "sentence")
    # Per-agent governance (2.26.4 guardrails); defaults match app-property defaults
    enable_guardrails     = _env_bool(cfg.get("enableGuardrails"), True)
    redact_sensitive_data = _env_bool(cfg.get("redactSensitiveData"), True)
    rate_limit            = _env_int(cfg.get("rateLimit"), 0)
    token_limit           = _env_int(cfg.get("tokenLimit"), 0)
    reasoning_effort      = str(cfg.get("reasoningEffort", "") or "")

    safe_name   = _compose_safe_name(agent_name)
    short       = agent_id[:8]
    ports       = _docker_slot_ports(agent_id)

    import textwrap, datetime
    # Single-quote escape for YAML block scalar
    prompt_escaped = prompt.replace("'", "''")

    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    yaml = textwrap.dedent(f"""\
        # Flogents — {agent_name} ({short})
        # Generated  : {generated_at}
        # Agent ID   : {agent_id}
        #
        # Images are built locally by the runtime manager before first deploy.
        # Re-build: POST /api/runtime/docker-build  (or click "Rebuild Images" in UI)
        #
        # Chat UI: http://localhost:{ports['chainlit']}
        # Ingest : http://localhost:{ports['ingestion']}/api/ingest
        # SSE    : http://localhost:{ports['sse_rest']}

        name: agent-{safe_name}

        networks:
          agent-net:
            driver: bridge

        volumes:
          weaviate-data:

        services:

          # ── Vector database ─────────────────────────────────────────────────
          weaviate:
            image: semitechnologies/weaviate:1.24.6
            restart: unless-stopped
            environment:
              AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
              PERSISTENCE_DATA_PATH: /var/lib/weaviate
              DEFAULT_VECTORIZER_MODULE: none
              ENABLE_MODULES: ""
              CLUSTER_HOSTNAME: node1
            volumes:
              - weaviate-data:/var/lib/weaviate
            networks:
              - agent-net
            healthcheck:
              test: ["CMD", "wget", "-qO-", "http://localhost:8080/v1/.well-known/ready"]
              interval: 10s
              timeout: 5s
              retries: 12

          # ── LLM provider ────────────────────────────────────────────────────
          ollama:
            image: ollama/ollama:latest
            restart: unless-stopped
            volumes:
              - ~/.ollama:/root/.ollama
            networks:
              - agent-net
            healthcheck:
              test: ["CMD", "ollama", "list"]
              interval: 15s
              timeout: 10s
              retries: 5

          # ── Agent chat + SSE service (single binary, three triggers) ────────
          # AgentChatRESTTrigger :7001 (internal only)
          # SSEStreamRESTTrigger :7005 (mapped to host {ports['sse_rest']})
          # SSEEventBus          :7099 (mapped to host {ports['sse_events']})
          agent-chat:
            image: flogo-agent-studio/agent-chat-service:latest
            platform: linux/amd64
            restart: unless-stopped
            ports:
              - "{ports['sse_rest']}:7005"
              - "{ports['sse_events']}:7099"
            environment:
              FLOGO_APP_PROPS_ENV: auto
              FLOGO_LOG_LEVEL: INFO
              COLLECTION_NAME: {collection}
              SYSTEM_PROMPT: '{prompt_escaped}'
              LLM_MODEL: {llm_model}
              LLM_PROVIDER: {llm_provider}
              LLM_BASE_URL: http://ollama:11434/v1
              ENABLE_GUARDRAILS: "{enable_guardrails}"
              REDACT_SENSITIVE_DATA: "{redact_sensitive_data}"
              RATE_LIMIT: "{rate_limit}"
              TOKEN_LIMIT: "{token_limit}"
              REASONING_EFFORT: "{reasoning_effort}"
              AGENT_ID: {agent_id}
              WEAVIATE_HOST: weaviate
              WEAVIATE_PORT: "8080"
              CHAT_SERVICE_URL: http://localhost:7001/api/chat
            networks:
              - agent-net
            depends_on:
              weaviate:
                condition: service_healthy
              ollama:
                condition: service_started
            healthcheck:
              test: ["CMD", "wget", "-qO-", "http://localhost:7001/api/health"]
              interval: 15s
              timeout: 5s
              retries: 5

          # ── Ingestion service ───────────────────────────────────────────────
          ingestion:
            image: flogo-agent-studio/ingestion-service:latest
            platform: linux/amd64
            restart: unless-stopped
            ports:
              - "{ports['ingestion']}:7002"
            environment:
              FLOGO_APP_PROPS_ENV: auto
              FLOGO_LOG_LEVEL: INFO
              COLLECTION_NAME: {collection}
              WEAVIATE_HOST: weaviate
              WEAVIATE_PORT: "8080"
              EMBEDDING_MODEL: {embedding_model}
              EMBEDDING_PROVIDER: {embedding_provider}
              EMBEDDING_BASE_URL: {embedding_base_url}
              CHUNK_STRATEGY: {chunk_strategy}
            networks:
              - agent-net
            depends_on:
              weaviate:
                condition: service_healthy
            healthcheck:
              test: ["CMD", "wget", "-qO-", "http://localhost:7002/api/health"]
              interval: 15s
              timeout: 5s
              retries: 5

          # ── Chainlit chat UI ────────────────────────────────────────────────
          chainlit:
            image: flogo-agent-studio/chainlit:latest
            platform: linux/amd64
            restart: unless-stopped
            ports:
              - "{ports['chainlit']}:7080"
            environment:
              AGENT_ID: {agent_id}
              DESIGN_SERVICE_URL: {DESIGN_URL}
              CHAT_SERVICE_URL: http://agent-chat:7001
              SSE_SERVICE_URL: http://agent-chat:7005
              SSE_EVENTS_URL: http://agent-chat:7099
              FEEDBACK_SERVICE_URL: {FEEDBACK_URL}
            networks:
              - agent-net
            depends_on:
              agent-chat:
                condition: service_healthy

          # ── Rule engine service (per-agent) ─────────────────────────────────
          rule-engine:
            image: flogo-agent-studio/rule-engine-service:latest
            platform: linux/amd64
            restart: unless-stopped
            volumes:
              - {str(PROJECT_ROOT / "config" / "rules")}:/rules:ro
            environment:
              FLOGO_APP_PROPS_ENV: auto
              FLOGO_LOG_LEVEL: INFO
              RULES_PATH: /rules
              API_KEY: changeme
            networks:
              - agent-net
            healthcheck:
              test: ["CMD", "wget", "-qO-", "http://localhost:7097/api/health"]
              interval: 15s
              timeout: 5s
              retries: 5
        """)
    return yaml


async def _handle_docker_build(request: web.Request) -> web.Response:
    """
    POST /api/runtime/docker-build
    Build (or rebuild) Docker images for all agent services.
    Query param: ?force=true to rebuild even when source is unchanged.
    """
    if not _docker_available():
        return web.json_response(
            {"error": "docker not found on PATH — install Docker Desktop or Docker Engine"},
            status=503,
        )

    force = request.rel_url.query.get("force", "").lower() in ("1", "true", "yes")

    try:
        results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: build_docker_images(force=force)
        )
    except Exception as exc:
        log.error("docker build failed: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"success": True, "images": results})


async def _handle_docker_deploy(request: web.Request) -> web.Response:
    """
    POST /api/agents/{agentId}/docker-deploy
    Fires a background task that:
      1. Builds Docker images if missing or stale (cached by .flogo SHA256).
      2. Generates a per-agent docker-compose.yml.
      3. Runs `docker compose up -d`.
    Returns 202 immediately; poll GET /api/agents/{agentId}/docker-deploy for status.
    Idempotent — calling again while a deploy is in-flight is a no-op.
    """
    agent_id = request.match_info["agentId"]

    if not _docker_available():
        return web.json_response(
            {"error": "docker not found on PATH — install Docker Desktop or Docker Engine"},
            status=503,
        )

    # If already in-flight, return current state
    job = _docker_jobs.get(agent_id, {})
    if job.get("status") == "deploying":
        return web.json_response({"status": "deploying", "agentId": agent_id}, status=202)

    # Mark as starting and kick off background task
    _docker_jobs[agent_id] = {
        "status": "deploying", "startedAt": time.time(),
        "error": None, "buildResults": {}, "composeFile": "", "stdout": "", "stderr": "",
    }
    asyncio.ensure_future(_run_docker_deploy(agent_id))

    return web.json_response({"status": "deploying", "agentId": agent_id}, status=202)


async def _run_docker_deploy(agent_id: str) -> None:
    """Background task: build images → generate compose → docker compose up -d."""
    job = _docker_jobs[agent_id]

    # ── Step 1: build images ─────────────────────────────────────────────────
    try:
        build_results = await asyncio.get_event_loop().run_in_executor(
            None, lambda: build_docker_images(force=False)
        )
        job["buildResults"] = build_results
        log.info("docker images ready for [%s]: %s",
                 agent_id[:8],
                 {k: ("built" if v["built"] else "cached") for k, v in build_results.items()})
    except Exception as exc:
        log.error("Image build failed for [%s]: %s", agent_id[:8], exc)
        job.update({"status": "error", "error": f"Image build failed: {exc}"})
        return

    # ── Step 2: fetch agent config ───────────────────────────────────────────
    agents = await _fetch_all_agents() or []
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        job.update({"status": "error", "error": f"agent {agent_id} not found"})
        return

    # ── Step 3: generate compose YAML ────────────────────────────────────────
    try:
        compose_yaml = _generate_compose_yaml(agent)
    except Exception as exc:
        log.error("Failed to generate compose YAML for [%s]: %s", agent_id[:8], exc)
        job.update({"status": "error", "error": f"Could not generate compose YAML: {exc}"})
        return

    agent_dir = DOCKER_DEPLOY_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    compose_file = agent_dir / "docker-compose.yml"
    compose_file.write_text(compose_yaml)
    job["composeFile"] = str(compose_file)

    # ── Step 4: docker compose up -d ─────────────────────────────────────────
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d"],
                capture_output=True, text=True, timeout=300,
            ),
        )
    except subprocess.TimeoutExpired:
        log.error("docker compose up timed out for [%s]", agent_id[:8])
        job.update({"status": "error", "error": "docker compose up timed out (300s)"})
        return
    except Exception as exc:
        log.error("docker compose up failed for [%s]: %s", agent_id[:8], exc)
        job.update({"status": "error", "error": f"docker compose failed: {exc}"})
        return

    log.info("docker compose up for [%s] exit=%s", agent_id[:8], result.returncode)
    job["stdout"] = result.stdout[-2000:]
    job["stderr"] = result.stderr[-2000:]

    if result.returncode != 0:
        job.update({"status": "error", "error": f"docker compose exited {result.returncode}"})
    else:
        job["status"] = "done"
        # Mark this agent as docker-managed so the reconcile loop won't
        # try to (re)start it as a local process.
        async with _state_lock:
            if agent_id in _state:
                _state[agent_id]["deploymentMode"] = "docker"
            else:
                _state[agent_id] = {"deploymentMode": "docker", "pids": {}}
        await _save_state()
        log.info("Agent [%s] marked as docker-managed", agent_id[:8])


async def _handle_docker_status(request: web.Request) -> web.Response:
    """
    GET /api/agents/{agentId}/docker-deploy
    Returns job state (deploying/done/error) merged with `docker compose ps` output.
    """
    agent_id = request.match_info["agentId"]
    compose_file = DOCKER_DEPLOY_DIR / agent_id / "docker-compose.yml"

    # Include in-progress or recent job state
    job = _docker_jobs.get(agent_id)
    if job and job.get("status") == "deploying":
        return web.json_response({
            "status":   "deploying",
            "agentId":  agent_id,
            "containers": [],
        })

    if not compose_file.exists():
        base = {"status": "not_deployed", "agentId": agent_id, "containers": []}
        if job:
            base.update({"jobStatus": job["status"], "jobError": job.get("error"),
                         "stdout": job.get("stdout", ""), "stderr": job.get("stderr", "")})
        return web.json_response(base, status=200)

    if not _docker_available():
        return web.json_response({"error": "docker not found on PATH"}, status=503)

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "ps", "--format", "json"],
                capture_output=True, text=True, timeout=15,
            ),
        )
    except Exception as exc:
        log.error("docker compose ps failed for [%s]: %s", agent_id[:8], exc)
        return web.json_response({"error": str(exc)}, status=500)

    containers: list = []
    for line in result.stdout.strip().splitlines():
        try:
            containers.append(json.loads(line))
        except Exception:
            pass

    running = any(c.get("State") == "running" for c in containers)
    response: dict = {
        "status":      "running" if running else "stopped",
        "agentId":     agent_id,
        "containers":  containers,
        "composeFile": str(compose_file),
    }
    if job:
        response.update({"jobStatus": job["status"], "jobError": job.get("error"),
                         "stdout": job.get("stdout", ""), "stderr": job.get("stderr", "")})
    return web.json_response(response)


async def _handle_docker_stop(request: web.Request) -> web.Response:
    """
    DELETE /api/agents/{agentId}/docker-deploy
    Runs `docker compose down` to stop and remove containers.
    """
    agent_id = request.match_info["agentId"]
    compose_file = DOCKER_DEPLOY_DIR / agent_id / "docker-compose.yml"

    if not compose_file.exists():
        return web.json_response({"error": "no compose file found — agent was not docker-deployed"}, status=404)

    if not _docker_available():
        return web.json_response({"error": "docker not found on PATH"}, status=503)

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "down"],
                capture_output=True, text=True, timeout=60,
            ),
        )
    except Exception as exc:
        log.error("docker compose down failed for [%s]: %s", agent_id[:8], exc)
        return web.json_response({"error": str(exc)}, status=500)

    log.info("docker compose down for [%s] exit=%s", agent_id[:8], result.returncode)
    return web.json_response({
        "success":  result.returncode == 0,
        "exitCode": result.returncode,
        "stdout":   result.stdout[-2000:],
        "stderr":   result.stderr[-2000:],
    })


# ── Admin helpers ─────────────────────────────────────────────────────────────

# ── Optional rageval (RAG quality eval) integration ──────────────────────────
# rageval (github.com/mpandav-tibco/rag-evaluator) is an external Go service that
# scores RAG answers. It lives in its own repo, so we only register/supervise it
# when its binary is present on this host (default ../rageval, override RAGEVAL_HOME).
RAGEVAL_HOME = Path(os.getenv("RAGEVAL_HOME", str(PROJECT_ROOT.parent / "rageval")))
RAGEVAL_PORT = int(os.getenv("RAGEVAL_PORT", "9090"))


def _rageval_bin() -> Optional[Path]:
    """Path to the rageval binary if installed, else None (feature stays off)."""
    b = RAGEVAL_HOME / "rageval"
    return b if b.exists() else None


_PLATFORM_SERVICES = [
    {"name": "platform-service",   "port": 7020, "healthPath": "/api/health"},
    {"name": "agent-builder",      "port": 7010, "healthPath": "/api/health"},
    {"name": "mcp-server",         "port": 7333, "healthPath": "/api/health"},
    {"name": "runtime-manager",    "port": 7050, "healthPath": "/api/health"},
    {"name": "forge-ui",           "port": 7025, "healthPath": "/"},
]
# rageval is surfaced + supervised only when its binary exists on this host.
if _rageval_bin():
    _PLATFORM_SERVICES.append({"name": "rageval", "port": RAGEVAL_PORT, "healthPath": "/health"})

# rule-engine-service is now per-agent (started by _start_runtime for each agent)
_AGENT_SUPPORT_SERVICES: list = []

# Services the API must NOT stop (killing them would break the admin console itself)
_PLATFORM_SVC_UNMANAGED = {"runtime-manager", "forge-ui"}

# Map service name → binary path relative to PROJECT_ROOT
_PLATFORM_SVC_BIN: dict[str, str] = {
    "platform-service": "bin/platform-service",
    "agent-builder":    "bin/agent-builder-service",
    "mcp-server":       "bin/mcp-server",
}

def _pid_on_port(port: int) -> Optional[int]:
    """Return the PID of the process listening on *port*, or None."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(p) for p in result.stdout.strip().split() if p.isdigit()]
        return pids[0] if pids else None
    except Exception:
        return None

async def _handle_admin_services(request: web.Request) -> web.Response:
    """GET /api/admin/services — health + PID of all platform and agent-support services."""
    import socket
    services = []
    for svc in _PLATFORM_SERVICES + _AGENT_SUPPORT_SERVICES:
        port = svc["port"]
        alive = False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                alive = True
        except OSError:
            pass
        pid = _pid_on_port(port) if alive else None
        category = "platform" if svc in _PLATFORM_SERVICES else "agent-support"
        services.append({
            "name":        svc["name"],
            "port":        port,
            "status":      "online" if alive else "offline",
            "pid":         pid,
            "category":    category,
            "controllable": svc["name"] not in _PLATFORM_SVC_UNMANAGED and (
                svc["name"] in _PLATFORM_SVC_BIN or svc["name"] == "rageval"),
        })
    return web.json_response(services)


# ── REST API handlers ─────────────────────────────────────────────────────────

# ── Platform service start/stop helpers ──────────────────────────────────────

async def _stop_platform_svc(name: str) -> dict:
    """SIGTERM the process owning a named platform service's port."""
    svc = next((s for s in _PLATFORM_SERVICES + _AGENT_SUPPORT_SERVICES if s["name"] == name), None)
    if not svc:
        return {"error": f"unknown service: {name}"}
    pid = _pid_on_port(svc["port"])
    if not pid:
        return {"message": "already stopped"}
    try:
        os.kill(pid, signal.SIGTERM)
        log.info("Stopped platform service [%s] (PID %s)", name, pid)
        return {"message": "stopped", "pid": pid}
    except ProcessLookupError:
        return {"message": "already stopped"}
    except Exception as exc:
        log.error("Failed to stop platform service [%s]: %s", name, exc)
        return {"error": str(exc)}


async def _start_platform_svc(name: str) -> dict:
    """Launch a named platform service using its binary + env/flogo-app files."""
    if name == "rageval":
        return await _start_rageval()
    if name not in _PLATFORM_SVC_BIN:
        return {"error": f"no binary configured for: {name}"}
    svc = next((s for s in _PLATFORM_SERVICES + _AGENT_SUPPORT_SERVICES if s["name"] == name), None)
    if not svc:
        return {"error": f"unknown service: {name}"}
    existing = _pid_on_port(svc["port"])
    if existing:
        return {"message": "already running", "pid": existing}
    bin_path = PROJECT_ROOT / _PLATFORM_SVC_BIN[name]
    if not bin_path.exists():
        return {"error": f"binary not found: {bin_path}"}
    if not os.access(bin_path, os.X_OK):
        bin_path.chmod(bin_path.stat().st_mode | 0o111)
    # Discover env file
    env_file = ""
    for edir in [PROJECT_ROOT / "services" / "platform" / "env",
                 PROJECT_ROOT / "services" / "agent" / "env"]:
        for stem in [f"{name}-service", name]:
            p = edir / f"{stem}.env"
            if p.exists():
                env_file = str(p)
                break
        if env_file:
            break
    # Discover flogo app override
    app_file = ""
    for fdir in [PROJECT_ROOT / "services" / "platform" / "flogo",
                 PROJECT_ROOT / "services" / "agent" / "flogo"]:
        for stem in [f"{name}-service", name]:
            p = fdir / f"{stem}.flogo"
            if p.exists():
                app_file = str(p)
                break
        if app_file:
            break
    launch_py = PROJECT_ROOT / "services" / "launch.py"
    log_path  = PROJECT_ROOT / "logs" / f"{name}.log"
    cmd = [sys.executable, str(launch_py), env_file, str(bin_path)]
    if app_file:
        cmd += ["-app", app_file]
    env = os.environ.copy()
    env["OTEL_SERVICE_NAME"] = name
    if name == "mcp-server":
        env["FLOGO_OTEL_TRACE"] = "false"
    try:
        with open(log_path, "w") as lf:
            lf.write(f"=== STARTED {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} {name} ===\n")
            lf.flush()
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=lf, env=env,
                cwd=str(PROJECT_ROOT), start_new_session=True,
            )
        log.info("Started platform service [%s] PID %s", name, proc.pid)
        return {"message": "started", "pid": proc.pid}
    except Exception as exc:
        log.error("Failed to start platform service [%s]: %s", name, exc)
        return {"error": str(exc)}


async def _start_rageval() -> dict:
    """Launch the external rageval Go service from its own repo dir (cwd=RAGEVAL_HOME
    so it loads config.yaml + rageval.db). No-op if already listening; off when the
    binary is absent."""
    b = _rageval_bin()
    if not b:
        return {"error": f"rageval not installed (looked in {RAGEVAL_HOME}; set RAGEVAL_HOME)"}
    existing = _pid_on_port(RAGEVAL_PORT)
    if existing:
        return {"message": "already running", "pid": existing}
    if not os.access(b, os.X_OK):
        b.chmod(b.stat().st_mode | 0o111)
    log_path = PROJECT_ROOT / "logs" / "rageval.log"
    try:
        with open(log_path, "a") as lf:
            lf.write(f"=== STARTED {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} rageval ===\n")
            lf.flush()
            proc = subprocess.Popen(
                [str(b)], stdout=lf, stderr=lf,
                cwd=str(RAGEVAL_HOME), start_new_session=True,
            )
        log.info("Started rageval PID %s (port %s, home %s)", proc.pid, RAGEVAL_PORT, RAGEVAL_HOME)
        return {"message": "started", "pid": proc.pid}
    except Exception as exc:
        log.error("Failed to start rageval: %s", exc)
        return {"error": str(exc)}


# ── Platform-service supervisor ───────────────────────────────────────────────
# The runtime-manager is the always-on orchestrator, so it also keeps the managed
# platform services alive. Without this, a dead platform-service makes the agent
# registry look empty (registry/runtime drift) while agent runtimes keep running.
_PLATFORM_RESTART_COOLDOWN = 30.0   # min seconds between auto-restart attempts per service
_platform_restart_attempts: dict[str, float] = {}

async def _supervise_platform_services():
    """Restart any managed platform service (platform-service, agent-builder,
    mcp-server) that is not listening on its port. Idempotent (_start_platform_svc
    no-ops if the port is already bound) and rate-limited so a crash-looping binary
    is retried at most once per cooldown window. Never touches runtime-manager
    (itself) or forge-ui (both excluded from _PLATFORM_SVC_BIN)."""
    for name in _PLATFORM_SVC_BIN:
        svc = next((s for s in _PLATFORM_SERVICES if s["name"] == name), None)
        if not svc:
            continue
        if _pid_on_port(svc["port"]):
            continue  # alive — leave it alone
        last = _platform_restart_attempts.get(name, 0.0)
        if time.time() - last < _PLATFORM_RESTART_COOLDOWN:
            continue  # within cooldown — don't hammer a service that won't boot
        _platform_restart_attempts[name] = time.time()
        log.warning("Supervisor: platform service [%s] DOWN on port %s — restarting",
                    name, svc["port"])
        res = await _start_platform_svc(name)
        log.info("Supervisor: restart [%s] -> %s", name, res)

    # Optional: supervise the external rageval service when it is installed.
    if _rageval_bin() and not _pid_on_port(RAGEVAL_PORT):
        last = _platform_restart_attempts.get("rageval", 0.0)
        if time.time() - last >= _PLATFORM_RESTART_COOLDOWN:
            _platform_restart_attempts["rageval"] = time.time()
            log.warning("Supervisor: rageval DOWN on port %s — restarting", RAGEVAL_PORT)
            res = await _start_rageval()
            log.info("Supervisor: restart [rageval] -> %s", res)


async def _handle_stop_platform_svc(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name in _PLATFORM_SVC_UNMANAGED:
        return web.json_response({"error": f"{name} cannot be controlled via API"}, status=400)
    result = await _stop_platform_svc(name)
    return web.json_response(result, status=500 if "error" in result else 200)


async def _handle_start_platform_svc(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name in _PLATFORM_SVC_UNMANAGED:
        return web.json_response({"error": f"{name} cannot be controlled via API"}, status=400)
    result = await _start_platform_svc(name)
    code = 500 if "error" in result else (201 if result.get("message") == "started" else 200)
    return web.json_response(result, status=code)


async def _handle_restart_platform_svc(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name in _PLATFORM_SVC_UNMANAGED:
        return web.json_response({"error": f"{name} cannot be controlled via API"}, status=400)
    stop_r = await _stop_platform_svc(name)
    if "error" in stop_r:
        return web.json_response(stop_r, status=500)
    await asyncio.sleep(0.6)
    start_r = await _start_platform_svc(name)
    status = 500 if "error" in start_r else 200
    return web.json_response({**stop_r, **start_r, "message": "restarted"}, status=status)


async def _handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status":       "ok",
        "managedAgents": len(_state),
        "uptime":       time.time(),
    })


async def _handle_list_agents(request: web.Request) -> web.Response:
    async with _state_lock:
        result = []
        for agent_id, rec in _state.items():
            health = _health_check_record(rec)
            result.append({"agentId": agent_id, **rec, "health": health})
    return web.json_response(result)


async def _handle_get_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agentId"]
    async with _state_lock:
        rec = _state.get(agent_id)
    if not rec:
        return web.json_response({"error": "not found"}, status=404)
    health = _health_check_record(rec)
    return web.json_response({**rec, "health": health})


async def _handle_start_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agentId"]

    async with _state_lock:
        already = agent_id in _state
    if already:
        async with _state_lock:
            rec = _state[agent_id]
        return web.json_response({"message": "already running", "runtime": rec})

    agents = await _fetch_all_agents() or []
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return web.json_response({"error": f"agent {agent_id} not found in design-service"}, status=404)

    try:
        record = await _start_runtime(agent)
    except Exception as exc:
        log.error("Failed to start runtime for [%s]: %s", agent_id[:8], exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response(record, status=201)


async def _handle_stop_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agentId"]
    async with _state_lock:
        exists = agent_id in _state
    if not exists:
        return web.json_response({"error": "not found"}, status=404)

    await _stop_runtime(agent_id)
    return web.json_response({"message": "stopped"})


async def _handle_restart_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agentId"]

    async with _state_lock:
        exists = agent_id in _state
    if not exists:
        return web.json_response({"error": "not running"}, status=404)

    log.info("Restarting runtime for agent [%s]", agent_id[:8])
    await _stop_runtime(agent_id)

    agents = await _fetch_all_agents() or []
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return web.json_response({"error": f"agent {agent_id} not found in design-service"}, status=404)

    try:
        record = await _start_runtime(agent)
    except Exception as exc:
        log.error("Failed to restart runtime for [%s]: %s", agent_id[:8], exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response(record)


async def _handle_ingestion_health(request: web.Request) -> web.Response:
    """
    GET /api/agents/{agentId}/ingestion-health
    Returns health + config the ingestion service is currently running with.
    Mode:
      per-agent — agent is deployed in _state; reads from RUNTIME_DIR/{agentId}/ingestion-service.env
      standalone — reads from SERVICES_AGENT_ENV/ingestion-service.env (port 7002)
    """
    agent_id = request.match_info["agentId"]

    async with _state_lock:
        runtime = _state.get(agent_id)

    if runtime:
        ing_port = runtime["ports"]["ingestion"]
        env_path = RUNTIME_DIR / agent_id / "ingestion-service.env"
        mode = "per-agent"
    else:
        ing_port = STANDALONE_INGESTION_PORT
        env_path = SERVICES_AGENT_ENV / "ingestion-service.env"
        mode = "standalone"

    env_vals = _read_env_dict(env_path)
    configured_with = {
        "chunkStrategy":    env_vals.get("CHUNK_STRATEGY",     "sentence"),
        "embeddingModel":   env_vals.get("EMBEDDING_MODEL",   "nomic-embed-text"),
        "collectionName":   env_vals.get("COLLECTION_NAME",   ""),
        "embeddingProvider": env_vals.get("EMBEDDING_PROVIDER", "Ollama"),
        "embeddingBaseUrl": env_vals.get("EMBEDDING_BASE_URL", ""),
    }

    healthy = False
    try:
        # Read the API key from the env file; fall back to "changeme" for
        # existing agents still carrying the old SECRET: encrypted value.
        api_key = env_vals.get("API_KEY", "changeme")
        if api_key.startswith("SECRET:"):
            api_key = "changeme"
        auth_value = base64.b64encode(f"flogo:{api_key}".encode()).decode()
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(
                f"http://localhost:{ing_port}/api/health",
                headers={"Authorization": f"Basic {auth_value}"},
            )
            healthy = r.status_code == 200
    except Exception:
        log.debug("Ingestion health check unreachable on port %s", ing_port)
        healthy = False

    return web.json_response({
        "healthy":        healthy,
        "url":            f"http://localhost:{ing_port}",
        "port":           ing_port,
        "mode":           mode,
        "configuredWith": configured_with,
    })


async def _handle_restart_ingestion(request: web.Request) -> web.Response:
    """
    POST /api/agents/{agentId}/restart-ingestion
    Rebuilds the ingestion service env from the agent's current saved config
    and restarts the process.  Works for both standalone (port 7002) and
    per-agent deployed services.
    """
    agent_id = request.match_info["agentId"]

    # Fetch latest agent config from design-service
    agents = await _fetch_all_agents() or []
    agent  = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return web.json_response({"error": f"agent {agent_id} not found"}, status=404)

    cfg = agent.get("_cfg", {})
    collection_name    = cfg.get("collectionName") or f"Agent_{agent_id.replace('-', '')[:16]}"
    embedding_model    = cfg.get("embeddingModel", "nomic-embed-text")
    embedding_provider = cfg.get("embeddingProvider", "Ollama")
    embedding_base_url = cfg.get("embeddingBaseUrl", "") or "http://localhost:11434/v1"
    chunk_strategy     = cfg.get("chunkStrategy", "sentence")

    overrides = {
        "COLLECTION_NAME":    collection_name,
        "EMBEDDING_MODEL":    embedding_model,
        "EMBEDDING_PROVIDER": embedding_provider,
        "EMBEDDING_BASE_URL": embedding_base_url,
        "CHUNK_STRATEGY":     chunk_strategy,
        # Migrate away from the Flogo-encrypted SECRET: value so the runtime
        # manager and UI can authenticate with a known plaintext key.
        "API_KEY":            "changeme",
    }

    base_proc_env = {
        **os.environ,
        "FLOGO_APP_PROPS_ENV":  "auto",
        "FLOGO_LOG_FORMAT":     "JSON",
        "FLOGO_LOG_CTX":        "TRUE",
        "FLOGO_ENV":            "dev",
        "FLOGO_LOG_CTX_FIELDS": "service.namespace=flogo-agent-studio,service.environment=dev",
    }

    async with _state_lock:
        runtime = _state.get(agent_id)

    if runtime:
        # ── Per-agent mode ─────────────────────────────────────────────────────
        short    = agent_id[:8]
        ing_port = runtime["ports"]["ingestion"]
        ing_flogo = RUNTIME_DIR / agent_id / "ingestion-service.flogo"
        ing_env   = RUNTIME_DIR / agent_id / "ingestion-service.env"

        # Kill old ingestion process — by PID, then by port as safety net for orphans
        old_pid = runtime.get("pids", {}).get("ingestion")
        _kill_pid(old_pid)
        _kill_port(ing_port)

        # Rewrite env with fresh config
        _generate_env_file(SERVICES_AGENT_ENV / "ingestion-service.env", ing_env, overrides)

        _ing_log = LOGS_AGENT_DIR / f"{short}-ingestion.log"
        log_f = open(_ing_log, "w")
        log_f.write(f"=== STARTED {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} [{short}] ingestion ===\n")
        log_f.flush()
        new_proc = subprocess.Popen(
            [sys.executable, str(LAUNCH_PY), str(ing_env),
             str(SERVICES_BIN / "ingestion-service"), "-app", str(ing_flogo)],
            stdout=log_f, stderr=subprocess.STDOUT,
            env={**base_proc_env, "OTEL_SERVICE_NAME": f"ingestion-{short}"},
        )

        async with _state_lock:
            if agent_id in _state:
                _state[agent_id]["pids"]["ingestion"] = new_proc.pid
        await _save_state()

        log.info("Restarted per-agent ingestion [%s] pid=%s port=%s strategy=%s",
                 short, new_proc.pid, ing_port, chunk_strategy)
    else:
        # ── Standalone mode ────────────────────────────────────────────────────
        ing_env   = SERVICES_AGENT_ENV / "ingestion-service.env"
        ing_flogo = SERVICES_AGENT_APPS / "ingestion-service.flogo"

        # Rewrite the shared env file in-place
        _generate_env_file(ing_env, ing_env, overrides)

        # Kill whatever is on the standalone port
        try:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{STANDALONE_INGESTION_PORT}"],
                capture_output=True, text=True, timeout=5,
            )
            for pid_str in result.stdout.strip().split():
                try:
                    _kill_pid(int(pid_str))
                except Exception:
                    pass
        except Exception as exc:
            log.debug("kill standalone ingestion: %s", exc)

        await asyncio.sleep(1.5)  # let the port free up

        _ing_log_path = PROJECT_ROOT / "logs" / "ingestion.log"
        log_f = open(_ing_log_path, "w")
        log_f.write(f"=== STARTED {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} ingestion ===\n")
        log_f.flush()
        new_proc = subprocess.Popen(
            [sys.executable, str(LAUNCH_PY), str(ing_env),
             str(SERVICES_BIN / "ingestion-service"), "-app", str(ing_flogo)],
            stdout=log_f, stderr=subprocess.STDOUT,
            env={**base_proc_env, "OTEL_SERVICE_NAME": "ingestion"},
        )

        log.info("Restarted standalone ingestion pid=%s port=%s strategy=%s",
                 new_proc.pid, STANDALONE_INGESTION_PORT, chunk_strategy)

    return web.json_response({
        "restarted":     True,
        "agentId":       agent_id,
        "mode":          "per-agent" if runtime else "standalone",
        "configuredWith": {
            "chunkStrategy":    chunk_strategy,
            "embeddingModel":   embedding_model,
            "collectionName":   collection_name,
            "embeddingProvider": embedding_provider,
        },
    })


# ── Deploy-service replacement ────────────────────────────────────────────────
# These handlers absorb the Flogo deploy-service (port 7030) so that service
# can be retired.  They expose the same URL paths the UI already calls:
#   POST   /api/v1/agents/{agentId}/deploy          → set status = active
#   DELETE /api/v1/agents/{agentId}/deploy          → set status = draft
#   GET    /api/v1/agents/{agentId}/deploy          → return current status
#   GET    /api/v1/agents/{agentId}/export/kubernetes
#   GET    /api/v1/agents/{agentId}/export/docker-compose


async def _get_single_agent(agent_id: str) -> dict | None:
    """Fetch one agent from design-service; unwrap the records envelope."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{DESIGN_URL}/api/v1/agents/{agent_id}",
                headers={"Authorization": _AUTH_HEADER},
            )
            r.raise_for_status()
            body = r.json()
            # design-service returns {"records": [...]} for single-record calls
            if isinstance(body, dict) and "records" in body:
                records = body["records"]
                return records[0] if records else None
            return body if isinstance(body, dict) and "id" in body else None
    except Exception as exc:
        log.warning("_get_single_agent %s: %s", agent_id[:8], exc)
        return None


async def _set_agent_status(agent_id: str, status: str) -> dict | None:
    """PUT status field on an agent; design-service COALESCEs missing fields."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{DESIGN_URL}/api/v1/agents/{agent_id}",
                json={"status": status},
                headers={"Authorization": _AUTH_HEADER, "Content-Type": "application/json"},
            )
            r.raise_for_status()
            body = r.json()
            if isinstance(body, dict) and "records" in body:
                records = body["records"]
                return records[0] if records else None
            return body if isinstance(body, dict) and "id" in body else None
    except Exception as exc:
        log.warning("_set_agent_status %s → %s: %s", agent_id[:8], status, exc)
        return None


def _agent_to_deploy_status(agent: dict) -> dict:
    """Shape an agent dict into the DeployStatus envelope the UI expects."""
    return {
        "records": [{
            "id":          agent.get("id", ""),
            "agentId":     agent.get("id", ""),
            "status":      agent.get("status", "draft"),
            "version":     agent.get("version", 1),
            "deployedAt":  agent.get("updatedAt") or agent.get("createdAt"),
        }]
    }


async def _handle_v1_deploy(request: web.Request) -> web.Response:
    """POST /api/v1/agents/{agentId}/deploy  — activate agent."""
    agent_id = request.match_info["agentId"]
    agent = await _set_agent_status(agent_id, "active")
    if not agent:
        return web.json_response({"error": "failed to activate agent"}, status=500)
    # Also trigger runtime start if not already running
    async with _state_lock:
        already_running = agent_id in _state
    if not already_running:
        agents = await _fetch_all_agents() or []
        a = next((x for x in agents if x["id"] == agent_id), None)
        if a:
            asyncio.ensure_future(_start_runtime(a))
    return web.json_response(_agent_to_deploy_status(agent))


async def _handle_v1_undeploy(request: web.Request) -> web.Response:
    """DELETE /api/v1/agents/{agentId}/deploy  — deactivate agent."""
    agent_id = request.match_info["agentId"]
    agent = await _set_agent_status(agent_id, "draft")
    if not agent:
        return web.json_response({"error": "failed to deactivate agent"}, status=500)
    # Stop the runtime processes
    async with _state_lock:
        running = agent_id in _state
    if running:
        await _stop_runtime(agent_id)
    return web.json_response(_agent_to_deploy_status(agent))


async def _handle_v1_deploy_status(request: web.Request) -> web.Response:
    """GET /api/v1/agents/{agentId}/deploy  — return current deploy status."""
    agent_id = request.match_info["agentId"]
    agent = await _get_single_agent(agent_id)
    if not agent:
        return web.json_response({"error": "agent not found"}, status=404)
    return web.json_response(_agent_to_deploy_status(agent))


async def _handle_export_kubernetes(request: web.Request) -> web.Response:
    """GET /api/v1/agents/{agentId}/export/kubernetes  — K8s manifests."""
    agent_id = request.match_info["agentId"]
    agent = await _get_single_agent(agent_id)
    if not agent:
        return web.json_response({"error": "agent not found"}, status=404)
    name = agent.get("name", agent_id)
    slug = f"agent-{agent_id}"
    yaml_text = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {slug}
  labels:
    app: {slug}
    agent-name: "{name}"
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {slug}
  template:
    metadata:
      labels:
        app: {slug}
    spec:
      containers:
      - name: agent-chat
        image: tibco/flogo-agent-studio:agent-chat-latest
        ports:
        - containerPort: 7001
        env:
        - name: AGENT_ID
          value: "{agent_id}"
        - name: FLOGO_LOG_LEVEL
          value: INFO
---
apiVersion: v1
kind: Service
metadata:
  name: {slug}
spec:
  selector:
    app: {slug}
  ports:
  - port: 7001
    targetPort: 7001
  type: ClusterIP
"""
    return web.Response(text=yaml_text, content_type="text/plain")


async def _handle_export_compose(request: web.Request) -> web.Response:
    """GET /api/v1/agents/{agentId}/export/docker-compose  — compose manifest."""
    agent_id = request.match_info["agentId"]
    agent = await _get_single_agent(agent_id)
    if not agent:
        return web.json_response({"error": "agent not found"}, status=404)
    slug = f"agent-{agent_id}"
    yaml_text = f"""version: '3.9'
services:
  {slug}:
    image: tibco/flogo-agent-studio:agent-chat-latest
    restart: unless-stopped
    ports:
      - '7001:7001'
    environment:
      AGENT_ID: {agent_id}
      FLOGO_LOG_LEVEL: INFO
    networks:
      - agent-studio
networks:
  agent-studio:
    driver: bridge
"""
    return web.Response(text=yaml_text, content_type="text/plain")


# ── Log tail helpers ──────────────────────────────────────────────────────────

# Map platform-service names (from _PLATFORM_SERVICES) to their log filenames
_PLATFORM_LOG_MAP: dict[str, str] = {
    "platform-service": "platform.log",
    "agent-builder":    "agent-builder.log",
    "mcp-server":       "mcp-server.log",
    "runtime-manager":  "runtime-manager.log",
    "forge-ui":         "forge.log",
}

_AGENT_LOG_SERVICES = {"chat", "ingestion", "rule-engine", "chainlit", "sse"}


def _tail_file(path: Path, n: int) -> list[str]:
    """Return the last *n* lines of *path* without loading the whole file."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            read_size = min(size, max(n * 200, 65536))
            fh.seek(max(0, size - read_size))
            raw = fh.read().decode("utf-8", errors="replace")
            lines = raw.splitlines()
            return lines[-n:] if len(lines) > n else lines
    except Exception as exc:
        return [f"[error reading log: {exc}]"]


async def _handle_agent_logs(request: web.Request) -> web.Response:
    """GET /api/runtime/agents/{agentId}/logs/{service}?lines=100"""
    agent_id = request.match_info["agentId"]
    service  = request.match_info["service"]
    if service not in _AGENT_LOG_SERVICES:
        return web.Response(status=400, text=f"Unknown service '{service}'. Valid: {sorted(_AGENT_LOG_SERVICES)}")
    try:
        n = max(1, min(int(request.rel_url.query.get("lines", "100")), 500))
    except ValueError:
        n = 100
    short    = agent_id[:8]
    log_file = LOGS_AGENT_DIR / f"{short}-{service}.log"
    if not log_file.exists():
        return web.json_response({"lines": [], "exists": False, "total": 0})
    lines = _tail_file(log_file, n)
    return web.json_response({"lines": lines, "exists": True, "total": len(lines)})


# ── Session history handlers (Phase 2.2 — persistent conversation memory) ────

async def _handle_get_session_history(request: web.Request) -> web.Response:
    """GET /api/sessions/{sessionId} — return ordered chat turns."""
    session_id = request.match_info["sessionId"]
    limit = int(request.rel_url.query.get("limit", "50"))
    try:
        db = await _get_db()
        rows = await db.fetch(
            "SELECT id, agent_id, role, content, metadata, created_at"
            " FROM chat_history WHERE session_id = $1"
            " ORDER BY created_at ASC LIMIT $2",
            session_id, limit,
        )
        turns = [
            {"id": r["id"], "agentId": r["agent_id"], "role": r["role"],
             "content": r["content"], "metadata": r["metadata"] or {},
             "createdAt": r["created_at"].isoformat()}
            for r in rows
        ]
        return web.json_response({"sessionId": session_id, "turns": turns, "count": len(turns)})
    except Exception as exc:
        return web.json_response({"error": str(exc), "turns": []}, status=500)


async def _handle_save_session_turn(request: web.Request) -> web.Response:
    """POST /api/sessions/{sessionId} — append a chat turn to history."""
    session_id = request.match_info["sessionId"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    agent_id = body.get("agentId", "")
    role     = body.get("role", "user")
    content  = body.get("content", "")
    metadata = body.get("metadata", {})
    if role not in ("user", "assistant"):
        return web.json_response({"error": "role must be 'user' or 'assistant'"}, status=400)
    if not content:
        return web.json_response({"error": "content required"}, status=400)
    try:
        db = await _get_db()
        row = await db.fetchrow(
            "INSERT INTO chat_history (session_id, agent_id, role, content, metadata)"
            " VALUES ($1, $2, $3, $4, $5::jsonb)"
            " RETURNING id, created_at",
            session_id, agent_id, role, content, json.dumps(metadata),
        )
        return web.json_response(
            {"id": row["id"], "sessionId": session_id, "createdAt": row["created_at"].isoformat()},
            status=201,
        )
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def _handle_delete_session_history(request: web.Request) -> web.Response:
    """DELETE /api/sessions/{sessionId} — purge all turns for a session."""
    session_id = request.match_info["sessionId"]
    try:
        db = await _get_db()
        result = await db.execute(
            "DELETE FROM chat_history WHERE session_id = $1", session_id
        )
        deleted = int(result.split()[-1]) if result else 0
        return web.json_response({"deleted": deleted})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


# ── Knowledge Base proxy handlers (Phase 3.1 + 3.2 — Weaviate collection mgmt) ─

async def _handle_kb_list_collections(request: web.Request) -> web.Response:
    """GET /api/kb/collections — list all Weaviate collections with stats."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_WEAVIATE_URL}/v1/schema")
            schema = r.json()
        classes = schema.get("classes", [])
        result = []
        for cls in classes:
            name = cls["class"]
            # Get object count via aggregate
            count = 0
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    gr = await client.post(
                        f"{_WEAVIATE_URL}/v1/graphql",
                        json={"query": f'{{ Aggregate {{ {name} {{ meta {{ count }} }} }} }}'},
                    )
                    gd = gr.json()
                    count = (gd.get("data", {}).get("Aggregate", {})
                             .get(name, [{}])[0].get("meta", {}).get("count", 0))
            except Exception:
                pass
            props = [p["name"] for p in cls.get("properties", [])]
            result.append({"name": name, "objectCount": count, "properties": props})
        return web.json_response({"collections": result, "count": len(result)})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def _handle_kb_get_collection(request: web.Request) -> web.Response:
    """GET /api/kb/collections/{name} — get collection details."""
    name = request.match_info["name"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_WEAVIATE_URL}/v1/schema/{name}")
            if r.status_code == 404:
                return web.json_response({"error": f"collection '{name}' not found"}, status=404)
            cls = r.json()
        count = 0
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                gr = await client.post(
                    f"{_WEAVIATE_URL}/v1/graphql",
                    json={"query": f'{{ Aggregate {{ {name} {{ meta {{ count }} }} }} }}'},
                )
                gd = gr.json()
                count = (gd.get("data", {}).get("Aggregate", {})
                         .get(name, [{}])[0].get("meta", {}).get("count", 0))
        except Exception:
            pass
        prop_names = [p["name"] for p in cls.get("properties", [])]
        return web.json_response({
            "name": cls.get("class", name),
            "objectCount": count,
            "properties": prop_names,
        })
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def _handle_kb_delete_collection(request: web.Request) -> web.Response:
    """DELETE /api/kb/collections/{name} — delete a Weaviate collection."""
    name = request.match_info["name"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(f"{_WEAVIATE_URL}/v1/schema/{name}")
            if r.status_code == 404:
                return web.json_response({"error": f"collection '{name}' not found"}, status=404)
            if r.status_code not in (200, 204):
                return web.json_response({"error": r.text}, status=r.status_code)
        return web.json_response({"deleted": name})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def _handle_platform_logs(request: web.Request) -> web.Response:
    """GET /api/runtime/platform-logs/{service}?lines=100"""
    service = request.match_info["service"]
    if service not in _PLATFORM_LOG_MAP:
        return web.Response(status=400, text=f"Unknown service '{service}'. Valid: {sorted(_PLATFORM_LOG_MAP)}")
    try:
        n = max(1, min(int(request.rel_url.query.get("lines", "100")), 500))
    except ValueError:
        n = 100
    log_file = PROJECT_ROOT / "logs" / _PLATFORM_LOG_MAP[service]
    if not log_file.exists():
        return web.json_response({"lines": [], "exists": False, "total": 0})
    lines = _tail_file(log_file, n)
    return web.json_response({"lines": lines, "exists": True, "total": len(lines)})


# ── Server setup ──────────────────────────────────────────────────────────────

def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health",                           _handle_health)
    app.router.add_get("/api/agents",                           _handle_list_agents)
    app.router.add_get("/api/agents/{agentId}",                 _handle_get_agent)
    app.router.add_post("/api/agents/{agentId}/start",          _handle_start_agent)
    app.router.add_delete("/api/agents/{agentId}/stop",         _handle_stop_agent)
    # Docker Compose deployment
    app.router.add_post("/api/agents/{agentId}/docker-deploy",  _handle_docker_deploy)
    app.router.add_get("/api/agents/{agentId}/docker-deploy",   _handle_docker_status)
    app.router.add_delete("/api/agents/{agentId}/docker-deploy",_handle_docker_stop)
    app.router.add_post("/api/runtime/docker-build",             _handle_docker_build)
    # /api/runtime prefix aliases (used by forge UI proxy)
    app.router.add_get("/api/admin/services",                              _handle_admin_services)
    app.router.add_post("/api/admin/services/{name}/start",                _handle_start_platform_svc)
    app.router.add_delete("/api/admin/services/{name}/stop",               _handle_stop_platform_svc)
    app.router.add_post("/api/admin/services/{name}/restart",              _handle_restart_platform_svc)
    app.router.add_get("/api/runtime/admin/services",                     _handle_admin_services)
    app.router.add_get("/api/runtime/health",                              _handle_health)
    app.router.add_get("/api/runtime/agents",                              _handle_list_agents)
    app.router.add_get("/api/runtime/agents/{agentId}",                          _handle_get_agent)
    app.router.add_post("/api/runtime/agents/{agentId}/start",                   _handle_start_agent)
    app.router.add_delete("/api/runtime/agents/{agentId}/stop",                  _handle_stop_agent)
    app.router.add_post("/api/runtime/agents/{agentId}/restart",                 _handle_restart_agent)
    app.router.add_post("/api/runtime/agents/{agentId}/docker-deploy",           _handle_docker_deploy)
    app.router.add_get("/api/runtime/agents/{agentId}/docker-deploy",            _handle_docker_status)
    app.router.add_delete("/api/runtime/agents/{agentId}/docker-deploy",         _handle_docker_stop)
    app.router.add_get("/api/runtime/agents/{agentId}/ingestion-health",         _handle_ingestion_health)
    app.router.add_post("/api/runtime/agents/{agentId}/restart-ingestion",       _handle_restart_ingestion)
    app.router.add_get("/api/runtime/agents/{agentId}/logs/{service}",           _handle_agent_logs)
    app.router.add_get("/api/runtime/platform-logs/{service}",                   _handle_platform_logs)
    # canonical /api/agents prefix aliases
    app.router.add_get("/api/agents/{agentId}/ingestion-health",                 _handle_ingestion_health)
    app.router.add_post("/api/agents/{agentId}/restart-ingestion",               _handle_restart_ingestion)
    # ── Deploy-service routes (replaces Flogo deploy-service on port 7030) ──────
    app.router.add_post("/api/v1/agents/{agentId}/deploy",                       _handle_v1_deploy)
    app.router.add_delete("/api/v1/agents/{agentId}/deploy",                     _handle_v1_undeploy)
    app.router.add_get("/api/v1/agents/{agentId}/deploy",                        _handle_v1_deploy_status)
    app.router.add_get("/api/v1/agents/{agentId}/export/kubernetes",             _handle_export_kubernetes)
    app.router.add_get("/api/v1/agents/{agentId}/export/docker-compose",         _handle_export_compose)
    # ── Session history (Phase 2.2 — persistent conversation memory) ─────────
    app.router.add_get("/api/sessions/{sessionId}",    _handle_get_session_history)
    app.router.add_post("/api/sessions/{sessionId}",   _handle_save_session_turn)
    app.router.add_delete("/api/sessions/{sessionId}", _handle_delete_session_history)
    # ── Knowledge Base proxy (Phase 3.1 + 3.2) ───────────────────────────────
    app.router.add_get("/api/kb/collections",           _handle_kb_list_collections)
    app.router.add_get("/api/kb/collections/{name}",    _handle_kb_get_collection)
    app.router.add_delete("/api/kb/collections/{name}", _handle_kb_delete_collection)
    return app


async def _startup(app: web.Application):
    # Initialise DB pool (for session history + KB queries)
    try:
        await _get_db()
        log.info("DB pool connected to %s", _DB_DSN.split("@")[-1])
    except Exception as exc:
        log.warning("DB pool unavailable at startup (%s) — history endpoints disabled", exc)

    # Load persisted state and re-adopt still-running processes
    global _state
    saved = _load_state()
    adopted = 0
    for agent_id, rec in saved.items():
        pids = rec.get("pids", {})
        if any(_is_pid_running(p) for p in pids.values() if p):
            # Adopted processes are already running; mark them ready so the UI
            # shows "Open Chat" immediately after a deployment.py restart.
            rec.setdefault("readiness", "ready")
            _state[agent_id] = rec
            adopted += 1
        else:
            log.debug("Discarding stale state for [%s] (no PIDs alive)", agent_id[:8])
            # Kill any orphaned processes still on those ports
            for port in rec.get("ports", {}).values():
                _kill_port(port)

    if adopted:
        log.info("Re-adopted %d running agent runtime(s) from saved state", adopted)
        # Refresh runtime URLs in design-service for all re-adopted agents.
        # This ensures ingestionUrl / chatApiUrl are up-to-date even if a
        # previous _patch_agent_urls call failed or the URLs were never written.
        for agent_id, rec in list(_state.items()):
            asyncio.ensure_future(_patch_agent_urls(agent_id, rec))

    # Start reconciliation loop
    asyncio.create_task(reconcile_loop())
    log.info("Runtime Manager started on port %s", PORT)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        stream=sys.stdout,
    )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCKER_DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

    # Truncate stale agent logs at startup so the log viewer shows only
    # content from the current runtime-manager session.
    _rm_start_ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    for _stale_log in LOGS_AGENT_DIR.glob("*.log"):
        try:
            _stale_log.write_text(
                f"=== CLEARED AT STARTUP {_rm_start_ts} "
                f"— activate an agent to see logs ===\n"
            )
        except OSError:
            pass

    app = _build_app()
    app.on_startup.append(_startup)

    web.run_app(app, host="0.0.0.0", port=PORT, print=lambda *a: None)


if __name__ == "__main__":
    main()
