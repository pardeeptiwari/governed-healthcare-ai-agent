import asyncio
import json
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from member_services.gateway import Budget, Deadline, Unavailable
from member_services.ports import ModelCompletion
from member_services.routing import classify


def test_model_projection_and_budget():
    settings = SimpleNamespace(model_url="http://model/classify", model_key="",
                               model_name="test", input_per_million=1, output_per_million=1)
    calls = []

    def respond(request):
        calls.append(json.loads(request.content)["messages"][-1]["content"])
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"operation":"claim","subject":"other"}',
        }}], "usage": {"prompt_tokens": 400, "completion_tokens": 20}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            budget = Budget(4, monotonic() + 3, .02)
            intent = await classify(settings, client, budget,
                                    "Claim C123456 for Jane Synthetic born 1990-01-01")
            assert intent.operation == "claim"
            assert calls == ["claim"]
            assert budget.model_cost == pytest.approx(.00042)
            assert budget.events[-1]["prompt_tokens"] == 400
            denied = Budget(4, monotonic() + 3, 0)
            with pytest.raises(Unavailable):
                await classify(settings, client, denied, "Claim status")
            assert denied.events[-1]["decision"] == "cost_denied"
            assert calls == ["claim"]

    asyncio.run(run())


def test_model_timeout_is_recorded_as_deadline_exhaustion():
    settings = SimpleNamespace(input_per_million=1, output_per_million=1)

    class Provider:
        async def complete(self, *, instruction, projection, max_tokens):
            raise httpx.ReadTimeout("synthetic provider timeout")

    async def run():
        budget = Budget(4, monotonic() + 3, .02)
        with pytest.raises(Deadline):
            await classify(settings, None, budget, "Claim status", provider=Provider())
        assert budget.events[-1]["decision"] == "deadline_timeout"

    asyncio.run(run())


@pytest.mark.parametrize(("completion", "decision", "cost"), [
    (ModelCompletion("invalid", 100, 10), "failed", .00011),
    (ModelCompletion('{"operation":"draft"}', 100, 10), "failed", .00011),
    (ModelCompletion('{"operation":"claim"}', -1, 10), "failed", .000708),
    (ModelCompletion('{"operation":"claim"}', 21_000, 10), "cost_exceeded", .02101),
])
def test_rejected_completions_still_charge_usage(completion, decision, cost):
    settings = SimpleNamespace(input_per_million=1, output_per_million=1)

    class Provider:
        async def complete(self, *, instruction, projection, max_tokens):
            return completion

    async def run():
        budget = Budget(4, monotonic() + 3, .02)
        with pytest.raises(Unavailable):
            await classify(settings, None, budget, "Claim status", provider=Provider())
        assert budget.model_cost == pytest.approx(cost)
        assert budget.events[-1]["decision"] == decision

    asyncio.run(run())
