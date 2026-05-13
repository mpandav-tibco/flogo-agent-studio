"""
Flogo Agent Studio — Chainlit UI (port 7080)

Thin proxy to the Flogo Agent Studio REST services:
  - design-service     (port 7020) — PostgreSQL-backed agent registry (single source of truth)
  - agent-chat-service (port 7001) — RAG chat + agentactivity
  - feedback-service   (port 7003) — thumbs-up/down ratings
"""

import os
import uuid
import json
import httpx
import chainlit as cl

# ── Service endpoints ──────────────────────────────────────────────────────────

DESIGN_URL   = os.getenv("DESIGN_SERVICE_URL",   "http://localhost:7020")
CHAT_URL     = os.getenv("CHAT_SERVICE_URL",     "http://localhost:7001")
FEEDBACK_URL = os.getenv("FEEDBACK_SERVICE_URL", "http://localhost:7003")

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))

# Basic auth header — matches Flogo service default credentials
_AUTH_HEADER = {"Authorization": "Basic ZmxvZ286Y2hhbmdlbWU="}


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
    """Call agent-chat-service POST /api/chat."""
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

@cl.on_chat_start
async def on_chat_start():
    session_id = str(uuid.uuid4())
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("last_message_id", None)
    cl.user_session.set("last_agent_id", None)

    # Load agents from config-service
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

    # Build agent selector
    agent_options = [
        cl.Action(
            name="select_agent",
            value=a.get("id", "default"),
            label=agent_label(a),
            payload={"agentId": a.get("id", "default")},
        )
        for a in agents
    ]

    # Default to first agent
    default_agent = agents[0]
    cl.user_session.set("agent_id", default_agent.get("id", "default"))
    cl.user_session.set("agent_name", default_agent.get("name", "Agent"))
    cl.user_session.set("collection_name", default_agent.get("collectionName", ""))

    welcome = (
        f"**Flogo Agent Studio**\n\n"
        f"Active agent: **{default_agent.get('name', 'Default Agent')}**\n"
        f"{default_agent.get('description', '')}\n\n"
        f"Type a message to start chatting. Use the buttons below to switch agents or rate responses."
    )

    await cl.Message(content=welcome, actions=agent_options, author="System").send()


@cl.action_callback("select_agent")
async def select_agent(action: cl.Action):
    agents: list[dict] = cl.user_session.get("agents", [])
    agent_id = action.payload.get("agentId", action.value)

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
    message_id = cl.user_session.get("last_message_id", "unknown")

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
        cl.Action(name="thumbs_up",   value=message_id, label="👍 Good answer", payload={}),
        cl.Action(name="thumbs_down", value=message_id, label="👎 Not helpful", payload={}),
    ]

    await cl.Message(
        content=answer,
        author=agent_name,
        elements=elements,
        actions=feedback_actions,
    ).send()
