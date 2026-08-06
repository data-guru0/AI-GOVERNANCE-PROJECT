"""Pulls real evidence for every checklist.yaml item from its actual source
service/log/file and renders compliance/reports/compliance_report.md.
This is step 7 (Compliance Mapping) — it runs after the fact, makes no
allow/deny decision itself, and never reimplements a check another layer
already does. Run via:
    docker compose run --rm agent python compliance/generate_report.py
"""
import datetime
import json
import os
import subprocess

import requests
import yaml
from jinja2 import Environment, FileSystemLoader
from mlflow.tracking import MlflowClient

from agent import audit_log, om_client
from agent.db import fetch_all

HERE = os.path.dirname(__file__)
KILLSWITCH_LOG_PATH = os.path.join(HERE, "..", "operations", "killswitch.log")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://langfuse:3000")
MODEL_NAME = "governance-agent-model"


def opa_decision_log() -> dict:
    """Reads the tamper-evident audit_log table (agent/audit_log.py) —
    not a plain-text file — and actually verifies the hash chain as part
    of the evidence, not just that entries exist."""
    rows = fetch_all("SELECT decision_id, opa_path, result FROM audit_log ORDER BY id DESC LIMIT 1")
    if not rows:
        return {"satisfied": False, "evidence": "audit_log is empty — run some /chat requests first"}
    chain_ok, chain_msg = audit_log.verify_chain()
    count_row = fetch_all("SELECT count(*) AS n FROM audit_log")
    count = count_row[0]["n"]
    sample = rows[0]
    return {
        "satisfied": True and chain_ok,
        "evidence": f"{count} OPA decision log entries in the tamper-evident audit_log table. "
        f"Hash-chain verification: {chain_msg}. Most recent: path={sample['opa_path']} result={sample['result']}",
    }


def mlflow_model_card() -> dict:
    client = MlflowClient()
    versions = client.get_latest_versions(MODEL_NAME, stages=["Production"])
    if not versions:
        return {"satisfied": False, "evidence": f"no Production version of {MODEL_NAME} — run model-governance/register_model.py"}
    v = versions[0]
    return {
        "satisfied": bool(v.description),
        "evidence": f"{MODEL_NAME} v{v.version} (stage=Production) has a model card:\n{v.description[:300]}...",
    }


def mlflow_risk_tier() -> dict:
    client = MlflowClient()
    versions = client.get_latest_versions(MODEL_NAME, stages=["Production"])
    if not versions:
        return {"satisfied": False, "evidence": f"no Production version of {MODEL_NAME}"}
    tier = versions[0].tags.get("risk_tier")
    return {"satisfied": bool(tier), "evidence": f"{MODEL_NAME} v{versions[0].version} risk_tier tag = '{tier}'"}


def killswitch_log() -> dict:
    if not os.path.exists(KILLSWITCH_LOG_PATH):
        return {"satisfied": False, "evidence": "operations/killswitch.log not found — run operations/kill_switch.py revoke first"}
    with open(KILLSWITCH_LOG_PATH) as f:
        lines = [l.strip() for l in f if l.strip()]
    if not lines:
        return {"satisfied": False, "evidence": "killswitch.log exists but has no entries"}
    return {"satisfied": True, "evidence": f"{len(lines)} kill-switch log entries. Most recent: {lines[-1]}"}


def openmetadata_pii_tags() -> dict:
    try:
        table = om_client.get_by_name(
            "/tables", "governance-demo-source.governance_demo.public.customers", fields=["tags", "columns"]
        )
        if table is None:
            return {"satisfied": False, "evidence": "customers table not found — run data-governance/ingest_and_tag_pii.py first"}
        tagged = [
            c["name"] for c in table.get("columns", []) if any(t["tagFQN"] == "PII.Sensitive" for t in (c.get("tags") or []))
        ]
        return {
            "satisfied": bool(tagged),
            "evidence": f"columns tagged PII.Sensitive in {table['fullyQualifiedName']}: {tagged or 'none'}",
        }
    except Exception as e:
        return {"satisfied": False, "evidence": f"OpenMetadata lookup failed ({e}) — run data-governance/ingest_and_tag_pii.py first"}


def langfuse_traces() -> dict:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return {"satisfied": False, "evidence": "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set in .env"}
    try:
        resp = requests.get(f"{LANGFUSE_HOST}/api/public/traces", auth=(public_key, secret_key), timeout=5)
        resp.raise_for_status()
        data = resp.json()
        count = len(data.get("data", []))
        return {"satisfied": count > 0, "evidence": f"{count} Langfuse traces found via {LANGFUSE_HOST}/api/public/traces"}
    except Exception as e:
        return {"satisfied": False, "evidence": f"Langfuse API call failed ({e}) — send a few /chat requests first"}


def agt_verify_output() -> tuple[dict, str]:
    """Runs the real `agt` CLI (from the agent-governance-toolkit package)
    and pulls its OWASP ASI 2026 grading directly — not reimplemented.
    `--json` is a GLOBAL flag (must come before the subcommand) and there
    is no `--target` option; both verified against the installed CLI's
    real --help output."""
    try:
        result = subprocess.run(
            ["agt", "--json", "verify"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        raw = (result.stdout or "") + (result.stderr or "")
        try:
            parsed = json.loads(result.stdout)
            coverage = parsed.get("coverage_pct", 0)
            passed_ct, total_ct = parsed.get("controls_passed", 0), parsed.get("controls_total", 0)
            satisfied = bool(parsed.get("passed"))
            evidence = f"agt verify: {passed_ct}/{total_ct} OWASP ASI 2026 controls present ({coverage}% coverage)"
            missing = [c["id"] for c in parsed.get("controls", []) if not c.get("present") and "agentmesh" in (c.get("error") or "")]
            if missing:
                evidence += (
                    f" — controls {', '.join(missing)} require the `agentmesh` companion package, which is "
                    "currently just a name-reservation stub on PyPI with no real implementation; this repo "
                    "provides the same guarantees natively (SPIRE identity, hash-chained audit log, OPA "
                    "policy enforcement) rather than installing a non-functional stub to inflate this score."
                )
        except (json.JSONDecodeError, AttributeError):
            satisfied = False
            evidence = "agt verify ran but its output could not be parsed as JSON — see raw output below"
        return {"satisfied": satisfied, "evidence": evidence}, raw or "(no output)"
    except FileNotFoundError:
        raw = "`agt` CLI not found on PATH — is agent-governance-toolkit installed in this image?"
        return {"satisfied": False, "evidence": raw}, raw
    except Exception as e:
        raw = f"agt verify failed: {e}"
        return {"satisfied": False, "evidence": raw}, raw


EVIDENCE_FUNCS = {
    "opa_decision_log": opa_decision_log,
    "mlflow_model_card": mlflow_model_card,
    "killswitch_log": killswitch_log,
    "openmetadata_pii_tags": openmetadata_pii_tags,
    "langfuse_traces": langfuse_traces,
    "mlflow_risk_tier": mlflow_risk_tier,
}


def main():
    with open(os.path.join(HERE, "checklist.yaml")) as f:
        checklist = yaml.safe_load(f)["items"]

    agt_result, agt_raw = agt_verify_output()

    items = []
    for entry in checklist:
        source = entry["evidence_source"]
        result = agt_result if source == "agt_verify_output" else EVIDENCE_FUNCS[source]()
        items.append({**entry, **result})

    env = Environment(loader=FileSystemLoader(HERE))
    template = env.get_template("report_template.md.j2")
    output = template.render(
        generated_at=datetime.datetime.utcnow().isoformat() + "Z",
        items=items,
        agt_verify_raw=agt_raw,
    )

    out_dir = os.path.join(HERE, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "compliance_report.md")
    with open(out_path, "w") as f:
        f.write(output)

    passed = sum(1 for i in items if i["satisfied"])
    print(f"[generate_report] {passed}/{len(items)} checklist items satisfied. Wrote {out_path}")


if __name__ == "__main__":
    main()
