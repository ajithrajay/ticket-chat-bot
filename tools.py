"""
tools.py — Tool definitions (JSON schemas) + Python implementations.

Architecture rule: the JSON schema definition and the Python function
that executes it live TOGETHER in this file. If you move one without
the other, they will drift out of sync. The dispatcher at the bottom
is the single execution point — nothing else in the codebase calls
these functions directly.
"""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.schema import CustomerRecord, OrderRecord, ToolError

# ─────────────────────────────────────────────
# DB path (relative to project root)
# ─────────────────────────────────────────────
DB_PATH = Path("data/tickets.db")


def _get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. Run: python data/seed.py"
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
# Tool 1: get_customer
# ─────────────────────────────────────────────
TOOL_GET_CUSTOMER = {
    "name": "get_customer",
    "description": (
        "Look up a customer by their email address. Returns name, tier, "
        "join date, total orders, and total spend. Use this whenever the "
        "customer's email appears in their message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "The customer's email address"
            }
        },
        "required": ["email"]
    }
}


def get_customer(email: str) -> dict:
    """
    Fetch customer + aggregate order stats from SQLite.
    Returns a dict matching CustomerRecord schema, or an error dict.
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, name, email, tier, join_date FROM customers WHERE email = ?",
            (email.strip().lower(),)
        ).fetchone()

        if not row:
            return ToolError(
                error="Customer not found",
                tool_name="get_customer",
                detail=f"No customer with email: {email}"
            ).model_dump()

        # Aggregate order stats in a single query
        stats = conn.execute(
            """SELECT COUNT(*) as total_orders, COALESCE(SUM(amount), 0) as total_spent
               FROM orders WHERE customer_id = ?""",
            (row["id"],)
        ).fetchone()

        conn.close()

        record = CustomerRecord(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            tier=row["tier"],
            join_date=row["join_date"],
            total_orders=stats["total_orders"],
            total_spent=round(stats["total_spent"], 2)
        )
        return record.model_dump()

    except Exception as e:
        return ToolError(
            error=str(e),
            tool_name="get_customer"
        ).model_dump()


# ─────────────────────────────────────────────
# Tool 2: get_order_status
# ─────────────────────────────────────────────
TOOL_GET_ORDER_STATUS = {
    "name": "get_order_status",
    "description": (
        "Retrieve details and current status for a specific order by its ID. "
        "Use this when the customer references an order number."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "integer",
                "description": "The numeric order ID"
            }
        },
        "required": ["order_id"]
    }
}


def get_order_status(order_id: int) -> dict:
    """
    Fetch a single order row. Returns OrderRecord or ToolError.
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT id, customer_id, product, amount, status, created_at "
            "FROM orders WHERE id = ?",
            (int(order_id),)
        ).fetchone()
        conn.close()

        if not row:
            return ToolError(
                error="Order not found",
                tool_name="get_order_status",
                detail=f"No order with ID: {order_id}"
            ).model_dump()

        return OrderRecord(
            id=row["id"],
            customer_id=row["customer_id"],
            product=row["product"],
            amount=row["amount"],
            status=row["status"],
            created_at=row["created_at"]
        ).model_dump()

    except Exception as e:
        return ToolError(error=str(e), tool_name="get_order_status").model_dump()


# ─────────────────────────────────────────────
# Tool 3: save_ticket
# ─────────────────────────────────────────────
TOOL_SAVE_TICKET = {
    "name": "save_ticket",
    "description": (
        "Save the analysed ticket to the database. Call this AFTER you have "
        "finished analysing the email and generated the reply. Pass all the "
        "analysis fields you have determined."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id":     {"type": "integer", "description": "Customer ID if found, else null"},
            "category":        {"type": "string"},
            "urgency":         {"type": "string"},
            "sentiment":       {"type": "number"},
            "summary":         {"type": "string"},
            "suggested_reply": {"type": "string"},
            "action_required": {"type": "boolean"}
        },
        "required": ["category", "urgency", "summary", "suggested_reply", "action_required"]
    }
}


def save_ticket(
    category: str,
    urgency: str,
    summary: str,
    suggested_reply: str,
    action_required: bool,
    sentiment: float = 0.0,
    customer_id: int | None = None,
    raw_email: str = ""
) -> dict:
    """
    Persist the ticket analysis to SQLite. Returns the new ticket ID.
    """
    try:
        conn = _get_conn()
        cursor = conn.execute(
            """INSERT INTO tickets
               (customer_id, raw_email, category, urgency, sentiment,
                summary, suggested_reply, action_required)
               VALUES (?,?,?,?,?,?,?,?)""",
            (customer_id, raw_email, category, urgency, sentiment,
             summary, suggested_reply, int(action_required))
        )
        ticket_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "ticket_id": ticket_id}

    except Exception as e:
        return ToolError(error=str(e), tool_name="save_ticket").model_dump()


# ─────────────────────────────────────────────
# Tool registry + dispatcher
# ─────────────────────────────────────────────
ALL_TOOLS = [TOOL_GET_CUSTOMER, TOOL_GET_ORDER_STATUS, TOOL_SAVE_TICKET]

TOOL_REGISTRY: dict[str, Any] = {
    "get_customer":    get_customer,
    "get_order_status": get_order_status,
    "save_ticket":     save_ticket,
}


def execute_tool(name: str, args: dict) -> str:
    """
    Single execution point for all tools.
    Returns a JSON string — the model always gets a string back.

    Design decision: we return errors as JSON (not raise exceptions)
    so the model can read the error and decide what to do next.
    """
    if name not in TOOL_REGISTRY:
        result = ToolError(
            error=f"Unknown tool: '{name}'",
            tool_name=name,
            detail=f"Available tools: {list(TOOL_REGISTRY.keys())}"
        ).model_dump()
        return json.dumps(result)

    try:
        result = TOOL_REGISTRY[name](**args)
        return json.dumps(result, default=str)
    except TypeError as e:
        # Wrong arguments passed by the model
        result = ToolError(
            error=f"Invalid arguments: {e}",
            tool_name=name,
            detail=f"Received args: {args}"
        ).model_dump()
        return json.dumps(result)
    except Exception as e:
        result = ToolError(error=str(e), tool_name=name).model_dump()
        return json.dumps(result)
