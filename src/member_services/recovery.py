import asyncio
import json
import logging
from hashlib import sha256
from sqlite3 import OperationalError
from time import monotonic

from .gateway import Budget, Gateway, Missing, Unavailable, UncertainWrite
from .workflow import outcome

logger = logging.getLogger(__name__)


async def recover_one(settings, state, upstream) -> bool:
    operation = state.claim_recovery(settings.grievance_durable_replay)
    if operation is None:
        return False
    key, tenant, member, body, body_hash = operation
    if sha256(body.encode()).hexdigest() != body_hash or tenant not in settings.upstream_tokens:
        state.complete_recovery(key, None, manual=True)
        return True
    try:
        category = json.loads(body)["category"]
    except (ValueError, KeyError, TypeError):
        state.complete_recovery(key, None, manual=True)
        return True
    intent = {"key": key, "body": body, "tenant": tenant, "member": member,
              "category": category, "state": "sending"}
    gateway = Gateway(settings, upstream, None, tenant, member,
                      Budget(1, monotonic() + settings.turn_seconds, 0), {}, filing_store=state)
    try:
        receipt = await gateway.file(intent)
    except (Missing, Unavailable, UncertainWrite, KeyError, TypeError):
        state.complete_recovery(key, None)
    else:
        state.complete_recovery(key, outcome("filed", f"Grievance {receipt['grievance_id']} was filed.", receipt=receipt))
    return True


async def recover_filings(settings, state, upstream) -> None:
    while True:
        try:
            await recover_one(settings, state, upstream)
        except OperationalError:
            logger.warning("operation=filing_recovery decision=storage_unavailable")
        await asyncio.sleep(1)
