"""Every governance hook the agent calls, in one file on purpose (see plan:
no LangChain agent-executor hiding this plumbing). Layers, in call order:
  0. SPIFFE identity fetch (module load / app startup)
  1. Kill-switch check          -> OPA Call 3 (governance.kill_switch)
  2. Guardrails input check     -> local heuristic validator
  3. Data access / PII masking  -> OpenMetadata tags + OPA Call 1 (governance.data_access)
  4. Model governance gate      -> MLflow registry stage check
  5. Tool-call authorization    -> OPA Call 2 (governance.tool_access)
  6. AGT runtime screening      -> agent_os.StatelessKernel (real runtime policy gate)
  7. Guardrails output check    -> local heuristic validator
"""
import os
import re

import requests
from mlflow.tracking import MlflowClient

from agent import om_client
from agent.agents import admin_agent, billing_agent, order_agent

ALL_TOOLS = {**order_agent.TOOLS, **billing_agent.TOOLS, **admin_agent.TOOLS}

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")
SPIFFE_SOCKET = os.environ.get("SPIFFE_ENDPOINT_SOCKET", "unix:///run/spire/sockets/agent.sock")
MODEL_NAME = "governance-agent-model"

# ---------------------------------------------------------------------------
# Layer 0 — Identity (SPIFFE/SPIRE)
# ---------------------------------------------------------------------------
_svid_cache: dict = {}


def fetch_svid(retries: int = 30, delay_seconds: float = 2.0) -> str:
    """Fetches the agent's own X.509-SVID from the SPIRE Workload API.
    Deliberately has NO fallback to a static/hardcoded credential — if every
    retry fails, the caller (main.py startup) must fail loudly, because a
    silent fallback would defeat the entire point of this layer. Retries
    only exist to absorb ordinary docker-compose startup ordering (spire-
    setup minting the join token, spire-agent attesting) — not to paper
    over a real SPIRE outage."""
    if "spiffe_id" in _svid_cache:
        return _svid_cache["spiffe_id"]
    from spiffe.workloadapi.workload_api_client import WorkloadApiClient  # noqa: PLC0415

    last_error = None
    for attempt in range(retries):
        try:
            client = WorkloadApiClient(socket_path=SPIFFE_SOCKET)
            svid = client.fetch_x509_svid()
            spiffe_id = str(svid.spiffe_id)
            _svid_cache["spiffe_id"] = spiffe_id
            return spiffe_id
        except Exception as e:  # pragma: no cover
            last_error = e
            print(f"[governance] SVID fetch attempt {attempt + 1}/{retries} failed: {e}")
            import time  # noqa: PLC0415

            time.sleep(delay_seconds)
    raise RuntimeError(f"could not fetch SPIFFE SVID from {SPIFFE_SOCKET} after {retries} attempts") from last_error


# ---------------------------------------------------------------------------
# OPA — the one shared policy hub, queried from 3 places
# ---------------------------------------------------------------------------
def _opa_query(package_path: str, input_doc: dict) -> dict:
    resp = requests.post(f"{OPA_URL}/v1/data/{package_path}", json={"input": input_doc}, timeout=5)
    resp.raise_for_status()
    return resp.json().get("result", {})


def check_kill_switch(identity: str) -> tuple[bool, str]:
    result = _opa_query("governance/kill_switch", {"identity": identity})
    return bool(result.get("allow", True)), result.get("reason", "")


def check_tool_access(role: str, tool: str, spiffe_id: str) -> tuple[bool, str]:
    result = _opa_query(
        "governance/tool_access",
        {"subject": {"role": role, "spiffe_id": spiffe_id}, "action": "invoke_tool", "tool": tool},
    )
    return bool(result.get("allow", False)), result.get("reason", "")


def check_model_access(role: str, risk_tier: str | None) -> tuple[bool, str]:
    """Risk-based gate (policy/policies/model_access.rego), independent of
    the registry-stage gate below: a role can be denied a specific model
    version because of its risk_tier even when that version is already in
    the Production stage — this is what actually makes 'risk tier' a
    control, not just a label in a report."""
    if not risk_tier:
        return True, ""
    result = _opa_query("governance/model_access", {"role": role, "risk_tier": risk_tier})
    return bool(result.get("allow", False)), result.get("reason", "")


def get_mask_columns(columns_with_tags: list[dict]) -> list[str]:
    result = _opa_query("governance/data_access", {"columns": columns_with_tags, "requester_role": "demo-agent"})
    return result.get("mask_columns", [])


# ---------------------------------------------------------------------------
# Layer 3 — Data governance: real OpenMetadata tags -> real OPA masking decision
# ---------------------------------------------------------------------------
def get_columns_to_mask(table_name: str = "customers") -> list[str]:
    """Looks up the table's real column tags in OpenMetadata, then asks OPA
    which of those tagged columns must be masked. Falls back to an empty
    mask list (nothing hidden) if OpenMetadata is unreachable, so a demo
    doesn't hard-fail just because that one service is still starting up —
    but the tags/masking themselves are always real, never simulated."""
    try:
        table = om_client.get_by_name(
            "/tables", f"governance-demo-source.governance_demo.public.{table_name}", fields=["tags", "columns"]
        )
        if table is None:
            return []
        columns_with_tags = [
            {"name": c["name"], "tags": [t["tagFQN"] for t in (c.get("tags") or [])]}
            for c in table.get("columns", [])
        ]
    except Exception as e:  # pragma: no cover
        print(f"[governance] OpenMetadata tag lookup failed ({e}); no columns masked this request")
        return []
    return get_mask_columns(columns_with_tags)


def mask_record(record: dict, mask_columns: list[str]) -> dict:
    masked = dict(record)
    for col in mask_columns:
        if col in masked:
            masked[col] = f"***MASKED ({col}, PII.Sensitive)***"
    return masked


# ---------------------------------------------------------------------------
# Layer 4 — Model governance: MLflow registry stage gate + risk-based OPA gate
# ---------------------------------------------------------------------------
def _model_info(v) -> dict:
    return {
        "pass": True,
        "version": v.version,
        "provider": v.tags.get("provider"),
        "model_id": v.tags.get("model_id"),
        "risk_tier": v.tags.get("risk_tier"),
    }


def check_model_version_gate(role: str, requested_version: str | None) -> dict:
    """Returns {"pass": True, "provider":.., "model_id":.., "version":..,
    "risk_tier":..} or {"pass": False, "reason": "..."}. Two independent
    checks, either can fail this layer: (1) MLflow registry stage — only
    Production may serve traffic (Scenario E); (2) OPA risk-based access —
    the requesting role must be cleared for this version's risk_tier, even
    if it's already in Production (this is the new check)."""
    client = MlflowClient()
    if not requested_version:
        versions = client.get_latest_versions(MODEL_NAME, stages=["Production"])
        if not versions:
            return {"pass": False, "reason": f"no version of {MODEL_NAME} is in stage 'Production'"}
        info = _model_info(versions[0])
    else:
        v = client.get_model_version(MODEL_NAME, requested_version)
        if v.current_stage != "Production":
            return {
                "pass": False,
                "reason": f"model version {requested_version} is in stage '{v.current_stage}', requires 'Production'",
            }
        info = _model_info(v)

    risk_ok, risk_reason = check_model_access(role, info["risk_tier"])
    if not risk_ok:
        return {"pass": False, "reason": risk_reason}
    return info


# ---------------------------------------------------------------------------
# Layer 6 — Guardrails AI input/output checks (local-only validators — no
# extra network calls beyond the real OpenAI/Groq ones, per the "only LLM
# APIs get called" hard requirement)
# ---------------------------------------------------------------------------
from guardrails import Guard  # noqa: E402
from guardrails.validators import FailResult, PassResult, Validator, register_validator  # noqa: E402

INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard your instructions",
    "disregard prior instructions",
    "you are now",
    "reveal your system prompt",
    "reveal your prompt",
]


@register_validator(name="governance/detect-injection", data_type="string")
class DetectInjection(Validator):
    def validate(self, value, metadata):
        lowered = value.lower()
        hit = next((p for p in INJECTION_PATTERNS if p in lowered), None)
        if hit:
            return FailResult(error_message=f"possible prompt-injection pattern detected: '{hit}'")
        return PassResult()


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@register_validator(name="governance/detect-pii-leak", data_type="string")
class DetectPiiLeak(Validator):
    def validate(self, value, metadata):
        if EMAIL_RE.search(value):
            return FailResult(error_message="output still contains an unmasked email address")
        return PassResult()


_input_guard = Guard().use(DetectInjection())
_output_guard = Guard().use(DetectPiiLeak())


def validate_input(text: str) -> tuple[bool, str]:
    try:
        _input_guard.validate(text)
        return True, ""
    except Exception as e:
        return False, str(e)


def validate_output(text: str) -> tuple[bool, str]:
    try:
        _output_guard.validate(text)
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Layer 5/6 — Agent Governance Toolkit: agent_os.StatelessKernel screens
# each tool call AFTER OPA has already authorized it, adding OWASP Agentic
# Top 10 runtime pattern screening on top of plain role-based
# authorization. Verified empirically that StatelessKernel.execute() is a
# policy GATE, not an executor — on an allowed action it returns a canned
# "executed successfully" stub, it never calls real code. So AGT decides
# allow/deny here, and WE run the real tool function ourselves once it
# says yes; the toolkit doesn't (and isn't meant to) know how to invoke
# arbitrary Python.
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402

from agent_os import ExecutionContext, StatelessKernel  # noqa: E402

_AGT_POLICIES = {
    
    "demo_agent_policy": {"blocked_actions": ["purge_customer_data", "search_orders"]},
}
_ROLE_TO_AGT_POLICIES = {"demo-agent": ["demo_agent_policy"], "admin": []}

_kernel = StatelessKernel(policies=_AGT_POLICIES)


def call_tool_governed(role: str, name: str, **kwargs) -> tuple[bool, str, object]:
    """Returns (ok, reason, result). Real AGT policy decision; real tool
    execution only happens if AGT allows it."""
    context = ExecutionContext(agent_id=role, policies=_ROLE_TO_AGT_POLICIES.get(role, []))
    gate = asyncio.run(_kernel.execute(action=name, params=kwargs, context=context))
    if not gate.success:
        return False, gate.error, None
    return True, "", ALL_TOOLS[name](**kwargs)
