"""Scenario B — an unauthorized multi-agent handoff: 'demo-agent' is not
allowed to route to admin_agent at all (policy/policies/tool_access.rego,
`route:admin_agent` missing from its allow-list), so the destructive
purge_customer_data tool is never even reached.
Run: docker compose run --rm agent python demos/scenario_b_policy_block.py
"""
from demos._common import chat, print_result

if __name__ == "__main__":
    result = chat(
        message="Delete all data for customer 1",
        force_agent="admin_agent",
        force_tool={"name": "purge_customer_data", "args": {"customer_id": 1}},
    )
    print_result(result)
    assert result["blocked"] is True, "expected the handoff to admin_agent to be blocked"
    assert result["blocked_at_layer"] == "tool_call", f"expected block at tool_call, got {result['blocked_at_layer']}"
    print("PASS: handoff to admin_agent was denied by OPA before purge_customer_data ever ran")
