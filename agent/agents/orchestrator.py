"""The orchestrator: one real LLM call that routes an incoming message to
exactly one specialist agent via a forced `handoff` tool call. This is the
multi-agent hop production systems actually use (a supervisor pattern) —
kept as plain functions/dicts rather than a framework so every governance
hook stays visible in main.py, same reasoning as not using LangChain for
the tool-calling loop itself.

Routing itself is governed too: main.py checks OPA for `route:<agent>`
before ever invoking the chosen specialist, so an unauthorized handoff
(e.g. demo-agent -> admin_agent) is blocked before that agent's tools are
even in play — see policy/policies/tool_access.rego.
"""
import json

from agent.agents import admin_agent, billing_agent, order_agent

AGENTS = {
    order_agent.NAME: order_agent,
    billing_agent.NAME: billing_agent,
    admin_agent.NAME: admin_agent,
}

HANDOFF_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "handoff",
            "description": "Route the user's request to the specialist agent best suited to handle it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "enum": list(AGENTS.keys())},
                    "instruction": {"type": "string", "description": "What the specialist agent should do."},
                },
                "required": ["agent", "instruction"],
            },
        },
    }
]

SYSTEM_PROMPT = (
    "You are a routing orchestrator for a customer support system with three "
    "specialist agents: " + ", ".join(f"{a.NAME} ({a.DESCRIPTION})" for a in AGENTS.values()) + ". "
    "Always call the `handoff` function to route the user's message to exactly one specialist."
)


def classify(client, model_id: str, message: str) -> tuple[str, str]:
    """Returns (agent_name, instruction). Raises if the model doesn't
    hand off to a known agent — callers should treat that as a routing
    failure, not silently default to one agent."""
    resp = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": message}],
        tools=HANDOFF_SCHEMA,
        tool_choice={"type": "function", "function": {"name": "handoff"}},
    )
    tc = resp.choices[0].message.tool_calls[0]
    args = json.loads(tc.function.arguments or "{}")
    agent_name = args["agent"]
    if agent_name not in AGENTS:
        raise ValueError(f"orchestrator routed to unknown agent '{agent_name}'")
    return agent_name, args.get("instruction", message)
