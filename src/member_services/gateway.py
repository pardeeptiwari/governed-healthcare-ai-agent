import asyncio
import math
import re
from dataclasses import dataclass, field
from datetime import date
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

import httpx

from .domain import SPECIALTIES


class Unavailable(Exception):
    pass


class Missing(Exception):
    pass


class Deadline(Unavailable):
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
        if self.remaining() <= 0:
            self.events.append({"operation": operation, "decision": "budget_denied"})
            raise Deadline
        if self.steps <= 0:
            self.events.append({"operation": operation, "decision": "budget_denied"})
            raise Unavailable
        self.steps -= 1
        return monotonic()

    def record(self, operation, start, decision, **metrics):
        self.events.append(dict(operation=operation, decision=decision,
                                ms=round((monotonic() - start) * 1000), **metrics))


class Gateway:
    def __init__(self, settings, client, kb, tenant, member, budget, claim_cache, *, filing_store=None):
        self.settings, self.client, self.kb = settings, client, kb
        self.tenant, self.member, self.budget = tenant, member, budget
        self.member_path = f"/tenants/{tenant}/members/{member}"
        self.claim_cache = claim_cache
        self.filing_store = filing_store

    async def _request(self, operation, path, *, params=None, directory=False, intent=None):
        headers = (
            {"X-Api-Key": self.settings.directory_key} if directory
            else {"Authorization": "Bearer " + self.settings.upstream_tokens[self.tenant]}
        )
        if intent:
            headers.update({"Idempotency-Key": intent["key"], "Content-Type": "application/json"})
        for attempt in range(2):
            start = self.budget.begin(operation)
            decision = "unavailable"
            attempt_id = self.filing_store.start_attempt(intent["key"]) if intent and self.filing_store else None
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
                    delay = float(response.headers.get("Retry-After", "nan"))
                    if self.budget.steps and math.isfinite(delay) and 0 <= delay < self.budget.remaining():
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
            except (TimeoutError, httpx.TimeoutException) as exc:
                if intent:
                    raise UncertainWrite from exc
                raise Deadline from exc
            except (httpx.HTTPError, ValueError) as exc:
                if intent:
                    raise UncertainWrite from exc
                raise Unavailable from exc
            finally:
                self.budget.record(operation, start, decision, downstream=True, retry=attempt > 0)
                if attempt_id is not None:
                    self.filing_store.finish_attempt(attempt_id, decision)
        raise Unavailable

    async def benefits(self):
        data = await self._request("benefits", self.member_path + "/benefits")
        self.check_member(data)
        data["accumulators"] = self._project_fields(data.get("accumulators"), (
            "plan_year", "as_of", "deductible_met_individual",
            "out_of_pocket_met_individual", "physical_therapy_visits_used",
        ))
        return {key: data[key] for key in (
            "product_code", "plan_name", "coverage_status", "coverage_termination", "accumulators"
        )}

    def check_member(self, data):
        if not isinstance(data, dict) or data.get("member_id") != self.member:
            self.budget.events.append({"operation": "scope", "decision": "deny"})
            raise Missing

    async def verify(self, suffix):
        data = await self._request("member_verify", self.member_path)
        self.check_member(data)
        ssn = data.get("ssn")
        if not isinstance(ssn, str):
            raise Unavailable
        digits = "".join(char for char in ssn if char.isascii() and char.isdigit())
        return len(digits) == 9 and digits[-4:] == suffix

    async def directory(self, specialty):
        if specialty not in SPECIALTIES.values():
            raise Unavailable
        data = await self._request("member_location", self.member_path)
        self.check_member(data)
        network, postal_code = data["network_id"], data["address"]["zip"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{1,31}", network) or not re.fullmatch(r"[0-9]{5}", postal_code):
            self.budget.events.append({"operation": "provider_search", "decision": "invalid_fields"})
            raise Unavailable
        result = await self._request("provider_search", "/directory/v3/providers", directory=True,
                                    params={"specialty": specialty, "query": specialty,
                                            "network_id": network, "zip": postal_code, "limit": 10})
        providers = result.get("results")
        if not isinstance(providers, list):
            raise Unavailable
        names = []
        for provider in providers:
            fields = self._project_fields(provider, ("name", "networks"))
            name, networks = fields.get("name"), fields.get("networks")
            if not isinstance(name, str) or not isinstance(networks, list):
                raise Unavailable
            if network in networks:
                names.append(name)
        return names

    @staticmethod
    def _project_fields(data, fields):
        if not isinstance(data, dict):
            raise Unavailable
        return {key: data[key] for key in fields if key in data}

    def project_claim(self, data):
        claim = {key: data[key] for key in (
            "claim_id", "service_date", "processed_date", "status", "plan_paid",
        )}
        date.fromisoformat(claim["service_date"])
        if claim["processed_date"] is not None:
            date.fromisoformat(claim["processed_date"])
        if claim["status"] not in {"IN_PROCESS", "PENDED", "PAID", "DENIED"}:
            raise Unavailable
        claim["service"] = self._project_fields(data.get("service"), ("description",))
        claim["member_responsibility"] = self._project_fields(
            data.get("member_responsibility"), ("deductible", "copay", "coinsurance")
        )
        if claim["status"] == "PAID" and len(claim["member_responsibility"]) != 3:
            raise Unavailable
        denial = data.get("denial")
        claim["denial"] = None if denial is None else self._project_fields(denial, ("carc",))
        return claim

    async def claim(self, claim_id):
        if not re.fullmatch(r"C[0-9]{6,20}", claim_id):
            raise Unavailable
        data = await self._request("claim", f"/tenants/{self.tenant}/claims/{claim_id}")
        self.check_member(data)
        if data["claim_id"] != claim_id:
            raise Missing
        return self.project_claim(data)

    async def claim_pages(self, *, allow_prefix: bool = True):
        key = self.tenant, self.member
        start = self.budget.begin("claims_cache")
        cached = self.claim_cache.get(key)
        path = self.member_path + "/claims"
        cursor = 0
        pages = []
        expires = monotonic() + 15
        if cached and cached[0] > monotonic() and (allow_prefix or cached[2] is None):
            expires, previous_pages, cursor = cached
            pages = list(previous_pages)
            self.claim_cache.move_to_end(key)
            self.budget.record("claims_cache", start, "hit" if cursor is None else "prefix_hit")
            for page in pages:
                yield page
            if cursor is None:
                return
        else:
            self.claim_cache.pop(key, None)
            self.budget.record("claims_cache", start, "miss")
        while cursor is not None:
            page = await self._request("claim_page", path, params={"cursor": cursor, "page_size": 5})
            projected = []
            if not isinstance(page["data"], list) or len(page["data"]) > 5:
                raise Unavailable
            for claim in page["data"]:
                self.check_member(claim)
                projected.append(self.project_claim(claim))
            pages.append(projected)
            next_cursor = cursor + len(projected)
            continuation = page["next"]
            if continuation is not None:
                if not isinstance(continuation, str) or continuation.partition("?")[0] != path:
                    self.budget.events.append({"operation": "pagination", "decision": "deny"})
                    raise Unavailable
                link = urlsplit(continuation)
                if (len(projected) != 5 or link.fragment
                        or sorted(parse_qsl(link.query, keep_blank_values=True))
                        != [("cursor", str(next_cursor)), ("page_size", "5")]):
                    self.budget.events.append({"operation": "pagination", "decision": "deny"})
                    raise Unavailable
            cursor = next_cursor if continuation is not None else None
            self.claim_cache[key] = expires, tuple(pages), cursor
            self.claim_cache.move_to_end(key)
            if len(self.claim_cache) > 128:
                self.claim_cache.popitem(last=False)
            yield projected

    async def grievances(self):
        response = await self._request("grievances", self.member_path + "/grievances")
        rows = response.get("data")
        if not isinstance(rows, list):
            raise Unavailable
        for row in rows:
            self.check_member(row)
            yield {key: row[key] for key in ("grievance_id", "received_date", "status")}

    async def file(self, intent):
        if intent["tenant"] != self.tenant or intent["member"] != self.member or intent["state"] != "sending":
            raise UncertainWrite
        result = await self._request("grievance_create", self.member_path + "/grievances", intent=intent)
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
