# EngineAI - Privacy Policy

_Last updated: 2026-08-08_

EngineAI is a locally run engineering assistant, single-operator deployment. If
you expose it to other users over a network, review and extend this document
(and add TLS) first.

## 1. Data stored on your machine
Project files and generated artifacts (CAD, meshes, drawings, reports) in the
working directories; memory, citations, approvals and run journals in local
files; provider API keys and owner credentials in restricted-permission files
(`.env`, `.keypool.json`, `.owner.json`) that are never displayed and never
committed. No telemetry is sent to the authors.

## 2. Data sent to third-party AI providers
Only when you invoke a model-backed feature: your prompt (and, for vision, the
image you supply) is sent to the configured provider (e.g. OpenRouter). Local,
deterministic features (unit checks, closed-form fluids/handbook relations,
FEA/CFD, file inspection) send nothing. Vision calls are consent-gated. Some free
tiers may retain submitted content - do not send confidential designs to a
provider whose terms you have not reviewed.

## 3. Your controls
Choose the active provider or run local/offline where available; delete stored
data by removing local files; rotate or revoke keys at any time.

## 4. Security
Keys/owner secrets are stored with restricted permissions and excluded from
commits by a pre-push check. Services bind to 127.0.0.1 only with no transport
encryption; do not expose beyond localhost without a TLS reverse proxy.

## 5. Scope
EngineAI outputs are engineering aids, not certified results. This covers data
handling only and is not legal advice.

