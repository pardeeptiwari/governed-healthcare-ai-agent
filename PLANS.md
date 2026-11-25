# Emids Lead AI Architect assessment implementation plan

## Objective, scope, and authority

Build a governed assistant for reps serving Harborview Health Plan and Prairie Sky Health. Each conversation is bound to one authenticated rep, tenant, and member. The assistant answers benefits and claims questions from authorized sources and files a grievance only after the rep confirms the displayed draft. Emids reviewers should be able to reproduce the slice and its evaluation with their own pack and model provider.

The original `BRIEF.md` and `data/README.md` in the external assessment pack define mandatory requirements. The mock implementation resolves wire behavior, and the 40 evaluation conversations define the supplied test expectations. The approved architecture and ADRs define engineering choices. When an expected answer conflicts with authorized source evidence, preserve the raw failure, document the conflict, and test the safe behavior separately. The supplied pack stays read-only and outside the repository.

The slice includes enrollment and benefits, claims and appeal-window explanations, grievance reads and filing, and scoped provider search. Provider search is optional in the brief but included for EV-20. Prior-authorization adjudication, appeal submission, and actual cloud provisioning are outside the slice. Production SLOs are design targets that require separate evidence.

## Mandatory requirements

| ID | Requirement |
|---|---|
| F1 | Answer rep questions for one tenant and one member using the supplied APIs and plan documents. |
| F2 | Route every tool call through a gateway that enforces deterministic scope, minimum necessary PHI, approval before writes, and per-conversation step, latency, and cost budgets. |
| F3 | Retrieve tenant-scoped policy with applicable product, plan year, and effective date. Combine benefit rules with matching accumulators and adjudication data where applicable. |
| F4 | Support multi-turn interaction through HTTP or CLI. File grievances after explicit rep confirmation and safely handle retries. |
| F5 | Trace model calls, tools, and gateway decisions with redacted metadata. |
| F6 | Run and score all supplied conversations. Provide a justified CI-capable regression gate and commit the sanitized report from the last run. |
| F7 | Deliver the public repository, real incremental history, tests, reproducible README, configuration for pack and model provider, production design, diagram, and NOTES. |

| ID | Client target | Design implication |
|---|---|---|
| NFR-1 | 99.99% availability per calendar month | Measure completed workflows, degraded outcomes, dependency availability, and error-budget burn. |
| NFR-2 | One cloud region at the lowest feasible cost in year one | Use zonal resilience within the region. Validate the cost and failure assumptions before rollout. |
| NFR-3 | End-to-end p95 under 3 seconds for any rep question, including claims | Measure by workflow and cache state. Address cold pagination and quota constraints in the production design. |
| NFR-4 | Zero cross-tenant data in prompts, retrieved context, caches, logs, or answers | Derive scope from trusted identity and enforce it independently at every data boundary. |
| NFR-5 | Minimum necessary PHI in model inputs and logs, with six years of audit retention | Emit allowlisted audit metadata. Use tenant-specific protected audit archives in production. |
| NFR-6 | Explicit rep confirmation and exactly-once grievance filing | Record durable intent before dispatch. Reuse immutable request bytes and the same key. Reconcile uncertain outcomes. |
| NFR-7 | At most $0.03 average model and infrastructure cost per conversation at 20,000 per day | Account for model usage, infrastructure, audit storage, and warm capacity. |
| NFR-8 | Prairie Sky may move to its own tenancy on another cloud in year two | Keep storage and model interfaces portable. Document data migration and single-writer cutover. |
| NFR-9 | 250 concurrent reps at Monday peak, 8–10 a.m. local time | Define a representative production load test and capacity model. |

## Approved stack and solution architecture

- **Slice** uses Python 3.11, FastAPI, Pydantic, HTTPX, LangGraph, SQLite, and metadata-filtered BM25. pytest, Ruff, Docker, and GitHub Actions cover verification and packaging. Ruff is a repository standard and remains to be verified or wired into CI.
- **Request flow** authenticates the rep, binds the session to one member, classifies intent, gathers evidence through the gateway, and computes or renders the answer. Use a provider-independent structured classifier and an offline deterministic mode for reproducible runs. Monetary arithmetic uses decimal values. Dates use the mock clock. The model cannot choose tenant, credentials, network destination, or write authority.
- **Trust boundaries** treat rep messages, claims notes, KB text, tool descriptions, and tool responses as data. Apply typed tool allowlists, fixed origins, safe pagination, and projection before model input. Verify SSN suffix only inside the gateway. Do not disclose full SSN or send member identifiers to the provider directory. Scope KB indexes, cache keys, SQLite operations, and audit access to authorized context.
- **Write path** binds a draft and its explicit approval to the rep, tenant, member, and session. Persist the confirmed operation, key, and immutable request before sending. Replay a matching turn receipt. On a possible commit, retry only under a valid deduplication contract and within budget. Otherwise mark the intent pending reconciliation. The mock loses its grievance and deduplication state on restart, so production recovery needs durable downstream deduplication and lookup by operation ID.
- **Cloud design** uses regional GCP Cloud Run, HA Cloud SQL for PostgreSQL, Vertex AI through the model interface, workload identities, Secret Manager, and protected regional audit storage. Azure maps to zone-redundant Container Apps and PostgreSQL Flexible Server, Azure OpenAI through the adapter, managed identities, Key Vault, and immutable Blob Storage. See [production design](docs/design.md) and [architecture diagram](docs/architecture.md).

### External API contracts

| Route | Handling |
|---|---|
| `GET /health` | Read readiness and the fixed as-of date. |
| `GET /tenants/{tenant}/members` | Search by member ID, last name, or DOB. Contract inventory only for the bound-member slice. |
| `GET /tenants/{tenant}/members/{member_id}` | Member detail includes sensitive fields. Use narrowly projected results for internal verification and directory location. |
| `GET /tenants/{tenant}/members/{member_id}/benefits` | Enrollment, termination, plan data, and current-year accumulators. Benefit design is in the KB. |
| `GET /tenants/{tenant}/members/{member_id}/claims` | Up to five rows per page, newest first. Validate continuation and exhaust pages when the question requires totals. |
| `GET /tenants/{tenant}/claims/{claim_id}` | Check member ownership after reading claim detail. Omit free-text notes from model and audit projections. |
| `GET /tenants/{tenant}/members/{member_id}/grievances` | Read member filings and derive deadlines from tenant policy. |
| `POST /tenants/{tenant}/members/{member_id}/grievances` | Send validated category and description up to 2,000 characters. Treat expedite as a request flag. Use an idempotency key. New filing is `201`, matching replay is `200`, key and body conflict is `422`, and `503` may follow a commit. |
| `GET /directory/v3/providers` and `GET /directory/v3/providers/{npi}` | Separate API key. Search uses normalized specialty, ZIP, and authorized network. NPI lookup is inventoried but outside the slice. |

The gateway chooses the bearer credential from session scope. The mock accepts valid credentials without checking their tenant against the route. Treat `400`, `401`, `404`, `422`, `429`, `503`, timeout, and malformed responses as distinct internal outcomes with safe user-facing messages. Respect `Retry-After` when budget allows.

### Repository structure

| Location | Responsibility |
|---|---|
| `src/member_services/api.py`, `config.py` | HTTP contracts, authentication, startup, and external configuration |
| `gateway.py`, `knowledge.py` under `src/member_services/` | Governed upstream adapters, budgets, projections, and KB |
| `routing.py`, `workflow.py`, `store.py` under `src/member_services/` | Classifier, LangGraph and domain logic, SQLite state and audit |
| `src/member_services/evaluate.py`, `evaluation/report.json` | External-pack replay, scorer, gate, and sanitized baseline |
| `tests/`, `.github/workflows/`, `Dockerfile` | Verification, CI workflows, and image |
| `docs/design.md`, `docs/architecture.md`, `README.md`, `NOTES.md`, `PLANS.md` | Design, diagram, operations, submission notes, and execution plan |

## Milestones and dependencies

The estimates total **8 hours 45 minutes** for the assessed slice and review, within the brief’s 8–10 hour guide. The submission window is seven calendar days from receipt. Status reflects existing artifacts and execution evidence. Unchecked tasks are pending or lack the specified evidence. Proposed commit messages apply to approved future work. Preserve the existing history.

### M1. Project foundation and configuration

- **Objective** — Make the slice reproducible and keep assessment assets outside Git.
- **Dependencies** — Original brief and pack access. **Effort** — 45 minutes.
- **Status** — Implemented and verified locally on Python 3.11.

- [x] Package the Python application and expose a FastAPI factory.
- [x] Configure external pack path, upstream origin, rep credentials, model endpoint, and SQLite path. Keep secrets external.
- [x] Ignore the supplied pack, local environments, databases, and credentials.

- **Files** — `pyproject.toml`, `.gitignore`, `config.py`, `api.py`.
- **Acceptance** — Python 3.11 imports the package and starts with the external pack. Tenant and provider configuration do not require code changes.
- **Automated tests** — API startup with an injected health response and authentication with scoped test credentials.
- **Validation** — `python --version`, `python -m pip install -e '.[test]'`, `python -m pytest -q tests/test_workflow.py`.
- **Suggested commit** — `Bootstrap Python application and external configuration`.

### M2. Governed tool gateway and healthcare API integration

- **Objective** — Provide authorized, field-minimized reads and a controlled upstream write interface.
- **Dependencies** — M1. **Effort** — 1 hour 15 minutes.
- **Status** — Core gateway and contract checks implemented. Edge-case coverage can be strengthened during M9.

- [x] Derive route and credential scope from authenticated state. Verify claim ownership after lookup.
- [x] Project enrollment, accumulators, claims, and grievances. Keep SSN verification local and directory egress field-allowlisted.
- [x] Guard continuations, disable redirects, and handle timeouts and bounded quota retries.
- [ ] Add focused integration assertions for malformed responses and `Retry-After` budget boundary behavior if the review finds missing evidence.

- **Files** — `gateway.py`, `api.py`, `tests/test_workflow.py`.
- **Acceptance** — Wrong-scope and malformed requests fail closed. Claim totals require complete data. Directory requests contain only approved fields.
- **Automated tests** — Foreign tenant and claim ownership, unsafe continuation, pagination, quota exhaustion, upstream error projection, and directory egress.
- **Validation** — `python -m pytest -q tests/test_workflow.py`, followed by the M6 full-suite command.
- **Suggested commit** — `Enforce tenant and member scope at the tool gateway`.

### M3. Tenant-aware RAG and policy retrieval

- **Objective** — Retrieve only applicable plan facts and policy rules.
- **Dependencies** — M1 and the M2 gateway interface. **Effort** — 1 hour.
- **Status** — Implemented with quarantine behavior and full-suite evidence.

- [x] Parse front matter and policy rows. Quarantine documents whose folder and tenant metadata disagree.
- [x] Filter by tenant, product or `ALL`, year, and effective date before BM25 ranking.
- [x] Return source references and abstain when required evidence is missing. Keep claim notes and unrelated document instructions outside model context.

- **Files** — `knowledge.py`, `gateway.py`, `workflow.py`, `tests/test_workflow.py`.
- **Acceptance** — Current and renewal-year questions use authorized evidence. The misplaced virtual-care document appears in neither index.
- **Automated tests** — Tenant mismatch quarantine, product and year selection, missing evidence, and EV-23 safe outcome verification.
- **Validation** — `python -m pytest -q tests/test_workflow.py`, followed by the M6 full-suite command.
- **Suggested commit** — `Index authorized plan policy by tenant and effective date`.

### M4. LangGraph agent orchestration

- **Objective** — Deliver a bounded multi-turn API with deterministic evidence handling.
- **Dependencies** — M2 and M3. **Effort** — 1 hour 15 minutes.
- **Status** — Implemented and tested with deterministic routing and a simulated provider. Live-provider validation is open.

- [x] Implement conversation creation and typed turn actions under `/v1/conversations`.
- [x] Connect optional structured classification, evidence gathering, decimal arithmetic, calendar policy, and safe response rendering.
- [x] Enforce step, processing-time, and cost limits and emit redacted per-step events.
- [ ] Run a minimal live-provider smoke test if an approved endpoint and credentials are available. Record the actual model and mode used.

- **Files** — `api.py`, `routing.py`, `workflow.py`, `store.py`, `tests/test_model.py`.
- **Acceptance** — Routing cannot change scope or authorize writes. Unavailable evidence and exhausted budgets produce safe outcomes. Model requests contain only the approved vocabulary projection.
- **Automated tests** — Scope binding, duplicate turn handling, budget rejection, model projection, and invalid output.
- **Validation** — `python -m pytest -q tests/test_model.py tests/test_workflow.py`.
- **Suggested commit** — `Orchestrate bounded member-service conversations with LangGraph`.

### M5. Human approval and idempotent grievance submission

- **Objective** — File only the approved draft and prevent duplicate submissions.
- **Dependencies** — M2 and M4. **Effort** — 1 hour 15 minutes.
- **Status** — Implemented and verified for cancellation, concurrent confirmation, and committed `503` replay. Production reconciliation remains design work.

- [x] Present a draft, bind it to the session and rep, expire stale drafts, and require confirmation again after edits.
- [x] Persist the key and exact payload before dispatch. Return the existing receipt on replay.
- [x] Handle committed `503` with bounded same-key replay and surface ambiguous outcomes as pending reconciliation.
- [ ] Confirm isolated test coverage for edited, expired, and foreign drafts and for the no-new-write behavior after unresolved state.

- **Files** — `workflow.py`, `store.py`, `gateway.py`, `tests/test_workflow.py`.
- **Acceptance** — Zero writes before approval or after cancellation. Exactly one downstream filing for a confirmed operation during safe replay. An uncertain receipt cannot trigger a fresh create.
- **Automated tests** — Concurrent duplicate approval, same-key and same-body retry, cancellation, invalid scope, stale draft, and unresolved receipt.
- **Validation** — `python -m pytest -q tests/test_workflow.py`, then inspect M6 write-case results.
- **Suggested commit** — `Persist confirmed grievance intents and safely replay writes`.

### M6. Evaluation framework for all 40 supplied scenarios

- **Objective** — Score answers, refusals, confirmation sequence, and actual side effects.
- **Dependencies** — M2–M5. **Effort** — 1 hour 30 minutes.
- **Status** — Implemented. The committed run scored 36/40 raw with four verified conflicts and passed the gate.

- [x] Read scenarios and expected facts from the external pack. Replay all turns against an isolated mock and verify grievance counts before and after each turn.
- [x] Score typed facts and behaviors. Report case IDs, sanitized outcomes, timing, and estimated model cost.
- [x] Gate on all safety cases, previously passing cases, and a minimum of 36 raw passes with no other failures. Verify each documented source conflict independently and retain its raw failure.
- [ ] Recheck the existing suite-specific case selectors against the repository's no-hardcoded-case-ID rule. Move selectors to external or configuration-driven evaluation metadata if approved. Keep runtime behavior entirely case-agnostic.

- **Files** — `src/member_services/evaluate.py`, `tests/test_evaluation.py`, `evaluation/report.json`.
- **Acceptance** — All 40 scenarios produce scored rows. Unauthorized writes and regressions fail the gate. Fixture-backed exceptions never replace raw results.
- **Automated tests** — Fact matcher, gate rejection of unsupported exceptions and regressions, full replay of all conversations, and write-side-effect verification.
- **Validation** — With README configuration set, run `python -m member_services.evaluate --baseline evaluation/report.json --report runtime-report.json`. Compare sanitized results and update the committed report only from an actual final run.
- **Suggested commit** — `Score all assessment scenarios and gate verified regressions`.

### M7. Production hardening, Docker, and CI/CD

- **Objective** — Provide repeatable packaging, validation, and operational safeguards within the slice.
- **Dependencies** — M1–M6. **Effort** — 1 hour.
- **Status** — Docker and CI definitions exist. Docker execution, hosted CI, and Ruff verification remain open.

- [x] Package a non-root Docker runtime with external read-only pack access and writable state.
- [x] Add public tests and image build. Isolate the opt-in full evaluation behind a protected environment and reviewed external pack.
- [ ] Run Ruff and wire it into CI if needed. Build and smoke-test the container and verify hosted jobs when a runner and repository are available.
- [ ] Confirm audit and access logs exclude PHI, credentials, prompts, and raw payloads. Record local and production operational limitations.

- **Files** — `Dockerfile`, `.dockerignore`, `.github/workflows/`, `pyproject.toml`, `README.md`.
- **Acceptance** — The approved image starts with mounted configuration. Public CI uses no pack or secrets. Full evaluation is private-input and emits only sanitized output.
- **Automated tests** — pytest, Ruff, container build and configured health smoke, and opt-in replay.
- **Validation** — `ruff check .`, `python -m pytest -q`, `docker build -t member-services .`, then run the README container command and check `GET /health`.
- **Suggested commit** — `Validate container packaging and protected CI workflows`.

### M8. Architecture documentation, ADRs, README, and NOTES

- **Objective** — Deliver concise, reproducible evidence and rationale for Emids reviewers.
- **Dependencies** — M1 for setup, M2–M5 for design, and M6–M7 for verification evidence. **Effort** — 45 minutes.
- **Status** — Core documentation exists. Final review and evidence refresh are pending.

- [x] Document GCP deployment, one-page Azure mapping, threat model, four ADRs, and architecture diagram.
- [x] Document mock, agent, tests, evaluation, Docker, and the actual deterministic development mode.
- [x] Capture fixture conflicts, decisions, AI use, and proposed next work in NOTES.
- [ ] Verify final page limits, README commands, links, and actual execution claims on the release candidate.

- **Files** — `docs/design.md`, `docs/architecture.md`, `README.md`, `NOTES.md`, `PLANS.md`.
- **Acceptance** — Design occupies at most four pages plus one diagram, includes 3–5 ADRs and a one-page secondary-cloud mapping. NOTES fits one page and explains where AI helped or was wrong.
- **Automated tests** — Reuse setup, smoke, and evaluation commands as documentation checks. Inspect the rendered diagram and page counts manually.
- **Validation** — `git diff --check`, `python -m pytest -q`, and the README's exact startup and evaluation commands from a clean checkout.
- **Suggested commit** — `Document deployment decisions and reviewer reproduction steps`.

### M9. Independent security review and final submission validation

- **Objective** — Have the Lead AI Architect or a separate reviewer confirm safety, evidence, and publishability.
- **Dependencies** — M1–M8. **Effort** — 45 minutes.
- **Status** — Pending independent review, owner sign-off, and publication. No GitHub remote was configured at review.

- [ ] Review tenant isolation, member ownership, credentials, caches, state, log redaction, and model input. Record reviewer findings and remediation evidence.
- [ ] Exercise claims-note, KB, and manifest injection regressions, foreign-scope requests, and approval and idempotency failure modes.
- [ ] Re-run the suite, review raw and gated outcomes, and scan tracked files and history for secrets or supplied data. Log approved implementation work and actual results in `DEVELOPMENT_LOG.md`.
- [ ] Obtain owner approval before commits and explicit approval before push. Verify the public destination and final commit hash before submission.

- **Files** — `tests/`, `evaluation/report.json`, `NOTES.md`, `DEVELOPMENT_LOG.md`, and final Git history.
- **Acceptance** — Reviewer sign-off identifies tested boundaries and unresolved findings. The approved final commit contains no supplied data or secrets and is reproducible from a clean checkout.
- **Automated tests** — Full pytest and evaluation gate, secret and artifact scan over tracked content and history, and verified CI evidence when available.
- **Validation** — `python -m pytest -q`, the M6 evaluation command, `git diff --check`, `git ls-files`, `git log --stat --all`, `git status --short`, and `git rev-parse HEAD`.
- **Suggested commit** — `Record independent review and final verification evidence`.

### M11.1. Release blocker analysis and remediation

- **Scope** Benchmark the default three-second profile before and after claims changes. Run the original 40 cases, lint and the complete test suite. Preserve the historical baseline and record the six-second profile separately as a diagnostic.
- **Claims** Keep direct ID reads and shared HTTP clients. Resume validated point-lookup prefixes within a bounded freshness window. Require complete traversal for aggregate counts and return unavailable if it cannot finish. Use the upstream continuation contract.
- **Grievances** Persist the approved key, immutable payload and hash before dispatch, record attempts, and use transactional operation states. Recover with bounded original-key replay only under a verified durable backend contract. Route the supplied mock's unresolved operations to manual review.
- **Exit evidence** See the M11.1 entry in DEVELOPMENT_LOG.md and the before, after and diagnostic reports in `evaluation/`. Claims and automatic recovery remain downstream-dependent. Stop for owner review before M12 or publication.

### M12. Submission preparation

- Verify the candidate from an isolated source snapshot and a fresh Python 3.11 environment.
- Check candidate artifacts and Git history for supplied-pack material and credential signatures. Keep local agent instructions outside the submission candidate.
- Preserve the failed three-second gate and label the six-second run as diagnostic. Run `.github/workflows/docker-smoke.yml` on `ubuntu-latest` after approval to push the candidate to a private repository. The workflow uses only generated fixtures and runs on demand and on relevant changes. Record the run URL and result before marking Docker verification complete.
- Prepare the candidate for owner review. Public visibility, a destination, the final commit hash, and submission remain subject to separate publication authorization.

## Testing strategy and traceability

- **Unit** — Test monetary and calendar calculations, evidence filtering, model projection and budget, fact matching, and gate rules with invented fixtures.
- **Integration** — Use injected HTTP transports for auth, ownership, pagination, retry, and error contracts. Run the full suite against a newly started copy of the reviewed external mock for realistic latency, quota, and filing effects.
- **Security** — Assert no unauthorized outbound call, PHI in model input or traces, cross-tenant evidence or cache hit, or grievance before approval. Test cancellation, replay, and malicious instructions in input, retrieved data, and third-party metadata. Check actual outbound fields and downstream record counts.
- **Evaluation** — Compare facts and outcomes, including intermediate turns and side effects. Keep only sanitized metadata in committed reports. Baseline mode is deterministic. A simulated model adapter test does not establish live model quality or cost.

| Requirements | Cases or verification | Milestones |
|---|---|---|
| F1, F3, NFR-4 | Benefits, accumulators, enrollment, or termination in EV-01–08, EV-23–28, EV-30, EV-33, EV-35, EV-37 | M2–M4, M6 |
| F1–F3, NFR-3 | Claims, appeal windows, and unavailable evidence in EV-09–14, EV-22, EV-29, EV-31, EV-34, EV-38, EV-40 | M2–M4, M6 |
| F2, F4, F5, NFR-6 | Grievance deadlines, approval, cancellation, and replay in EV-13, EV-15–17, EV-32, EV-36 | M2, M4–M6, M9 |
| F2, F5, NFR-4, NFR-5 | SSN, directory egress, and cross-tenant refusal in EV-18–21 and EV-39. Also inspect audit and model projections. | M2–M4, M6–M7, M9 |
| F2, F5, NFR-7 | Budget and redaction tests, sanitized traces, and production cost assumptions | M4, M6–M9 |
| F6 | Exactly 40 scored conversations, independent source checks for four mismatches, and regression-gate tests | M6, M9 |
| F7, NFR-1–3, NFR-7–9 | GCP/Azure design, production monitoring and capacity plan, ADRs, and delivery checklist | M7–M9 |

## Risks, assumptions, and trade-offs

| Risk or assumption | Decision and consequence |
|---|---|
| Permissive mock tenant authorization and member-agnostic claim detail | The gateway owns deterministic authorization. Production also needs upstream tenant enforcement and real rep-to-member access checks. |
| Vendor directions request sensitive identifiers and claim or KB text can contain instructions | Approve tools statically, restrict egress, omit raw notes, validate provenance, and treat all content as evidence. A rep's identity verification is assumed to occur before session selection. |
| Four source conflicts in the reviewed pack | EV-23 refers to a wrong-tenant benefit, EV-25 appears to combine a future limit with current accumulators, EV-34 conflicts with claim status, and EV-37 conflicts with enrolled-product terms. Do not encode fixture answers or alter the mock. Revalidate independently if the pack changes. |
| Serial claims pages and quota threaten the three-second target | The observed deterministic baseline was 3.031 seconds p95 with a six-second evaluation claims allowance. Use a freshness-bounded projection or better upstream query contract for production. Measure under target load before claiming compliance. |
| Regional outage and dependency failures threaten availability | Use zonal redundancy within one region and track degraded outcomes. A composite 99.99% SLO is unproven. |
| Local SQLite and mock restart behavior limit recovery | Keep the slice single-process. Production requires durable deduplication, reconciliation and shared HA state before scaling write replicas. |
| Policy year and coverage interpretation | Use the pack clock and applicable service or renewal year. Match accumulator year, use inclusive deadline dates, and distinguish terminated current coverage from historical claims. |
| Timebox and optional enhancements | Complete the runnable slice, regression evidence, and docs first. Defer live cloud deployment, a production claims projection, broad model comparison, and extended load testing. |

## Definition of done

The working slice is implemented, tested, documented, and supported by reproducible execution evidence. Every mandatory requirement has a mapped artifact, test, or explicit production design treatment. All 40 cases run, the security and regression gate passes, and the committed sanitized report preserves raw discrepancies. An independent reviewer has reviewed the security boundaries and any open issues are recorded. Production claims, live-model testing, Docker, and hosted CI are described according to evidence available at the final commit.

## Final GitHub submission checklist

- [ ] Include the code, tests, evaluator, justified gate, sanitized last-run report, README, production design, diagram, and NOTES in the approved release commit.
- [ ] Confirm the design page limit, one-page Azure mapping, 3–5 ADRs, and one-page NOTES. If submitting a PDF, include its source.
- [ ] Confirm configurable pack path and model provider. Verify a clean checkout with a reviewer-provided external pack.
- [ ] Confirm no supplied services, fixtures, KB, member data, secrets, local databases, or raw traces in public history.
- [ ] Preserve real incremental commits. Obtain owner approval before any new commit and explicit approval before pushing.
- [ ] Verify the GitHub repository is public and the reviewed final commit is present before the seven-calendar-day deadline.
- [ ] Reply to the original assessment email with only the public repository URL and the final pre-deadline commit hash.
