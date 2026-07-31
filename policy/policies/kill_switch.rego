package governance.kill_switch

# OPA Call 3 (Operations layer): checked first, before any LLM call.
# operations/kill_switch.py revokes an identity by PUTting it into
# data.governance.kill_switch.revoked_identities (see README/Scenario D).
#
# input:  {"identity": "spiffe://governance.demo/agent/demo-agent"}
# output: {"allow": false, "reason": "identity '...' has been revoked..."}

default allow := true

allow := false {
    data.governance.kill_switch.revoked_identities[input.identity]
}

default reason := ""

reason := sprintf("identity '%s' has been revoked via the kill switch", [input.identity]) {
    not allow
}
