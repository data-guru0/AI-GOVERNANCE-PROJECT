"""The agent service. One FastAPI endpoint (`/chat`) that runs the LangGraph
state graph in agent/graph.py — every governance layer as an explicit node,
in the exact order the dashboard shows them: Identity -> Policy -> Data ->
Model -> Agent/Guardrails -> Tool-call -> Logged.

SECURITY: `role` is derived from the verified Keycloak token's
`realm_access.roles` claim (`require_human_identity` below) — it is never
read from the request body. A client cannot self-declare "admin" by
setting a field; the only way to get the "admin" role is to authenticate
as the `instructor` Keycloak account, which actually holds that realm role
(see identity/keycloak/realm-export.json). This was a real bypass in an
earlier version of this file (the role used to be `req.role`, a plain
client-supplied JSON field) — fixed here, not just documented.

`force_agent`/`force_tool` remain as demo-script determinism hooks — they
only affect *routing*/*tool choice*, never authorization; every choice
they make still goes through the same OPA/AGT checks as an LLM-chosen one.
"""
import os
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from langfuse import Langfuse

from agent import audit_log
from agent import governance_middleware as gov
from agent import keycloak_auth
from agent import startup_automation
from agent.graph import CHAT_GRAPH

app = FastAPI(title="AI Governance Demo Agent")
langfuse_client = Langfuse() if os.environ.get("LANGFUSE_PUBLIC_KEY") else None

# Priority order when a token somehow carries more than one recognized role.
KNOWN_ROLES = ["admin", "demo-agent"]


def require_human_identity(authorization: str = Header(default=None)) -> tuple[dict, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token — a Keycloak-issued token is required")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = keycloak_auth.verify_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid Keycloak token: {e}") from e

    token_roles = set(claims.get("realm_access", {}).get("roles", []))
    role = next((r for r in KNOWN_ROLES if r in token_roles), None)
    if role is None:
        raise HTTPException(status_code=403, detail=f"token has no recognized governance role (has: {sorted(token_roles)})")
    return claims, role


class ForceTool(BaseModel):
    name: str
    args: dict = {}


class ChatRequest(BaseModel):
    message: str
    model_version: str | None = None
    force_agent: str | None = None
    force_tool: ForceTool | None = None


@app.on_event("startup")
def startup():
    app.state.spiffe_id = gov.fetch_svid()
    print(f"[agent] booted with SPIFFE ID {app.state.spiffe_id}")

    startup_automation.run_initial_setup()
    startup_automation.start_background_scheduler()


@app.get("/health")
def health():
    return {"status": "ok", "spiffe_id": app.state.spiffe_id}


@app.post("/internal/audit-log")
async def internal_audit_log(request: Request):
    """OPA's decision_logs plugin POSTs here (see docker-compose.yml's
    opa-config.yaml `services`/`decision_logs.service`) — not for browser
    or demo-script use. Each decision gets hash-chained into Postgres's
    audit_log table; see agent/audit_log.py."""
    body = await request.body()
    count = audit_log.ingest(body)
    return {"ingested": count}


@app.post("/chat")
def chat(req: ChatRequest, identity: tuple[dict, str] = Depends(require_human_identity)):
    claims, role = identity
    spiffe_id = app.state.spiffe_id
    human_user = claims.get("preferred_username", "unknown")

    trace = (
        langfuse_client.trace(
            name="agent_chat", input=req.message, metadata={"spiffe_id": spiffe_id, "role": role, "keycloak_user": human_user}
        )
        if langfuse_client
        else None
    )

    initial_state = {
        "message": req.message,
        "role": role,
        "spiffe_id": spiffe_id,
        "human_user": human_user,
        "model_version": req.model_version,
        "force_agent": req.force_agent,
        "force_tool": req.force_tool.model_dump() if req.force_tool else None,
        "layers": [],
        "blocked_at": None,
        "answer": None,
        "trace": trace,
    }
    final_state = CHAT_GRAPH.invoke(initial_state)

    return {
        "request_id": str(uuid.uuid4()),
        "message": req.message,
        "role": role,
        "answer": final_state.get("answer"),
        "blocked": final_state.get("blocked_at") is not None,
        "blocked_at_layer": final_state.get("blocked_at"),
        "spiffe_id": spiffe_id,
        "layer_results": final_state["layers"],
    }
