"""Data models for the ambient expense agent."""
from pydantic import BaseModel, Field

class ExpenseReport(BaseModel):
    amount: float = Field(..., description="The total amount of the expense in USD")
    submitter: str = Field(..., description="The name or email of the submitter")
    category: str = Field(..., description="The expense category")
    description: str = Field(..., description="A short description of the expense")
    date: str = Field(..., description="The date of the expense")

class RiskAssessment(BaseModel):
    risk_level: str = Field(..., description="The assessed risk level (e.g., LOW, MEDIUM, HIGH)")
    reasoning: str = Field(..., description="The reasoning behind the risk assessment")
