"""Read-only order lookups. The specialist agent OPA authorizes for the
`demo-agent` role by default (see policy/policies/tool_access.rego)."""
from agent.db import fetch_all, fetch_one

_SELECT = """
    SELECT o.order_id, c.name AS customer_name, c.email AS email, p.name AS product, o.status
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    JOIN products p ON p.product_id = o.product_id
"""


def search_orders(query: str) -> list[dict]:
    sql = _SELECT + " WHERE CAST(o.order_id AS TEXT) ILIKE :q OR p.name ILIKE :q OR o.status ILIKE :q"
    return fetch_all(sql, {"q": f"%{query}%"})


def get_order(order_id: int) -> dict:
    return fetch_one(_SELECT + " WHERE o.order_id = :oid", {"oid": order_id}) or {}


NAME = "order_agent"
DESCRIPTION = "Looks up orders (read-only)."
TOOLS = {"search_orders": search_orders, "get_order": get_order}
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_orders",
            "description": "Search orders by id, product name, or status keyword.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Look up a single order (includes customer name/email) by numeric order id.",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "integer"}}, "required": ["order_id"]},
        },
    },
]
