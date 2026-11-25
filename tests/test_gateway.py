import asyncio
from collections import OrderedDict
from time import monotonic
from types import SimpleNamespace

import httpx
import pytest

from member_services.gateway import Budget, Deadline, Gateway, Unavailable


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


def test_claim_projects_nested_fields_and_resumes_incomplete_cache():
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
            assert tool.claim_cache[("plan_a", "M1234")][2] == 5
            assert "private" not in str(first)
            assert len(await anext(pages)) == 1
            assert [len(page) async for page in tool.claim_pages()] == [5, 1]

    asyncio.run(run())
    assert len(calls) == 2


def test_complete_page_rejects_cross_tenant_continuation():
    calls = []
    claim = {
        "claim_id": "C123456", "member_id": "M1234", "service_date": "2026-11-01",
        "processed_date": None, "status": "PENDED", "plan_paid": None,
        "service": {}, "member_responsibility": {}, "denial": None,
    }

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": [claim] * 5,
            "next": "/tenants/plan_b/members/M1234/claims?cursor=5&page_size=5",
        })

    async def run():
        async with httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(respond)) as client:
            tool = gateway(client)
            with pytest.raises(Unavailable):
                await anext(tool.claim_pages())
            assert not tool.claim_cache
            assert tool.budget.events[-1] == {"operation": "pagination", "decision": "deny"}

    asyncio.run(run())
    assert len(calls) == 1


def test_claim_cache_isolated_and_aggregate_cannot_consume_only_a_prefix():
    calls = []

    async def respond(request):
        calls.append((request.url.path, request.url.params["cursor"]))
        if request.url.params["cursor"] == "5":
            await asyncio.sleep(0.2)
        claim = {
            "claim_id": "C123456", "member_id": "M1234", "service_date": "2026-11-01",
            "processed_date": None, "status": "PENDED", "plan_paid": None,
            "service": {}, "member_responsibility": {}, "denial": None,
        }
        return httpx.Response(200, json={"data": [claim] * 5,
            "next": str(request.url.path) + "?cursor=5&page_size=5"})

    async def run():
        async with httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(respond)) as client:
            first = gateway(client)
            pages = first.claim_pages()
            assert len(await anext(pages)) == 5
            await pages.aclose()

            foreign_settings = SimpleNamespace(upstream_tokens={"plan_b": "synthetic"})
            foreign = Gateway(foreign_settings, client, None, "plan_b", "M1234",
                              Budget(8, monotonic() + 5, 0), first.claim_cache)
            pages = foreign.claim_pages()
            assert len(await anext(pages)) == 5
            await pages.aclose()

            first.budget.deadline = monotonic() + 0.05
            with pytest.raises(Deadline):
                _ = [page async for page in first.claim_pages(allow_prefix=False)]
            assert first.claim_cache[("plan_a", "M1234")][2] == 5

    asyncio.run(run())
    assert calls == [
        ("/tenants/plan_a/members/M1234/claims", "0"),
        ("/tenants/plan_b/members/M1234/claims", "0"),
        ("/tenants/plan_a/members/M1234/claims", "0"),
        ("/tenants/plan_a/members/M1234/claims", "5"),
    ]
