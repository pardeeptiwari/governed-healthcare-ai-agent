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
