# Development log

## 2026-11-25 · M1 project foundation

Reviewed `AGENTS.md`, the approved plan, and the original brief, API contract, and mock startup and health behavior. Extended the existing package and application composition without adding new member-service workflows.

- `domain.py` contains Pydantic models for session creation, turns, health, and classifier output.
- `ports.py` defines the evaluation clock, model provider, and state repository interfaces, with fixed-clock and model-completion value types.
- `model_provider.py` adapts the existing OpenAI-compatible classifier request to the provider interface. The routing layer still controls minimization, budgets, and allowed intents.
- `config.py` resolves KB, evaluation, and mock script paths relative to the external `ASSIGNMENT_PACK` root, suppresses credential fields in settings representations, and supports an optional fixed date.
- `api.py` composes injected dependencies, defaults to SQLite and the mock's clock, and exposes a typed `GET /health` response. The evaluation runner uses the same path configuration.
- `tests/test_foundation.py` checks environment-based configuration, credential representation, and application-level clock, provider, and repository injection.
- `.env.example` and the README document configuration without shipping credentials. `pyproject.toml` adds the lint extra and explicit Ruff and pytest configuration. `.gitignore` excludes the Ruff cache.

### Actual validation

| Check | Result |
|---|---|
| `python -m ruff check .` | Passed with Ruff 0.17.6. The project selects Python syntax, Pyflakes, and import checks. |
| `python -m pytest -q` | 8 passed on Python 3.11.18. |
| External mock connectivity | Started the inspected script on loopback port 8093. `GET /health` returned 200 with the fixed date `2026-11-15`. |
| Application smoke test | FastAPI startup reached the actual mock. The application's `GET /health` returned 200. The process was stopped and the temporary SQLite state cleaned up. |

The 40-case benchmark was outside this foundation validation. The clock, state, and model-provider injection tests used synthetic fixtures. AI assisted with source review, implementation, and local verification. Changes are pending owner review before committing.

## 2026-11-25 · M2 governed tool gateway

Tightened the existing gateway at the upstream boundary. Claim requests validate identifiers, project nested fields, and validate status and dates before caching. Pagination checks the same scoped route, the expected next cursor, and the page size before proceeding. Only complete snapshots are cached. The directory gateway enforces the shared specialty allowlist before making a request and validates its response shape. Claims retries require a finite nonnegative `Retry-After` value and remaining time and step budget.

- Ruff passed. pytest passed all 15 tests, including new cases for retry limits, malformed pages, nested-field projection, and complete snapshot caching.
- The external mock replay passed the gate with 36/40 raw passes and four verified fixture conflicts. The rerun report is local and ignored. The committed baseline was retained for comparison.
- AI assisted with review, implementation, and verification. M2 changes are pending owner review before committing.

## 2026-11-25 · M3 tenant-aware policy retrieval

Hardened policy ingestion and retrieval. Front matter requires explicit delimiters and unique keys. Documents with wrong-tenant or invalid metadata are quarantined. Markdown tables inside fenced examples are skipped. Eligible rows are filtered by tenant, product, plan year, and effective date before ranking. Retrieval keeps the source filename and abstains when equally applicable rows disagree.

- Ruff passed. pytest passed all 18 tests. New fixtures cover tenant and product isolation, effective-date boundaries, renewal years, missing evidence, metadata quarantine, and conflicting rows.
- The external mock replay passed the regression gate with 36/40 raw passes and four verified fixture conflicts. The wrong-tenant document remained excluded. The rerun report is local and ignored.
- AI assisted with review, implementation, and verification. M3 changes are pending owner review before committing.

## 2026-11-25 · M4 bounded LangGraph orchestration

Model usage is accounted for even when the classifier response is invalid or proposes a reserved operation. Invalid counters leave the estimate in place. A response that exceeds the remaining cost budget cannot route the workflow. New API tests verify that duplicate read turns replay the prior result without another upstream request or budget charge, conflicting turn payloads return 409, and exhausted step or time budgets stop before upstream I/O.

- Ruff passed and pytest passed all 25 tests, including model input projection and rejected completion tests.
- All 40 supplied scenarios ran against the local mock. The gate passed with 36/40 raw passes and four verified fixture conflicts. The rerun report is local and ignored.
- No approved live model endpoint was configured. Provider boundary verification used a simulated provider. AI assisted with implementation and test verification. M4 is pending owner review before committing.

## 2026-11-25 · M5 grievance approval and idempotency

Made draft expiry inclusive and blocked further draft actions while a confirmed operation in the conversation is sending or pending reconciliation. Tests verify that edits invalidate prior approval, a draft from another conversation or tenant cannot be approved, and the approved request body matches the displayed revision. A simulated post-commit timeout verifies the durable key and request body, stable turn replay, and one outbound create attempt.

- Ruff passed. pytest passed all 28 tests.
- The full external-mock replay passed the regression gate with 36/40 raw passes and four verified fixture conflicts. The confirmation and cancellation cases passed.
- The local reconciliation block applies to the conversation. Production recovery across downstream restarts still requires durable downstream deduplication and operation lookup. AI assisted with implementation and verification. M5 remains pending review before committing.

## 2026-11-25 · M6 evaluation framework

Removed hardcoded case ID selectors from the evaluator. It now validates 40 unique IDs from the supplied suite, derives safety cases from behavior and request intent, and verifies candidate conflicts using scoped live claim records or applicable policy and accumulators. Disagreeing dollar amounts must be demonstrated from evidence before a conflict can qualify. An existing baseline prevents new conflict exceptions, and suffix verification is scored using the structured match result.

- Ruff passed and all 29 pytest tests passed.
- The full external-mock replay passed the regression gate with 36/40 raw passes, nine passing safety cases, and four verified fixture conflicts. Measured p95 was 3.031 seconds. Refreshed the sanitized report from this run.
- AI assisted with implementation and verification. M6 remains pending owner review before committing.

## 2026-11-25 · M7 packaging, CI, and logging

Added Ruff and a Linux container smoke test to public CI. The test generates a small KB and loopback health stub, then checks container health, non-root execution, and writable SQLite state. Bounded both GitHub jobs and the protected pack download, checked that the configured pack subdirectory stays within the reviewed archive, and set a seven-day report artifact retention. The application and evaluator entry points now suppress raw HTTP client diagnostics; the Docker command continues to disable Uvicorn access logging.

- Ruff and all 30 pytest tests passed. Both workflow YAML files parsed, and embedded Bash commands and the image smoke script passed Bash syntax checks.
- Confirmed with DEBUG logging enabled that HTTPX request URLs are suppressed. A synthetic API test verified audit events exclude the raw request, sample identifiers, and credentials.
- The full mock replay passed the regression gate with 36/40 raw passes and four verified fixture conflicts.
- Docker and hosted Actions still require verification. No container engine or Linux subsystem was available locally, and no GitHub remote was configured. AI assisted with implementation and verification. M7 is pending review before committing.

## 2026-11-25 · M8 architecture and submission documentation

Clarified the README health check, Docker environment format, and baseline evidence. Updated the architecture diagram to show explicit draft approval and the durable intent before the gateway write. Documented the slice-to-production prerequisites and explained how the initial AI-assisted analysis missed three source conflicts.

- Installed the package and test extras in a fresh Python 3.11 environment from a clean archive of the M7 commit. Ruff and all 30 tests passed. The README's mock and Uvicorn commands returned HTTP 200 on their health routes.
- Ran the documented evaluator command from that snapshot. The gate passed with 36/40 raw passes and four verified fixture conflicts. Measured p95 was 3.015 seconds on this run. The committed baseline was retained for comparison and is identified separately in the README.
- Verified relative documentation links and four ADRs. A headless Chrome preview confirmed each of the four design sections fit an A4 text area at 11 pt, including the final Azure mapping. NOTES fit one page and the separate Mermaid diagram rendered.
- Docker, hosted Actions, and live model execution remain unverified. AI assisted with documentation and local verification. M8 remains pending owner review before committing.

## 2026-11-25 · M9 release validation and security review notes

This is an implementation-side review of the release candidate. The repository owner approved M9 and the local release commit after receiving these findings.

| Boundary | Evidence and remaining limits |
|---|---|
| Tenant and member scope | Reviewed identity-derived tenant routes, bound conversation scope, claim ownership, indexed policy provenance, and tenant/member claim cache keys. API and gateway tests cover foreign scope and mismatched ownership. The slice assumes upstream caller verification and uses demo rep credentials. Real rep-to-member authorization remains a production prerequisite. |
| Untrusted content and minimum necessary data | The gateway uses a reviewed directory field allowlist and excludes claim notes from its projection. Retrieval quarantines wrong-tenant files and excludes fenced examples. Tests cover the directory request contract, note injection without a write, model projection, and audit-field redaction. Raw transport diagnostics are suppressed at CLI entry points. SQLite contains operational state and needs restricted access. |
| Write approval and recovery | Tests cover cancellation, edited and expired drafts, foreign confirmations, concurrent replay, and a lost acknowledgment. The local reconciliation block applies within a conversation. Production recovery across processes and downstream restarts requires durable deduplication and lookup by operation ID. |
| Publication hygiene | Scanned 85 unique tracked historical blobs across 12 commits for prohibited supplied-pack paths, exact mock credentials, and selected private-key and GitHub token signatures. None matched. The refreshed report contains only allowlisted case metadata, timings, and cost estimates. |

- Ruff passed and all 30 pytest tests passed. The final mock replay ran all 40 conversations, passed all nine safety cases, and passed the gate with 36/40 raw passes and four verified fixture conflicts. Measured p95 was 3.016 seconds, above the three-second target.
- Refreshed the sanitized release-candidate report and aligned the README and NOTES. Docker, hosted Actions, and live model execution remain unverified.
- GitHub publication remains pending an approved destination and explicit push approval. The submission URL and final hash must be checked after publication and before the deadline calculated from the original receipt date.

## 2026-11-25 · Independent review remediation

The owner approved work on all findings in the independent review. AI assisted with the changes and verification below.

- **H1** The slice now requires a server-configured member entitlement for conversation creation and every turn. A same-tenant regression test verifies that denied requests make no upstream call. The evaluator provisions fixture entitlements only inside its isolated application. Corporate identity and entitlement provisioning remain deployment dependencies.
- **H2** The evaluator and protected workflow use the application claims allowance by default. A six-second override remains available for diagnostics. The supplied serial and throttled claims API still misses the default target. A freshness-bounded read model needs an authorized feed or a revised upstream query contract.
- **H3** The SQLite filing path atomically checks for an unresolved intent for the same tenant and member before sending. The lost-acknowledgment regression now covers a second conversation. Automatic recovery remains open because the supplied service has no operation lookup and discards deduplication on restart. The slice continues to use a single process.
- **H4** Configured time and price limits must be finite. Model URLs require explicit price settings, and the turn lock has a bounded wait. SQLite finalization, provider-side cost guarantees and cross-worker accounting remain open.
- **M1–M6 and L2** Added allowlisted denial and replay log events, a negated-money scoring check, expedited draft handling, a stronger continuation regression, a stateful container smoke step, and updated authorization, workflow, and evaluation documentation. Complete audit retention, typed scoring, wider benefit and conversation semantics, and deployment verification remain open. The single-node graph and retrieval optimization in L1 remain open.

| Check | Result |
|---|---|
| Python 3.11.18 pytest | 32 passed |
| Ruff, Bash smoke-script syntax, Git whitespace check | Passed |
| Default-profile external mock replay | 32/40 raw, 9/9 safety, 3,016 ms internal p95, gate failed. EV-10, EV-11, EV-12 and EV-29 failed behavior in addition to the four documented fixture conflicts. |
| Six-second diagnostic external mock replay | 36/40 raw, 9/9 safety, 3,031 ms internal p95, gate passed with the four documented fixture conflicts. |

The tracked evaluation report remains the historical baseline. Docker execution, hosted CI, live inference, cloud SLOs, and public publication remain unverified.

### Follow-up verification

- Persisted the active filing key and approved request body in a dedicated SQLite reservation keyed by tenant and member. Startup migrates unresolved intents from existing conversation records. A regression test verifies the reservation survives reopening SQLite and replacing the conversation payload. Durable downstream lookup is still needed to resolve ambiguous filings safely.
- Added UTC timestamps and tenant scope to completed-turn audit events, bypassed BM25 for exact authorized policy topics, and documented an assumed peak workload for production load testing.
- Python 3.11.18 ran all 33 tests successfully. Ruff and the Git whitespace check passed. The default external replay scored 32/40 with a 3,015 ms internal p95 and failed the gate. The six-second diagnostic scored 36/40 with a 3,031 ms internal p95 and passed the gate. Both passed all nine safety cases.

## 2026-11-25 · M11.1 release blocker analysis and remediation

- Verified the original brief, data contracts, mock implementation, M11 evidence and current request paths. Kept the source pack read-only. Added client-observed turn latency and gateway downstream and retry counts to the benchmark. The before and after default runs each started a fresh mock and SQLite database and replayed the same 40 cases in order with a three-second allowance.
- Claims point lookups can resume validated tenant/member page prefixes for up to 15 seconds. The clock starts at the first fetch and does not slide. Aggregate counts cannot resume incomplete prefixes. Deadline exhaustion returns unavailable. Exact IDs still use direct lookup, and the shared HTTP clients remain in place.
- Added transactional filing states, immutable payload hashes, durable attempt history and a bounded recovery worker. Background replay requires a verified durable downstream idempotency contract. With the supplied mock, unresolved operations go to manual review with the original reservation still held.
- Isolated mock probe returned HTTP 503 on first dispatch, HTTP 200 with the replay header for the same key/body, and HTTP 422 for the same key with a different body. The grievance list contained one matching record. A fresh downstream process showed zero matching records. The probe made no write after the restart.
- 37 pytest tests and Ruff passed. The default profile remained at 32/40 with nine safety cases passing. Raw before/after p50 was 37/48 ms, p95 was 3,031/3,013 ms, and maximum was 3,065/3,034 ms. Both recorded 48 downstream calls, 17 claims calls and four retries. The six-second diagnostic passed at 36/40 with four source conflicts and nine safety passes. It recorded p50 55 ms, p95 2,537 ms, maximum 4,064 ms, 45 downstream calls, 14 claims calls and four retries.

The [M11.1 review](FINAL_REVIEW.md#m111-release-blocker-review) records the remaining design dependencies and acceptance criteria. M12 and publication await owner review.

## 2026-11-25 · M12 submission preparation

The owner approved moving forward with the M11.1 limitations disclosed. The README now leads with the failed default-profile gate and links the successful six-second diagnostic separately.

- Built and installed the candidate wheel from an isolated source snapshot in a fresh Python 3.11 environment. The host blocked the uv launcher, so the offline verification reused locally installed dependencies and cached setuptools. All 37 tests passed against the installed wheel. The existing Ruff executable passed against an isolated source snapshot.
- Started Uvicorn and the supplied mock on loopback. Both health checks returned 200. An authorized conversation returned 201 and its first turn returned 200 with the expected outcome.
- Scanned 40 candidate files and 117 Git history blobs for three exact supplied credentials and selected private-key and GitHub token signatures. None matched. The candidate path inventory excluded the supplied pack and local agent instructions. Checked local Markdown links and the allowed metadata fields of all three M11.1 reports.
- Parsed both workflow definitions, checked the Bash smoke script syntax, and passed the Git whitespace check. Docker, Podman and WSL were unavailable. The container and hosted jobs still need execution evidence. Model and production performance evidence remain open.

The prepared candidate remains local. A reviewed Git commit, GitHub destination, public visibility, and submission are still pending. The default three-second gate remains failed at 32/40.

### Hosted Docker smoke preparation

Moved container verification to the dedicated Ubuntu Actions workflow. It checks the default image command, readiness, health, non-root execution, writable SQLite state, member authorization, a synthetic benefits turn and SSN refusal. It logs failures before container and network cleanup. The default evaluation gate remains a separate check.

Workflow structure, Bash syntax and Git whitespace validation passed. The targeted scan covered 41 candidate files and 117 historical blobs, with no supplied credentials, sampled fixture identifiers, selected secret signatures, or supplied-pack paths found. A successful hosted Docker run is still pending. No commit or push was made.
