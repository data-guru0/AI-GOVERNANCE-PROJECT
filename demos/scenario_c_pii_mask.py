"""Scenario C — a question that hits the PII.Sensitive-tagged
`customers.email` column: the request is NOT blocked, but the value is
masked before it reaches the model (policy/policies/data_access.rego +
OpenMetadata tags).
Run: docker compose run --rm agent python demos/scenario_c_pii_mask.py
(run data-governance/ingest_and_tag_pii.py first so the tag exists)
"""
from demos._common import chat, print_result

if __name__ == "__main__":
    result = chat(
        message="What's the customer's email on order 1?",
        force_agent="order_agent",
        force_tool={"name": "get_order", "args": {"order_id": 1}},
    )
    print_result(result)
    assert result["blocked"] is False, "PII masking should NOT block the request, only redact the field"
    data_layer = next(l for l in result["layer_results"] if l["layer"] == "data")
    assert "email" in data_layer["reason"], f"expected 'email' in the masked-columns list, got: {data_layer['reason']}"
    print("PASS: request completed with the email column masked, not blocked")
