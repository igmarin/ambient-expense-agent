#!/usr/bin/env python3
"""Trace generator for the ambient expense agent evaluation.

Runs each eval case through the local ADK workflow runner, intercepts
human-in-the-loop approval steps, automates decisions (approves clean
requests, rejects prompt injections), and serializes traces into
``artifacts/traces/generated_traces.json`` in the Vertex AI
``EvaluationDataset`` format consumed by ``agents-cli eval grade``.

Usage::

    python tests/eval/generate_traces.py
    # or
    make generate-traces
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so `expense_agent` is importable.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from expense_agent.agent import root_agent  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DATASET_PATH = _PROJECT_ROOT / "tests/eval/datasets/basic-dataset.json"
_OUTPUT_PATH = _PROJECT_ROOT / "artifacts/traces/generated_traces.json"
_APP_NAME = "eval_run"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("generate_traces")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_path(event: Any) -> str:
    """Extract the node path from an ADK event."""
    try:
        return event.node_info.path or ""
    except AttributeError:
        return ""


def _event_route(event: Any) -> str:
    """Extract the route from an ADK event's actions."""
    try:
        return event.actions.route or ""
    except AttributeError:
        return ""


def _event_output(event: Any) -> Any:
    """Extract the structured output from an ADK event."""
    try:
        return event.output
    except AttributeError:
        return None


def _function_call(event: Any) -> types.FunctionCall | None:
    """Extract the first function call from an event's content parts."""
    try:
        for part in event.content.parts:
            if part.function_call:
                return part.function_call
    except (AttributeError, TypeError):
        pass
    return None


def _serialize_output(output: Any) -> str:
    """Serialize an event output to a string for the trace."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if hasattr(output, "model_dump_json"):
        return output.model_dump_json()
    try:
        return json.dumps(output, default=str)
    except (TypeError, ValueError):
        return str(output)


def _detect_security_event(events: list[Any]) -> bool:
    """Check if any event's security_checkpoint routed to 'security_event'.

    This determines whether the human approval step should auto-reject
    (prompt injection / security event) or auto-approve (clean expense).
    """
    for event in events:
        node = _node_path(event)
        route = _event_route(event)
        if "security_checkpoint" in node and route == "security_event":
            return True
    return False


def _build_event_dict(
    author: str,
    text: str,
    *,
    function_call: dict[str, Any] | None = None,
    function_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a trace event dict in the Vertex AI eval format."""
    parts: list[dict[str, Any]] = []
    if function_call:
        parts.append({"function_call": function_call})
    elif function_response:
        parts.append({"function_response": function_response})
    elif text:
        parts.append({"text": text})

    return {
        "author": author,
        "content": {
            "role": "model" if author != "user" else "user",
            "parts": parts,
        },
    }


# ---------------------------------------------------------------------------
# Core: run a single eval case through the workflow
# ---------------------------------------------------------------------------


async def _run_single_case(
    runner: Runner,
    session_service: InMemorySessionService,
    case: dict[str, Any],
) -> dict[str, Any]:
    """Run one eval case through the ADK workflow and return a trace dict.

    Handles human-in-the-loop interrupts by:
    - Auto-approving clean expenses (routed through llm_reviewer)
    - Auto-rejecting prompt injections (routed through security_event)
    """
    # pylint: disable=too-many-locals
    case_id = case.get("eval_case_id", "unknown")
    prompt = case.get("prompt", {})
    prompt_text = prompt.get("parts", [{}])[0].get("text", "")

    logger.info("Running case: %s", case_id)

    # Create a fresh session for this case.
    session = await session_service.create_session(
        app_name=_APP_NAME,
        user_id=f"eval_{case_id}",
    )

    # Send the expense JSON as the user message.
    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt_text)],
    )

    # Collect all events from the workflow execution.
    raw_events: list[Any] = []
    async for event in runner.run_async(
        user_id=f"eval_{case_id}",
        session_id=session.id,
        new_message=message,
    ):
        raw_events.append(event)

    # Build the trace events list.
    trace_events: list[dict[str, Any]] = []

    # 1. User message event
    trace_events.append(
        _build_event_dict("user", prompt_text),
    )

    # 2. Agent events
    final_output_text = ""
    interrupt_detected = False
    is_security_event = _detect_security_event(raw_events)

    for event in raw_events:
        node = _node_path(event)
        author = event.author or _APP_NAME
        output = _event_output(event)
        output_str = _serialize_output(output)
        route = _event_route(event)

        # Build a descriptive text for the event
        event_text = ""
        if node:
            event_text += f"[{node}]"
        if route:
            event_text += f" route={route}"
        if output_str:
            event_text += f" output={output_str}"

        # Check for function calls (RequestInput / interrupt)
        fc = _function_call(event)
        fc_dict = None
        if fc:
            interrupt_detected = True
            fc_dict = {
                "name": fc.name,
                "id": fc.id,
                "args": fc.args,
            }

        trace_events.append(
            _build_event_dict(author, event_text, function_call=fc_dict),
        )

        # Track the last non-empty output as the final response
        if output_str and not fc:
            final_output_text = output_str

    # 3. If an interrupt was detected, add the automated human decision
    if interrupt_detected:
        if is_security_event:
            decision = "Rejected"
            reason = "Auto-rejected: Security event detected (prompt injection attempt)"
        else:
            decision = "Approved"
            reason = (
                "Auto-approved: Clean expense reviewed by LLM,"
                " no security concerns"
            )

        decision_text = f"[human_approval] Human decision: {decision}. Reason: {reason}"

        # Add the function response event (human decision)
        trace_events.append(
            _build_event_dict(
                "user",
                decision_text,
                function_response={
                    "name": "adk_request_input",
                    "id": "human_review",
                    "response": {"decision": decision, "reason": reason},
                },
            ),
        )

        # Add the final agent event with the decision
        final_output_text = json.dumps({"status": decision, "reasoning": reason})
        final_text = f"[human_approval] Final decision: {decision}. Reason: {reason}"
        trace_events.append(
            _build_event_dict(_APP_NAME, final_text),
        )

    # Build the eval case trace in EvaluationDataset format
    eval_case_trace = {
        "eval_case_id": case_id,
        "prompt": prompt,
        "agent_data": {
            "turns": [
                {
                    "turn_index": 0,
                    "turn_id": "turn_0",
                    "events": trace_events,
                }
            ]
        },
        "responses": [
            {
                "response": {
                    "role": "model",
                    "parts": [{"text": final_output_text or "No output"}],
                }
            }
        ],
    }

    logger.info(
        "Case %s complete: %d events, interrupt=%s, security=%s, final=%s",
        case_id,
        len(trace_events),
        interrupt_detected,
        is_security_event,
        final_output_text[:80] if final_output_text else "N/A",
    )

    return eval_case_trace


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    """Generate traces for all eval cases and write to output file."""
    logger.info("Loading dataset from %s", _DATASET_PATH)
    with open(_DATASET_PATH, encoding="utf-8") as f:
        dataset = json.load(f)

    cases = dataset.get("eval_cases", [])
    logger.info("Loaded %d eval case(s)", len(cases))

    # Create a fresh runner and session service for the entire run.
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name=_APP_NAME,
        session_service=session_service,
    )

    # Run each case sequentially (fresh session per case).
    trace_cases: list[dict[str, Any]] = []
    for i, case in enumerate(cases):
        logger.info("Processing case %d/%d", i + 1, len(cases))
        try:
            trace_case = await _run_single_case(runner, session_service, case)
            trace_cases.append(trace_case)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to process case %s", case.get("eval_case_id"))
            # Add a minimal trace with the error
            trace_cases.append({
                "eval_case_id": case.get("eval_case_id", f"case_{i}"),
                "prompt": case.get("prompt", {}),
                "agent_data": {
                    "turns": [
                        {
                            "turn_index": 0,
                            "turn_id": "turn_0",
                            "events": [
                                _build_event_dict(
                                    "user",
                                    case.get("prompt", {})
                                    .get("parts", [{}])[0]
                                    .get("text", ""),
                                ),
                                _build_event_dict(
                                    _APP_NAME,
                                    "ERROR: Case failed during execution",
                                ),
                            ],
                        }
                    ]
                },
                "responses": [
                    {
                        "response": {
                            "role": "model",
                            "parts": [{"text": "ERROR: Case failed during execution"}],
                        }
                    }
                ],
            })

    # Write the output in EvaluationDataset format.
    output = {"eval_cases": trace_cases}
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("Wrote %d traces to %s", len(trace_cases), _OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
