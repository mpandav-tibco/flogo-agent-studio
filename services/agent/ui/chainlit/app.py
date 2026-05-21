"""
Flogo Agent Studio — Chainlit UI (port 7080)

Thin proxy to the Flogo Agent Studio REST services:
  - design-service     (port 7020) — PostgreSQL-backed agent registry (single source of truth)
  - agent-chat-service (port 7001) — RAG chat + agentactivity  [API-direct path]
  - agent-chat-service (port 7005) — SSE REST trigger (merged) [SSE path, optional]
  - feedback-service   (port 7003) — thumbs-up/down ratings

Chat path selection (controlled by SSE_SERVICE_URL env var):
  - SSE_SERVICE_URL unset  → API-direct: POST /api/chat on agent-chat-service (synchronous, full response)
  - SSE_SERVICE_URL set    → SSE path:   POST /api/stream/chat on agent-chat-service (SSE REST trigger),
                             then consume SSE events from SSE_EVENTS_URL/events filtered by sessionId.
                             SSE events: stream.start, stream.answer {answer, sources}, stream.done

Note: SSE streaming is now part of agent-chat-service (merged). The SSE REST trigger runs on port 7005
(per-agent dynamic port via deployment.py) as a separate trigger on the same process.
Events MUST be filtered by sessionId to avoid receiving another session's answer.
"""

import asyncio
import logging
import os
import uuid
import json
import urllib.parse
import httpx
import chainlit as cl

log = logging.getLogger("chainlit-ui")

# ── Service endpoints ──────────────────────────────────────────────────────────

DESIGN_URL   = os.getenv("DESIGN_SERVICE_URL",   "http://localhost:7020")
CHAT_URL     = os.getenv("CHAT_SERVICE_URL",     "http://localhost:7001")
FEEDBACK_URL = os.getenv("FEEDBACK_SERVICE_URL", "http://localhost:7003")

# Optional SSE streaming path — leave empty to use API-direct path
SSE_SERVICE_URL = os.getenv("SSE_SERVICE_URL", "")   # e.g. http://localhost:7005
SSE_EVENTS_URL  = os.getenv("SSE_EVENTS_URL",  "")   # e.g. http://localhost:7099

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))

# Basic auth header — matches Flogo service default credentials
_AUTH_HEADER = {"Authorization": "Basic ZmxvZ286Y2hhbmdlbWU="}

# ── Single-agent mode ─────────────────────────────────────────────────────────
# When AGENT_ID is set (injected by Runtime Manager), this Chainlit instance
# is dedicated to one agent — no selector, no profile switcher.
AGENT_ID = os.getenv("AGENT_ID", "").strip()


# ── Helpers ────────────────────────────────────────────────────────────────────

async def fetch_agents() -> list[dict]:
    """Load active agents from design-service (PostgreSQL-backed, single source of truth).

    Returns only agents with status='active', with config fields flattened for easy access.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{DESIGN_URL}/api/v1/agents", headers=_AUTH_HEADER)
        resp.raise_for_status()
        body = resp.json()
        records = body if isinstance(body, list) else body.get("records", [])

        agents: list[dict] = []
        for a in records:
            if a.get("status") != "active":
                continue
            cfg = a.get("config", {})
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = {}
            agents.append({
                "id": a["id"],
                "name": a.get("name", a["id"]),
                "description": a.get("description", ""),
                "collectionName": cfg.get("collectionName", ""),
                "topK": cfg.get("topK", 5),
                "systemPrompt": cfg.get("systemPrompt", ""),
            })

        return agents


async def post_chat(
    agent_id: str,
    query: str,
    session_id: str,
    collection_name: str = "",
    top_k: int = 5,
) -> dict:
    """Call agent-chat-service POST /api/chat (API-direct, synchronous)."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        payload: dict = {
            "message": query,          # field name is "message" not "query"
            "agentId": agent_id,
            "sessionId": session_id,
            "topK": top_k,
        }
        if collection_name:
            payload["collectionName"] = collection_name
        resp = await client.post(
            f"{CHAT_URL}/api/chat",
            json=payload,
            headers=_AUTH_HEADER,
        )
        resp.raise_for_status()
        return resp.json()


async def stream_chat_sse(
    agent_id: str,
    query: str,
    session_id: str,
    collection_name: str = "",
    top_k: int = 5,
) -> dict:
    """
    Trigger the RAG+LLM pipeline via agent-chat-service SSE REST trigger, then consume the SSE
    event bus and return the answer once stream.done is received.

    Flow:
      1. POST SSE_SERVICE_URL/api/stream/chat  → 202 accepted
      2. GET  SSE_EVENTS_URL/events            → SSE stream (shared bus)
         Filter by sessionId to isolate this session's events.

    SSE event types (emitted by agent-chat-service SSE trigger):
      stream.start  → {sessionId, agentId, query}
      stream.answer → {sessionId, agentId, answer, sources}
      stream.done   → {sessionId, agentId}
    """
    payload: dict = {
        "message": query,
        "agentId": agent_id,
        "sessionId": session_id,
        "topK": top_k,
    }
    if collection_name:
        payload["collectionName"] = collection_name

    # 1. Initiate the pipeline (fire and proceed — service responds 202)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        await client.post(
            f"{SSE_SERVICE_URL}/api/stream/chat",
            json=payload,
            headers=_AUTH_HEADER,
        )

    # 2. Consume SSE event bus, filter by sessionId, collect answer
    answer: str = ""
    raw_sources: list = []
    events_url = f"{SSE_EVENTS_URL}/events"
    current_event_type: str = ""

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", events_url, headers=_AUTH_HEADER) as resp:
                resp.raise_for_status()
                async def _consume():
                    nonlocal answer, raw_sources, current_event_type
                    async for raw_line in resp.aiter_lines():
                        if raw_line.startswith("event:"):
                            current_event_type = raw_line[6:].strip()
                        elif raw_line.startswith("data:"):
                            try:
                                evt = json.loads(raw_line[5:].strip())
                            except Exception:
                                continue
                            if evt.get("sessionId") != session_id:
                                continue
                            if current_event_type == "stream.answer":
                                answer = evt.get("answer", "")
                                raw_sources = evt.get("sources", [])
                            elif current_event_type == "stream.done":
                                return
                await asyncio.wait_for(_consume(), timeout=REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("stream_chat_sse: SSE stream timed out after %ss (session=%s)", REQUEST_TIMEOUT, session_id)
        if not answer:
            answer = "⚠️ The request timed out before a response was received. Please try again."

    # Format sources into a readable string (mirrors agent-chat formattedContext)
    formatted_ctx = ""
    if raw_sources:
        parts = []
        for i, src in enumerate(raw_sources, 1):
            content = src.get("content", src.get("pageContent", str(src)))[:400]
            parts.append(f"[{i}] {content}")
        formatted_ctx = "\n\n".join(parts)

    return {"answer": answer, "formattedContext": formatted_ctx, "duration": ""}


async def post_feedback(
    agent_id: str,
    session_id: str,
    message_id: str,
    rating: str,
    comment: str = "",
) -> None:
    """Call feedback-service POST /api/feedback (fire-and-forget; errors are silently logged)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{FEEDBACK_URL}/api/feedback",
                json={
                    "agentId": agent_id,
                    "sessionId": session_id,
                    "messageId": message_id,
                    "rating": rating,
                    "comment": comment,
                },
                headers=_AUTH_HEADER,
            )
    except Exception as exc:
        print(f"[Feedback] failed to submit: {exc}")


def agent_label(agent: dict) -> str:
    return f"{agent.get('name', agent.get('id', '?'))} — {agent.get('description', '')[:60]}"


# ── Chainlit lifecycle ─────────────────────────────────────────────────────────

@cl.set_chat_profiles
async def set_chat_profiles(current_user, language=None):
    """Register active agents as Chainlit Chat Profiles.
    In single-agent mode (AGENT_ID set) no profiles are registered — the agent
    is fixed and the profile picker would be confusing/misleading.
    """
    if AGENT_ID:
        return []   # single-agent mode: no profile selector
    try:
        agents = await fetch_agents()
    except Exception:
        return []
    return [
        cl.ChatProfile(
            name=a["name"],
            markdown_description=a.get("description") or "No description.",
        )
        for a in agents
    ]


def _agent_id_from_referer(environ: dict) -> str | None:
    """Parse ?agent_id=<uuid> from the HTTP Referer header of the WS upgrade request.

    Forge Studio opens Chainlit as:
        http://localhost:7080?agent_id=<uuid>
    The browser sets Referer to that URL when the Socket.IO connection is made,
    so we can recover the intended agent from it.
    """
    referer = environ.get("HTTP_REFERER", "") or environ.get("HTTP_ORIGIN", "")
    if not referer:
        # ASGI scope stores headers as a list of byte tuples
        for k, v in environ.get("headers", []):
            key = k.decode("latin-1").lower() if isinstance(k, bytes) else k.lower()
            if key == "referer":
                referer = v.decode("latin-1") if isinstance(v, bytes) else v
                break
    if not referer:
        return None
    try:
        parsed = urllib.parse.urlparse(referer)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get("agent_id", [None])[0]
    except Exception:
        return None


@cl.on_chat_start
async def on_chat_start():
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("last_message_id", None)
    cl.user_session.set("last_agent_id", None)

    # ── Single-agent mode: AGENT_ID is baked in by Runtime Manager ────────────
    if AGENT_ID:
        try:
            agents = await fetch_agents()   # fetch to get full config
            agent  = next((a for a in agents if a["id"] == AGENT_ID), None)
        except Exception:
            agent = None

        if not agent:
            # Runtime Manager set AGENT_ID but design-service doesn't know it yet
            # (race on first start). Use a minimal placeholder.
            agent = {"id": AGENT_ID, "name": "Agent", "description": "", "collectionName": ""}

        cl.user_session.set("agents",          [agent])
        cl.user_session.set("agent_id",        agent["id"])
        cl.user_session.set("agent_name",       agent.get("name", "Agent"))
        cl.user_session.set("collection_name",  agent.get("collectionName", ""))

        await cl.Message(
            content=(
                f"**{agent.get('name', 'Agent')}**\n\n"
                + (agent.get('description', '') + "\n\n" if agent.get('description') else "")
                + "Type a message to start chatting."
            ),
            author="System",
        ).send()
        return

    # ── Shared mode: load all active agents and let the user pick ─────────────
    try:
        agents = await fetch_agents()
    except Exception as exc:
        await cl.Message(
            content=f"Could not reach design-service ({DESIGN_URL}): {exc}\n\nEnsure all Flogo services are running.",
            author="System",
        ).send()
        agents = []

    if not agents:
        # Fall back to a minimal default so the session is usable
        agents = [{"id": "default", "name": "Default Agent", "description": "General assistant", "collectionName": ""}]

    cl.user_session.set("agents", agents)

    # ── Determine which agent to start with, in priority order: ──────────────
    # 1. ?agent_id=<uuid> from the URL (passed by Forge's "Open Chat" link)
    # 2. chat_profile selected in the Chainlit profile dialog
    # 3. First active agent (fallback)
    default_agent = agents[0]

    environ = getattr(cl.context.session, "environ", {})
    url_agent_id = _agent_id_from_referer(environ)
    if url_agent_id:
        matched = next((a for a in agents if a["id"] == url_agent_id), None)
        if matched:
            default_agent = matched
    else:
        profile_name = cl.user_session.get("chat_profile")
        if profile_name:
            matched = next((a for a in agents if a["name"] == profile_name), None)
            if matched:
                default_agent = matched

    cl.user_session.set("agent_id", default_agent.get("id", "default"))
    cl.user_session.set("agent_name", default_agent.get("name", "Agent"))
    cl.user_session.set("collection_name", default_agent.get("collectionName", ""))

    # Build agent switcher actions (excluding the already-selected agent)
    switch_options = [
        cl.Action(
            name="select_agent",
            payload={"agentId": a.get("id", "default")},
            label=agent_label(a),
        )
        for a in agents
        if a.get("id") != default_agent.get("id")
    ]

    welcome = (
        f"**{default_agent.get('name', 'Default Agent')}**\n\n"
        f"{default_agent.get('description', '')}\n\n"
        f"Type a message to start chatting."
        + ("\n\nSwitch agent:" if switch_options else "")
    )

    await cl.Message(content=welcome, actions=switch_options, author="System").send()


@cl.action_callback("select_agent")
async def select_agent(action: cl.Action):
    agents: list[dict] = cl.user_session.get("agents", [])
    agent_id = action.payload.get("agentId")

    matched = next((a for a in agents if a.get("id") == agent_id), None)
    agent_name = matched.get("name", agent_id) if matched else agent_id
    description = matched.get("description", "") if matched else ""
    collection_name = matched.get("collectionName", "") if matched else ""

    cl.user_session.set("agent_id", agent_id)
    cl.user_session.set("agent_name", agent_name)
    cl.user_session.set("collection_name", collection_name)

    await cl.Message(
        content=f"Switched to **{agent_name}**\n{description}",
        author="System",
    ).send()
    await action.remove()


@cl.action_callback("thumbs_up")
async def thumbs_up(action: cl.Action):
    await _record_feedback("thumbsUp", action)


@cl.action_callback("thumbs_down")
async def thumbs_down(action: cl.Action):
    await _record_feedback("thumbsDown", action)


async def _record_feedback(rating: str, action: cl.Action):
    session_id = cl.user_session.get("session_id", "unknown")
    agent_id   = cl.user_session.get("last_agent_id") or cl.user_session.get("agent_id", "default")
    message_id = action.payload.get("messageId") or cl.user_session.get("last_message_id", "unknown")

    await post_feedback(agent_id, session_id, message_id, rating)
    emoji = "👍" if rating == "thumbsUp" else "👎"
    await cl.Message(content=f"{emoji} Feedback recorded.", author="System").send()
    await action.remove()


@cl.on_message
async def on_message(message: cl.Message):
    agent_id        = cl.user_session.get("agent_id", "default")
    agent_name      = cl.user_session.get("agent_name", "Agent")
    session_id      = cl.user_session.get("session_id", str(uuid.uuid4()))
    collection_name = cl.user_session.get("collection_name", "")
    message_id      = str(uuid.uuid4())

    cl.user_session.set("last_message_id", message_id)
    cl.user_session.set("last_agent_id", agent_id)

    async with cl.Step(name=f"{agent_name} — thinking", show_input=False) as step:
        try:
            if SSE_SERVICE_URL and SSE_EVENTS_URL:
                result = await stream_chat_sse(agent_id, message.content, session_id, collection_name)
            else:
                result = await post_chat(agent_id, message.content, session_id, collection_name)
        except httpx.HTTPStatusError as exc:
            step.output = f"HTTP {exc.response.status_code}: {exc.response.text}"
            await cl.Message(
                content=f"Service error ({exc.response.status_code}). Please try again.",
                author=agent_name,
            ).send()
            return
        except Exception as exc:
            step.output = str(exc)
            await cl.Message(
                content=f"Could not reach agent-chat-service ({CHAT_URL}): {exc}",
                author=agent_name,
            ).send()
            return

        # agent-chat returns: {answer, formattedContext, duration, error}
        answer          = result.get("answer") or result.get("data", {}).get("answer", str(result))
        formatted_ctx   = result.get("formattedContext") or result.get("data", {}).get("formattedContext", "")
        duration        = result.get("duration") or result.get("data", {}).get("duration", "")

        step.output = f"Done in {duration}" if duration else "Done"

    # Build source elements from formattedContext (pre-formatted string from service)
    elements = []
    if formatted_ctx:
        elements.append(
            cl.Text(
                name="Sources",
                content=formatted_ctx,
                display="side",
            )
        )

    # Feedback actions
    feedback_actions = [
        cl.Action(name="thumbs_up",   payload={"messageId": message_id}, label="👍 Good answer"),
        cl.Action(name="thumbs_down", payload={"messageId": message_id}, label="👎 Not helpful"),
    ]

    await cl.Message(
        content=answer,
        author=agent_name,
        elements=elements,
        actions=feedback_actions,
    ).send()
