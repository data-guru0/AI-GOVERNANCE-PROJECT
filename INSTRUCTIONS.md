# Project Brief for Claude Code: Local AI Governance Teaching Platform

## Goal
Build a complete, fully working, **local-only** AI governance platform around a
real AI agent, for teaching students every layer of AI governance hands-on in form of a complete project.
Students must be able to clone it, run it, and
literally trigger each governance control passing or failing.

## Hard Requirements
- **Local only.** Runs entirely via Docker Desktop + Docker Compose. No cloud
  deployment, no hosted third-party services — the only external calls
  allowed are to the LLM APIs themselves.
- **Real LLM calls, no mocks.** The agent must call OpenAI's API and Groq's
  API for real. No stubbed/simulated responses anywhere — not for the agent, and not for any governance check.
  Every control and service must execute against a real running service.

- **Open source only, everywhere.** Every tool used must be free and
  self-hostable with no paid tier requirement anywhere in the setup or
  usage path. Do not substitute any SaaS-only product (e.g. Okta, Auth0
  FGA, Credo AI, OneTrust, Vanta, Databricks Unity Catalog) for the
  open-source tools listed below, even as a "quick option" — the whole
  point of the project is that a student can run it for free.

- **Simple, flat, teachable folder structure.** This will be taught step by
  step on camera — a beginner must open the repo and immediately see where
  each governance layer lives. Do not over-engineer into deep nested
  microservice folders. 
  ```

- **README.md** must contain complete, beginner-level (assume a 15-year-old
  with no DevOps background) step-by-step instructions: prerequisites, how
  to get OpenAI/Groq API keys and where to put them, exact commands to
  bring the stack up, and a walkthrough of each demo scenario below in the
  format "run this command → this is what should happen → here's what it
  looks like when it's blocked instead of allowed." In README.md like whenever you are using a tool or tech or somehting mention a simple theory with exmpale also what that tool or tech and step is doing in our project in simple easy.

## The Governance Flow to Implement, in Order, With the OSS Tool for Each Step

1. **Identity Governance** — Keycloak for human login (OIDC); SPIFFE/SPIRE
   for the agent's own workload identity, so the agent never uses a static
   hardcoded API key for its own identity.

2. **Policy Engine** — Open Policy Agent (OPA) with Rego policies, running
   as ONE central service. This is not a single stage in the flow — it must
   be queried from steps 3, 5, and 6 below, proving it's a shared hub, not
   a one-time check.

3. **Data Governance** — OpenMetadata: catalog a sample dataset, tag PII
   columns, and show lineage into whatever source the agent retrieves from.

4. **Model Governance** — MLflow Model Registry: register the model
   config, attach a model card (intended use, eval notes, limitations,
   and a `risk_tier` field), and gate "production" access behind an
   approved registry stage. Use whatver features you want from mlflow for the model governance on a advanced level like do all types of evaluation and every checks on the model using MLflow

5. **Agent Governance** — the actual agent, making real OpenAI and Groq API
   calls. Use Guardrails AI or NeMo Guardrails for input/output checks,
   Microsoft's open-source **Agent Governance Toolkit** (MIT license) for
   runtime protection against the OWASP Top 10 for Agentic Applications
   (goal hijacking, tool misuse, identity abuse, memory poisoning,
   cascading failures, rogue agents, etc.), and a call to OPA before
   executing any tool, so the tool-call authorization reuses the same
   policy hub from step 2.

6. **Operations Governance** — Langfuse for full tracing of every agent
   run, plus a working kill-switch script that revokes the agent's
   identity/policy on demand and immediately blocks its next request.

7. **Compliance Mapping** — a script that pulls audit evidence from OPA's
   decision logs, MLflow, OpenMetadata, Langfuse, AND the Agent Governance
   Toolkit's own built-in compliance-grading output (it already produces
   EU AI Act / HIPAA / SOC 2 mapping and OWASP Agentic Top 10 evidence for
   the agent layer — pull that directly instead of re-implementing it).
   Merge all of this into a YAML checklist covering NIST AI RMF, ISO 42001,
   and EU AI Act items. Unlike steps 1-6, this step does not make a
   real-time allow/deny decision — it runs after the fact and generates a
   human-readable report file (`compliance_report.md`, rendered with
   Jinja2) listing each checklist item alongside the actual log evidence
   proving it was satisfied. This report must be viewable from the
   dashboard below.

   Implement this exact starter checklist (a `checklist.yaml` file), one
   evidence check per item:
   - NIST AI RMF (Govern) — "Access to the AI system is role-based and
     enforced" → evidence: OPA decision log contains real allow/deny entries
   - NIST AI RMF (Measure) — "Model version and performance are tracked
     before deployment" → evidence: MLflow model card + eval metrics exist
   - NIST AI RMF (Manage) — "The system can be immediately disabled if it
     misbehaves" → evidence: a kill-switch execution log entry exists
   - ISO 42001 — "Sensitive data used by the AI system is identified and
     classified" → evidence: OpenMetadata has PII tags on the source table
   - ISO 42001 — "AI system decisions are logged for traceability" →
     evidence: Langfuse traces exist for agent runs
   - EU AI Act — "The system's risk tier is documented" → evidence: a
     `risk_tier` field is set on the MLflow model card
   - EU AI Act — "Human oversight / override capability exists" →
     evidence: the kill-switch script exists and has a real revocation
     log entry
   - OWASP Agentic AI Top 10 — "Agent runtime risks are actively defended
     against" → evidence: pulled directly from the Agent Governance
     Toolkit's own compliance-grading output for the current agent

## Governance Dashboard (required UI)

A single lightweight local web dashboard (Streamlit) that makes every layer's decision visible instead of invisible. It must show, for each request sent to the agent, a live
checklist of all 7 layers in order (Identity → Policy → Data → Model →
Agent/Guardrails → Tool-call → Logged), each marked pass/fail in real time.
If a layer blocks the request, clicking it must show the actual reason
(e.g. the specific OPA rule that denied it, or which PII column got
masked) pulled from that layer's real logs — not a generic message. The
dashboard must also have a section/tab that displays the latest generated
compliance report from step 7, including the Agent Governance Toolkit's
own compliance grade.

## Demo Scenarios the README Must Document and the App Must Actually Support
- **Scenario A** — an approved end-to-end agent request, passing every layer.
- **Scenario B** — a request blocked at the policy layer (unauthorized tool call).
- **Scenario C** — a request where PII gets masked before it reaches the model.
- **Scenario D** — the kill-switch triggered, agent immediately loses access.

and try other scenarios as well according to you

Each scenario needs its own runnable script or command, and the README must
show the expected output for both the "allowed" and "blocked" outcome.