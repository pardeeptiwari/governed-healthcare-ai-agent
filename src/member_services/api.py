import asyncio
import hashlib
import hmac
import json
import re
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import date
from time import monotonic
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Literal

from .config import Settings
from .gateway import Budget, Gateway
from .knowledge import Knowledge
from .store import State
from .workflow import apply_action, create_graph, outcome


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: str = Field(pattern=r"^M[0-9]{4,20}$")


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    action: Literal["message", "confirm", "cancel"] = "message"
    message: str | None = Field(default=None, min_length=1, max_length=4000)
    draft_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def create_app():
    return build_app(Settings.from_env())


def build_app(settings, *, upstream_transport=None, model_transport=None):
    kb = Knowledge(settings.pack / "kb")
    state = State(settings.database)
    claim_cache = OrderedDict()
    # Bounded lock table for the single-process SQLite slice.
    locks = [asyncio.Lock() for _ in range(64)]

    @asynccontextmanager
    async def lifespan(app):
        async with (
            httpx.AsyncClient(base_url=settings.upstream, transport=upstream_transport,
                              follow_redirects=False, trust_env=False) as upstream,
            httpx.AsyncClient(transport=model_transport, follow_redirects=False, trust_env=False) as model,
        ):
            health = await upstream.get("/health", timeout=2)
            health.raise_for_status()
            app.state.clock = date.fromisoformat(health.json()["clock"])
            app.state.upstream = upstream
            app.state.graph = create_graph(settings, model)
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.store = state
    app.state.knowledge = kb

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse(status_code=422, content={"detail": "Invalid request."})

    def authenticate(authorization):
        supplied = (authorization or "").removeprefix("Bearer ")
        if (authorization or "").startswith("Bearer "):
            for credential, principal in settings.reps.items():
                if hmac.compare_digest(supplied.encode(), credential.encode()):
                    if principal["tenant"] in {"plan_a", "plan_b"}:
                        return principal
        raise HTTPException(401, "Authentication required.")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/conversations", status_code=201)
    async def start(body: Start, authorization: str | None = Header(default=None)):
        principal = authenticate(authorization)
        identifier = uuid4().hex
        state.create(identifier, {**principal, "member": body.member_id,
                                  "steps": settings.max_actions,
                                  "seconds": settings.processing_seconds, "cost": settings.model_budget})
        return {"conversation_id": identifier}

    @app.post("/v1/conversations/{identifier}/turns")
    async def turn(identifier: str, body: Turn, authorization: str | None = Header(default=None)):
        principal = authenticate(authorization)
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise HTTPException(404, "Conversation unavailable.")
        is_message = body.action == "message"
        if is_message != bool(body.message) or is_message == bool(body.draft_id):
            raise HTTPException(422, "Provide a message or a draft action.")
        start_time = monotonic()
        async with locks[int(identifier[:8], 16) % len(locks)]:
            session = state.load(identifier)
            if not session or any(session.get(key) != principal[key] for key in ("tenant", "rep")):
                raise HTTPException(404, "Conversation unavailable.")
            digest = hmac.digest(authorization.encode(), body.model_dump_json().encode(), hashlib.sha256).hex()
            prior = state.reserve(identifier, body.turn_id, digest)
            if prior:
                if prior[0] != digest:
                    raise HTTPException(409, "Turn identifier already used.")
                if prior[1]:
                    return json.loads(prior[1])
                return outcome("pending_reconciliation", "This turn has an unresolved outcome.")

            time_limit = settings.turn_seconds
            if is_message and re.search(
                r"\b(claims?|appeal|c\d{6,}|owe|owed|billed)\b", body.message, re.I
            ):
                time_limit = settings.claim_seconds
            budget = Budget(session["steps"], start_time + min(session["seconds"], time_limit), session["cost"])
            gateway = Gateway(settings, app.state.upstream, kb, session["tenant"], session["member"],
                              budget, claim_cache)
            if budget.remaining() <= 0 or budget.steps <= 0:
                budget.events.append({"operation": "turn", "decision": "budget_denied"})
                result = outcome("cannot_answer", "The conversation budget was exhausted.")
            elif is_message:
                with tracing_context(enabled=False):
                    graph_state = await app.state.graph.ainvoke(
                        {"message": body.message}, context=(gateway, session, app.state.clock)
                    )
                result = graph_state["result"]
            else:
                result = await apply_action(body.action, body.draft_id, session, gateway,
                                            lambda: state.save(identifier, session))
            elapsed = monotonic() - start_time
            session["steps"] = budget.steps
            session["seconds"] = max(0, session["seconds"] - elapsed)
            session["cost"] = max(0, session["cost"] - budget.model_cost)
            state.finish(identifier, session, body.turn_id, result, {
                "outcome": result["outcome"], "elapsed_ms": round(elapsed * 1000),
                "estimated_model_cost": round(budget.model_cost, 8), "events": budget.events,
            })
            return result

    return app
