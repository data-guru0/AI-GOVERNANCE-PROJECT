"""Real Keycloak JWT enforcement — the gap in the original build: the
Keycloak realm/clients existed but nothing actually checked a token before
serving a request. Now every /chat request must carry a valid Keycloak-
issued Bearer token, verified against Keycloak's own signing keys (JWKS),
not just trusted at face value.
"""
import os

import jwt
import requests

KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080")
REALM = os.environ.get("KEYCLOAK_REALM", "governance-demo")
JWKS_URL = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs"
TOKEN_URL = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token"

_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(JWKS_URL)
    return _jwks_client


def verify_token(token: str) -> dict:
    """Verifies signature + expiry against Keycloak's real JWKS. Raises on
    any failure — callers turn that into an HTTP 401, never a soft pass."""
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(token, signing_key.key, algorithms=["RS256"], options={"verify_aud": False})


def get_demo_token(username: str = "student", password: str = "student123") -> str:
    """Resource-owner-password grant against the public 'dashboard' client
    (see identity/keycloak/realm-export.json) — used by the dashboard and
    demo scripts to obtain a real token before calling /chat."""
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "password", "client_id": "dashboard", "username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]
