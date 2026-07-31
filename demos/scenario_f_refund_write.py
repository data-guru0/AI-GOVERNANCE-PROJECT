"""Scenario F — a real database WRITE, not just a read: billing_agent
issues a refund, which is authorized (OPA), screened (AGT), and actually
executes an UPDATE against Postgres. Open the dashboard's Live Database
tab before/after running this to see `orders.status` for order 2 flip
to 'refunded' for real.
Run: docker compose run --rm agent python demos/scenario_f_refund_write.py
"""
from demos._common import chat, print_result

if __name__ == "__main__":
    result = chat(
        message="Please refund order 2",
        force_agent="billing_agent",
        force_tool={"name": "issue_refund", "args": {"order_id": 2}},
    )
    print_result(result)
    assert result["blocked"] is False, "demo-agent is allowed to issue refunds — this should succeed"
    tool_layer = next(l for l in result["layer_results"] if l["layer"] == "tool_call")
    assert tool_layer["pass"] is True, f"expected the refund tool call to be authorized, got: {tool_layer['reason']}"
    print("PASS: refund was authorized, screened, and written to Postgres — check the dashboard's Live Database tab")
