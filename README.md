# Governed member services

A FastAPI assistant for health plan representatives. A LangGraph workflow routes questions through a tenant- and member-bound gateway. Answers use the external plan documents and mock APIs. Grievance filing requires confirmation of the displayed draft.

## Setup

Use Python 3.11. Keep the extracted assessment pack outside this repository. Use `.env.example` as a template and export the values in your shell. The application reads the process environment. Run these commands in Bash or adapt environment assignments for PowerShell.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test,lint]'
export ASSIGNMENT_PACK=/absolute/path/to/extracted/pack
export REP_CREDENTIALS_JSON='{"replace-with-random-rep-key":{"tenant":"plan_a","rep":"demo-a"},"replace-with-another-random-key":{"tenant":"plan_b","rep":"demo-b"}}'
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

Send `Authorization: Bearer <rep key>` on both endpoints. Open a session with `POST /v1/conversations` and body `{"member_id":"<member ID>"}`. Use the returned `conversation_id` in `POST /v1/conversations/{id}/turns`. For example:

```json
{"turn_id":"turn-1","message":"What is the in-network specialist copay?"}
```

For a filing request, the assistant returns a draft. Read it back to the member, then send a new turn with `{"turn_id":"turn-2","action":"confirm","draft_id":"<draft ID>"}` or `"action":"cancel"`. Reuse the original turn ID and payload when retrying a turn. A conflicting payload returns HTTP 409. A pending reconciliation outcome requires investigating the downstream record before any new filing attempt.

### Model configuration

Development and the committed baseline use the built-in deterministic intent classifier. It operates over general service and policy categories, with no evaluation question or answer lookup. To enable a model, set `MODEL_URL` to an OpenAI-compatible chat completions endpoint, `MODEL_NAME` to the model or deployment name, and `MODEL_KEY` if required. Configure `MODEL_INPUT_PER_MILLION` and `MODEL_OUTPUT_PER_MILLION` in USD for paid inference. Model calls receive a vocabulary projection for classification. Application logic computes amounts and deadlines and authorizes writes. The provider contract expects a JSON response and token usage when available.

### Test and evaluate

```sh
python -m ruff check .
python -m pytest -q
python -m member_services.evaluate --baseline evaluation/report.json --report runtime-report.json
```

The evaluator starts a fresh supplied mock on loopback port 8091. Use `--port` if needed. It derives case IDs and safety checks from the external scenarios. The gate requires at least 36/40 raw passes, rejects safety failures, and verifies any candidate fixture conflict against authorized sources. With the committed baseline specified, only previously verified conflicts can qualify for an exception. The four mismatches still count as raw failures. The full runner allows up to six seconds for claims turns so it can score complete answers and measure the gap against the three-second production target.

The [committed evaluation report](evaluation/report.json) records 36/40 raw passes, nine passing safety cases, four source-verified fixture conflicts, and a passing gate. Its 3.031-second p95 is above the production target. The baseline used deterministic routing. The model adapter was tested with a simulated provider.

Public CI runs Ruff and pytest, then builds and smoke-tests the container using a generated policy and loopback health stub. The manually dispatched full workflow uses an ephemeral runner and a protected `assessment-evaluation` environment. Restrict the environment to trusted branches and require reviewer approval. Configure the three API credential secrets above, a direct private HTTPS download URL in `PACK_ARCHIVE_URL`, the reviewed ZIP checksum in `PACK_ARCHIVE_SHA256`, and optionally the pack folder within the ZIP in `PACK_SUBDIR`. Only run a copy whose mock script you have reviewed. The workflow uploads only the sanitized report and expires it after seven days.

### Docker

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
