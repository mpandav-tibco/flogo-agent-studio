#!/usr/bin/env python3
"""
AgentForge Runtime Manager  (port 7050)
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

SERVICES_BIN          = PROJECT_ROOT / "services" / "bin"
SERVICES_PLATFORM_APPS = PROJECT_ROOT / "services" / "platform" / "flogo"
SERVICES_AGENT_APPS    = PROJECT_ROOT / "services" / "agent"   / "flogo"
SERVICES_PLATFORM_ENV  = PROJECT_ROOT / "services" / "platform" / "env"
SERVICES_AGENT_ENV     = PROJECT_ROOT / "services" / "agent"   / "env"
DATA_DIR         = PROJECT_ROOT / "data"
RUNTIME_DIR      = DATA_DIR / "agent-runtimes"   # per-agent generated files
LOGS_AGENT_DIR   = PROJECT_ROOT / "logs" / "agents"
STATE_FILE       = DATA_DIR / "agent-runtime.json"
LAUNCH_PY        = PROJECT_ROOT / "services" / "launch.py"
CHAINLIT_DIR     = PROJECT_ROOT / "services" / "agent" / "ui" / "chainlit"

# ── Port pool ──────────────────────────────────────────────────────────────────

_PORT_BASE  = 7200
_MAX_SLOTS  = 10
_PORTS_PER_SLOT = 10    # reserve 10 ports per slot for future growth

_PORT_OFFSETS = {
    "chat":      1,
    "sse_rest":  2,
    "sse_events":3,
    "ingestion": 4,
    "chainlit":  5,
}


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
        "-c", "flogo-studio",
        "-n", svc_name,
        "-o", str(PROJECT_ROOT / "bin"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode == 0:
        _build_failures.pop(svc_name, None)  # clear failure record on success
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


# ── Design-service integration ────────────────────────────────────────────────

async def _fetch_all_agents() -> list[dict]:
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
                        cfg = {}
                a["_cfg"] = cfg
                result.append(a)
            return result
    except Exception as exc:
        log.warning("fetch_all_agents failed: %s", exc)
        return []


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
        log.debug("patch_agent_urls %s: %s", agent_id[:8], exc)


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
        return open(LOGS_AGENT_DIR / f"{short}-{name}.log", "a")

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
            # CHAT_SERVICE_URL is used by the SSE stream_chat flow (loopback within same process)
            "CHAT_SERVICE_URL":  f"http://localhost:{ports['chat']}/api/chat",
        },
    )
    chat_proc = subprocess.Popen(
        [sys.executable, str(LAUNCH_PY), str(chat_env),
         str(SERVICES_BIN / "agent-chat-service"), "-app", str(chat_flogo)],
        stdout=_open_log("chat"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"agent-chat-{short}"},
    )
    pids["chat"]       = chat_proc.pid
    pids["sse_rest"]   = chat_proc.pid   # same process — SSE REST trigger on port sse_rest
    pids["sse_events"] = chat_proc.pid   # same process — SSE events trigger on port sse_events
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
        stdout=_open_log("ingestion"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"ingestion-{short}"},
    )
    pids["ingestion"] = ing_proc.pid
    log.info("  [%s] ingestion   pid=%-7s port=%s", short, ing_proc.pid, ports["ingestion"])

    # ── Chainlit UI ────────────────────────────────────────────────────────────
    cmd = _chainlit_cmd()
    if cmd and CHAINLIT_DIR.exists():
        chainlit_env = {
            **os.environ,
            "AGENT_ID":              agent_id,
            "DESIGN_SERVICE_URL":    DESIGN_URL,
            "CHAT_SERVICE_URL":      f"http://localhost:{ports['chat']}",
            "FEEDBACK_SERVICE_URL":  FEEDBACK_URL,
            "SSE_SERVICE_URL":       f"http://localhost:{ports['sse_rest']}",
            "SSE_EVENTS_URL":        f"http://localhost:{ports['sse_events']}",
        }
        chainlit_proc = subprocess.Popen(
            cmd + ["--port", str(ports["chainlit"])],
            cwd=str(CHAINLIT_DIR),
            stdout=_open_log("chainlit"), stderr=subprocess.STDOUT,
            env=chainlit_env,
        )
        pids["chainlit"] = chainlit_proc.pid
        log.info("  [%s] chainlit    pid=%-7s port=%s", short, chainlit_proc.pid, ports["chainlit"])
    else:
        pids["chainlit"] = None
        log.warning("  [%s] chainlit skipped (not installed or services/agent/ui/chainlit missing)", short)

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
        "startedAt":    time.time(),
        "readiness":    "starting",
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

    health = _health_check_record(record)
    dead = [r for r, s in health.items() if s == "dead" and record["pids"].get(r) is not None]
    if not dead:
        return

    log.warning("Agent [%s] has dead processes %s — restarting runtime", agent_id[:8], dead)
    # Fetch current agent config and restart
    agents = await _fetch_all_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if agent and agent.get("status") == "active":
        await _stop_runtime(agent_id)
        await _start_runtime(agent)


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
    agents = await _fetch_all_agents()
    if not agents:
        return

    active_ids  = {a["id"] for a in agents if a.get("status") == "active"}
    running_ids = set(_state.keys())

    # Start runtimes for newly-active agents
    for agent in agents:
        if agent.get("status") == "active" and agent["id"] not in running_ids:
            log.info("Reconcile: starting runtime for [%s] (%s)",
                     agent["id"][:8], agent.get("name"))
            try:
                await _start_runtime(agent)
            except Exception as exc:
                log.error("Failed to start runtime for [%s]: %s", agent["id"][:8], exc)

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


# ── Docker Compose deployment ────────────────────────────────────────────────

DOCKER_DEPLOY_DIR = DATA_DIR / "docker-deployments"   # per-agent compose files + state


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


def _generate_compose_yaml(agent: dict) -> str:
    """
    Generate a complete per-agent docker-compose.yml.

    Services included:
      weaviate      — dedicated vector DB for this agent
      ollama        — LLM backend (shared model cache via ~/.ollama volume)
      agent-chat    — RAG chat service
      sse-stream    — SSE REST gateway + event bus
      ingestion     — document ingestion
      chainlit      — web chat UI

    Host ports: 7400-7490 range, deterministically assigned per agentId.
    Internal ports match the standard Flogo service defaults (7001/7002/7005/7099).
    Images: ${FLOGO_IMAGE:-tibco/flogo-agent-studio}:*-${VERSION:-latest}
    All per-agent configuration is passed via environment variables — no
    image rebuild needed per agent.
    """
    agent_id    = agent["id"]
    agent_name  = agent.get("name", agent_id)
    cfg         = agent.get("_cfg") or agent.get("config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}

    collection  = cfg.get("collectionName") or f"Agent_{agent_id.replace('-','')[:16]}"
    prompt      = cfg.get("systemPrompt", "You are a helpful assistant.")
    llm_model   = cfg.get("llmModel", "llama3.2:3b")
    llm_provider = cfg.get("llmProvider", "Ollama")
    embedding_model    = cfg.get("embeddingModel", "nomic-embed-text")
    embedding_provider = cfg.get("embeddingProvider", "Ollama")
    embedding_base_url = cfg.get("embeddingBaseUrl", "") or "http://ollama:11434/v1"
    chunk_strategy     = cfg.get("chunkStrategy", "sentence")

    safe_name   = _compose_safe_name(agent_name)
    short       = agent_id[:8]
    ports       = _docker_slot_ports(agent_id)

    import textwrap, datetime
    # Escape special YAML characters in the system prompt
    prompt_escaped = prompt.replace("'", "''")

    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    yaml = textwrap.dedent(f"""\
        # AgentForge — {agent_name} ({short})
        # Generated : {generated_at}
        # Agent ID  : {agent_id}
        #
        # Quick start:
        #   docker compose up -d
        #   docker compose logs -f chainlit      # tail chat UI logs
        #   docker compose ps                    # check status
        #   docker compose down                  # stop all
        #
        # Images are pulled from the registry. To override:
        #   FLOGO_IMAGE=myregistry/flogo-agent-studio VERSION=v1.2 docker compose up -d
        #
        # Chat UI: http://localhost:{ports['chainlit']}
        # Ingest : http://localhost:{ports['ingestion']}/api/ingest

        name: agent-{safe_name}

        networks:
          agent-net:
            driver: bridge

        volumes:
          weaviate-data:

        services:

          # ── Vector database ──────────────────────────────────────────────────
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

          # ── LLM provider ─────────────────────────────────────────────────────
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

          # ── Agent chat service ────────────────────────────────────────────────
          agent-chat:
            image: ${{FLOGO_IMAGE:-tibco/flogo-agent-studio}}:agent-chat-${{VERSION:-latest}}
            restart: unless-stopped
            environment:
              FLOGO_APP_PROPS_ENV: auto
              FLOGO_LOG_LEVEL: INFO
              COLLECTION_NAME: {collection}
              SYSTEM_PROMPT: '{prompt_escaped}'
              LLM_MODEL: {llm_model}
              LLM_PROVIDER: {llm_provider}
              LLM_BASE_URL: http://ollama:11434/v1
              WEAVIATE_HOST: weaviate
              WEAVIATE_PORT: "8080"
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

          # ── SSE stream service ────────────────────────────────────────────────
          sse-stream:
            image: ${{FLOGO_IMAGE:-tibco/flogo-agent-studio}}:sse-stream-${{VERSION:-latest}}
            restart: unless-stopped
            ports:
              - "{ports['sse_rest']}:7005"
              - "{ports['sse_events']}:7099"
            environment:
              FLOGO_APP_PROPS_ENV: auto
              FLOGO_LOG_LEVEL: INFO
              CHAT_SERVICE_URL: http://agent-chat:7001
              LLM_MODEL: {llm_model}
            networks:
              - agent-net
            depends_on:
              agent-chat:
                condition: service_healthy
            healthcheck:
              test: ["CMD", "wget", "-qO-", "http://localhost:7005/api/health"]
              interval: 15s
              timeout: 5s
              retries: 5

          # ── Ingestion service ─────────────────────────────────────────────────
          ingestion:
            image: ${{FLOGO_IMAGE:-tibco/flogo-agent-studio}}:ingestion-${{VERSION:-latest}}
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

          # ── Chainlit chat UI ──────────────────────────────────────────────────
          chainlit:
            image: ${{FLOGO_IMAGE:-tibco/flogo-agent-studio}}:chainlit-${{VERSION:-latest}}
            restart: unless-stopped
            ports:
              - "{ports['chainlit']}:7080"
            environment:
              AGENT_ID: {agent_id}
              DESIGN_SERVICE_URL: {DESIGN_URL}
              CHAT_SERVICE_URL: http://agent-chat:7001
              SSE_SERVICE_URL: http://sse-stream:7005
              SSE_EVENTS_URL: http://sse-stream:7099
              FEEDBACK_SERVICE_URL: {FEEDBACK_URL}
            networks:
              - agent-net
            depends_on:
              - agent-chat
              - sse-stream
        """)
    return yaml


async def _handle_docker_deploy(request: web.Request) -> web.Response:
    """
    POST /api/agents/{agentId}/docker-deploy
    Generates a complete docker-compose.yml for the agent and runs
    `docker compose up -d`.  Idempotent — safe to call multiple times.
    """
    agent_id = request.match_info["agentId"]

    if not _docker_available():
        return web.json_response(
            {"error": "docker not found on PATH — install Docker Desktop or Docker Engine"},
            status=503,
        )

    # Fetch current agent config from design-service
    agents = await _fetch_all_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return web.json_response({"error": f"agent {agent_id} not found"}, status=404)

    # Generate compose YAML locally (no dependency on deploy-service stub)
    try:
        compose_yaml = _generate_compose_yaml(agent)
    except Exception as exc:
        return web.json_response({"error": f"Could not generate compose YAML: {exc}"}, status=500)

    # Write to a stable location so `docker compose` can reference it later
    agent_dir = DOCKER_DEPLOY_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    compose_file = agent_dir / "docker-compose.yml"
    compose_file.write_text(compose_yaml)

    # docker compose up -d
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["docker", "compose", "-f", str(compose_file), "up", "-d", "--pull", "missing"],
                capture_output=True, text=True, timeout=120,
            ),
        )
    except subprocess.TimeoutExpired:
        return web.json_response({"error": "docker compose up timed out (120s)"}, status=504)
    except Exception as exc:
        return web.json_response({"error": f"docker compose failed: {exc}"}, status=500)

    log.info("docker compose up for [%s] exit=%s", agent_id[:8], result.returncode)

    if result.returncode != 0:
        return web.json_response({
            "success": False,
            "exitCode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "composeFile": str(compose_file),
        }, status=500)

    return web.json_response({
        "success":     True,
        "agentId":     agent_id,
        "composeFile": str(compose_file),
        "stdout":      result.stdout[-2000:],
        "stderr":      result.stderr[-2000:],
    }, status=200)


async def _handle_docker_status(request: web.Request) -> web.Response:
    """
    GET /api/agents/{agentId}/docker-deploy
    Returns `docker compose ps` output for the agent.
    """
    agent_id = request.match_info["agentId"]
    compose_file = DOCKER_DEPLOY_DIR / agent_id / "docker-compose.yml"

    if not compose_file.exists():
        return web.json_response({"status": "not_deployed", "containers": []}, status=200)

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
        return web.json_response({"error": str(exc)}, status=500)

    containers: list = []
    for line in result.stdout.strip().splitlines():
        try:
            containers.append(json.loads(line))
        except Exception:
            pass

    running = any(c.get("State") == "running" for c in containers)
    return web.json_response({
        "status":     "running" if running else "stopped",
        "agentId":    agent_id,
        "containers": containers,
        "composeFile": str(compose_file),
    })


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
        return web.json_response({"error": str(exc)}, status=500)

    log.info("docker compose down for [%s] exit=%s", agent_id[:8], result.returncode)
    return web.json_response({
        "success":  result.returncode == 0,
        "exitCode": result.returncode,
        "stdout":   result.stdout[-2000:],
        "stderr":   result.stderr[-2000:],
    })


# ── REST API handlers ─────────────────────────────────────────────────────────

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
            result.append({**rec, "health": health})
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

    agents = await _fetch_all_agents()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        return web.json_response({"error": f"agent {agent_id} not found in design-service"}, status=404)

    try:
        record = await _start_runtime(agent)
    except Exception as exc:
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
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"http://localhost:{ing_port}/api/health")
            healthy = r.status_code in (200, 401)  # 401 = auth required = service is up
    except Exception:
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
    agents = await _fetch_all_agents()
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

        log_f = open(LOGS_AGENT_DIR / f"{short}-ingestion.log", "a")
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

        log_f = open(PROJECT_ROOT / "logs" / "ingestion.log", "a")
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
        agents = await _fetch_all_agents()
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
    # /api/runtime prefix aliases (used by forge UI proxy)
    app.router.add_get("/api/runtime/health",                              _handle_health)
    app.router.add_get("/api/runtime/agents",                              _handle_list_agents)
    app.router.add_get("/api/runtime/agents/{agentId}",                          _handle_get_agent)
    app.router.add_post("/api/runtime/agents/{agentId}/docker-deploy",           _handle_docker_deploy)
    app.router.add_get("/api/runtime/agents/{agentId}/docker-deploy",            _handle_docker_status)
    app.router.add_delete("/api/runtime/agents/{agentId}/docker-deploy",         _handle_docker_stop)
    app.router.add_get("/api/runtime/agents/{agentId}/ingestion-health",         _handle_ingestion_health)
    app.router.add_post("/api/runtime/agents/{agentId}/restart-ingestion",       _handle_restart_ingestion)
    # canonical /api/agents prefix aliases
    app.router.add_get("/api/agents/{agentId}/ingestion-health",                 _handle_ingestion_health)
    app.router.add_post("/api/agents/{agentId}/restart-ingestion",               _handle_restart_ingestion)
    # ── Deploy-service routes (replaces Flogo deploy-service on port 7030) ──────
    app.router.add_post("/api/v1/agents/{agentId}/deploy",                       _handle_v1_deploy)
    app.router.add_delete("/api/v1/agents/{agentId}/deploy",                     _handle_v1_undeploy)
    app.router.add_get("/api/v1/agents/{agentId}/deploy",                        _handle_v1_deploy_status)
    app.router.add_get("/api/v1/agents/{agentId}/export/kubernetes",             _handle_export_kubernetes)
    app.router.add_get("/api/v1/agents/{agentId}/export/docker-compose",         _handle_export_compose)
    return app


async def _startup(app: web.Application):
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

    app = _build_app()
    app.on_startup.append(_startup)

    web.run_app(app, host="0.0.0.0", port=PORT, print=lambda *a: None)


if __name__ == "__main__":
    main()
