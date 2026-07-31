"""Operations governance kill switch. Revokes (or restores) the agent's
SPIFFE identity by writing directly into OPA's data document — the same
document policy/policies/kill_switch.rego reads on every /chat request
(OPA Call 3), so the very next request after `revoke` is blocked for real,
not just "logged as if" it were blocked.

Usage:
    docker compose run --rm agent python operations/kill_switch.py revoke
    docker compose run --rm agent python operations/kill_switch.py restore

Every run appends a line to operations/killswitch.log — this file is the
evidence for Scenario D and for the NIST AI RMF (Manage) / EU AI Act
(human oversight) compliance checklist items.
"""
import datetime
import os
import sys
import urllib.parse

import requests

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")
IDENTITY = os.environ.get("AGENT_SPIFFE_ID", "spiffe://governance.demo/agent/demo-agent")
LOG_PATH = os.path.join(os.path.dirname(__file__), "killswitch.log")


def _data_url() -> str:
    encoded = urllib.parse.quote(IDENTITY, safe="")
    return f"{OPA_URL}/v1/data/governance/kill_switch/revoked_identities/{encoded}"


def revoke():
    resp = requests.put(_data_url(), json=True, timeout=5)
    resp.raise_for_status()
    _log("REVOKE", "agent identity revoked — next /chat request will be blocked at the 'policy' layer")


def restore():
    resp = requests.delete(_data_url(), timeout=5)
    if resp.status_code not in (200, 204, 404):
        resp.raise_for_status()
    _log("RESTORE", "agent identity restored — /chat requests will pass the 'policy' layer again")


def _log(action: str, note: str):
    line = f"{datetime.datetime.utcnow().isoformat()}Z action={action} identity={IDENTITY} note=\"{note}\"\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)
    print(f"[kill_switch] {line.strip()}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("revoke", "restore"):
        print("usage: kill_switch.py [revoke|restore]")
        sys.exit(1)
    {"revoke": revoke, "restore": restore}[sys.argv[1]]()
