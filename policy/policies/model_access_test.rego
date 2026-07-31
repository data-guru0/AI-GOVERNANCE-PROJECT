package governance.model_access

test_demo_agent_allowed_medium_risk {
    allow with input as {"role": "demo-agent", "risk_tier": "medium"}
}

test_demo_agent_allowed_low_risk {
    allow with input as {"role": "demo-agent", "risk_tier": "low"}
}

test_demo_agent_denied_high_risk {
    not allow with input as {"role": "demo-agent", "risk_tier": "high"}
}

test_admin_allowed_high_risk {
    allow with input as {"role": "admin", "risk_tier": "high"}
}

test_denied_high_risk_has_reason {
    r := reason with input as {"role": "demo-agent", "risk_tier": "high"}
    contains(r, "not cleared")
}
