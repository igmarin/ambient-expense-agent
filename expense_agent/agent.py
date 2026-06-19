import json
import base64
from typing import Any, Dict

from google.adk.workflow import Workflow, node
from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context

from .config import THRESHOLD_USD, REVIEW_MODEL
from .models import ExpenseReport, RiskAssessment

from google.genai import types

@node
def parse_event(node_input: Any) -> ExpenseReport:
    """Parse the incoming JSON event into an ExpenseReport."""
    # Handle the input appropriately based on its type
    data = node_input
    
    # ADK 2.0 START node emits Content objects by default
    if isinstance(data, types.Content):
        data = data.parts[0].text
        
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass

    if isinstance(data, dict) and "data" in data:
        raw_data = data["data"]
        # Handle base64 encoded Pub/Sub messages
        if isinstance(raw_data, str):
            try:
                # Try decoding base64
                decoded = base64.b64decode(raw_data).decode('utf-8')
                data = json.loads(decoded)
            except Exception:
                # Fallback to plain JSON string parsing
                try:
                    data = json.loads(raw_data)
                except Exception:
                    pass
        elif isinstance(raw_data, dict):
            data = raw_data

    # Return the Pydantic model (it handles auto conversion if dict is passed)
    return ExpenseReport(**data)

@node
def route_expense(node_input: ExpenseReport) -> Event:
    """Route the expense based on the threshold."""
    if node_input.amount < THRESHOLD_USD:
        return Event(output=node_input, route="auto_approve")
    else:
        # Route to LLM review for higher amounts
        return Event(output=node_input, route="llm_review")

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

@node(rerun_on_resume=False)
def human_approval(ctx: Context, node_input: RiskAssessment):
    """Pause workflow for human approval if an alert is raised."""
    interrupt_id = "human_review"
    
    if not ctx.resume_inputs or interrupt_id not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id=interrupt_id, 
            message=f"Expense flagged for review. Risk level: {node_input.risk_level}. Reasoning: {node_input.reasoning}. Approve or Reject?"
        )
        return

    # Once human provides input, record and emit final decision
    decision = ctx.resume_inputs[interrupt_id]
    yield Event(output={"status": decision, "reasoning": node_input.reasoning})

@node
def auto_approver(node_input: ExpenseReport) -> Event:
    """Auto approve low-dollar expenses."""
    return Event(output={"status": "Approved", "reason": "Amount below threshold. Auto-approved."})

from google.adk.workflow import Edge, START

root_agent = Workflow(
    name="ambient_expense_agent",
    edges=[
        Edge(from_node=START, to_node=parse_event),
        Edge(from_node=parse_event, to_node=route_expense),
        Edge(from_node=route_expense, to_node=auto_approver, route="auto_approve"),
        Edge(from_node=route_expense, to_node=llm_reviewer, route="llm_review"),
        Edge(from_node=llm_reviewer, to_node=human_approval),
    ]
)
