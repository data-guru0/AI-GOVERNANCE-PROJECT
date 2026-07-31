import os
import requests

from agent.keycloak_auth import get_demo_token

AGENT_URL = os.environ.get("AGENT_URL", "http://agent:8000")


def chat(as_user: str = "student", as_password: str = "student123", **payload) -> dict:
    """`role` is NOT a request field — it's derived server-side from which
    Keycloak account you log in as (see agent/main.py:require_human_identity).
    `as_user='instructor'` logs in as the account holding the 'admin' realm
    role instead of the default 'student' ('demo-agent' role)."""
    token = get_demo_token(username=as_user, password=as_password)
    resp = requests.post(f"{AGENT_URL}/chat", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def print_result(result: dict) -> None:
    print(f"blocked={result['blocked']} blocked_at_layer={result['blocked_at_layer']} role={result.get('role')}")
    print(f"answer={result['answer']!r}")
    print("layer_results:")
    for layer in result["layer_results"]:
        print(f"  {layer['layer']:16s} pass={layer['pass']!s:5s} {layer['reason']}")
