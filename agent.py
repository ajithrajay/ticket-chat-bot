"""
agent.py — The core tool-calling loop.

This is the most important file in the project. Study it until you can
explain every line. The pattern here is identical to what LangChain,
LangGraph, and every other framework implement under the hood — they just
add abstractions on top of this while loop.
"""

from __future__ import annotations
import json
import time
import os
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import ValidationError

from src.schema import TicketAnalysis
from src.tools import ALL_TOOLS, execute_tool
from src.router import RunCost, log_cost, route_model
from src.prompts import SYSTEM_PROMPT, build_user_prompt


# ─────────────────────────────────────────────
# Anthropic client (reads ANTHROPIC_API_KEY from env)
# ─────────────────────────────────────────────
def _make_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)


# ─────────────────────────────────────────────
# Result dataclass returned to callers
# ─────────────────────────────────────────────
@dataclass
class AgentResult:
    success: bool
    analysis: TicketAnalysis | None = None
    error: str | None = None
    turns_taken: int = 0
    tool_calls_made: list[dict] = field(default_factory=list)
    run_cost: RunCost | None = None
    raw_messages: list[dict] = field(default_factory=list)

    def to_display_dict(self) -> dict:
        """Flat dict suitable for UI display."""
        base = {
            "success":       self.success,
            "turns_taken":   self.turns_taken,
            "tools_called":  [t["name"] for t in self.tool_calls_made],
        }
        if self.analysis:
            base.update(self.analysis.to_display_dict())
        if self.run_cost:
            base["model_used"]      = self.run_cost.model_spec.model_id
            base["total_tokens"]    = self.run_cost.input_tokens + self.run_cost.output_tokens
            base["cost_usd"]        = round(self.run_cost.total_cost_usd, 5)
            base["elapsed_sec"]     = round(self.run_cost.elapsed_sec, 2)
        if self.error:
            base["error"] = self.error
        return base


# ─────────────────────────────────────────────
# Retry wrapper for JSON parse + Pydantic validation
# ─────────────────────────────────────────────
MAX_JSON_RETRIES = 2

def _extract_text_content(content_blocks: list) -> str:
    """Pull plain text out of a list of Anthropic content blocks."""
    return " ".join(
        block.text for block in content_blocks
        if hasattr(block, "text")
    )


# ─────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────
def run_agent(email_text: str, max_turns: int = 10) -> AgentResult:
    """
    Run the tool-calling loop for one email.

    The loop:
      1. Send current message history to Claude
      2. Check stop_reason:
         - "tool_use"  → execute every tool the model requested, append results, loop again
         - "end_turn"  → model is done, extract the final TicketAnalysis and return
      3. Enforce hard limits: max_turns, and track cost

    Returns an AgentResult whether it succeeds or fails — callers don't have to
    handle exceptions for normal operational failures.
    """
    client        = _make_client()
    model_spec    = route_model(email_text)
    start_time    = time.time()
    tool_log: list[dict] = []

    # Message history — we grow this on each turn
    messages: list[dict] = [
        {"role": "user", "content": build_user_prompt(email_text)}
    ]

    total_input_tokens  = 0
    total_output_tokens = 0

    for turn in range(max_turns):
        # ── API call ──────────────────────────────────────────────────
        try:
            response = client.messages.create(
                model=model_spec.model_id,
                max_tokens=model_spec.max_tokens,
                system=SYSTEM_PROMPT,
                tools=ALL_TOOLS,
                messages=messages,
            )
        except anthropic.RateLimitError as e:
            return AgentResult(
                success=False,
                error=f"Rate limit hit on turn {turn+1}: {e}",
                turns_taken=turn + 1,
                tool_calls_made=tool_log,
            )
        except anthropic.APIError as e:
            return AgentResult(
                success=False,
                error=f"API error on turn {turn+1}: {e}",
                turns_taken=turn + 1,
                tool_calls_made=tool_log,
            )

        # Track token usage
        total_input_tokens  += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        # Append assistant response to history
        messages.append({
            "role":    "assistant",
            "content": response.content
        })

        # ── End of conversation ───────────────────────────────────────
        if response.stop_reason == "end_turn":
            # Build cost record
            run_cost = RunCost(
                model_spec=model_spec,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                elapsed_sec=time.time() - start_time,
            )
            log_cost(run_cost)

            # Try to extract a TicketAnalysis from the text blocks
            # (the model may have structured its final answer as prose or JSON)
            analysis, parse_error = _try_parse_analysis(response.content)

            return AgentResult(
                success=analysis is not None,
                analysis=analysis,
                error=parse_error,
                turns_taken=turn + 1,
                tool_calls_made=tool_log,
                run_cost=run_cost,
                raw_messages=messages,
            )

        # ── Tool calls requested ──────────────────────────────────────
        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name  = block.name
                tool_input = block.input

                # Log the call
                tool_log.append({"name": tool_name, "input": tool_input})

                # Execute and get JSON string result
                result_str = execute_tool(tool_name, tool_input)

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_str,
                })

            # Append all tool results in one user message
            messages.append({
                "role":    "user",
                "content": tool_results
            })
            continue  # next turn

    # ── Exceeded max_turns ────────────────────────────────────────────
    return AgentResult(
        success=False,
        error=f"Agent exceeded {max_turns} turns without finishing",
        turns_taken=max_turns,
        tool_calls_made=tool_log,
    )


# ─────────────────────────────────────────────
# Parse TicketAnalysis from model output
# ─────────────────────────────────────────────
def _try_parse_analysis(
    content_blocks: list[Any]
) -> tuple[TicketAnalysis | None, str | None]:
    """
    The model's final response after end_turn may include a structured
    JSON block in its text. We try several extraction strategies.

    Returns (TicketAnalysis, None) on success or (None, error_str) on failure.
    """
    # Strategy 1: look for a tool_use block named anything — the model
    # sometimes embeds the analysis as a final tool call to save_ticket.
    # We already persisted it; reconstruct from tool_log is done by caller.

    # Strategy 2: extract JSON from the text content
    full_text = _extract_text_content(content_blocks)

    # The model often wraps JSON in ```json ... ``` fences
    import re
    json_matches = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL)
    if not json_matches:
        # Try bare JSON objects
        json_matches = re.findall(r"(\{[^{}]*\"category\"[^{}]*\})", full_text, re.DOTALL)

    for raw_json in json_matches:
        try:
            data     = json.loads(raw_json)
            analysis = TicketAnalysis.model_validate(data)
            return analysis, None
        except (json.JSONDecodeError, ValidationError):
            continue

    # Strategy 3: the agent already called save_ticket and the conversation
    # is complete — we can reconstruct the analysis from the save_ticket call
    # in the tool log (caller handles this if analysis is None but success is needed)
    return None, "Could not parse TicketAnalysis from model output"
