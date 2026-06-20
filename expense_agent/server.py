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
