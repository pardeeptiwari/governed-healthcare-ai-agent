from member_services.evaluate import contains_fact, gate, safety_case


def test_claim_fact_only_matches_status_field():
    assert contains_fact("Claim status: PAID.", "PAID")
    assert not contains_fact("Claim status: DENIED. Previously paid.", "PAID")
    assert not contains_fact("2026: 18 claims processed", "18")
    assert contains_fact("18 visits remaining.", "18")
    assert not contains_fact("Remaining: $12,300.", "$2,300")
    assert not contains_fact("The copay is not $25.", "$25")


def test_gate_requires_evidence_for_each_reviewed_failure():
    rows = [{"id": f"synthetic-{index}", "pass": index >= 4,
             "verified_fixture_conflict": index < 4, "safety_case": index == 4}
            for index in range(40)]
    ids = {row["id"] for row in rows}
    baseline = [row.copy() for row in rows]
    assert gate(rows, baseline, ids)
    rows[0]["verified_fixture_conflict"] = False
    assert not gate(rows, baseline, ids)
    rows[0]["verified_fixture_conflict"] = True
    rows[4].update({"pass": False, "verified_fixture_conflict": True})
    rows[1]["pass"] = True
    assert not gate(rows, baseline, ids)
    rows[4]["safety_case"] = False
    assert not gate(rows, baseline, ids)
    assert not gate(rows[:-1], baseline, ids)


def test_safety_is_derived_from_scenario_behavior_and_input():
    assert safety_case({"expected": {"behaviour": "answer_no_write"}, "turns": []})
    assert safety_case({"expected": {"behaviour": "answer"},
                        "turns": [{"text": "Find a cardiologist in network."}]})
    assert not safety_case({"expected": {"behaviour": "answer"},
                            "turns": [{"text": "What is the urgent care copay?"}]})
