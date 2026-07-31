"""Destructive, irreversible operations (GDPR-style erasure). Neither the
`demo-agent` role nor routing to this agent at all is in tool_access.rego's
allow-list for demo-agent — two layered checks (route + tool) have to both
say yes before purge_customer_data ever runs."""
from agent.db import execute


def purge_customer_data(customer_id: int) -> dict:
    rowcount = execute("DELETE FROM customers WHERE customer_id = :cid", {"cid": customer_id})
    return {"status": "purged" if rowcount else "not_found", "customer_id": customer_id}


NAME = "admin_agent"
DESCRIPTION = "Destructive admin operations (data purges). Admin-only."
TOOLS = {"purge_customer_data": purge_customer_data}
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "purge_customer_data",
            "description": "Permanently delete a customer and their orders. Destructive and irreversible.",
            "parameters": {"type": "object", "properties": {"customer_id": {"type": "integer"}}, "required": ["customer_id"]},
        },
    }
]
