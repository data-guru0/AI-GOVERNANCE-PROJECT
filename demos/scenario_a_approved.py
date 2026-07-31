"""Scenario A — an approved end-to-end request that passes every layer.
Run: docker compose run --rm agent python demos/scenario_a_approved.py
"""
from demos._common import chat, print_result

if __name__ == "__main__":
    result = chat(message="What's the status of order 1?")
    print_result(result)
    assert result["blocked"] is False, "expected an approved, unblocked request"
    assert all(l["pass"] is not False for l in result["layer_results"]), "expected no layer to fail"
    print("PASS: request went through all 7 layers")
