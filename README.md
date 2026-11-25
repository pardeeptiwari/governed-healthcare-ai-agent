# Governed member services

A FastAPI assistant for health plan representatives. A LangGraph workflow routes questions through a tenant- and member-bound gateway. Answers use the external plan documents and mock APIs. Grievance filing requires confirmation of the displayed draft.

## Setup

Use Python 3.11. Keep the extracted assessment pack outside this repository. Use `.env.example` as a template and export the values in your shell. The application reads the process environment. Run these commands in Bash or adapt environment assignments for PowerShell.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test,lint]'
export ASSIGNMENT_PACK=/absolute/path/to/extracted/pack
export REP_CREDENTIALS_JSON='{"replace-with-random-rep-key":{"tenant":"plan_a","rep":"demo-a","member_ids":["<approved plan_a member ID>"]},"replace-with-another-random-key":{"tenant":"plan_b","rep":"demo-b","member_ids":["<approved plan_b member ID>"]}}'
export TENANT_TOKENS_JSON='{"plan_a":"<token from data README>","plan_b":"<token from data README>"}'
export DIRECTORY_KEY='<directory key from data README>'
```

Start the supplied mock on loopback in a separate terminal. Keep its raw request logs local or redirect them to a null sink.

```sh
python -B "$ASSIGNMENT_PACK/mock_services/server.py" --port 8090 >/dev/null 2>&1
```

Run the slice as one process with a writable local SQLite database. Set `MOCK_URL` to change the upstream origin and `STATE_DB` to change the database location.
The evaluation clock defaults to the date reported by the mock health endpoint. Set `EVALUATION_AS_OF` to an ISO date for a controlled local run, and leave it unset for the supplied benchmark.

```sh
python -m uvicorn member_services.api:create_app --factory --no-access-log
```

Check startup from another terminal with `curl --fail http://127.0.0.1:8000/health`. Expect HTTP 200 and `{"status":"ok"}`.

### Conversation API

Send `Authorization: Bearer <rep key>` on both endpoints. Configure each rep's authorized member IDs in the server-side credential configuration. Missing entitlements deny access. Open a session with `POST /v1/conversations` and body `{"member_id":"<member ID>"}`. Use the returned `conversation_id` in `POST /v1/conversations/{id}/turns`. For example:

```json
{"turn_id":"turn-1","message":"What is the in-network specialist copay?"}
```

For a filing request, the assistant returns a draft. Read it back to the member, then send a new turn with `{"turn_id":"turn-2","action":"confirm","draft_id":"<draft ID>"}` or `"action":"cancel"`. Reuse the original turn ID and payload when retrying a turn. A conflicting payload returns HTTP 409. An unresolved filing blocks new submissions for the same tenant and member. The worker sends unresolved operations to manual review by default. Set `GRIEVANCE_DURABLE_REPLAY=true` only for a backend that atomically persists the original operation key, payload fingerprint and receipt across restarts and failover. Leave it off for the supplied mock.

### Model configuration

Development and the committed baseline use the built-in deterministic intent classifier. It operates over general service and policy categories, with no evaluation question or answer lookup. To enable a model, set `MODEL_URL` to an OpenAI-compatible chat completions endpoint, `MODEL_NAME` to the model or deployment name, and `MODEL_KEY` if required. Configure `MODEL_INPUT_PER_MILLION` and `MODEL_OUTPUT_PER_MILLION` in USD for paid inference. Model calls receive a vocabulary projection for classification. Application logic computes amounts and deadlines and authorizes writes. The provider contract expects a JSON response and token usage when available.

### Test and evaluate

```sh
python -m ruff check .
python -m pytest -q
python -m member_services.evaluate --baseline evaluation/report.json --report runtime-report.json
```

The evaluator starts a fresh supplied mock on loopback port 8091. Use `--port` if needed. It derives case IDs, isolated fixture entitlements and safety checks from the external scenarios. The gate requires at least 36/40 raw passes, rejects safety failures, and verifies any candidate fixture conflict against authorized sources. With the committed baseline specified, only previously verified conflicts can qualify for an exception. The four mismatches still count as raw failures. The runner defaults to the application's claims allowance. Use `--claim-budget-seconds 6` for a diagnostic comparison with the committed baseline. Claims that require serial pages or quota recovery can fail the default gate.

The [default-profile release candidate report](evaluation/m11.1-after.json) records 32/40 raw passes, nine passing safety cases, a 3.013-second p95 and a failed gate. Four claims cases failed at the default allowance in addition to the four source-verified fixture conflicts. The [six-second diagnostic](evaluation/m11.1-diagnostic.json) scored 36/40 and passed. Both used deterministic routing. The model adapter was tested with a simulated provider. The [historical baseline](evaluation/report.json) supplies the previously reviewed fixture exceptions to the gate.

The regression workflow runs Ruff and pytest. The manually dispatched full evaluation workflow uses an ephemeral runner and a protected `assessment-evaluation` environment. Restrict the environment to trusted branches and require reviewer approval. Configure the three API credential secrets above, a direct private HTTPS download URL in `PACK_ARCHIVE_URL`, the reviewed ZIP checksum in `PACK_ARCHIVE_SHA256`, and optionally the pack folder within the ZIP in `PACK_SUBDIR`. Only run a copy whose mock script you have reviewed. The workflow uploads only the sanitized report and expires it after seven days.

### Docker

For hosted verification, use **Actions → Docker smoke → Run workflow** once the workflow is available on the repository's default branch. Relevant pushes and pull requests also trigger it. The Ubuntu job builds the image and checks the default entry point, health, non-root execution, writable state, member authorization, a synthetic benefits request, and SSN refusal. It uses generated fixtures, prints container logs on smoke failure, and removes the containers after the checks. Hosted Docker verification is pending an actual successful run. It is independent of the failed 32/40 default evaluation gate.

```sh
docker build -t member-services .
docker run --rm -p 127.0.0.1:8000:8000 \
  --env-file /absolute/path/to/local.env \
  --mount type=bind,src="$ASSIGNMENT_PACK",dst=/pack,readonly \
  --mount type=volume,src=member-services-state,dst=/state member-services
```

In the Docker env file, set `ASSIGNMENT_PACK=/pack` and `MOCK_URL` to a mock address reachable from the container. Write JSON values directly after `=`, without the shell quote characters used in the Bash example. For the first run, omit `STATE_DB` to use the image default inside the mounted volume. Use a private container network or an explicitly scoped host listener for the mock.

The application entry point suppresses HTTP client transport logging. Keep Uvicorn access logging disabled and the mock's access output off shared logs. Conversation audit events carry decisions and timing. The SQLite database also stores operational state and receipts, so restrict access to its file and volume. Six-year protected audit retention belongs to the production design.

See the [production design and ADRs](docs/design.md), [architecture diagram](docs/architecture.md), and [submission notes](NOTES.md).
