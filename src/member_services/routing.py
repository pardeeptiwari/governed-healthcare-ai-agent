import asyncio
import re

from .domain import SPECIALTIES, Intent
from .gateway import Unavailable
from .model_provider import ChatCompletionsProvider
from .ports import ModelProvider

BENEFITS = {
    "specialist": "Specialist visit", "emergency": "Emergency room",
    "generic": "Generic drugs (Tier 1, 30-day retail)", "video": "Telehealth (video) visit",
    "virtual_behavioral": "Behavioral health outpatient visit (in person or virtual)",
    "therapy": "Physical therapy", "urgent": "Urgent care", "primary": "Primary care visit",
    "deductible": "Deductible (individual / family)", "oop": "Out-of-pocket maximum (individual / family)",
    "pt_limit": "Physical therapy visit limit",
}

def guarded_intent(message):
    if re.search(r"\b(ssn|social security(?: number)?)\b", message):
        return Intent(operation="verify" if re.search(r"\blast (four|4)\b", message) and
                      not re.search(r"\b(full|entire|complete)\b", message) else "decline")
    if "grievance" in message and re.search(r"\b(file|submit|create)\b", message) and not re.search(
        r"\b(filed|deadline)\b|\b(?:do not|don't|never|without)\s+(?:file|submit|create)\b", message
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
    if ("claim" in message or re.search(r"\bc\d{6,}\b", message)
            or (re.search(r"\b(?:owe|owed|paid|billed)\b", message)
                and re.search(r"\b\d{4}-\d{2}-\d{2}\b", message))):
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


async def classify(settings, client, budget, text, *, provider: ModelProvider | None = None):
    lower = text.lower()
    guarded = guarded_intent(lower)
    if guarded:
        budget.events.append({"operation": "classify", "decision": "policy"})
        return guarded
    if provider is None and not settings.model_url:
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
            active_provider = provider if provider is not None else ChatCompletionsProvider(
                client, settings.model_url, settings.model_name, settings.model_key
            )
            completion = await active_provider.complete(
                instruction="Classify member-services keywords. Return JSON with operation and optional subject. "
                            "Never select draft, verify, or decline. Schema: " + str(Intent.model_json_schema()),
                projection=projection,
                max_tokens=max_tokens,
            )
            if completion.prompt_tokens is not None and completion.completion_tokens is not None:
                prompt, output = completion.prompt_tokens, completion.completion_tokens
                if type(prompt) is not int or type(output) is not int or min(prompt, output) < 0:
                    raise ValueError("Invalid model token usage")
                usage_metrics = {"prompt_tokens": prompt, "completion_tokens": output}
                budget.model_cost += ((usage_metrics["prompt_tokens"] * settings.input_per_million
                                       + usage_metrics["completion_tokens"] * settings.output_per_million)
                                      / 1_000_000 - estimated)
            if budget.model_cost > budget.model_remaining:
                result = "cost_exceeded"
                raise ValueError("Model usage exceeded available budget")
            intent = Intent.model_validate_json(completion.content)
            if intent.operation in {"draft", "verify", "decline"}:
                raise ValueError("Model proposed a reserved operation")
            result = "allowed"
            return intent
    except Exception as exc:
        raise Unavailable from exc
    finally:
        budget.record("model", start, result, **usage_metrics)
