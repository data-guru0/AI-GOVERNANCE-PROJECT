package governance.tool_access

test_demo_agent_can_read_orders {
    allow with input as {"subject": {"role": "demo-agent"}, "tool": "get_order"}
}

test_demo_agent_can_issue_refund {
    allow with input as {"subject": {"role": "demo-agent"}, "tool": "issue_refund"}
}

test_demo_agent_cannot_purge {
    not allow with input as {"subject": {"role": "demo-agent"}, "tool": "purge_customer_data"}
}

test_demo_agent_cannot_route_to_admin_agent {
    not allow with input as {"subject": {"role": "demo-agent"}, "tool": "route:admin_agent"}
}

test_admin_can_purge {
    allow with input as {"subject": {"role": "admin"}, "tool": "purge_customer_data"}
}

test_unknown_role_denied_with_reason {
    not allow with input as {"subject": {"role": "nobody"}, "tool": "get_order"}
    reason != "" with input as {"subject": {"role": "nobody"}, "tool": "get_order"}
}
