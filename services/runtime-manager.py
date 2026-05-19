#!/usr/bin/env python3
"""
AgentForge Runtime Manager  (port 7050)
========================================
Manages per-agent process groups.  Each *deployed* (status=active) agent gets
its own isolated set of four processes:

  ┌─────────────────────────────────────────────────────────────────────┐
  │  agent-chat-service  :base+1  — RAG chat, COLLECTION_NAME baked in │
  │  sse-stream-service  :base+2  — SSE REST gateway                   │
  │  sse-event-bus       :base+3  — SSE broadcast bus                  │
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
  GET    /api/agents                    — list managed agents + their URLs
  GET    /api/agents/{agentId}          — single agent runtime status
  POST   /api/agents/{agentId}/start    — force-start (bypass reconciler)
  DELETE /api/agents/{agentId}/stop     — force-stop

State persistence: data/agent-runtime.json
  Survives restarts — existing processes are re-adopted if their PID is
  still alive; dead ones are restarted on the next reconciliation pass.

Usage:
  python3 services/runtime-manager.py
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
FEEDBACK_URL       = os.getenv("FEEDBACK_SERVICE_URL", "http://localhost:7003")
RECONCILE_INTERVAL = int(os.getenv("RECONCILE_INTERVAL", "15"))

_AUTH_HEADER = os.getenv("SERVICE_AUTH_HEADER", "Basic ZmxvZ286Y2hhbmdlbWU=")

# Resolved once at startup from the location of this file
_THIS_FILE   = Path(__file__).resolve()
PROJECT_ROOT = _THIS_FILE.parent.parent          # flogo-agent-studio/

SERVICES_BIN     = PROJECT_ROOT / "services" / "bin"
SERVICES_APPS    = PROJECT_ROOT / "services" / "apps"
SERVICES_ENV_DIR = PROJECT_ROOT / "services" / "env"
DATA_DIR         = PROJECT_ROOT / "data"
RUNTIME_DIR      = DATA_DIR / "agent-runtimes"   # per-agent generated files
LOGS_AGENT_DIR   = PROJECT_ROOT / "logs" / "agents"
STATE_FILE       = DATA_DIR / "agent-runtime.json"
LAUNCH_PY        = PROJECT_ROOT / "services" / "launch.py"
CHAINLIT_DIR     = PROJECT_ROOT / "ui" / "chainlit"

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
        # Try to match by trigger ref suffix (e.g. "#rest", "#trigger")
        matched_port = None
        for key, p in port_map.items():
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
    """Best-effort: write chatUiUrl / chatApiUrl back to design-service."""
    payload = {
        "chatUiUrl":    record.get("chatUiUrl", ""),
        "chatApiUrl":   record.get("chatApiUrl", ""),
        "sseUrl":       record.get("sseUrl", ""),
        "ingestionUrl": record.get("ingestionUrl", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.patch(
                f"{DESIGN_URL}/api/v1/agents/{agent_id}",
                json=payload,
                headers={"Authorization": _AUTH_HEADER},
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

    pids: dict[str, Optional[int]] = {}
    short = agent_id[:8]

    base_env = {**os.environ, "FLOGO_APP_PROPS_ENV": "auto"}

    def _open_log(name: str):
        return open(LOGS_AGENT_DIR / f"{short}-{name}.log", "a")

    # ── agent-chat-service ─────────────────────────────────────────────────────
    chat_src    = SERVICES_APPS / "agent-chat-service.flogo"
    chat_flogo  = rt_dir / "agent-chat-service.flogo"
    chat_env    = rt_dir / "agent-chat-service.env"

    if not chat_src.exists():
        raise FileNotFoundError(f"Missing source: {chat_src}")
    if not (SERVICES_BIN / "agent-chat-service").exists():
        raise FileNotFoundError(f"Missing binary: {SERVICES_BIN / 'agent-chat-service'}")

    _modify_flogo_port(chat_src, chat_flogo, {"#rest": ports["chat"]})
    _generate_env_file(
        SERVICES_ENV_DIR / "agent-chat-service.env",
        chat_env,
        {
            "COLLECTION_NAME": collection_name,
            "SYSTEM_PROMPT":   system_prompt,
            "LLM_MODEL":       llm_model,
            "LLM_PROVIDER":    llm_provider,
            "LLM_BASE_URL":    llm_base_url,
        },
    )
    chat_proc = subprocess.Popen(
        [sys.executable, str(LAUNCH_PY), str(chat_env),
         str(SERVICES_BIN / "agent-chat-service"), "-app", str(chat_flogo)],
        stdout=_open_log("chat"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"agent-chat-{short}"},
    )
    pids["chat"] = chat_proc.pid
    log.info("  [%s] agent-chat  pid=%-7s port=%s", short, chat_proc.pid, ports["chat"])

    # ── sse-stream-service ─────────────────────────────────────────────────────
    sse_src    = SERVICES_APPS / "sse-stream-service.flogo"
    sse_flogo  = rt_dir / "sse-stream-service.flogo"
    sse_env    = rt_dir / "sse-stream-service.env"

    if not sse_src.exists():
        raise FileNotFoundError(f"Missing source: {sse_src}")

    _modify_flogo_port(sse_flogo if False else sse_src, sse_flogo, {
        "#rest_1": ports["sse_rest"],
        "#trigger": ports["sse_events"],
        "ssestream": ports["sse_rest"],   # fallback label match
    })
    _generate_env_file(
        SERVICES_ENV_DIR / "sse-stream-service.env",
        sse_env,
        {
            "CHAT_SERVICE_URL": f"http://localhost:{ports['chat']}/api/chat",
            "SYSTEM_PROMPT":    system_prompt,
            "LLM_MODEL":        llm_model,
        },
    )
    sse_proc = subprocess.Popen(
        [sys.executable, str(LAUNCH_PY), str(sse_env),
         str(SERVICES_BIN / "sse-stream-service"), "-app", str(sse_flogo)],
        stdout=_open_log("sse"), stderr=subprocess.STDOUT,
        env={**base_env, "OTEL_SERVICE_NAME": f"sse-stream-{short}"},
    )
    pids["sse_rest"]   = sse_proc.pid
    pids["sse_events"] = sse_proc.pid   # same process, two triggers
    log.info("  [%s] sse-stream  pid=%-7s ports=%s/%s",
             short, sse_proc.pid, ports["sse_rest"], ports["sse_events"])

    # ── ingestion-service ──────────────────────────────────────────────────────
    ing_src   = SERVICES_APPS / "ingestion-service.flogo"
    ing_flogo = rt_dir / "ingestion-service.flogo"
    ing_env   = rt_dir / "ingestion-service.env"

    if not ing_src.exists():
        raise FileNotFoundError(f"Missing source: {ing_src}")

    _modify_flogo_port(ing_src, ing_flogo, {"#rest": ports["ingestion"]})
    _generate_env_file(
        SERVICES_ENV_DIR / "ingestion-service.env",
        ing_env,
        {"COLLECTION_NAME": collection_name},
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
        log.warning("  [%s] chainlit skipped (not installed or ui/chainlit missing)", short)

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
    }

    async with _state_lock:
        _state[agent_id] = record

    await _save_state()
    await _patch_agent_urls(agent_id, record)

    log.info("Started runtime for [%s] (%s) — chatUI: %s", short, agent_name, chat_ui_url or "n/a")
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


# ── Server setup ──────────────────────────────────────────────────────────────

def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health",                    _handle_health)
    app.router.add_get("/api/agents",                    _handle_list_agents)
    app.router.add_get("/api/agents/{agentId}",          _handle_get_agent)
    app.router.add_post("/api/agents/{agentId}/start",   _handle_start_agent)
    app.router.add_delete("/api/agents/{agentId}/stop",  _handle_stop_agent)
    return app


async def _startup(app: web.Application):
    # Load persisted state and re-adopt still-running processes
    global _state
    saved = _load_state()
    adopted = 0
    for agent_id, rec in saved.items():
        pids = rec.get("pids", {})
        if any(_is_pid_running(p) for p in pids.values() if p):
            _state[agent_id] = rec
            adopted += 1
        else:
            log.debug("Discarding stale state for [%s] (no PIDs alive)", agent_id[:8])

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

    app = _build_app()
    app.on_startup.append(_startup)

    web.run_app(app, host="0.0.0.0", port=PORT, print=lambda *a: None)


if __name__ == "__main__":
    main()
