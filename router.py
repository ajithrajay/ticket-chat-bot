"""
router.py — Provider and model routing based on ticket content.

In production, routing is a first-class architectural decision:
- Route cheap, simple tickets to a fast cheap model
- Route sensitive (billing/legal) or complex tickets to a stronger model

This file is fully testable in isolation — no API calls, pure logic.
Write unit tests for route_model() before integrating it with the agent.
"""

from __future__ import annotations
import re
import csv
import time
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────
# Model catalogue — easy to update when prices change
# ─────────────────────────────────────────────
@dataclass
class ModelSpec:
    model_id: str
    provider: str
    input_cost_per_1k:  float   # USD per 1k input tokens
    output_cost_per_1k: float   # USD per 1k output tokens
    max_tokens: int
    description: str


MODEL_CATALOGUE: dict[str, ModelSpec] = {
    "fast": ModelSpec(
        model_id="claude-haiku-4-5-20251001",
        provider="anthropic",
        input_cost_per_1k=0.00025,
        output_cost_per_1k=0.00125,
        max_tokens=1024,
        description="Fast model for simple, low-stakes tickets"
    ),
    "balanced": ModelSpec(
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        max_tokens=2048,
        description="Balanced model for most tickets"
    ),
    "powerful": ModelSpec(
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        max_tokens=4096,
        description="Powerful model for billing, legal, critical escalations"
    ),
}


# ─────────────────────────────────────────────
# Keyword sets for routing
# ─────────────────────────────────────────────
BILLING_LEGAL_PATTERNS = re.compile(
    r"\b(refund|charge|invoice|dispute|fraud|unauthori[sz]ed|legal|law|"
    r"attorney|lawyer|court|credit card|payment|subscription|cancel|chargeback|"
    r"overcharged|double.charged|billing error|money back)\b",
    re.IGNORECASE
)

CRITICAL_PATTERNS = re.compile(
    r"\b(data breach|security|hack|compromised|urgent|immediately|asap|"
    r"critical|outage|down|not working|broken|emergency|sue|lawsuit)\b",
    re.IGNORECASE
)

SIMPLE_PATTERNS = re.compile(
    r"\b(password|how to|tutorial|documentation|feature request|question|"
    r"update|change|username|login|sign in|account settings)\b",
    re.IGNORECASE
)


def route_model(email_text: str) -> ModelSpec:
    """
    Pure function: text in → ModelSpec out.
    No API calls, no side effects. Fully unit-testable.

    Routing priority (highest wins):
      1. Billing/legal keywords   → powerful
      2. Critical/security issues → powerful
      3. Simple how-to questions  → fast
      4. Everything else          → balanced
    """
    text = email_text.lower()

    if BILLING_LEGAL_PATTERNS.search(text):
        return MODEL_CATALOGUE["powerful"]

    if CRITICAL_PATTERNS.search(text):
        return MODEL_CATALOGUE["powerful"]

    if SIMPLE_PATTERNS.search(text) and not CRITICAL_PATTERNS.search(text):
        return MODEL_CATALOGUE["fast"]

    return MODEL_CATALOGUE["balanced"]


# ─────────────────────────────────────────────
# Cost estimation + logging
# ─────────────────────────────────────────────
@dataclass
class RunCost:
    model_spec:     ModelSpec
    input_tokens:   int = 0
    output_tokens:  int = 0
    elapsed_sec:    float = 0.0
    timestamp:      str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def input_cost_usd(self) -> float:
        return (self.input_tokens / 1000) * self.model_spec.input_cost_per_1k

    @property
    def output_cost_usd(self) -> float:
        return (self.output_tokens / 1000) * self.model_spec.output_cost_per_1k

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.output_cost_usd

    def to_dict(self) -> dict:
        return {
            "timestamp":      self.timestamp,
            "model":          self.model_spec.model_id,
            "route":          self.model_spec.description,
            "input_tokens":   self.input_tokens,
            "output_tokens":  self.output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "elapsed_sec":    round(self.elapsed_sec, 2),
        }


COST_LOG_PATH = Path("data/cost_log.csv")
_CSV_HEADERS  = ["timestamp", "model", "route", "input_tokens",
                  "output_tokens", "total_cost_usd", "elapsed_sec"]


def log_cost(run: RunCost) -> None:
    """Append one row to the CSV cost log. Creates the file if needed."""
    write_header = not COST_LOG_PATH.exists()
    COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COST_LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: run.to_dict()[k] for k in _CSV_HEADERS})


def load_cost_summary() -> list[dict]:
    """Read all logged cost rows (for the UI cost panel)."""
    if not COST_LOG_PATH.exists():
        return []
    with open(COST_LOG_PATH) as f:
        return list(csv.DictReader(f))
