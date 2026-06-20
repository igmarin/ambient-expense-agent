"""Ambient webhook server — accepts Pub/Sub push messages and feeds them
into the expense-approval workflow.

Run with::

    make serve          # uvicorn on port 8080

Pub/Sub sends a fully-qualified subscription path in the request body
(e.g. ``projects/my-project/subscriptions/expense-approvals``).  We
normalise that down to the short subscription name (``expense-approvals``)
so session records stay readable.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from .agent import root_agent

# ---------------------------------------------------------------------------
# Logging — standard Python logging to console
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("ambient_expense_agent")

# ---------------------------------------------------------------------------
# Telemetry — otel_to_cloud=False (local only, no Cloud Trace export)
# ---------------------------------------------------------------------------
trace.set_tracer_provider(TracerProvider())

# ---------------------------------------------------------------------------
# Runner — one shared Runner + in-memory session service
# ---------------------------------------------------------------------------
APP_NAME = "ambient_expense_agent"
_session_service = InMemorySessionService()
_runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=_session_service,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Ambient Expense Agent",
    description="Pub/Sub push webhook for the expense-approval workflow.",
)


def normalize_subscription(subscription: str | None) -> str:
    """Reduce a fully-qualified subscription path to its short name.

    ``projects/my-project/subscriptions/expense-approvals`` →
    ``expense-approvals``

    Falls back to ``"pubsub-caller"`` when the field is absent.
    """
    if not subscription:
        return "pubsub-caller"
    # The short name is the segment after the last "/".
    return subscription.rsplit("/", 1)[-1]


async def _run_workflow(message_text: str, user_id: str) -> list[dict[str, Any]]:
    """Create an ephemeral session, run the workflow, return event summaries."""
    session = await _session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
    )

    content = types.Content(
        role="user",
        parts=[types.Part(text=message_text)],
    )

    events: list[dict[str, Any]] = []
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        node_path = ""
        try:
            node_path = event.node_info.path or ""
        except AttributeError:
            pass

        route = ""
        try:
            route = event.actions.route or ""
        except AttributeError:
            pass

        events.append(
            {
                "node": node_path,
                "route": route,
                "output": event.output,
                "author": event.author,
            }
        )

    return events


@app.post("/push")
async def push(request: Request) -> dict[str, Any]:
    """Pub/Sub push endpoint.

    Accepts the standard Pub/Sub push envelope::

        {
          "message": {
            "data": "<base64-encoded JSON>",
            "messageId": "...",
            "attributes": {...}
          },
          "subscription": "projects/p/subscriptions/s"
        }
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON body: {exc}"
        ) from exc

    subscription = body.get("subscription")
    short_name = normalize_subscription(subscription)

    message = body.get("message", {})
    raw_data = message.get("data")

    if raw_data is None:
        raise HTTPException(status_code=400, detail="Missing message.data field")

    # Decode the base64-encoded Pub/Sub message data.
    # parse_event expects {"data": <decoded_payload>} so we reconstruct
    # that envelope here — matching the ADK trigger-route convention.
    try:
        decoded = base64.b64decode(raw_data).decode("utf-8")
        try:
            data_payload = json.loads(decoded)
        except json.JSONDecodeError:
            data_payload = decoded
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid base64 message data: {exc}"
        ) from exc

    message_text = json.dumps(
        {"data": data_payload, "attributes": message.get("attributes") or {}}
    )

    logger.info(
        "Pub/Sub push received: subscription=%s → short=%s, messageId=%s",
        subscription,
        short_name,
        message.get("messageId", "unknown"),
    )

    try:
        events = await _run_workflow(message_text, user_id=short_name)
    except Exception as exc:
        logger.exception("Workflow execution failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Agent processing failed: {exc}"
        ) from exc

    logger.info(
        "Workflow complete: subscription=%s, events=%d",
        short_name,
        len(events),
    )

    return {
        "status": "success",
        "subscription": short_name,
        "events": events,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Session inspection — discover userIds and session details
#
# DEV-ONLY: These endpoints expose all session data (including event outputs
# with expense descriptions and submitter names) with no authentication.
# They are intended for local development and debugging only. Do NOT expose
# port 8080 publicly without adding authentication (e.g. IAP, API key, or
# Pub/Sub token verification) in front of these routes.
# ---------------------------------------------------------------------------


@app.get("/sessions")
async def list_sessions(user_id: str | None = None) -> dict[str, Any]:
    """List sessions, optionally filtered by user_id.

    Without a ``user_id`` query parameter, returns all sessions grouped by
    user.  This is how you discover the ``userId`` to use in the dev-ui URL::

        /sessions                  → list all sessions grouped by user
        /sessions?user_id=expense-approvals  → list sessions for one user

    .. warning::
        Unauthenticated — dev-only. See module-level comment above.
    """
    if user_id:
        result = await _session_service.list_sessions(
            app_name=APP_NAME, user_id=user_id
        )
        return {
            "user_id": user_id,
            "sessions": [
                {
                    "id": s.id,
                    "user_id": s.user_id,
                    "app_name": s.app_name,
                    "last_update_time": s.last_update_time,
                    "event_count": len(s.events),
                }
                for s in result.sessions
            ],
        }

    # No user_id filter — list all sessions across all users.
    # list_sessions without user_id returns every session for the app.
    result = await _session_service.list_sessions(app_name=APP_NAME)

    sessions_by_user: dict[str, list[dict[str, Any]]] = {}
    for s in result.sessions:
        sessions_by_user.setdefault(s.user_id, []).append(
            {
                "id": s.id,
                "user_id": s.user_id,
                "app_name": s.app_name,
                "last_update_time": s.last_update_time,
                "event_count": len(s.events),
            }
        )

    return {
        "users": [
            {"user_id": uid, "session_count": len(sessions)}
            for uid, sessions in sorted(sessions_by_user.items())
        ],
        "sessions_by_user": sessions_by_user,
    }


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, user_id: str) -> dict[str, Any]:
    """Get full session details including all events.

    Query parameters:
        user_id: The user_id (normalized subscription name) that owns the session.

    Example::

        /sessions/abc-123?user_id=expense-approvals

    .. warning::
        Unauthenticated — dev-only. See module-level comment above.
    """
    session = await _session_service.get_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )
    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id} not found for user '{user_id}'",
        )

    return {
        "id": session.id,
        "user_id": session.user_id,
        "app_name": session.app_name,
        "state": session.state,
        "last_update_time": session.last_update_time,
        "events": [
            {
                "id": e.id,
                "author": e.author,
                "node": e.node_info.path if e.node_info else "",
                "route": e.actions.route if e.actions else "",
                "output": e.output,
                "timestamp": e.timestamp,
            }
            for e in session.events
        ],
    }


def main() -> None:
    """Entry point for ``make serve``."""
    uvicorn.run(
        "expense_agent.server:app",
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )


if __name__ == "__main__":
    main()
