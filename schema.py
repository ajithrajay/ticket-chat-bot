"""
schema.py — Pydantic v2 models for structured LLM output.

Why Pydantic here?
  The LLM is forced to return JSON that matches TicketAnalysis.
  If the model hallucinates a field or puts the wrong type,
  model_validate() raises a ValidationError immediately —
  we catch it, log it, and retry. This is your quality gate.
"""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class TicketAnalysis(BaseModel):
    """
    The structured output schema we force the LLM to produce.
    Every field has a clear type constraint — Pydantic enforces these
    at parse time, not at usage time. Fail fast, fail clearly.
    """

    category: Literal["billing", "technical", "shipping", "general"] = Field(
        description="Primary category of the support request"
    )

    urgency: Literal["low", "medium", "high", "critical"] = Field(
        description="How urgently this needs a response"
    )

    sentiment: float = Field(
        ge=-1.0, le=1.0,
        description="Customer sentiment: -1.0 (very negative) to 1.0 (very positive)"
    )

    summary: str = Field(
        max_length=150,
        description="One-sentence summary of the customer's issue, max 150 chars"
    )

    action_required: bool = Field(
        description="True if a human agent must take an action (refund, escalate, etc.)"
    )

    suggested_reply: str = Field(
        description="A professional, empathetic reply to send the customer"
    )

    # Optional: the model may or may not fill these
    detected_order_id: Optional[int] = Field(
        default=None,
        description="Order ID if the customer mentioned one, else null"
    )

    detected_email: Optional[str] = Field(
        default=None,
        description="Customer email address if found in the message, else null"
    )

    @field_validator("summary")
    @classmethod
    def summary_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("summary cannot be empty")
        return v.strip()

    @field_validator("suggested_reply")
    @classmethod
    def reply_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("suggested_reply cannot be empty")
        return v.strip()

    def urgency_score(self) -> int:
        """Numeric urgency for sorting/routing: critical=4, low=1"""
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.urgency]

    def to_display_dict(self) -> dict:
        """Clean dict for UI display — removes None fields."""
        d = self.model_dump()
        return {k: v for k, v in d.items() if v is not None}


class CustomerRecord(BaseModel):
    """Returned by the get_customer tool."""
    id: int
    name: str
    email: str
    tier: Literal["free", "pro", "enterprise"]
    join_date: str
    total_orders: int
    total_spent: float


class OrderRecord(BaseModel):
    """Returned by the get_order_status tool."""
    id: int
    customer_id: int
    product: str
    amount: float
    status: str
    created_at: str


class ToolError(BaseModel):
    """Structured error response from any tool."""
    error: str
    tool_name: str
    detail: Optional[str] = None
