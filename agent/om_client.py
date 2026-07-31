"""Plain-REST OpenMetadata client — no SDK. The official openmetadata-
ingestion SDK was tried first but hard-conflicts with the `spiffe` package
on protobuf version (confirmed via a real ImportError: the SDK pulls in
google-cloud-secret-manager, which pins protobuf<5, while spiffe's
generated code needs protobuf>=6.31).

Authentication: real, live-tested `POST /api/v1/users/login` with
`{"email":..., "password": base64(password)}` — this endpoint is NOT
implemented anywhere in the openmetadata-ingestion SDK's Python client
(which only supports pre-issued JWTs), which earlier led to a wrong
conclusion that no scriptable login existed. Confirmed by actually calling
it against a live server: it's a real endpoint, just undocumented in the
SDK. So this is fully automatic — no manual bot-token generation via the
UI needed, using the default admin account
(OPENMETADATA_ADMIN_EMAIL/OPENMETADATA_ADMIN_PASSWORD, defaulting to the
server's own default admin/admin). Tokens expire in ~1 hour; cached with a
safety margin and refreshed automatically rather than on every call.
"""
import base64
import os
import time

import requests

BASE_URL = os.environ.get("OPENMETADATA_URL", "http://openmetadata-server:8585/api").rstrip("/") + "/v1"
ADMIN_EMAIL = os.environ.get("OPENMETADATA_ADMIN_EMAIL", "admin@open-metadata.org")
ADMIN_PASSWORD = os.environ.get("OPENMETADATA_ADMIN_PASSWORD", "admin")

_token_cache: dict = {}


def _login() -> str:
    b64_password = base64.b64encode(ADMIN_PASSWORD.encode()).decode()
    resp = requests.post(
        f"{BASE_URL}/users/login", json={"email": ADMIN_EMAIL, "password": b64_password}, timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["accessToken"]
    _token_cache["expires_at"] = time.time() + 3000  # real token TTL is ~3600s; refresh with a 600s safety margin
    return _token_cache["token"]


def _get_token() -> str:
    if "token" not in _token_cache or time.time() >= _token_cache["expires_at"]:
        return _login()
    return _token_cache["token"]


def _headers(content_type: str = "application/json") -> dict:
    return {"Content-Type": content_type, "Authorization": f"Bearer {_get_token()}"}


def health_check() -> bool:
    try:
        resp = requests.get(f"{BASE_URL}/system/version", headers=_headers(), timeout=5)
        return resp.status_code == 200 and resp.json().get("version") is not None
    except requests.RequestException:
        return False


def create_or_update(suffix: str, payload: dict) -> dict:
    """PUT is OpenMetadata's real create-or-update verb for these
    collection endpoints (verified in the SDK's create_or_update())."""
    resp = requests.put(f"{BASE_URL}{suffix}", json=payload, headers=_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_by_name(suffix: str, fqn: str, fields: list[str] | None = None) -> dict | None:
    params = {"fields": ",".join(fields)} if fields else {}
    resp = requests.get(
        f"{BASE_URL}{suffix}/name/{requests.utils.quote(fqn, safe='')}",
        headers=_headers(),
        params=params,
        timeout=15,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def patch(suffix: str, entity_id: str, patch_ops: list[dict]) -> dict:
    """RFC 6902 JSON Patch — OpenMetadata's real tag/description update
    mechanism (verified: Content-Type must be application/json-patch+json)."""
    resp = requests.patch(
        f"{BASE_URL}{suffix}/{entity_id}",
        json=patch_ops,
        headers=_headers(content_type="application/json-patch+json"),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def add_lineage(from_id: str, to_id: str, entity_type: str = "table") -> None:
    payload = {
        "edge": {
            "fromEntity": {"id": from_id, "type": entity_type},
            "toEntity": {"id": to_id, "type": entity_type},
        }
    }
    resp = requests.put(f"{BASE_URL}/lineage", json=payload, headers=_headers(), timeout=15)
    resp.raise_for_status()
