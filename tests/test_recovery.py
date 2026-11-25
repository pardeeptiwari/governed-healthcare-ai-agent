import asyncio
from dataclasses import replace

import httpx
import pytest

from member_services.config import Settings
from member_services.recovery import recover_one
from member_services.store import State


@pytest.mark.parametrize("durable,status,calls", [(True, "FILED", 1), (False, "MANUAL_REVIEW", 0)])
def test_recovery_after_restart_claims_one_operation(tmp_path, durable, status, calls):
    settings = Settings(tmp_path, "http://mock", tmp_path / "state.sqlite", {}, {"plan_a": "test"}, "")
    settings = replace(settings, grievance_durable_replay=durable)
    state = State(settings.database)
    scope = {"tenant": "plan_a", "member": "M1234"}
    state.create("case", scope)
    state.reserve("case", "confirm", "request")
    body = '{"category":"billing","description":"Approved"}'
    assert state.begin_filing("case", {
        **scope, "intent": {"key": "original-key", "body": body, "state": "sending"},
    }, "confirm")

    restarted = State(settings.database)
    restarted.recover_interrupted()
    requests = []

    async def respond(request):
        requests.append(request)
        assert request.headers["Idempotency-Key"] == "original-key"
        assert request.content == body.encode()
        await asyncio.sleep(0)
        return httpx.Response(200, headers={"Idempotent-Replayed": "true"}, json={
            "member_id": "M1234", "grievance_id": "G26-1234", "category": "billing",
        })

    async def run():
        async with httpx.AsyncClient(base_url=settings.upstream, transport=httpx.MockTransport(respond)) as client:
            await asyncio.gather(*(recover_one(settings, restarted, client) for _ in range(2)))

    asyncio.run(run())
    assert len(requests) == calls
    with restarted.transaction() as connection:
        assert connection.execute("SELECT status FROM filing_operations").fetchone() == (status,)
        assert connection.execute("SELECT decision FROM filing_attempts").fetchall() == ([("200",)] if calls else [])
    if durable:
        assert restarted.load("case")["intent"]["receipt"]["outcome"] == "filed"
        assert "filed" in restarted.reserve("case", "confirm", "request")[1]
    else:
        assert restarted.reserve("case", "confirm", "request")[1] is None


def test_exhausted_recovery_requires_manual_review(tmp_path):
    settings = Settings(tmp_path, "http://mock", tmp_path / "state.sqlite", {}, {"plan_a": "test"}, "",
                        grievance_durable_replay=True)
    state = State(settings.database)
    scope = {"tenant": "plan_a", "member": "M1234"}
    state.create("case", scope)
    assert state.begin_filing("case", {
        **scope, "intent": {"key": "key", "body": '{"category":"billing"}', "state": "sending"},
    })
    state.recover_interrupted()

    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(503))
        async with httpx.AsyncClient(base_url=settings.upstream, transport=transport) as client:
            assert await recover_one(settings, state, client)
            assert await recover_one(settings, state, client)
            assert not await recover_one(settings, state, client)

    asyncio.run(run())
    with state.transaction() as connection:
        assert connection.execute("SELECT status, recoveries FROM filing_operations").fetchone() == (
            "MANUAL_REVIEW", 2,
        )
        assert connection.execute("SELECT decision FROM filing_attempts").fetchall() == [("503",), ("503",)]
