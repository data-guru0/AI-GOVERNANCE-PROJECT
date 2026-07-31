"""Scenario D — trigger the kill switch, then show the agent's very next
request is blocked at the policy layer, before any LLM call is made.
Run: docker compose run --rm agent python demos/scenario_d_killswitch.py
"""
from operations.kill_switch import revoke, restore
from demos._common import chat, print_result

if __name__ == "__main__":
    print("--- revoking agent identity ---")
    revoke()

    print("\n--- next request ---")
    result = chat(message="What's the status of order 1?")
    print_result(result)
    assert result["blocked"] is True, "expected the request to be blocked after revocation"
    assert result["blocked_at_layer"] == "policy", f"expected block at policy, got {result['blocked_at_layer']}"
    print("PASS: revoked identity was blocked at the policy layer")

    print("\n--- restoring agent identity for the next demo ---")
    restore()
