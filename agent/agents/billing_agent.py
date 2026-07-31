"""Issues real refunds — a genuine database WRITE, not a read-only lookup.
Still OPA-authorized and AGT-screened before it runs, same as every other
tool; the point is that "allowed to write" and "governed on every write"
are not mutually exclusive."""
from agent.db import execute, fetch_one


def issue_refund(order_id: int) -> dict:
    order = fetch_one("SELECT order_id, status FROM orders WHERE order_id = :oid", {"oid": order_id})
    if not order:
        return {"status": "error", "reason": f"order {order_id} not found"}
    execute("UPDATE orders SET status = 'refunded' WHERE order_id = :oid", {"oid": order_id})
    return {"status": "refunded", "order_id": order_id}


NAME = "billing_agent"
DESCRIPTION = "Issues refunds (writes to the database)."
TOOLS = {"issue_refund": issue_refund}
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": "Issue a refund for an order by marking its status as refunded.",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]},
        },
    }
]
