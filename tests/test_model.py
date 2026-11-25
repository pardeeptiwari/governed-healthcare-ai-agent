import asyncio
import json
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from member_services.gateway import Budget, Unavailable
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
