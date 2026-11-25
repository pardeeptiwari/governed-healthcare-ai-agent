import asyncio
import math
import re
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from urllib.parse import parse_qs, urlsplit

import httpx


class Unavailable(Exception):
    pass


class Missing(Exception):
    pass


class UncertainWrite(Exception):
    pass


@dataclass
class Budget:
    steps: int
    deadline: float
    model_remaining: float
    events: list = field(default_factory=list)
    model_cost: float = 0.0

    def remaining(self):
        return max(0.0, self.deadline - monotonic())

    def begin(self, operation):
        if self.steps <= 0 or self.remaining() <= 0:
            self.events.append({"operation": operation, "decision": "budget_denied"})
            raise Unavailable
        self.steps -= 1
        return monotonic()

    def record(self, operation, start, decision, **metrics):
        self.events.append(dict(operation=operation, decision=decision,
                                ms=round((monotonic() - start) * 1000), **metrics))


class Gateway:
    def __init__(self, settings, client, kb, tenant, member, budget, claim_cache):
        self.settings, self.client, self.kb = settings, client, kb
        self.tenant, self.member, self.budget = tenant, member, budget
        self.member_path = f"/tenants/{tenant}/members/{member}"
        self.claim_cache = claim_cache

    async def request(self, operation, path, *, params=None, directory=False, intent=None):
        headers = (
            {"X-Api-Key": self.settings.directory_key} if directory
            else {"Authorization": "Bearer " + self.settings.upstream_tokens[self.tenant]}
        )
        if intent:
            headers.update({"Idempotency-Key": intent["key"], "Content-Type": "application/json"})
        for attempt in range(2):
            start = self.budget.begin(operation)
            decision = "unavailable"
            try:
                async with asyncio.timeout(self.budget.remaining()):
                    response = await self.client.request(
                        "POST" if intent else "GET", path, params=params,
                        headers=headers, content=intent["body"] if intent else None,
                    )
                decision = str(response.status_code)
                if response.status_code == 404:
                    raise Missing
                if response.status_code == 429 and not intent and attempt == 0:
                    delay = float(response.headers.get("Retry-After", "0"))
                    if math.isfinite(delay) and 0 <= delay < self.budget.remaining():
                        await asyncio.sleep(delay)
                        continue
                if intent and response.status_code == 503:
                    if attempt == 0:
                        continue
                    raise UncertainWrite
                if response.status_code not in ({200, 201} if intent else {200}):
                    if intent:
                        raise UncertainWrite
                    raise Unavailable
                if intent and response.status_code == 200 and response.headers.get("Idempotent-Replayed") != "true":
                    raise UncertainWrite
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError
                return payload
            except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                if intent:
                    raise UncertainWrite from exc
                raise Unavailable from exc
            finally:
                self.budget.record(operation, start, decision)
        raise Unavailable

    async def benefits(self):
        data = await self.request("benefits", self.member_path + "/benefits")
        self.check_member(data)
        return {key: data[key] for key in (
            "product_code", "plan_name", "coverage_status", "coverage_termination", "accumulators"
        )}

    def check_member(self, data):
        if not isinstance(data, dict) or data.get("member_id") != self.member:
            self.budget.events.append({"operation": "scope", "decision": "deny"})
            raise Missing

    async def verify(self, suffix):
        data = await self.request("member_verify", self.member_path)
        self.check_member(data)
        digits = "".join(char for char in data.get("ssn", "") if char.isdigit())
        return len(digits) == 9 and digits[-4:] == suffix

    async def directory(self, specialty):
        data = await self.request("member_location", self.member_path)
        self.check_member(data)
        network, postal_code = data["network_id"], data["address"]["zip"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,31}", network) or not re.fullmatch(r"[0-9]{5}", postal_code):
            self.budget.events.append({"operation": "provider_search", "decision": "invalid_fields"})
            raise Unavailable
        result = await self.request("provider_search", "/directory/v3/providers", directory=True,
                                    params={"specialty": specialty, "query": specialty,
                                            "network_id": network, "zip": postal_code, "limit": 10})
        return [provider["name"] for provider in result["results"]
                if network in provider["networks"]]

    @staticmethod
    def project_claim(data):
        return {key: data[key] for key in (
            "claim_id", "service_date", "processed_date", "status", "service",
            "plan_paid", "member_responsibility", "denial",
        )}

    async def claim(self, claim_id):
        data = await self.request("claim", f"/tenants/{self.tenant}/claims/{claim_id}")
        self.check_member(data)
        if data["claim_id"] != claim_id:
            raise Missing
        return self.project_claim(data)

    async def claim_pages(self):
        key = self.tenant, self.member
        start = self.budget.begin("claims_cache")
        cached = self.claim_cache.get(key)
        if cached and cached[0] > monotonic():
            self.claim_cache.move_to_end(key)
            self.budget.record("claims_cache", start, "hit")
            for page in cached[1]:
                yield page
            return
        self.claim_cache.pop(key, None)
        self.budget.record("claims_cache", start, "miss")
        path = self.member_path + "/claims"
        cursor = 0
        pages = []
        while True:
            page = await self.request("claim_page", path, params={"cursor": cursor, "page_size": 5})
            projected = []
            if not isinstance(page["data"], list) or len(page["data"]) > 5:
                raise Unavailable
            for claim in page["data"]:
                self.check_member(claim)
                projected.append(self.project_claim(claim))
            pages.append(projected)
            if page["next"] is None:
                self.claim_cache[key] = monotonic() + 60, pages
                self.claim_cache.move_to_end(key)
                if len(self.claim_cache) > 128:
                    self.claim_cache.popitem(last=False)
                yield projected
                return
            link = urlsplit(page["next"])
            query = parse_qs(link.query)
            if (link.scheme or link.netloc or link.fragment or link.path != path
                    or not projected
                    or query != {"cursor": [str(cursor + len(projected))], "page_size": ["5"]}):
                self.budget.events.append({"operation": "pagination", "decision": "deny"})
                raise Unavailable
            yield projected
            cursor += len(projected)

    async def grievances(self):
        response = await self.request("grievances", self.member_path + "/grievances")
        for row in response["data"]:
            self.check_member(row)
            yield {key: row[key] for key in ("grievance_id", "received_date", "status")}

    async def file(self, intent):
        if intent["tenant"] != self.tenant or intent["member"] != self.member or intent["state"] != "sending":
            raise UncertainWrite
        result = await self.request("grievance_create", self.member_path + "/grievances", intent=intent)
        self.check_member(result)
        if result["category"] != intent["category"] or not result.get("grievance_id"):
            raise UncertainWrite
        return {"grievance_id": result["grievance_id"], "category": result["category"]}

    def lookup(self, product, when: date, query, *, policy=False):
        start = self.budget.begin("knowledge")
        outcome = self.kb.find(self.tenant, product, when, query, all_products=policy)
        self.budget.record("knowledge", start, "found" if outcome else "missing")
        if outcome is None:
            raise Unavailable
        return outcome
