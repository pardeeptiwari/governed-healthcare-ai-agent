from datetime import date

import httpx
from fastapi.testclient import TestClient

from member_services.api import build_app
from member_services.config import Settings
from member_services.ports import FixedClock, ModelCompletion
from member_services.store import State


def test_unresolved_filing_survives_repository_restart_and_session_overwrite(tmp_path):
    database = tmp_path / "state.sqlite"
    scope = {"tenant": "plan_a", "member": "M1234"}
    state = State(database)
    state.create("original", scope)
    state.create("new", scope)
    approved = {**scope, "intent": {"key": "first", "body": '{"description":"approved"}', "state": "sending"}}
    assert state.begin_filing("original", approved)

    reopened = State(database)
    reopened.save("original", scope)
    assert not reopened.begin_filing("new", {**scope, "intent": {"key": "second", "body": "{}"}})
    with reopened.transaction() as connection:
        assert connection.execute("SELECT operation_key, body FROM active_filings").fetchone() == (
            "first", approved["intent"]["body"],
        )


def test_environment_paths_and_secret_repr(monkeypatch, tmp_path):
    for name, value in {
        "ASSIGNMENT_PACK": str(tmp_path),
        "REP_CREDENTIALS_JSON": '{"demo-password":{"tenant":"plan_a","rep":"demo"}}',
        "TENANT_TOKENS_JSON": '{"plan_a":"tenant-password"}',
        "DIRECTORY_KEY": "directory-password",
        "MODEL_KEY": "model-password",
        "EVALUATION_AS_OF": "2027-02-01",
    }.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_env()
    assert settings.knowledge_path == tmp_path / "kb"
    assert settings.evaluation_path == tmp_path / "eval" / "conversations.json"
    assert settings.mock_script == tmp_path / "mock_services" / "server.py"
    assert settings.evaluation_as_of == date(2027, 2, 1)
    assert "password" not in repr(settings)


def test_app_injects_clock_model_and_state(tmp_path):
    for tenant in ("plan_a", "plan_b"):
        folder = tmp_path / "kb" / tenant
        folder.mkdir(parents=True)
        (folder / "benefit.md").write_text(
            f"---\ntenant: {tenant}\nplan_year: 2027\neffective_date: 2027-01-01\n"
            "product_code: TEST\n---\n| Specialist visit | $15 copay | Not covered |\n",
            encoding="utf-8",
        )

    def upstream(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"clock": "2026-11-15"})
        return httpx.Response(200, json={
            "member_id": "M1234", "product_code": "TEST", "plan_name": "Synthetic",
            "coverage_status": "ACTIVE", "coverage_termination": None, "accumulators": {},
        })

    class Provider:
        async def complete(self, *, instruction, projection, max_tokens):
            assert projection == "specialist"
            return ModelCompletion('{"operation":"benefit","subject":"specialist"}')

    settings = Settings(tmp_path, "http://mock", tmp_path / "unused.sqlite",
                        {"demo": {"rep": "demo", "tenant": "plan_a", "member_ids": ["M1234"]}},
                        {"plan_a": "mock-token"}, "mock-key")
    state = State(tmp_path / "injected.sqlite")
    app = build_app(settings, clock=FixedClock(date(2027, 2, 1)), model_provider=Provider(),
                    state_repository=state, upstream_transport=httpx.MockTransport(upstream))
    headers = {"Authorization": "Bearer demo"}

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        conversation = client.post("/v1/conversations", json={"member_id": "M1234"}, headers=headers)
        assert conversation.status_code == 201
        identifier = conversation.json()["conversation_id"]
        response = client.post(f"/v1/conversations/{identifier}/turns",
                               json={"turn_id": "one", "message": "Specialist M1234"}, headers=headers)

    assert response.status_code == 200
    assert "2027" in response.json()["answer"]
    assert "$15 copay" in response.json()["answer"]
    assert state.load(identifier)["member"] == "M1234"
    assert not settings.database.exists()
