import asyncio
from collections import OrderedDict
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from member_services.gateway import Budget, Gateway, Unavailable


def gateway(client, seconds=5, steps=8):
    settings = SimpleNamespace(upstream_tokens={"plan_a": "synthetic"}, directory_key="directory")
    return Gateway(settings, client, None, "plan_a", "M1234",
                   Budget(steps, monotonic() + seconds, 0), OrderedDict())


@pytest.mark.parametrize(("retry_after", "steps", "expected_calls"), [
    ("0", 8, 2), ("0", 1, 1), ("60", 8, 1), ("invalid", 8, 1), (None, 8, 1),
])
def test_retry_is_bounded_by_header_time_and_steps(retry_after, steps, expected_calls):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic"
        return httpx.Response(429, headers={"Retry-After": retry_after} if retry_after is not None else {})

    async def run():
        async with httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(Unavailable):
                await gateway(client, steps=steps).claim("C123456")

    asyncio.run(run())
    assert len(calls) == expected_calls


def test_malformed_page_and_unapproved_specialty_fail_closed():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": {}, "next": None})

    async def run():
        async with httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(respond)) as client:
            tool = gateway(client)
            with pytest.raises(Unavailable):
                await tool.directory("unapproved")
            assert not calls
            with pytest.raises(Unavailable):
                await anext(tool.claim_pages())
            assert not tool.claim_cache

    asyncio.run(run())


def test_claim_projects_nested_fields_and_caches_only_complete_pages():
    calls = []
    claim = {
        "claim_id": "C123456", "member_id": "M1234", "service_date": "2026-11-01",
        "processed_date": None, "status": "PENDED", "plan_paid": None,
        "service": {"description": "office visit", "extra": "private"},
        "member_responsibility": {"private": "private"}, "denial": None, "notes": "private",
    }

    def respond(request):
        calls.append(request)
        if request.url.params["cursor"] == "0":
            return httpx.Response(200, json={"data": [claim] * 5,
                "next": "/tenants/plan_a/members/M1234/claims?page_size=5&cursor=5"})
        return httpx.Response(200, json={"data": [claim], "next": None})

    async def run():
        async with httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(respond)) as client:
            tool = gateway(client)
            pages = tool.claim_pages()
            first = await anext(pages)
            assert not tool.claim_cache
            assert "private" not in str(first)
            assert len(await anext(pages)) == 1
            assert [len(page) async for page in tool.claim_pages()] == [5, 1]

    asyncio.run(run())
    assert len(calls) == 2
