"""The governance flow as a real LangGraph StateGraph — one node per
governance layer plus the multi-agent routing/tool-execution steps, wired
with LangGraph's `Command(update=..., goto=...)` pattern so each node
decides both what changed and where to go next. This replaces the
hand-rolled if/return chain that used to live in main.py: same checks, same
order, same "blocked run still gets logged" behavior, now expressed as an
explicit, inspectable graph instead of nested conditionals — while still
keeping every governance hook as a plain, readable Python function (no
governance logic hides inside a framework's agent-executor).

Node order == layer order:
  identity -> policy -> data -> model -> guardrails_input -> routing ->
  route_authz -> tool_select -> tool_authz -> agt_execute -> final_answer ->
  guardrails_output -> logged
Any gate node can short-circuit straight to `logged` via Command(goto=...).
"""
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent import governance_middleware as gov
from agent.agents import orchestrator
from agent.llm_client import get_client


class ChatState(TypedDict, total=False):
    message: str
    role: str
    spiffe_id: str
    human_user: str
    model_version: str | None
    force_agent: str | None
    force_tool: dict | None
    layers: list[dict]
    blocked_at: str | None
    mask_columns: list[str]
    provider: str
    model_id: str
    agent_name: str
    instruction: str
    routing_note: str
    tool_name: str
    tool_args: dict
    tool_result: object
    answer: str | None
    trace: object


def _layer(name: str, passed: bool | None, reason: str = "") -> dict:
    return {"layer": name, "pass": passed, "reason": reason}


def _skip_to_logged(layers: list[dict], remaining: list[str], blocked_at: str) -> list[dict]:
    return layers + [_layer(n, None, f"not reached — blocked at layer '{blocked_at}'") for n in remaining]


ALL_LAYER_NAMES = ["identity", "policy", "data", "model", "agent_guardrails", "tool_call", "logged"]


# --- Node: identity -----------------------------------------------------
def identity_node(state: ChatState) -> Command:
    layers = state["layers"] + [
        _layer("identity", True, f"Keycloak user '{state['human_user']}' + SPIFFE ID {state['spiffe_id']} both attached")
    ]
    return Command(update={"layers": layers}, goto="policy")


# --- Node: policy (kill switch) -----------------------------------------
def policy_node(state: ChatState) -> Command:
    allowed, reason = gov.check_kill_switch(state["spiffe_id"])
    layers = state["layers"] + [_layer("policy", allowed, reason or "identity is active, not revoked")]
    if not allowed:
        layers = _skip_to_logged(layers, ["data", "model", "agent_guardrails", "tool_call"], "policy")
        return Command(update={"layers": layers, "blocked_at": "policy"}, goto="logged")
    return Command(update={"layers": layers}, goto="data")


# --- Node: data (PII masking decision) ----------------------------------
def data_node(state: ChatState) -> Command:
    mask_columns = gov.get_columns_to_mask()
    layers = state["layers"] + [_layer("data", True, f"columns to mask this request: {mask_columns or 'none'}")]
    return Command(update={"layers": layers, "mask_columns": mask_columns}, goto="model")


# --- Node: model (registry stage + risk-tier gate) ----------------------
def model_node(state: ChatState) -> Command:
    check = gov.check_model_version_gate(state["role"], state.get("model_version"))
    reason = check.get(
        "reason",
        f"using version {check.get('version')} ({check.get('provider')}/{check.get('model_id')}, risk_tier={check.get('risk_tier')})",
    )
    layers = state["layers"] + [_layer("model", check["pass"], reason)]
    if not check["pass"]:
        layers = _skip_to_logged(layers, ["agent_guardrails", "tool_call"], "model")
        return Command(update={"layers": layers, "blocked_at": "model"}, goto="logged")
    return Command(update={"layers": layers, "provider": check["provider"], "model_id": check["model_id"]}, goto="guardrails_input")


# --- Node: agent_guardrails (input check) --------------------------------
def guardrails_input_node(state: ChatState) -> Command:
    ok, reason = gov.validate_input(state["message"])
    if not ok:
        layers = state["layers"] + [_layer("agent_guardrails", False, reason)]
        layers = _skip_to_logged(layers, ["tool_call"], "agent_guardrails")
        return Command(update={"layers": layers, "blocked_at": "agent_guardrails"}, goto="logged")
    return Command(goto="routing")


# --- Node: routing (orchestrator handoff + OPA route authorization) -----
def routing_node(state: ChatState) -> Command:
    client = get_client(state["provider"])
    trace = state["trace"]

    if state.get("force_agent"):
        agent_name, instruction = state["force_agent"], state["message"]
        routing_note = f"routing to '{agent_name}' forced by request"
    else:
        agent_name, instruction = orchestrator.classify(client, state["model_id"], state["message"])
        routing_note = f"orchestrator routed to '{agent_name}'"
        if trace:
            trace.generation(name="orchestrator-routing", model=state["model_id"], input=state["message"], output=agent_name)

    route_allowed, route_reason = gov.check_tool_access(state["role"], f"route:{agent_name}", state["spiffe_id"])
    layers = state["layers"] + [_layer("agent_guardrails", True, "input check passed")]
    if not route_allowed:
        layers = layers + [_layer("tool_call", False, f"{routing_note}, but denied: {route_reason}")]
        return Command(update={"layers": layers, "blocked_at": "tool_call"}, goto="logged")

    return Command(update={"layers": layers, "agent_name": agent_name, "instruction": instruction, "routing_note": routing_note}, goto="tool_select")


# --- Node: tool_select (specialist's own LLM turn) -----------------------
def tool_select_node(state: ChatState) -> Command:
    specialist = orchestrator.AGENTS[state["agent_name"]]
    client = get_client(state["provider"])
    trace = state["trace"]
    messages = [{"role": "user", "content": state["instruction"]}]

    if state.get("force_tool"):
        tool_name, tool_args = state["force_tool"]["name"], state["force_tool"]["args"]
        return Command(update={"tool_name": tool_name, "tool_args": tool_args}, goto="tool_authz")

    resp = client.chat.completions.create(model=state["model_id"], messages=messages, tools=specialist.TOOL_SCHEMAS)
    choice = resp.choices[0].message
    if trace:
        trace.generation(
            name=f"{state['agent_name']}-tool-selection", model=state["model_id"], input=messages,
            output=choice.content or str(choice.tool_calls),
        )
    if choice.tool_calls:
        tc = choice.tool_calls[0]
        return Command(update={"tool_name": tc.function.name, "tool_args": json.loads(tc.function.arguments or "{}")}, goto="tool_authz")

    # No tool needed — the specialist answered directly.
    answer = choice.content or ""
    out_ok, out_reason = gov.validate_output(answer)
    layers = state["layers"]
    for l in layers:
        if l["layer"] == "agent_guardrails":
            l["reason"] += "; output check passed" if out_ok else f"; {out_reason}"
            l["pass"] = l["pass"] and out_ok
    layers = layers + [_layer("tool_call", True, f"{state['routing_note']}; no tool call needed")]
    return Command(update={"layers": layers, "answer": answer}, goto="logged")


# --- Node: tool_authz (OPA Call 2) ---------------------------------------
def tool_authz_node(state: ChatState) -> Command:
    allowed, reason = gov.check_tool_access(state["role"], state["tool_name"], state["spiffe_id"])
    if not allowed:
        layers = state["layers"] + [_layer("tool_call", False, f"{state['routing_note']}; tool denied: {reason}")]
        return Command(update={"layers": layers, "blocked_at": "tool_call"}, goto="logged")
    return Command(goto="agt_execute")


# --- Node: agt_execute (AGT StatelessKernel screen + real execution) -----
def agt_execute_node(state: ChatState) -> Command:
    ok, reason, result = gov.call_tool_governed(state["role"], state["tool_name"], **state["tool_args"])
    if not ok:
        layers = state["layers"] + [_layer("tool_call", False, f"{state['routing_note']}; blocked by Agent Governance Toolkit: {reason}")]
        return Command(update={"layers": layers, "blocked_at": "tool_call"}, goto="logged")

    mask_columns = state["mask_columns"]
    if isinstance(result, dict):
        result = gov.mask_record(result, mask_columns)
    elif isinstance(result, list):
        result = [gov.mask_record(r, mask_columns) if isinstance(r, dict) else r for r in result]

    layers = state["layers"] + [
        _layer("tool_call", True, f"{state['routing_note']}; tool '{state['tool_name']}' authorized (OPA) and screened (AGT); result masked per data layer")
    ]
    return Command(update={"layers": layers, "tool_result": result}, goto="final_answer")


# --- Node: final_answer (LLM call combining the tool result) -------------
def final_answer_node(state: ChatState) -> Command:
    client = get_client(state["provider"])
    messages = [
        {"role": "user", "content": state["instruction"]},
        {"role": "assistant", "content": f"Calling {state['tool_name']}({state['tool_args']})"},
        {"role": "user", "content": f"Tool result: {json.dumps(state['tool_result'])}. Answer the original question using this."},
    ]
    resp = client.chat.completions.create(model=state["model_id"], messages=messages)
    answer = resp.choices[0].message.content or ""
    return Command(update={"answer": answer}, goto="guardrails_output")


# --- Node: guardrails_output ---------------------------------------------
def guardrails_output_node(state: ChatState) -> Command:
    out_ok, out_reason = gov.validate_output(state["answer"] or "")
    answer = state["answer"]
    if not out_ok:
        answer = f"[response withheld: {out_reason}]"
    layers = state["layers"]
    for l in layers:
        if l["layer"] == "agent_guardrails":
            l["reason"] += "; output check passed" if out_ok else f"; {out_reason}"
            l["pass"] = l["pass"] and out_ok
    return Command(update={"layers": layers, "answer": answer}, goto="logged")


# --- Node: logged (Langfuse finalize) -------------------------------------
def logged_node(state: ChatState) -> Command:
    trace = state["trace"]
    layers = state["layers"] + [
        _layer("logged", trace is not None, f"trace {trace.id} recorded in Langfuse" if trace else "LANGFUSE_PUBLIC_KEY not set — tracing disabled")
    ]
    if trace:
        trace.update(output=state.get("answer"), metadata={"layer_results": layers, "blocked_at": state.get("blocked_at")})
    return Command(update={"layers": layers}, goto=END)


def build_graph():
    g = StateGraph(ChatState)
    g.add_node("identity", identity_node)
    g.add_node("policy", policy_node)
    g.add_node("data", data_node)
    g.add_node("model", model_node)
    g.add_node("guardrails_input", guardrails_input_node)
    g.add_node("routing", routing_node)
    g.add_node("tool_select", tool_select_node)
    g.add_node("tool_authz", tool_authz_node)
    g.add_node("agt_execute", agt_execute_node)
    g.add_node("final_answer", final_answer_node)
    g.add_node("guardrails_output", guardrails_output_node)
    g.add_node("logged", logged_node)
    g.add_edge(START, "identity")
    g.add_edge("logged", END)
    return g.compile()


CHAT_GRAPH = build_graph()
