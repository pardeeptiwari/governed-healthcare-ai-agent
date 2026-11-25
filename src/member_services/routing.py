import asyncio
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .gateway import Unavailable


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["benefit", "claim", "claim_count", "appeal", "grievance_status",
                       "draft", "verify", "decline", "provider", "unavailable"]
    subject: Literal["specialist", "emergency", "generic", "video", "therapy",
                     "virtual_behavioral", "urgent", "primary", "deductible", "oop", "pt_limit",
                     "other"] = "other"


BENEFITS = {
    "specialist": "Specialist visit", "emergency": "Emergency room",
    "generic": "Generic drugs (Tier 1, 30-day retail)", "video": "Telehealth (video) visit",
    "virtual_behavioral": "Behavioral health outpatient visit (in person or virtual)",
    "therapy": "Physical therapy", "urgent": "Urgent care", "primary": "Primary care visit",
    "deductible": "Deductible (individual / family)", "oop": "Out-of-pocket maximum (individual / family)",
    "pt_limit": "Physical therapy visit limit",
}

SPECIALTIES = {"dermatolog": "Dermatology", "cardiolog": "Cardiology", "neurolog": "Neurology",
               "pediatric": "Pediatrics", "orthoped": "Orthopedics", "primary care": "Primary Care"}


def guarded_intent(message):
    if re.search(r"\b(ssn|social security(?: number)?)\b", message):
        return Intent(operation="verify" if re.search(r"\blast (four|4)\b", message) and
                      not re.search(r"\b(full|entire|complete)\b", message) else "decline")
    if "grievance" in message and re.search(r"\b(file|submit|create)\b", message) and not re.search(
        r"\b(filed|deadline|already|do not|don't|without)\b", message
    ):
        return Intent(operation="draft")
    return None


def local_intent(message):
    if "appeal" in message:
        return Intent(operation="appeal")
    if "grievance" in message:
        return Intent(operation="grievance_status")
    if re.search(r"\b(find|search|locate)\b", message) and any(k in message for k in SPECIALTIES):
        return Intent(operation="provider")
    if "claim" in message or re.search(r"\bc\d{6,}\b", message):
        return Intent(operation="claim_count" if "how many" in message else "claim")
    if "prior authorization" in message:
        return Intent(operation="unavailable")
    patterns = (
        ("virtual_behavioral", r"(virtual|video).*(therap|behavioral)|(therap|behavioral).*virtual"),
        ("pt_limit", r"physical therapy.*(left|visit|still use)|how many.*physical therapy"),
        ("oop", r"out.of.pocket"), ("deductible", r"deductible"),
        ("emergency", r"emergency room|\ber visit"), ("urgent", r"urgent care"),
        ("generic", r"generic"), ("specialist", r"specialist"), ("primary", r"primary care"),
        ("video", r"video visit|telehealth"), ("therapy", r"physical therapy"),
    )
    for subject, pattern in patterns:
        if re.search(pattern, message):
            return Intent(operation="benefit", subject=subject)
    return Intent(operation="unavailable")


def sanitized(message):
    message = re.sub(r"\b(?:\d{3}[- ]?\d{2}[- ]?\d{4}|[MCG]\d[\d-]{5,})\b",
                     "[identifier]", message, flags=re.I)
    message = re.sub(r"\S+@\S+|\b\d{4}-\d\d-\d\d\b|(?:\+?1[- .]?)?(?:\(\d{3}\)|\d{3})[- .]\d{3}[- .]\d{4}",
                     "[redacted]", message)
    return message


async def classify(settings, client, budget, text):
    lower = text.lower()
    guarded = guarded_intent(lower)
    if guarded:
        budget.events.append({"operation": "classify", "decision": "policy"})
        return guarded
    if not settings.model_url:
        budget.events.append({"operation": "classify", "decision": "local_rules"})
        return local_intent(lower)

    start = budget.begin("model")
    # Give the classifier a vocabulary projection. Arbitrary user text, names,
    # identifiers, and dates stay in the deterministic workflow.
    vocabulary = set("""
        specialist emergency generic video therapy virtual behavioral urgent primary deductible
        oop out of pocket physical visit copay coinsurance retail pharmacy prescription care
        claim claims denied denial appeal status pended paid owe owed received count how many
        provider find search locate dermatologist dermatology cardiologist cardiology clinician
        neurologist neurology pediatrics orthopedics grievance resolution deadline
        authorization prior approved coverage enrol enrolled limit remaining annual family
        individual service plan network cost maximum visit visits appointment hospital
        renewal renew waived window
    """.split())
    projection = " ".join(word for word in re.findall(r"[a-z]+", lower) if word in vocabulary)
    max_tokens = 96
    estimated = ((len(projection) + 600) * settings.input_per_million
                 + max_tokens * settings.output_per_million) / 1_000_000
    if estimated > budget.model_remaining:
        budget.record("model", start, "cost_denied")
        raise Unavailable
    budget.model_cost += estimated
    result = "failed"
    usage_metrics = {}
    try:
        async with asyncio.timeout(budget.remaining()):
            response = await client.post(
                settings.model_url,
                headers={"Authorization": f"Bearer {settings.model_key}"} if settings.model_key else {},
                json={"model": settings.model_name, "temperature": 0, "max_tokens": max_tokens,
                      "response_format": {"type": "json_object"}, "messages": [
                          {"role": "system", "content":
                           "Classify member-services keywords. Return JSON with operation and optional subject. "
                           "Never select draft, verify, or decline. Schema: " + str(Intent.model_json_schema())},
                          {"role": "user", "content": projection},
                      ]},
            )
            response.raise_for_status()
            payload = response.json()
            intent = Intent.model_validate_json(payload["choices"][0]["message"]["content"])
            if intent.operation in {"draft", "verify", "decline"}:
                raise ValueError
            usage = payload.get("usage") or {}
            if "prompt_tokens" in usage and "completion_tokens" in usage:
                usage_metrics = {key: int(usage[key]) for key in ("prompt_tokens", "completion_tokens")}
                budget.model_cost += ((usage_metrics["prompt_tokens"] * settings.input_per_million
                                       + usage_metrics["completion_tokens"] * settings.output_per_million)
                                      / 1_000_000 - estimated)
            if budget.model_cost > budget.model_remaining:
                raise ValueError
            result = "allowed"
            return intent
    except Exception as exc:
        raise Unavailable from exc
    finally:
        budget.record("model", start, result, **usage_metrics)

