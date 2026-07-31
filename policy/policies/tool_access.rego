package governance.tool_access

# OPA Call 2 (Agent Governance layer): checked before (a) an orchestrator
# handoff to a specialist agent ("route:<agent>") and (b) any tool that
# specialist wants to execute. Central hub — the same OPA service also
# answers data_access (Call 1) and kill_switch (Call 3) queries.
#
# input:  {"subject": {"role": "demo-agent", "spiffe_id": "spiffe://..."},
#          "action": "invoke_tool", "tool": "purge_customer_data"}
# output: {"allow": false, "reason": "role 'demo-agent' not permitted..."}
#
# demo-agent can reach order_agent and billing_agent (read orders, issue
# refunds — normal support actions) but NOT admin_agent at all — the
# handoff itself is denied, before purge_customer_data is even reachable.
# admin_agent/purge_customer_data are also left off demo-agent's tool
# allow-list as defense in depth, in case a caller ever reaches this check
# by another path.
base_allowed_tools := {
    "demo-agent": {"search_orders", "get_order", "issue_refund", "route:order_agent", "route:billing_agent"},
    "admin": {
        "search_orders", "get_order", "issue_refund", "purge_customer_data",
        "route:order_agent", "route:billing_agent", "route:admin_agent",
    },
}

# Live overrides — this is what makes policy editable by a non-engineer:
# the dashboard's Policy tab PUTs directly into these data paths via OPA's
# data API (same mechanism operations/kill_switch.py already uses, and the
# same direct `data.<path>[...]` reference style kill_switch.rego uses —
# NOT `object.get(data, [...])`, which OPA's static analyzer treats as a
# dependency on the ENTIRE data document regardless of the path argument,
# creating a real rego_recursion_error against this package's own `allow`/
# `reason` rules — caught by `opa test`, not a hypothetical). Deliberately
# a SIBLING data path (`tool_access_overrides`, not nested under this
# package) so there's no rule here that could depend on it circularly.
# Undefined (nothing overridden yet) behaves as "no override", same as
# kill_switch.rego's revoked_identities when empty.
default allow := false

allow {
    base_allowed_tools[input.subject.role][input.tool]
    not data.governance.tool_access_overrides.denied[input.subject.role][input.tool]
}

allow {
    data.governance.tool_access_overrides.added[input.subject.role][input.tool]
    not data.governance.tool_access_overrides.denied[input.subject.role][input.tool]
}

default reason := ""

reason := sprintf("role '%s' not permitted to call tool '%s'", [input.subject.role, input.tool]) {
    not allow
}
