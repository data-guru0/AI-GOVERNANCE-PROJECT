package governance.kill_switch

test_active_identity_allowed {
    allow with input as {"identity": "spiffe://governance.demo/agent/demo-agent"}
        with data.governance.kill_switch.revoked_identities as {}
}

test_revoked_identity_denied {
    not allow with input as {"identity": "spiffe://governance.demo/agent/demo-agent"}
        with data.governance.kill_switch.revoked_identities as {"spiffe://governance.demo/agent/demo-agent": true}
}

test_revoked_identity_has_reason {
    r := reason with input as {"identity": "spiffe://governance.demo/agent/demo-agent"}
        with data.governance.kill_switch.revoked_identities as {"spiffe://governance.demo/agent/demo-agent": true}
    contains(r, "revoked")
}

test_other_identity_unaffected_by_unrelated_revocation {
    allow with input as {"identity": "spiffe://governance.demo/agent/demo-agent"}
        with data.governance.kill_switch.revoked_identities as {"spiffe://governance.demo/agent/someone-else": true}
}
