from member_services.evaluate import REVIEWED_CONFLICTS, contains_fact, gate


def test_claim_fact_only_matches_status_field():
    assert contains_fact("Claim status: PAID.", "PAID")
    assert not contains_fact("Claim status: DENIED. Previously paid.", "PAID")
    assert not contains_fact("2026: 18 claims processed", "18")
    assert contains_fact("18 visits remaining.", "18")
    assert not contains_fact("Remaining: $12,300.", "$2,300")


def test_gate_requires_evidence_for_each_reviewed_failure():
    rows = [{"id": f"EV-{index:02}", "pass": f"EV-{index:02}" not in REVIEWED_CONFLICTS,
             "verified_fixture_conflict": f"EV-{index:02}" in REVIEWED_CONFLICTS}
            for index in range(1, 41)]
    baseline = [row.copy() for row in rows]
    assert gate(rows, baseline)
    rows[22]["verified_fixture_conflict"] = False
    assert not gate(rows, baseline)
    rows[22]["verified_fixture_conflict"] = True
    rows[12]["pass"] = False
    assert not gate(rows, baseline)
