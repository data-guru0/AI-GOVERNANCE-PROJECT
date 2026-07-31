"""Non-engineer policy authoring: thin wrapper over OPA's real data API
(same PUT/DELETE mechanism operations/kill_switch.py uses to revoke an
identity) so the dashboard's Policy tab can grant/revoke a role's access
to a tool WITHOUT touching policy/policies/tool_access.rego or requiring a
redeploy. See that file's `tool_access_overrides` data path — this is the
only thing that writes to it."""
import os

import requests

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")
ROLES = ["demo-agent", "admin"]
TOOLS = ["search_orders", "get_order", "issue_refund", "purge_customer_data", "route:order_agent", "route:billing_agent", "route:admin_agent"]


def _url(kind: str, role: str, tool: str) -> str:
    return f"{OPA_URL}/v1/data/governance/tool_access_overrides/{kind}/{role}/{tool}"


def grant(role: str, tool: str) -> None:
    requests.put(_url("added", role, tool), json=True, timeout=5).raise_for_status()
    requests.delete(_url("denied", role, tool), timeout=5)  # clear any conflicting deny


def revoke(role: str, tool: str) -> None:
    requests.put(_url("denied", role, tool), json=True, timeout=5).raise_for_status()
    requests.delete(_url("added", role, tool), timeout=5)  # clear any conflicting grant


def clear(role: str, tool: str) -> None:
    """Removes any override, reverting to the base Rego allow-list."""
    requests.delete(_url("added", role, tool), timeout=5)
    requests.delete(_url("denied", role, tool), timeout=5)


def current_overrides() -> dict:
    resp = requests.get(f"{OPA_URL}/v1/data/governance/tool_access_overrides", timeout=5)
    resp.raise_for_status()
    return resp.json().get("result", {})
