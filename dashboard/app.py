"""Governance dashboard. Five tabs:
  - Live Checklist: send a /chat request, see all 7 governance layers
    pass/fail in real time (which specialist agent handled it, why any
    layer failed — pulled from that layer's real response).
  - Live Database: the real Postgres content, read via the dashboard_viewer
    role (SELECT-only). Shown unmasked on purpose — compare it against a
    masked agent answer to see the masking layer do something real.
  - Audit Log: the tamper-evident hash-chained decision log
    (agent/audit_log.py) — recent entries plus a live integrity check.
  - Policy: grant/revoke a role's access to a tool without touching Rego —
    the non-engineer policy-authoring surface (agent/policy_admin.py).
  - Compliance Report: the latest generated compliance_report.md.

No live "regenerate report" or "run docker compose" button here on purpose
— giving the dashboard container the Docker socket just for convenience
would be a real privilege escalation for a teaching repo. The kill switch
IS wired up directly though, since operations/kill_switch.py only needs an
HTTP call to OPA, no special privileges.
"""
import os
import time

import pandas as pd
import requests
import streamlit as st

from agent import policy_admin
from agent.audit_log import verify_chain
from agent.db import fetch_all, get_engine
from agent.keycloak_auth import get_demo_token
from operations.kill_switch import IDENTITY as AGENT_IDENTITY
from operations.kill_switch import restore as ks_restore
from operations.kill_switch import revoke as ks_revoke

AGENT_URL = os.environ.get("AGENT_URL", "http://agent:8000")
MLFLOW_URL = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "compliance", "reports", "compliance_report.md")
DB_TABLES = ["customers", "products", "orders"]
MODEL_NAME = "governance-agent-model"

# Validated status palette (dataviz skill reference palette — fixed roles,
# never themed, always paired with an icon + label, never color alone).
GOOD, WARNING, CRITICAL, MUTED = "#0ca30c", "#fab219", "#d03b3b", "#898781"
ACCENT = "#2a78d6"

LOGINS = {
    "Student (demo-agent role)": ("student", "student123"),
    "Instructor (admin role)": ("instructor", "instructor123"),
}

LAYER_LABELS = {
    "identity": "Identity",
    "policy": "Policy",
    "data": "Data",
    "model": "Model",
    "agent_guardrails": "Agent / Guardrails",
    "tool_call": "Tool-call",
    "logged": "Logged",
}
LAYER_ORDER = list(LAYER_LABELS)

PRESETS = {
    "Scenario A — Approved request": dict(message="What's the status of order 1?", force_agent=None, force_tool=None, model_version=None),
    "Scenario B — Unauthorized agent handoff": dict(
        message="Delete all data for customer 1", force_agent="admin_agent",
        force_tool={"name": "purge_customer_data", "args": {"customer_id": 1}}, model_version=None,
    ),
    "Scenario C — PII gets masked": dict(
        message="What's the customer's email on order 1?", force_agent="order_agent",
        force_tool={"name": "get_order", "args": {"order_id": 1}}, model_version=None,
    ),
    "Scenario D — Kill-switch (revoke first below, then send)": dict(message="What's the status of order 1?", force_agent=None, force_tool=None, model_version=None),
    "Scenario E — Model stuck in Staging": dict(message="What's the status of order 1?", force_agent=None, force_tool=None, model_version="1"),
    "Custom": dict(message="", force_agent=None, force_tool=None, model_version=None),
}

st.set_page_config(page_title="AI Governance Dashboard", page_icon="🛡️", layout="wide")

st.markdown(
    f"""
    <style>
    .block-container {{ padding-top: 2rem; max-width: 1200px; }}
    .gov-header {{
        display: flex; align-items: center; gap: 14px; margin-bottom: 0.25rem;
    }}
    .gov-header h1 {{ font-size: 1.7rem; margin: 0; }}
    .gov-subtitle {{ color: #52514e; font-size: 0.95rem; margin-bottom: 1.5rem; }}
    .layer-card {{
        border-radius: 10px; padding: 14px 12px; text-align: center;
        border: 1px solid rgba(11,11,11,0.10); background: #fcfcfb;
        box-shadow: 0 1px 2px rgba(11,11,11,0.04);
    }}
    .layer-card.pass {{ border-top: 4px solid {GOOD}; }}
    .layer-card.fail {{ border-top: 4px solid {CRITICAL}; }}
    .layer-card.skip {{ border-top: 4px solid {MUTED}; opacity: 0.6; }}
    .layer-card .layer-name {{ font-weight: 600; font-size: 0.82rem; margin-bottom: 6px; color: #0b0b0b; }}
    .layer-card .layer-icon {{ font-size: 1.6rem; }}
    .badge {{
        display: inline-block; padding: 2px 10px; border-radius: 999px;
        font-size: 0.78rem; font-weight: 600; color: white;
    }}
    .badge.good {{ background: {GOOD}; }}
    .badge.critical {{ background: {CRITICAL}; }}
    .badge.warning {{ background: {WARNING}; color: #0b0b0b; }}
    div[data-testid="stMetricValue"] {{ font-size: 1.3rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """<div class="gov-header"><span style="font-size:2rem">🛡️</span><h1>AI Governance Dashboard</h1></div>
    <div class="gov-subtitle">Live view into every governance layer an agent request actually passes through — not a mockup, every check below is real.</div>""",
    unsafe_allow_html=True,
)

tab_live, tab_db, tab_audit, tab_policy, tab_monitoring, tab_compliance = st.tabs(
    ["🔍 Live Checklist", "🗄️ Live Database", "🔐 Audit Log", "⚙️ Policy", "📈 Model Monitoring", "📋 Compliance Report"]
)

with tab_live:
    col_ks1, col_ks2, col_ks3 = st.columns([2, 1, 1])
    with col_ks1:
        st.caption(f"Agent identity: `{AGENT_IDENTITY}`")
    with col_ks2:
        if st.button("🔴 Revoke (kill switch)", use_container_width=True):
            ks_revoke()
            st.success("Identity revoked — next request will be blocked at the Policy layer.")
    with col_ks3:
        if st.button("🟢 Restore", use_container_width=True):
            ks_restore()
            st.success("Identity restored.")

    st.divider()
    col1, col2 = st.columns([1, 1])
    with col1:
        login_choice = st.selectbox("Log in as", list(LOGINS.keys()))
        st.caption("`role` is derived server-side from this login's Keycloak realm role — it is never a field you can set on the request.")
    with col2:
        preset_name = st.selectbox("Scenario preset", list(PRESETS.keys()))
    preset = PRESETS[preset_name]
    message = st.text_input("Message", value=preset["message"])
    model_version = st.text_input("Force model_version (blank = use Production)", value=preset["model_version"] or "")

    if st.button("Send request", type="primary"):
        payload = {"message": message}
        if preset["force_agent"]:
            payload["force_agent"] = preset["force_agent"]
        if preset["force_tool"]:
            payload["force_tool"] = preset["force_tool"]
        if model_version:
            payload["model_version"] = model_version
        with st.spinner("Walking all 7 governance layers..."):
            try:
                username, password = LOGINS[login_choice]
                token = get_demo_token(username=username, password=password)
                resp = requests.post(f"{AGENT_URL}/chat", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                resp.raise_for_status()
                st.session_state["last_result"] = resp.json()
            except Exception as e:
                st.error(f"Request failed: {e}")

    result = st.session_state.get("last_result")
    if result:
        st.subheader("Result")
        if result["blocked"]:
            st.markdown(f'<span class="badge critical">BLOCKED at layer: {result["blocked_at_layer"]}</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span class="badge good">APPROVED</span>', unsafe_allow_html=True)
            st.write(result["answer"] or "(no answer)")
        st.caption(f"Authenticated role: `{result.get('role', '?')}`")

        st.subheader("7-layer checklist")
        by_layer = {l["layer"]: l for l in result["layer_results"]}
        cols = st.columns(7)
        for i, key in enumerate(LAYER_ORDER):
            layer = by_layer.get(key)
            state = "skip" if (not layer or layer["pass"] is None) else ("pass" if layer["pass"] else "fail")
            icon = {"skip": "⚪", "pass": "✅", "fail": "❌"}[state]
            with cols[i]:
                st.markdown(
                    f'<div class="layer-card {state}"><div class="layer-name">{i+1}. {LAYER_LABELS[key]}</div>'
                    f'<div class="layer-icon">{icon}</div></div>',
                    unsafe_allow_html=True,
                )
                if layer and st.button("details", key=f"details_{key}", use_container_width=True):
                    st.session_state["show_reason"] = key

        if st.session_state.get("show_reason"):
            k = st.session_state["show_reason"]
            layer = by_layer.get(k)
            if layer:
                st.info(f"**{LAYER_LABELS[k]}** — {layer['reason']}")

with tab_db:
    st.subheader("Live database — dashboard_viewer role (read-only, unmasked)")
    st.caption(
        "Reads the real Postgres tables directly, bypassing the agent entirely. "
        "Compare `customers.email` here against a masked agent answer in Live Checklist "
        "to see the Data-governance layer actually redact something, not just claim to."
    )
    if st.button("Refresh", key="refresh_db"):
        st.rerun()
    try:
        engine = get_engine()
        for table in DB_TABLES:
            st.markdown(f"**{table}**")
            df = pd.read_sql(f"SELECT * FROM {table} ORDER BY 1", engine)
            st.dataframe(df, use_container_width=True)
    except Exception as e:
        st.error(f"Could not read the database: {e}")

with tab_audit:
    st.subheader("Tamper-evident audit log")
    st.caption(
        "Every OPA policy decision is hash-chained into Postgres as it happens "
        "(agent/audit_log.py) — each row's hash covers its own content plus the "
        "previous row's hash, so editing or deleting any past row is detectable."
    )
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔎 Verify chain integrity", type="primary"):
            ok, msg = verify_chain()
            st.session_state["audit_verify"] = (ok, msg)
    with col2:
        verify_result = st.session_state.get("audit_verify")
        if verify_result:
            ok, msg = verify_result
            badge = "good" if ok else "critical"
            label = "INTACT" if ok else "TAMPERED"
            st.markdown(f'<span class="badge {badge}">{label}</span>&nbsp; {msg}', unsafe_allow_html=True)

    st.markdown("**Recent decisions**")
    try:
        rows = fetch_all("SELECT id, opa_path, result, decided_at, entry_hash FROM audit_log ORDER BY id DESC LIMIT 50")
        if rows:
            df = pd.DataFrame(rows)
            df["entry_hash"] = df["entry_hash"].str[:16] + "…"
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No decisions logged yet — send a request from the Live Checklist tab first.")
    except Exception as e:
        st.error(f"Could not read audit_log: {e}")

with tab_policy:
    st.subheader("Policy authoring — no Rego required")
    st.caption(
        "Grants/revokes here take effect on the **next** request, live, via OPA's data API — "
        "the same mechanism the kill switch uses. This is what makes governance policy editable "
        "by a compliance/risk reviewer, not just an engineer who can edit policy/policies/*.rego."
    )
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        p_role = st.selectbox("Role", policy_admin.ROLES)
    with col2:
        p_tool = st.selectbox("Tool", policy_admin.TOOLS)
    with col3:
        st.write("")
        st.write("")
        b1, b2, b3 = st.columns(3)
        if b1.button("✅ Grant"):
            policy_admin.grant(p_role, p_tool)
            st.success(f"Granted {p_role} -> {p_tool}")
        if b2.button("🚫 Revoke"):
            policy_admin.revoke(p_role, p_tool)
            st.success(f"Revoked {p_role} -> {p_tool}")
        if b3.button("↩️ Clear"):
            policy_admin.clear(p_role, p_tool)
            st.success(f"Cleared override for {p_role} -> {p_tool} (back to base policy)")

    st.markdown("**Current overrides** (live from OPA)")
    try:
        st.json(policy_admin.current_overrides())
    except Exception as e:
        st.error(f"Could not reach OPA: {e}")

with tab_monitoring:
    st.subheader("Model governance — champion/challenger evaluation")
    st.caption(
        "Every version below actually competed for Production in the last real evaluation run "
        "(model-governance/promote_model.py) — real API calls to OpenAI/Groq, a real OpenAI-judge "
        "quality score, a real prompt-injection safety check, and real readability metrics. Nothing "
        "here is simulated; re-run the gate with the button below to see it happen live."
    )

    if st.button("🔁 Re-run promotion gate now (real API calls, ~30-60s)"):
        with st.spinner("Evaluating every nominated candidate against the real eval set..."):
            try:
                import subprocess
                result = subprocess.run(
                    ["python", "model-governance/promote_model.py"],
                    cwd=os.path.join(os.path.dirname(__file__), ".."),
                    capture_output=True, text=True, timeout=180,
                )
                st.session_state["promote_output"] = result.stdout + result.stderr
            except Exception as e:
                st.session_state["promote_output"] = f"failed to run: {e}"
        st.rerun()

    if st.session_state.get("promote_output"):
        with st.expander("Last re-run output", expanded=False):
            st.code(st.session_state["promote_output"])

    st.markdown("**Current registered versions**")
    try:
        resp = requests.get(
            f"{MLFLOW_URL}/api/2.0/mlflow/model-versions/search",
            params={"filter": f"name='{MODEL_NAME}'"}, timeout=10,
        )
        resp.raise_for_status()
        versions = resp.json().get("model_versions", [])
        rows = []
        for v in sorted(versions, key=lambda v: v["version"]):
            tags = {t["key"]: t["value"] for t in v.get("tags", [])}
            rows.append({
                "version": v["version"], "alias": tags.get("version_alias", "?"),
                "provider": tags.get("provider"), "model_id": tags.get("model_id"),
                "stage": v["current_stage"], "risk_tier": tags.get("risk_tier"),
                "risk_tier_reason": tags.get("risk_tier_reason"),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info(f"No versions registered yet for '{MODEL_NAME}'.")
    except Exception as e:
        st.error(f"Could not reach MLflow: {e}")

    st.markdown("**Evaluation run history** (every real promotion-gate run, most recent first)")
    st.caption(
        "This grows every time the gate re-runs — on startup, on the 6-hour schedule "
        "(RE_EVAL_INTERVAL_SECONDS), or the button above — so a real quality regression over time "
        "would show up here as a trend, not just a single pass/fail moment."
    )
    try:
        resp = requests.post(
            f"{MLFLOW_URL}/api/2.0/mlflow/runs/search",
            json={
                "experiment_ids": ["0"],
                "filter": "tags.mlflow.runName LIKE 'promotion_gate%'",
                "order_by": ["attributes.start_time DESC"],
                "max_results": 20,
            },
            timeout=10,
        )
        resp.raise_for_status()
        runs = resp.json().get("runs", [])
        rows = []
        for r in runs:
            metrics = {m["key"]: m["value"] for m in r.get("data", {}).get("metrics", [])}
            rows.append({
                "run": r["info"]["run_name"],
                "when": pd.to_datetime(r["info"]["start_time"], unit="ms"),
                "coherence (ari_grade_level)": metrics.get("ari_grade_level/v1/mean"),
                "safety (injection_resistance)": metrics.get("injection_resistance/mean"),
                "quality (answer_correctness /5)": metrics.get("answer_correctness/v1/mean"),
                "latency (mean sec)": metrics.get("latency/mean"),
            })
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            if df["run"].nunique() > 0:
                chart_df = df.set_index("when")[["quality (answer_correctness /5)", "coherence (ari_grade_level)"]]
                st.line_chart(chart_df)
        else:
            st.info("No promotion-gate runs logged yet.")
    except Exception as e:
        st.error(f"Could not reach MLflow: {e}")

with tab_compliance:
    st.subheader("Latest compliance report")
    if os.path.exists(REPORT_PATH):
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(REPORT_PATH)))
        st.caption(f"Last generated: {mtime} — to refresh, run: `docker compose run --rm agent python compliance/generate_report.py`")
        with open(REPORT_PATH) as f:
            st.markdown(f.read())
    else:
        st.warning("No report yet. Generate one with: `docker compose run --rm agent python compliance/generate_report.py`")
