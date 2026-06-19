"""Ambient expense-approval workflow with PII scrubbing and security checks."""

import base64
import json
import re
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import START, Edge, Workflow, node
from google.genai import types

from .config import REVIEW_MODEL, THRESHOLD_USD
from .models import ExpenseReport, RiskAssessment


@node
def parse_event(node_input: Any) -> ExpenseReport:
    """Parse the incoming JSON event into an ExpenseReport."""
    data = node_input

    # ADK 2.0 START node emits Content objects by default
    if isinstance(data, types.Content) and data.parts:
        data = data.parts[0].text

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass

    if isinstance(data, dict) and "data" in data:
        raw_data = data["data"]
        # Handle base64-encoded Pub/Sub messages
        if isinstance(raw_data, str):
            try:
                decoded = base64.b64decode(raw_data).decode("utf-8")
                data = json.loads(decoded)
            except (ValueError, OSError):
                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError:
                    pass
        elif isinstance(raw_data, dict):
            data = raw_data

    return ExpenseReport.model_validate(data)


@node
def route_expense(node_input: ExpenseReport) -> Event:
    """Route the expense based on the threshold."""
    if node_input.amount < THRESHOLD_USD:
        return Event(output=node_input, route="auto_approve")
    return Event(output=node_input, route="security_check")


INJECTION_KEYWORDS = [
    "ignore previous instructions",
    "bypass",
    "auto-approve",
    "system prompt",
    "you must approve",
]


@node
def security_checkpoint(node_input: ExpenseReport) -> Event:
    """Scrub PII and detect prompt injection."""
    desc = node_input.description

    # 1. Scrub PII
    desc = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN]", desc)
    desc = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CC]", desc)
    node_input.description = desc

    # 2. Prompt injection heuristics
    lower_desc = desc.lower()
    for keyword in INJECTION_KEYWORDS:
        if keyword in lower_desc:
            risk = RiskAssessment(
                risk_level="HIGH",
                reasoning=(
                    f"Security Event: Prompt injection attempt detected ('{keyword}')"
                ),
            )
            return Event(output=risk, route="security_event")

    # Clean expense — proceed to LLM review
    return Event(output=node_input, route="clean")


llm_reviewer = LlmAgent(
    name="llm_reviewer",
    model=REVIEW_MODEL,
    instruction=(
        "You are a finance risk assessor. Review the expense details "
        "and determine the risk level of fraud or policy violation. "
        "Assess as LOW, MEDIUM, or HIGH risk, and provide reasoning."
    ),
    output_schema=RiskAssessment,
)


@node
def human_approval(ctx: Context, node_input: RiskAssessment):
    """Pause workflow for human approval if an alert is raised."""
    interrupt_id = "human_review"

    if not ctx.resume_inputs or interrupt_id not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id=interrupt_id,
            message=(
                f"Expense flagged for review. "
                f"Risk level: {node_input.risk_level}. "
                f"Reasoning: {node_input.reasoning}. "
                f"Approve or Reject?"
            ),
        )
        return

    # Once human provides input, record and emit final decision
    decision = ctx.resume_inputs[interrupt_id]
    yield Event(output={"status": decision, "reasoning": node_input.reasoning})


@node
def auto_approver(node_input: ExpenseReport) -> Event:  # pylint: disable=unused-argument
    """Auto approve low-dollar expenses."""
    return Event(
        output={
            "status": "Approved",
            "reason": "Amount below threshold. Auto-approved.",
        }
    )


root_agent = Workflow(
    name="ambient_expense_agent",
    edges=[
        Edge(from_node=START, to_node=parse_event),
        Edge(from_node=parse_event, to_node=route_expense),
        Edge(from_node=route_expense, to_node=auto_approver, route="auto_approve"),
        Edge(
            from_node=route_expense, to_node=security_checkpoint, route="security_check"
        ),
        Edge(from_node=security_checkpoint, to_node=llm_reviewer, route="clean"),
        Edge(
            from_node=security_checkpoint,
            to_node=human_approval,
            route="security_event",
        ),
        Edge(from_node=llm_reviewer, to_node=human_approval),
    ],
)
