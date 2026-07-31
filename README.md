# AI Governance Teaching Platform

A local-only, fully working AI governance platform built around a real AI
agent. Every governance control here is real: real calls to OpenAI and Groq,
a real policy engine, a real model registry, a real data catalog, real
tracing — no mocks, no stubs. You run it on your own laptop with Docker
Desktop, trigger each control passing, then trigger it failing, and see the
actual reason on a dashboard.

If you've never used Docker before: Docker lets you run a piece of software
(a "container") without installing it directly on your computer — like a
mini, disposable virtual computer for each tool. `docker compose up` starts
every tool this project needs, all at once, each in its own container.

## What you're building

The agent isn't a single monolithic chatbot — it's a small **multi-agent
system built on LangGraph** (`agent/graph.py`), the way a real company
would actually build one. Every governance layer AND the routing/tool
steps are explicit nodes in one `StateGraph`:

```
identity -> policy -> data -> model -> guardrails_input -> routing
   -> route_authz -> tool_select -> tool_authz -> agt_execute
   -> final_answer -> guardrails_output -> logged

routing picks a specialist:
       +--> order_agent    (read-only: look up orders)
       +--> billing_agent  (writes: issue refunds)
       +--> admin_agent    (destructive: purge customer data — admin-only)
```

Any node can short-circuit straight to `logged` (LangGraph's
`Command(goto=...)`) the moment a layer says no — the graph is the actual
control flow, not a diagram describing code that does something else.

The handoff itself is authorized by the same central policy engine as
everything else (`route:order_agent`, `route:billing_agent`, etc. in
`policy/policies/tool_access.rego`) — so an unauthorized agent-to-agent
handoff gets blocked before that specialist's tools are even reachable,
not just its individual tool calls.

Behind all of it is a **real Postgres database** (`customers`, `products`,
`orders`) — not a CSV file — with two least-privilege application roles
(`agent_app`, `dashboard_viewer`) instead of one god-mode connection, the
way a real backend would be set up.

Every request — whichever specialist ends up handling it — goes through
**7 governance layers**, in this exact order, before it's allowed to
answer you:

```
 1. Identity   -> is this a real, non-fake agent identity? (Keycloak + SPIFFE/SPIRE)
 2. Policy     -> has this identity been shut off? (Open Policy Agent)
 3. Data       -> does the data it's about to touch contain private info? (OpenMetadata)
 4. Model      -> is the AI model version actually approved for use? (MLflow)
 5. Agent      -> does the request look like an attack? (Guardrails AI + Agent Governance Toolkit)
 6. Tool-call  -> is this agent allowed to use this specific tool? (Open Policy Agent again)
 7. Logged     -> was everything about this request recorded? (Langfuse)
```

A Streamlit **dashboard** shows all 7 lights for every request, live. A
**compliance script** later reads the real logs from all these tools and
writes a report proving which regulations (NIST AI RMF, ISO 42001, EU AI
Act, OWASP Agentic Top 10) are actually being enforced — not just claimed.

---

## 1. Prerequisites

- **Docker Desktop**, installed and running. [Get it here](https://www.docker.com/products/docker-desktop/).
  - This project runs ~12 containers at once. In Docker Desktop's Settings
    → Resources, give it **at least 16 GB of RAM** and 4+ CPUs. (One of the
    tools, OpenMetadata, needs 6 GB of RAM on its own — that's normal, not
    a bug.)
- **An OpenAI API key.** Sign up at [platform.openai.com](https://platform.openai.com/),
  go to API Keys, create one. You'll need a few dollars of credit for the
  demos (this project makes a small number of real API calls, it's cheap).
- **A Groq API key.** Sign up at [console.groq.com](https://console.groq.com/) —
  Groq has a generous free tier, no credit card needed for this project's
  usage.
- Basic terminal/command-line comfort (copy-pasting commands is enough).

### Get the keys into the project

```bash
cp .env.example .env
```

Open `.env` — it has exactly two things to fill in:

**1. Real provider keys (required, fetched from a website):**
```
OPENAI_API_KEY=sk-...your real key...
GROQ_API_KEY=gsk_...your real key...
```

**2. Self-generated keys (required, but you invent them — nothing to sign up for):**
`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` aren't fetched from anywhere.
Langfuse's real, documented headless-init feature
(`LANGFUSE_INIT_*` in `docker-compose.yml`) creates your tracing project
on first boot using *whatever values you put here* — there's no signup
form. Generate two random strings and paste them in:
```bash
openssl rand -hex 16   # run twice, once per key
```
```
LANGFUSE_PUBLIC_KEY=pk-lf-<paste first random string>
LANGFUSE_SECRET_KEY=sk-lf-<paste second random string>
```
(The `pk-lf-`/`sk-lf-` prefixes are just convention, matching what
Langfuse's own UI would have generated — not a required format.)

**That's it — nothing else to fill in.** Every other credential this
project uses (Keycloak admin password, database passwords, Langfuse's
internal NextAuth secret, etc.) already has a working `${VAR:-safe_default}`
fallback in `docker-compose.yml`, so a fresh clone runs with just the two
sections above filled in. Only add one of those to `.env` yourself if you
specifically don't want to share the same baked-in local-demo default
everyone else using this repo gets — the variable names to use are the
ones inside each `${...}` in `docker-compose.yml`.

---

## 2. Bring the stack up

```bash
docker compose up -d --build
```

**What this actually does:** Docker Compose reads `docker-compose.yml`,
which lists every tool this project needs as a "service" (Keycloak, OPA,
MLflow, the agent, and so on). For each one it either pulls a ready-made
image from the internet (Keycloak, OPA, MLflow...) or builds one from this
repo's own code — that's what `--build` is for, it applies to the two
services that are actually *this project's* code: `agent` and `dashboard`.
It then starts all of them together on one private network where they can
reach each other by name (the agent can just call `http://opa:8181` — no
IP addresses to figure out). `-d` means "detached": it runs in the
background and hands your terminal back immediately, instead of sitting
there printing logs forever.

This takes a few minutes the first time (it's downloading ~10 tools'
worth of images). Watch progress with:

```bash
docker compose ps
```
This lists every container this project defined and its current status —
`starting` while a tool is still booting up, `running` or `healthy` once
it's ready to use. Re-run it any time; it just reports current state, it
doesn't change anything.

**Nothing to run by hand for model registration or data cataloging** — the
`agent` container does this itself on startup (`agent/startup_automation.py`):
it registers both model versions, runs a real evaluation against a real
threshold, and only promotes the version that passes to `Production`
(`model-governance/register_model.py` + `promote_model.py`, called
automatically — see "Layer 4 — Model" below). It also re-runs that
evaluation on a schedule (`RE_EVAL_INTERVAL_SECONDS` in `.env`, default 6h)
and will demote a model back to `Staging` on its own if a later check
fails. This is the same behavior you'd get running those scripts by hand
— just automatic, the way a real service would do it.

**Nothing manual needed for OpenMetadata or Langfuse either** — both used
to require a one-time UI signup step; both are now fully automatic
(live-verified against the running stack):

- **OpenMetadata**: `agent/om_client.py` logs in on its own via
  OpenMetadata's real `POST /users/login` endpoint (undocumented in the
  official SDK, but a genuine working endpoint — verified by calling it
  directly against a live server) using the server's own default
  `admin@open-metadata.org` / `admin` account, then caches and refreshes
  the token automatically. `data-governance/ingest_and_tag_pii.py` runs
  with no credential to paste in first.
- **Langfuse**: its real, documented `LANGFUSE_INIT_*` headless-init
  feature (see `docker-compose.yml`) creates the org/project/API-key-pair
  on first boot using whatever `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`
  values you generated and pasted into `.env` in step 1 — no UI signup, no
  copy-pasting a generated key back afterward.

(Optional, for a deeper look at real evaluation metrics beyond the
automatic promotion gate: `docker compose run --rm agent python model-governance/evaluate_model.py`.)

You're set up. Open the dashboard: **[http://localhost:8501](http://localhost:8501)**.
Every demo script and the dashboard itself first log into Keycloak to get a
real Bearer token — `/chat` rejects requests without one. Two pre-seeded
accounts exist, and **which one you log in as is what determines your
role** — there is no "role" field you can set on a request (see Layer 1
below for why that matters):

| Keycloak account | Password | Realm role | What it can do |
|---|---|---|---|
| `student` | `student123` | `demo-agent` | order lookups, refunds — the default for every demo script |
| `instructor` | `instructor123` | `admin` | everything, including `admin_agent`/`purge_customer_data` |

---

## 3. The tools, layer by layer (what & why)

### Layer 1 — Identity: Keycloak + SPIFFE/SPIRE

**Keycloak** is an open-source login system (the tech term is "Identity
Provider", speaking "OIDC" — a standard way apps check who you are). In
this project it represents the *human* side of identity: the student/admin
using the dashboard. Think of it like "Sign in with Google", but
self-hosted. **This is actually enforced, not decorative:**
`agent/main.py`'s `/chat` endpoint has a real FastAPI dependency
(`require_human_identity`) that verifies every request's Bearer token
against Keycloak's own signing keys (`agent/keycloak_auth.py`, using
`PyJWT`'s `PyJWKClient`) — no valid Keycloak token, no response, full stop
(HTTP 401). Try it yourself: `curl -X POST localhost:8000/chat -d '{"message":"hi"}'`
with no `Authorization` header and you'll get rejected before any other
layer even runs.

**Your `role` comes from the token's `realm_access.roles` claim, never
from anything you send in the request body.** An earlier version of this
project read `role` straight off the JSON payload — meaning any caller
could just write `{"role": "admin"}` and walk past every role check in
the system. That's fixed: `agent/main.py`'s `require_human_identity`
reads the *verified, signed* claims and rejects tokens carrying no
recognized role (HTTP 403). The only way to act as `admin` is to actually
authenticate as the `instructor` Keycloak account, which really holds
that realm role.

**SPIFFE/SPIRE** solves a different, sneakier problem: how does the *agent
software itself* prove who it is to other services, without a programmer
just pasting a password into a config file (a "static hardcoded credential")?
SPIRE issues the agent a short-lived, cryptographic ID document (an
"SVID") the moment its container starts, based on provable facts checked
over the Workload API's Unix socket connection — not a secret a developer
typed in. **What it's doing here:** `agent/governance_middleware.py`'s
`fetch_svid()` asks SPIRE for this ID at startup — if SPIRE is unreachable,
the agent **refuses to start** rather than silently using no identity or a
fake one.

*Honesty note on the selector:* this originally tried SPIRE's `docker`
attestor (matching the caller by its container ID), but a real bug showed
up running it against Docker Desktop — its VM backend produces a cgroup v2
path (`0::/../<container-id>`) that attestor's matcher doesn't recognize,
so every connection got zero selectors and nothing was ever attested. It
now uses the `unix` attestor instead (`identity/spire/agent.conf.template`),
which identifies the caller by the real UID the kernel reports on the
socket connection (`SO_PEERCRED`) — selector `unix:uid:0`. On its own, a
bare UID check is weak (anything else running as UID 0 would also match);
what actually makes this real defense-in-depth is that the Workload API
socket is a Docker volume mounted into *only* the `agent` service
(`docker-compose.yml`) — no other container can even reach it to ask.

### Layer 2 — Policy: Open Policy Agent (OPA)

OPA is a general-purpose "yes/no" decision engine. You write small rules
(in a language called **Rego**) describing who's allowed to do what, and
any service can ask OPA "is this allowed?" over a simple HTTP call. The
big idea: your allow/deny rules live in ONE place (`policy/policies/*.rego`),
not scattered as `if` statements across five different services.
**What it's doing here:** OPA is asked FOUR separate real questions during
one chat request — "is this identity revoked?" (kill switch), "should this
data be masked?" (PII), "is this role cleared for this model's risk tier?"
(risk-based access — see Layer 4), and "can this role call this tool?"
(authorization, asked once for the agent handoff and again for the actual
tool) — proving it's a genuinely shared hub, not a one-off check.

**Policy is editable without touching Rego** — the dashboard's ⚙️ Policy
tab grants/revokes a role's access to a tool live, via the exact same
data-API mechanism the kill switch uses (`agent/policy_admin.py` PUTs into
`data.governance.tool_access_overrides`, which `tool_access.rego` checks
on every request). This is what makes governance policy something a
compliance/risk reviewer can actually manage — not just whoever can edit
and redeploy a `.rego` file. `policy/policies/*_test.rego` (run via
`opa test policy/policies/`) covers the base rules with 18 real
assertions — a real engineering safety net for the policies themselves,
not just for the code that calls them.

### Layer 3 — Data: OpenMetadata

OpenMetadata is a **data catalog**: it keeps track of what data your
organization has, what each column means, and — critically for governance —
which columns contain sensitive info (PII: personally identifiable
information, like an email address). **What it's doing here:**
`data-governance/ingest_and_tag_pii.py` connects to the real Postgres
database, reads its actual schema live (via SQLAlchemy reflection — it
doesn't hand-type the column list, it asks Postgres what's really there),
catalogs `customers`/`products`/`orders`, tags `customers.email`
`PII.Sensitive`, and records lineage matching the real foreign keys
(`customers` → `orders`, `products` → `orders`). Later, every time an
agent tool would return that column, the agent asks OPA "should this be
masked?" based on that real tag — not a hardcoded Python `if column == "email"`.

### Layer 4 — Model: MLflow Model Registry

MLflow tracks machine-learning models the way Git tracks code: every
version gets a number, a stage (`Staging`, `Production`), and a
description. This project registers two "model" entries representing the
two LLM configs the agent could use — one on Groq, one on OpenAI — attach a
**model card** (what it's for, its limitations, its `risk_tier`) to each.
**What it's doing here:** before every answer, the agent asks MLflow "is
the model version I was asked to use actually in `Production`?" If someone
asks for a version still in `Staging`, the request is blocked — this is
what a "model governance gate" means in practice.

**No version reaches Production by self-declaration.** `register_model.py`
caps every version at `Staging`, no matter what `model_card.yaml` says —
`promote_model.py` is the only thing that can move a version further, and
only after a *real* `mlflow.evaluate()` run against a real
`MetricThreshold` (average answer-readability grade level must stay under
20 — a bar real LLM output can and does fail, not a rubber stamp; verified
by deliberately setting an impossible threshold during development and
watching it correctly refuse to promote and demote an existing Production
version back to Staging). This runs automatically on startup and again on
a schedule (see "Bring the stack up" above) — a model's Production status
reflects its most recent check, not just whatever passed once at
registration time.

**Risk tier is a control, not just a label.** A second, independent OPA
check (`policy/policies/model_access.rego`) asks "is this role cleared for
this model version's `risk_tier`?" — `demo-agent` is cleared for
`low`/`medium`, only `admin` is cleared for `high`. This is what makes
"risk tier" actually mean something operationally (the point of risk-based
governance in NIST AI RMF / EU AI Act) instead of being informational text
in a report that never affects what the system actually does.

### Layer 5 — Agent: Guardrails AI + Microsoft Agent Governance Toolkit

**Guardrails AI** is a lightweight library that checks text going *into*
and *out of* an LLM against rules — e.g., "does this look like someone
trying to override my instructions?" (a "prompt injection" attack) or "did
the model's answer leak an email address it shouldn't have?" These checks
run locally, no extra network calls.

**Microsoft's Agent Governance Toolkit** (open source, MIT license) is a
newer, more specialized tool built specifically for AI *agents* (as
opposed to plain chatbots) — because agents that can call tools/take
actions have a different, worse risk profile than a chatbot that just
talks. It's actually several pip packages: `agent-governance-toolkit`
(the `agt` CLI, used for compliance grading — see step 5) and
`agent-os-kernel` (the real runtime policy engine, a `StatelessKernel`
class). **What it's doing here:** every tool call any specialist agent
wants to make is checked by `agent_os.StatelessKernel.execute()` (see
`agent/governance_middleware.py`) against a small policy dict, *after* OPA
has already confirmed the role is allowed to use that tool at all — two
different, complementary checks that can each independently say no. One
real wrinkle worth knowing: the kernel is a policy *gate*, not a code
executor — on an allowed action it returns a canned "executed
successfully" placeholder rather than actually running anything, so the
agent code still calls the real tool function itself once AGT says yes.
This applies uniformly to all three specialist agents
(`agent/agents/order_agent.py`, `billing_agent.py`, `admin_agent.py`) and
to the orchestrator's own handoff decision — governance doesn't get weaker
just because there's more than one agent in the system.

*Honesty note on the OWASP coverage score:* `agt verify` (step 5) grades
coverage by checking which of the toolkit's optional component packages
are actually installed and wired in. This project installs and uses
`agent-os-kernel` for real (covering 5 of 10 OWASP Agentic controls: prompt
injection, insecure tool use, excessive agency, unauthorized escalation,
supply chain integrity). The other 5 controls check for a companion
package called `agentmesh` — live-verified on PyPI (`pip show agentmesh`)
to be "a placeholder for reserving the agentmesh package name," version
0.1.1, with no actual code behind it. Installing it would flip those 5
flags to `present: true` with zero real control behind them — exactly the
kind of mock this project is trying not to do — so it's left uninstalled
and the gap is documented in the generated report instead. This project
independently provides the guarantees those 5 controls describe through
its own stack (SPIFFE/SPIRE identity, the hash-chained `audit_log`, OPA
policy enforcement) — just not wired through that specific expected
module path. The compliance report shows the real, current number
(currently 50%), not a maximized one.

### Layer 6 — Tool-call: OPA again

The same OPA service from Layer 2, asked two different questions: "is
`demo-agent` allowed to hand off to `admin_agent`?" and "is `demo-agent`
allowed to call `purge_customer_data`?" This reuses the same Rego policy
hub — proof it's a shared service, not copy-pasted logic.

**Every decision is tamper-evident, not just logged.** OPA pushes each
decision to `POST /internal/audit-log` on the agent (its real
`decision_logs` HTTP-service plugin — verified by capturing and decoding
an actual gzip-compressed payload during development), which
`agent/audit_log.py` writes into a Postgres `audit_log` table where each
row's hash covers its own content *and* the previous row's hash — a hash
chain, the same construction (not the same tech) that makes a blockchain
ledger tamper-evident. Edit or delete any past row directly in the
database and `verify_chain()` (🔐 Audit Log tab, or
`compliance/generate_report.py`) will detect exactly which entry broke —
confirmed by deliberately corrupting a row during development and
watching it get caught. The earlier version of this project just `tee`'d
OPA's console output to a text file, which anyone with container access
could silently edit.

*Real bug this caught:* OPA's decision-log uploader posts to
`<services.url>` **plus** a `resource` path that defaults to `/logs` — an
earlier `policy/opa-config.yaml` put the full target path in `url` itself,
so OPA was silently POSTing to `/internal/audit-log/logs` and every upload
failed with a 404. The `audit_log` table was empty the entire time this
was misconfigured, with no error visible anywhere except `docker compose
logs opa`. Fixed by splitting `url` (base) from `resource` (the real
path); re-verified live by running a scenario and confirming fresh rows
actually appear in Postgres with an intact hash chain. This is exactly the
class of bug that only real end-to-end testing (not reading the config)
catches.

### Layer 7 — Logged: Langfuse

Langfuse records a detailed trace of *everything* that happened during a
request — every layer's pass/fail, every real LLM call and its latency —
so you (or an auditor) can look back at exactly what the agent did and why,
long after the request is over. **What it's doing here:** every single
`/chat` request creates a Langfuse trace, including requests that got
*blocked* partway through — a blocked run is still a run worth auditing.
View traces at [http://localhost:3000](http://localhost:3000).

### The dashboard: Streamlit

Streamlit is a Python library for building simple data-driven web pages
without writing HTML/JavaScript. `dashboard/app.py` has five tabs:

- **🔍 Live Checklist** — pick which account to log in as (`student` or
  `instructor` — see Layer 1), send a message, and see the 7 pass/fail
  lights plus which specialist agent handled it, with the real reason
  behind any red light pulled from that layer's actual response.
- **🗄️ Live Database** — connects to Postgres directly (read-only
  `dashboard_viewer` role) and shows the real, unmasked table content —
  compare it against a masked agent answer, or watch a refund (Scenario F)
  actually change a row.
- **🔐 Audit Log** — the last 50 tamper-evident decision entries plus a
  live "Verify chain integrity" button (see Layer 6).
- **⚙️ Policy** — grant/revoke a role's access to a tool without touching
  Rego (see Layer 2's "Policy is editable without touching Rego").
- **📋 Compliance Report** — the latest generated report.

---