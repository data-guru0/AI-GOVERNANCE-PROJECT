"""Scenario E — request pinned to MLflow model registry version 1
(v1_llama_staging, stage=Staging). Blocked at the model layer before any
LLM call, because only a Production-stage version may serve traffic.
Run: docker compose run --rm agent python demos/scenario_e_model_stage_gate.py
(run model-governance/register_model.py first so version 1 exists)
"""
from demos._common import chat, print_result

if __name__ == "__main__":
    result = chat(message="What's the status of order 1?", model_version="1")
    print_result(result)
    assert result["blocked"] is True, "expected the Staging-only model version to be blocked"
    assert result["blocked_at_layer"] == "model", f"expected block at model, got {result['blocked_at_layer']}"
    print("PASS: request pinned to a Staging-only model version was blocked")
