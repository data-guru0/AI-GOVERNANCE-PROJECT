package governance.model_access

# OPA Call (Model governance layer): risk-based access, not just role-based.
# This is the actual mechanism behind NIST AI RMF / EU AI Act "risk-tiered"
# governance — a role that's otherwise fully authorized can still be denied
# a specific model version because of that version's risk_tier, independent
# of the registry-stage gate (a Staging-stage model is already blocked
# regardless of risk; this is a SEPARATE check so a role can also be
# blocked from a Production-stage model whose risk_tier is above what
# that role is cleared for).
#
# input:  {"role": "demo-agent", "risk_tier": "high"}
# output: {"allow": false, "reason": "role 'demo-agent' is not cleared..."}

allowed_risk_tiers := {
    "demo-agent": {"low", "medium"},
    "admin": {"low", "medium", "high"},
}

default allow := false

allow {
    allowed_risk_tiers[input.role][input.risk_tier]
}

default reason := ""

reason := sprintf("role '%s' is not cleared to use a model with risk_tier '%s'", [input.role, input.risk_tier]) {
    not allow
}
