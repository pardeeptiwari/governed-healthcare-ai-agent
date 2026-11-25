from datetime import date

from member_services.knowledge import Knowledge


def document(root, filename, row, *, folder="plan_a", tenant="plan_a",
             product="TEST", effective="2026-01-01", metadata=""):
    directory = root / folder
    directory.mkdir(exist_ok=True)
    (directory / filename).write_text(
        f"---\ntenant: {tenant}\nproduct_code: {product}\nplan_year: {effective[:4]}\n"
        f"effective_date: {effective}\n{metadata}---\n{row}\n",
        encoding="utf-8",
    )


def test_product_tenant_and_effective_date_are_resolved_before_retrieval(tmp_path):
    topic = "Specialist visit"
    document(tmp_path, "current.md", "| Specialist visit | Current |")
    document(tmp_path, "later.md", "| Specialist visit | Later |", effective="2026-07-01")
    document(tmp_path, "renewal.md", "| Specialist visit | Renewal |", effective="2027-01-01")
    document(tmp_path, "general.md", "| Specialist visit | General |", product="ALL")
    document(tmp_path, "other.md", "| Specialist visit | Different product |", product="OTHER")
    document(tmp_path, "foreign.md", "| Specialist visit | Foreign |", folder="plan_b", tenant="plan_b")
    index = Knowledge(tmp_path)

    assert index.find("plan_a", "TEST", date(2026, 6, 30), topic) == ("current.md", (topic, "Current"))
    assert index.find("plan_a", "TEST", date(2026, 7, 1), topic)[0] == "later.md"
    assert index.find("plan_a", "TEST", date(2027, 1, 1), topic)[0] == "renewal.md"
    assert index.find("plan_a", "TEST", date(2026, 7, 1), topic, all_products=True)[0] == "general.md"
    assert index.find("plan_b", "TEST", date(2026, 7, 1), topic)[0] == "foreign.md"
    assert index.find("plan_a", "TEST", date(2025, 7, 1), topic) is None
    assert index.find("unknown", "TEST", date(2026, 7, 1), topic) is None


def test_quarantine_ambiguous_metadata_and_ignore_fenced_rows(tmp_path):
    document(tmp_path, "safe.md",
             "~~~markdown\n| Hidden example | Example only |\n~~~\n| Specialist visit | Valid |")
    document(tmp_path, "wrong_tenant.md", "| Injected benefit | Foreign |", tenant="plan_b")
    document(tmp_path, "duplicate_metadata.md", "| Injected benefit | Duplicate |", metadata="tenant: plan_a\n")
    document(tmp_path, "other_tenant.md", "| Specialist visit | Valid |", folder="plan_b", tenant="plan_b")
    index = Knowledge(tmp_path)

    assert index.quarantined == 2
    assert index.find("plan_a", "TEST", date(2026, 7, 1), "Hidden example") is None
    assert index.find("plan_a", "TEST", date(2026, 7, 1), "Injected benefit") is None
    assert index.find("plan_b", "TEST", date(2026, 7, 1), "Injected benefit") is None


def test_equally_applicable_conflicting_rows_abstain(tmp_path):
    document(tmp_path, "one.md", "| Specialist visit | First value |")
    document(tmp_path, "two.md", "| Specialist visit | Conflicting value |")
    document(tmp_path, "other_tenant.md", "| Specialist visit | Valid |", folder="plan_b", tenant="plan_b")
    index = Knowledge(tmp_path)
    assert index.find("plan_a", "TEST", date(2026, 7, 1), "Specialist visit") is None
