import json
import re
from datetime import date, timedelta
from decimal import Decimal
from time import time
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from .gateway import Missing, Unavailable, UncertainWrite
from .ports import ModelProvider
from .routing import BENEFITS, SPECIALTIES, classify


class TurnState(TypedDict):
    message: str
    result: dict


def outcome(code, answer, **fields):
    return {"outcome": code, "answer": answer, "evidence": [], **fields}


def money(value):
    return f"${Decimal(str(value)):,.2f}".removesuffix(".00")


def identifier(text, pattern):
    match = re.search(pattern, text, re.I)
    return match[0].upper() if match else None


def service_date(text):
    literal = identifier(text, r"\b\d{4}-\d{2}-\d{2}\b")
    return date.fromisoformat(literal) if literal else None


def policy_days(gateway, today, topic):
    name, row = gateway.lookup("ALL", today, topic, policy=True)
    if row[0] != topic:
        raise Unavailable
    match = re.search(r"\b(\d+) calendar days\b", row[1])
    if not match:
        raise Unavailable
    return int(match[1]), name


async def benefits(gateway, intent, message, today):
    enrollment = await gateway.benefits()
    if enrollment["coverage_status"] == "TERMINATED":
        return outcome("coverage_terminated", f"Coverage ended on {enrollment['coverage_termination']}.")
    if enrollment["coverage_status"] != "ACTIVE":
        raise Unavailable
    product = enrollment["product_code"]
    when = service_date(message) or today
    if when == today and re.search(r"renew|next (?:plan )?year|january 1\b", message):
        when = date(today.year + 1, 1, 1)
    explicit_year = re.search(r"\b20\d{2}\b", message)
    if not service_date(message) and explicit_year:
        year = int(explicit_year[0])
        when = today if year == today.year else date(year, 1, 1)
    topic = BENEFITS.get(intent.subject)
    if not topic:
        raise Unavailable
    source, row = gateway.lookup(product, when, topic)
    if row[0] != topic or len(row) < 3:
        raise Unavailable
    out_of_network = bool(re.search(r"out[ -]of[ -]network", message))
    value = row[2] if out_of_network and row[2] != "Same as in-network" else row[1]
    family = "family" in message
    if "/" in value and intent.subject in {"deductible", "oop"}:
        value = value.split("/")[int(family)].strip()
    accumulators = enrollment["accumulators"]
    remaining = bool(re.search(r"left|still|remain|before .*met|how far|how much more|need to spend", message))
    if intent.subject == "pt_limit" or (remaining and intent.subject in {"deductible", "oop"}):
        if when.year != accumulators.get("plan_year") or (family and remaining) or out_of_network:
            raise Unavailable
        digits = re.search(r"\d[\d,.]*", value)
        if not digits:
            raise Unavailable
        limit = Decimal(digits[0].replace(",", ""))
        if intent.subject == "pt_limit":
            value = f"{max(0, int(limit) - accumulators['physical_therapy_visits_used'])} visits remaining"
        else:
            field = "deductible_met_individual" if intent.subject == "deductible" else "out_of_pocket_met_individual"
            value = money(max(Decimal(0), limit - Decimal(str(accumulators[field]))))
    network = "out-of-network" if out_of_network else "in-network"
    if "see virtual care benefit" in value.lower():
        if intent.subject == "virtual_behavioral":
            raise Unavailable
        value = re.sub(r"\s*\(see Virtual Care Benefit\)", "", value, flags=re.I)
        value += " for the plan's designated virtual care benefit"
    answer = f"{enrollment['plan_name']} ({when.year}), {network} {topic.lower()}: {value}."
    return outcome("answer", answer) | {"evidence": [source]}


async def get_claim(gateway, text):
    claim_id = identifier(text, r"\bC\d{6,20}\b")
    if claim_id:
        return await gateway.claim(claim_id)
    when = service_date(text)
    if not when:
        raise Unavailable
    types = {"mri": r"mri|magnetic", "office visit": r"office|evaluation",
             "therapy": r"therapy|therapeutic", "x-ray": r"x.ray|radiolog"}
    requested = next((pattern for name, pattern in types.items() if name in text), None)
    matches = []
    async for page in gateway.claim_pages():
        matches.extend(claim for claim in page if claim["service_date"] == when.isoformat()
                       and (not requested or re.search(requested, claim["service"]["description"], re.I)))
        if page and page[-1]["service_date"] < when.isoformat():
            break
    if len(matches) != 1:
        raise Unavailable
    return matches[0]


async def claims(gateway, intent, text, today):
    if intent.operation == "claim_count":
        status = next((enum for key, enum in (
            ("denied", "DENIED"), ("paid", "PAID"), ("pended", "PENDED"),
            ("in process", "IN_PROCESS"),
        ) if key in text), None)
        year = re.search(r"\b20\d{2}\b", text)
        year = year[0] if year else str(today.year)
        if not status:
            raise Unavailable
        count = 0
        async for page in gateway.claim_pages():
            count += sum(c["status"] == status and c["service_date"][:4] == year for c in page)
        return outcome("answer", f"{count} {status.lower()} claims in {year}.")
    claim = await get_claim(gateway, text)
    if intent.operation == "appeal":
        if claim["status"] != "DENIED" or not claim["processed_date"]:
            raise Unavailable
        days, source = policy_days(gateway, today, "Claim appeal filing window")
        deadline = date.fromisoformat(claim["processed_date"]) + timedelta(days=days)
        answer = "Yes, the appeal window is open" if today <= deadline else "No, the appeal window has closed"
        return outcome("answer", f"{answer}. The filing deadline is {deadline}.") | {"evidence": [source]}
    answer = f"Claim status: {claim['status']}."
    if claim["denial"]:
        code = claim["denial"]["carc"]
        source, row = gateway.lookup("ALL", today, code, policy=True)
        if row[0] != code or len(row) < 3:
            raise Unavailable
        answer += f" Denial {code}. {row[2]}"
        return outcome("answer", answer) | {"evidence": [source]}
    if claim["status"] == "PAID":
        owing = sum(Decimal(str(v)) for v in claim["member_responsibility"].values())
        answer += f" Member responsibility: {money(owing)}. Plan paid: {money(claim['plan_paid'])}."
    return outcome("answer", answer)


async def grievance_status(gateway, text, today):
    grievance_id = identifier(text, r"\bG\d{2}-\d{4,20}\b")
    if not grievance_id:
        raise Unavailable
    async for entry in gateway.grievances():
        if entry["grievance_id"] == grievance_id:
            days, source = policy_days(gateway, today, "Grievance resolution timeframe")
            deadline = date.fromisoformat(entry["received_date"]) + timedelta(days=days)
            answer = "Yes, it is overdue" if today > deadline else "No, it is within the resolution window"
            return outcome("answer", f"{answer}. The resolution deadline is {deadline}.") | {"evidence": [source]}
    raise Unavailable


def draft(message, session):
    if session.get("intent", {}).get("state") in {"sending", "uncertain"}:
        return outcome("pending_reconciliation", "A confirmed filing is pending reconciliation.")
    categories = {"billing": r"\bbill(?:ing|ed|s)?\b", "quality_of_care": r"quality(?: of care)?",
                  "access": r"\baccess\b", "customer_service": r"customer service"}
    category = next((key for key, pattern in categories.items() if re.search(pattern, message, re.I)), "other")
    # The rep reviews the exact text that will be filed. It stays in the bound
    # session and upstream grievance record.
    description = re.sub(r"\b\d{3}[. -]?\d{2}[. -]?\d{4}\b", "[redacted]", message).strip()
    if not description or len(description) > 2000:
        return outcome("clarify", "Provide a grievance description of at most 2,000 characters.")
    fields = {"category": category, "description": description, "expedite_requested": False}
    pending = {"id": uuid4().hex, "expires": time() + 900, "fields": fields}
    session["draft"] = pending
    return outcome("confirm", "Read this draft back to the member and confirm or cancel.",
                   draft={"id": pending["id"], **fields})


def create_graph(settings, model_client, *, model_provider: ModelProvider | None = None):
    async def respond(state: TurnState, runtime: Runtime[tuple]):
        gateway, session, today = runtime.context
        original = state["message"]
        message = original.lower()
        explicit_member = identifier(original, r"\bM\d{4,20}\b")
        try:
            if explicit_member and explicit_member != gateway.member:
                raise Missing
            if re.search(r"\b(harborview health plan|prairie sky health)\b", message):
                foreign = "prairie sky health" if gateway.tenant == "plan_a" else "harborview health plan"
                if foreign in message:
                    raise Missing
            intent = await classify(settings, model_client, gateway.budget, original, provider=model_provider)
            if intent.operation == "decline":
                result = outcome("decline", "I can verify the last four digits. I cannot disclose a full SSN.")
            elif intent.operation == "verify":
                suffix = identifier(original, r"\b\d{4}\b")
                if not suffix:
                    result = outcome("clarify", "Provide the last four digits to check.")
                else:
                    matched = await gateway.verify(suffix)
                    result = outcome("answer", "The last four digits match." if matched else
                                     "The last four digits do not match.", verified=matched)
            elif intent.operation == "draft":
                await gateway.benefits()
                result = draft(original, session)
            elif intent.operation == "benefit":
                result = await benefits(gateway, intent, message, today)
            elif intent.operation in {"claim", "claim_count", "appeal"}:
                result = await claims(gateway, intent, message, today)
            elif intent.operation == "grievance_status":
                result = await grievance_status(gateway, message, today)
            elif intent.operation == "provider":
                specialty = next((name for key, name in SPECIALTIES.items() if key in message), None)
                if not specialty:
                    raise Unavailable
                names = await gateway.directory(specialty)
                result = outcome("answer", "In-network results: " + ", ".join(names) + "." if names
                                 else "No matching in-network providers were found.")
            else:
                await gateway.benefits()
                raise Unavailable
        except Missing:
            result = outcome("not_in_tenant", "The requested record could not be found in this session.")
        except (Unavailable, ValueError, KeyError, TypeError, AttributeError, ArithmeticError):
            result = outcome("cannot_answer", "The information is unavailable from the authorized sources.")
        return {"result": result}

    graph = StateGraph(TurnState, context_schema=tuple)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile()


async def apply_action(action, draft_id, session, gateway, persist):
    pending = session.get("draft")
    prior = session.get("intent")
    if prior and prior["draft"] == draft_id:
        gateway.budget.events.append({"operation": action, "decision": "already_confirmed"})
        return prior.get("receipt") or outcome("pending_reconciliation", "Filing is pending reconciliation.")
    if not pending or pending["id"] != draft_id or pending["expires"] < time():
        gateway.budget.events.append({"operation": action, "decision": "draft_denied"})
        return outcome("no_write", "There is no matching active draft.")
    gateway.budget.events.append({"operation": action, "decision": "allowed"})
    del session["draft"]
    if action == "cancel":
        return outcome("no_write", "The draft was cancelled.")
    intent = {"draft": draft_id, "tenant": gateway.tenant, "member": gateway.member,
              "key": uuid4().hex, "state": "sending",
              "body": json.dumps(pending["fields"], sort_keys=True, separators=(",", ":")),
              "category": pending["fields"]["category"]}
    session["intent"] = intent
    persist()
    try:
        receipt = await gateway.file(intent)
    except (Missing, Unavailable, UncertainWrite, KeyError, TypeError):
        intent["state"] = "uncertain"
        return outcome("pending_reconciliation", "Filing is pending reconciliation.")
    intent["state"] = "filed"
    result = outcome("filed", f"Grievance {receipt['grievance_id']} was filed.", receipt=receipt)
    intent["receipt"] = result
    del intent["body"]
    return result
