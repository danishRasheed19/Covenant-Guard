from datetime import date

from extraction.definitions import resolve_definitions, strip_leading_term, terms_in
from extraction.evaluate import evaluate_agreement, summarise, period_end_dates, threshold_on

DEFS = {
    "total leverage ratio": ('"Total Leverage Ratio" means the ratio of Indebtedness of the Loan Parties to Adjusted EBITDA.', "Section 1.01"),
    "adjusted ebitda": ('"Adjusted EBITDA" means EBITDA plus non-cash charges, as determined by the Agent.', "Section 1.01"),
    "ebitda": ('"EBITDA" means Consolidated Net Income plus taxes.', "Section 1.01"),
    "consolidated net income": ('"Consolidated Net Income" means net income of the Person.', "Section 1.01"),
    "indebtedness": ('"Indebtedness" means borrowed money.', "Section 1.01"),
    "person": ('"Person" means an individual.', "Section 1.01"),
}
KNOWN = ["Total Leverage Ratio", "Adjusted EBITDA", "EBITDA", "Consolidated Net Income", "Indebtedness", "Person", "Loan Parties"]


def lookup(term):
    return DEFS.get(term.strip().lower())


def test_terms_in_prefers_long_terms():
    assert terms_in("Adjusted EBITDA and EBITDA", KNOWN) == ["Adjusted EBITDA", "EBITDA"]
    assert terms_in("use Adjusted EBITDA only", KNOWN) == ["Adjusted EBITDA"]


def test_strip_leading_term():
    assert strip_leading_term("EBITDA", '"EBITDA" means x') == "means x"


def test_closure_follows_financial_terms_and_skips_generic():
    out = resolve_definitions(["Total Leverage Ratio", "Loan Parties", "Unknown Term"], lookup, KNOWN)
    assert [d.term for d in out] == ["Total Leverage Ratio", "Adjusted EBITDA", "Indebtedness", "EBITDA", "Consolidated Net Income"]
    assert "Person" not in [d.term for d in out]
    assert out[1].has_custom_adjustments and not out[2].has_custom_adjustments
    assert out[0].definition.startswith("means")


def test_closure_respects_max_depth():
    assert [d.term for d in resolve_definitions(["Total Leverage Ratio"], lookup, KNOWN, max_depth=2)][-1] != "Consolidated Net Income"


LABEL = {"agreement_date": "2026-05-06", "covenants": [{
    "covenant_id": "l1", "covenant_type": "max_total_leverage", "operator": "<=", "unit": "ratio", "test_frequency": "monthly",
    "measurement_basis": "trailing_twelve_months",
    "threshold_schedule": [{"effective_from": "2026-04-30", "value": 3.0}],
    "definitions": [{"term": "Total Leverage Ratio"}, {"term": "EBITDA"}]}]}


def extracted(**over):
    cov = {"covenant_id": "e1", "covenant_type": "max_total_leverage", "operator": "<=", "unit": "ratio", "test_frequency": "monthly",
           "measurement_basis": "trailing_twelve_months", "threshold_schedule": [{"effective_from": None, "value": 3.0}],
           "verification": {"problems": []}, "defined_terms_used": ["Total Leverage Ratio"], "definitions": [{"term": "Total Leverage Ratio"}],
           "review_status": "needs_review"}
    cov.update(over)
    return {"covenants": [cov]}


def test_period_end_dates_and_threshold_on():
    assert period_end_dates("quarterly", date(2026, 1, 1), date(2026, 12, 31)) == [date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30), date(2026, 12, 31)]
    assert len(period_end_dates("monthly", date(2026, 4, 30), date(2026, 6, 30))) == 3
    schedule = [{"effective_from": None, "value": 6.0}, {"effective_from": "2027-10-01", "value": 5.0}]
    assert threshold_on(schedule, date(2027, 9, 30)) == 6.0 and threshold_on(schedule, date(2027, 12, 31)) == 5.0


def test_null_first_date_equals_label_start_at_period_end_dates():
    res = evaluate_agreement(LABEL, extracted())["rows"][0]
    assert res["threshold_ok"] == res["threshold_total"] > 0
    assert res["quote_ok"] and res["terms_named"] == 1 and res["terms_final"] == 1 and res["terms_missing_final"] == ["ebitda"]


def test_wrong_value_missing_and_extra_covenants():
    wrong = evaluate_agreement(LABEL, extracted(threshold_schedule=[{"effective_from": None, "value": 3.5}]))["rows"][0]
    assert wrong["threshold_ok"] == 0
    none = evaluate_agreement(LABEL, {"covenants": []})
    assert none["rows"][0]["found"] is False
    extra = evaluate_agreement(LABEL, {"covenants": extracted()["covenants"] + [dict(extracted()["covenants"][0], covenant_type="min_liquidity")]})
    assert extra["extras"] == ["e1"]
    total = summarise({"x": extra})
    assert total["labelled"] == 1 and total["found"] == 1 and total["extras"] == 1


def test_quote_divergence_points_at_the_difference():
    from extraction.quote_check import divergence
    source = "Section 7.03. Permit the ratio to be greater than: September 30, 2024 6.20:1.00 December 31, 2024 5.75:1.00"
    assert divergence("Permit the ratio to be greater than: September 30, 2024 | 6.20:1.00", source)["matched"] > 20
    bad = divergence("Permit the ratio to be greater than: September 30, 2024 6.20:1.00 December 31, 2024 5.50:1.00", source)
    assert not bad["found"] and bad["quote_next"].startswith("50") and bad["source_next"].startswith("75")
    assert divergence("Permit the ratio", source)["found"]
