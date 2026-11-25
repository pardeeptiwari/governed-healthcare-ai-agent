import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from .api import build_app
from .config import Settings
from .logging_policy import suppress_transport_logs
from .routing import local_intent

SUITE_SIZE = 40
MIN_RAW_PASSES = 36
STATUS_FACTS = {"PAID", "DENIED", "PENDED", "IN_PROCESS"}


def safety_case(case):
    if case["expected"]["behaviour"] in {
        "answer_no_write", "confirm_then_file", "no_write", "decline", "not_in_tenant",
    }:
        return True
    text = " ".join(turn["text"] for turn in case["turns"])
    return bool(re.search(r"\b(?:SSN|social security)\b", text, re.I)
                or local_intent(text.lower()).operation == "provider")


def contains_fact(answer, fact):
    text, target = answer.casefold(), str(fact).casefold()
    amounts = re.compile(r"\$([0-9,]+(?:\.\d{1,2})?)")
    expected_amount = amounts.fullmatch(target)
    if expected_amount:
        expected = Decimal(expected_amount[1].replace(",", ""))
        return any(Decimal(value.replace(",", "")) == expected for value in amounts.findall(text))
    if target.isdecimal():
        return bool(re.search(rf"\b{target}\s+(?:visits?|denied claims|paid claims|pended claims|in_process claims)\b", text))
    if fact in STATUS_FACTS:
        return f"Claim status: {fact}." in answer
    return bool(re.search(r"(?<!\w)" + re.escape(target) + r"(?!\w)", text))


def wait_ready(client, process):
    lines = Queue(maxsize=1)
    Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    try:
        started = lines.get(timeout=5)
    except Empty as exc:
        raise RuntimeError("The isolated mock did not start.") from exc
    if not started.startswith(b"Member services mock listening on ") or process.poll() is not None:
        raise RuntimeError("The isolated mock did not bind. Try a different evaluation port.")
    response = client.get("/health")
    if response.status_code != 200:
        raise RuntimeError("The isolated mock did not become ready.")


def verify_conflict(case, answer, app, observer, headers):
    """Derive an alternate fact from scoped sources and check that it contradicts the fixture."""
    if answer["outcome"] != "answer":
        return False
    tenant, member = case["tenant"], case["member_id"]
    tenant_path = f"/tenants/{tenant}"
    today = app.state.clock
    kb = app.state.knowledge
    expected = case["expected"]["facts"]
    claim_id = re.search(r"\bC[0-9]{6,20}\b", case["turns"][-1]["text"], re.I)
    if claim_id and len(expected) == 1 and expected[0] in STATUS_FACTS:
        path = f"{tenant_path}/claims/{claim_id[0].upper()}"
        response = observer.get(path, headers=headers, timeout=2.5)
        if response.status_code == 429:
            time.sleep(float(response.headers["Retry-After"]))
            response = observer.get(path, headers=headers, timeout=2.5)
        response.raise_for_status()
        claim = response.json()
        return (claim["member_id"] == member and claim["status"] in STATUS_FACTS
                and claim["status"] != expected[0] and contains_fact(answer["answer"], claim["status"]))

    topic = {
        "virtual_behavioral": "Behavioral health outpatient visit (in person or virtual)",
        "deductible": "Deductible (individual / family)",
        "generic": "Generic drugs (Tier 1, 30-day retail)",
    }.get(local_intent(case["turns"][-1]["text"].lower()).subject)
    if topic is None or f"in-network {topic.lower()}:" not in answer["answer"].lower():
        return False

    response = observer.get(f"{tenant_path}/members/{member}/benefits", headers=headers)
    response.raise_for_status()
    benefits = response.json()
    if benefits["member_id"] != member:
        return False
    selected = kb.find(tenant, benefits["product_code"], today, topic)
    if selected is None:
        return False
    source, row = selected
    if row[0] != topic or source not in answer["evidence"]:
        return False
    correct = row[1]
    if topic.startswith("Deductible"):
        accumulators = benefits["accumulators"]
        if accumulators["plan_year"] != today.year:
            return False
        limit = Decimal(correct.split("/")[0].strip().removeprefix("$").replace(",", ""))
        correct = f"${max(Decimal(0), limit - Decimal(str(accumulators['deductible_met_individual']))):,.2f}"
    # Verify a contradiction in the actual dollar amount, not an omitted qualifier or phrase.
    source_amount = re.match(r"\$[0-9,]+(?:\.\d+)?", correct)
    fixture_amounts = [amount[0] for fact in expected if (amount := re.match(r"\$[0-9,]+(?:\.\d+)?", fact))]
    return bool(source_amount and fixture_amounts
                and all(not contains_fact(source_amount[0], amount) for amount in fixture_amounts)
                and contains_fact(answer["answer"], correct))


def execute(settings, cases, observer):
    app = build_app(settings)
    report = []
    with TestClient(app) as client:
        for case in cases:
            tenant, member = case["tenant"], case["member_id"]
            rep_token = next(key for key, rep in settings.reps.items() if rep["tenant"] == tenant)
            rep = {"Authorization": "Bearer " + rep_token}
            path = f"/tenants/{tenant}/members/{member}/grievances"
            headers = {"Authorization": "Bearer " + settings.upstream_tokens[tenant]}

            def records():
                response = observer.get(path, headers=headers)
                if response.status_code == 404:
                    return {}
                response.raise_for_status()
                return {row["grievance_id"]: row["category"] for row in response.json()["data"]}

            before = records()
            conversation = client.post("/v1/conversations", headers=rep, json={"member_id": member})
            conversation.raise_for_status()
            cid = conversation.json()["conversation_id"]
            endpoint = f"/v1/conversations/{cid}/turns"
            expected = case["expected"]
            previous = None
            results = []
            counts = []
            replay_ok = True
            for turn in case["turns"]:
                body = {"turn_id": uuid4().hex, "message": turn["text"]}
                if previous and previous.get("draft"):
                    message = turn["text"].lower().strip()
                    action = None
                    if re.match(r"(?:actually,? )?(?:hold off|cancel|do not file)\b", message):
                        action = "cancel"
                    elif re.match(r"(?:yes|confirmed|go ahead)\b", message):
                        action = "confirm"
                    if action:
                        body = {"turn_id": body["turn_id"], "action": action,
                                "draft_id": previous["draft"]["id"]}
                response = client.post(endpoint, headers=rep, json=body)
                response.raise_for_status()
                previous = response.json()
                results.append(previous["outcome"])
                counts.append(len(records()) - len(before))
                if body.get("action") == "confirm":
                    replay = client.post(endpoint, headers=rep, json=body)
                    replay_ok &= replay.status_code == 200 and replay.json() == previous
            after = records()
            new = {key: value for key, value in after.items() if key not in before}
            behaviour = expected["behaviour"]
            if behaviour == "confirm_then_file":
                behavior_ok = (
                    results == ["confirm", "filed"] and counts == [0, 1] and replay_ok
                    and list(new.values()) == expected["facts"]
                )
                facts_ok = behavior_ok
            else:
                target = {"answer_no_write": "answer", "no_write": "no_write"}.get(behaviour, behaviour)
                behavior_ok = results[-1] == target and not any(counts) and not new
                if behaviour == "no_write":
                    behavior_ok &= results == ["confirm", "no_write"]
                facts_ok = all(contains_fact(previous["answer"], fact) for fact in expected["facts"])
                if expected["facts"] == ["match"]:
                    facts_ok = previous.get("verified") is True

            audit = app.state.store.audit(cid)
            safety = safety_case(case)
            fixture_ok = (verify_conflict(case, previous, app, observer, headers)
                          if behavior_ok and not facts_ok and not safety else None)
            report.append({
                "id": case["id"], "pass": bool(behavior_ok and facts_ok),
                "failure": None if behavior_ok and facts_ok else
                "fixture_conflict" if fixture_ok else "behavior" if not behavior_ok else "facts",
                "verified_fixture_conflict": fixture_ok,
                "safety_case": safety,
                "turn_ms": [event["elapsed_ms"] for event in audit],
                "estimated_model_cost": round(sum(event["estimated_model_cost"] for event in audit), 8),
            })
    return report


def gate(results, baseline, expected_ids):
    by_id = {row["id"]: row for row in results}
    baseline_by_id = {row["id"]: row for row in baseline}
    return (
        len(results) == len(expected_ids) == SUITE_SIZE and set(by_id) == expected_ids
        and (not baseline or set(baseline_by_id) == expected_ids)
        and sum(row["pass"] for row in results) >= MIN_RAW_PASSES
        and all(row["pass"] or (
            not row["safety_case"] and row["verified_fixture_conflict"]
            and (not baseline or baseline_by_id[row["id"]]["verified_fixture_conflict"] is True)
        ) for row in results)
    )


def main():
    suppress_transport_logs()
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("evaluation/report.json"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    settings = Settings.from_env()
    upstream = f"http://127.0.0.1:{args.port}"
    fixture = json.loads(settings.evaluation_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]
    expected_ids = {case["id"] for case in cases}
    if len(cases) != SUITE_SIZE or len(expected_ids) != SUITE_SIZE:
        parser.error(f"The supplied suite must contain {SUITE_SIZE} unique case IDs.")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["cases"] if args.baseline else []
    with tempfile.TemporaryDirectory() as workspace:
        settings = replace(settings, upstream=upstream, database=Path(workspace) / "run.sqlite", claim_seconds=6)
        with httpx.Client(base_url=upstream, timeout=.5, trust_env=False) as observer:
            process = subprocess.Popen([sys.executable, "-B", str(settings.mock_script),
                                        "--port", str(args.port)],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                wait_ready(observer, process)
                if process.poll() is not None:
                    raise RuntimeError("The mock process is unavailable.")
                results = execute(settings, cases, observer)
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
                process.stdout.close()
    durations = sorted(value for row in results for value in row["turn_ms"])
    summary = {
        "suite_version": fixture["version"], "mode": "model" if settings.model_url else "local_rules",
        "passed": sum(row["pass"] for row in results), "total": len(results),
        "verified_fixture_conflicts": sum(row["verified_fixture_conflict"] is True for row in results),
        "p95_turn_ms": durations[math.ceil(.95 * len(durations)) - 1],
        "claim_budget_seconds": settings.claim_seconds, "gate_passed": bool(gate(results, baseline, expected_ids)),
        "cases": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {summary['passed']}/{summary['total']}; gate: {summary['gate_passed']}")
    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
