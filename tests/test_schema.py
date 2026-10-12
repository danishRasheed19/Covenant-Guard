from datetime import date

import pytest
from pydantic import ValidationError

from schema import (
    Agreement,
    Covenant,
    ComplianceResult,
    ComplianceStatus,
    Operator,
    evaluate_threshold,
    headroom_pct,
)

QUOTE = (
    "The Borrower shall not permit the Total Leverage Ratio as of the last day "
    "of any fiscal quarter to exceed 3.50 to 1.00."
)


def make_leverage(**overrides):
    base = dict(
        covenant_id="acme-lev-001",
        covenant_type="max_total_leverage",
        operator="<=",
        unit="ratio",
        threshold_schedule=[{"value": 4.0}, {"effective_from": "2026-01-01", "value": 3.5}],
        numerator_term="Consolidated Total Debt",
        denominator_term="Consolidated EBITDA",
        citation={"section": "Section 7.11(a)", "quote": QUOTE},
    )
    base.update(overrides)
    return Covenant(**base)


def test_valid_leverage_covenant():
    c = make_leverage()
    assert c.review_status.value == "needs_review"  # safe default
    assert not c.requires_approximation


def test_step_down_threshold():
    c = make_leverage()
    assert c.threshold_on(date(2025, 6, 30)) == 4.0
    assert c.threshold_on(date(2026, 1, 1)) == 3.5
    assert c.threshold_on(date(2027, 3, 31)) == 3.5


def test_wrong_operator_for_type_rejected():
    with pytest.raises(ValidationError):
        make_leverage(operator=">=")


def test_ratio_requires_denominator():
    with pytest.raises(ValidationError):
        make_leverage(denominator_term=None)


def test_usd_requires_measure_term():
    with pytest.raises(ValidationError):
        make_leverage(
            covenant_type="min_liquidity", operator=">=", unit="usd",
            numerator_term=None, denominator_term=None,
        )


def test_schedule_must_be_ordered():
    with pytest.raises(ValidationError):
        make_leverage(
            threshold_schedule=[
                {"effective_from": "2026-01-01", "value": 3.5},
                {"effective_from": "2025-01-01", "value": 4.0},
            ]
        )


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        make_leverage(made_up_field="llm hallucination")


def test_quote_matching_tolerates_whitespace_and_curly_quotes():
    c = make_leverage()
    messy = (
        "ARTICLE VII\n  The Borrower shall not permit the Total  Leverage Ratio as of\n"
        "the last day of any fiscal quarter to exceed 3.50 to 1.00.\u00a0"
    )
    assert c.citation.quote_in(messy)
    assert not c.citation.quote_in("The Borrower shall maintain minimum liquidity of $50,000,000.")


def test_headroom_example_from_plan():
    # 3.2x actual vs 3.5x max -> ~8.6% headroom
    assert headroom_pct(Operator.LTE, 3.2, 3.5) == pytest.approx(8.571, abs=0.01)
    assert headroom_pct(Operator.GTE, 4.0, 3.0) == pytest.approx(33.33, abs=0.01)
    assert headroom_pct(Operator.LTE, 3.8, 3.5) < 0  # breach -> negative


def test_equality_passes():
    assert evaluate_threshold(Operator.LTE, 3.5, 3.5) == ComplianceStatus.COMPLIANT
    assert evaluate_threshold(Operator.GTE, 3.0, 3.0) == ComplianceStatus.COMPLIANT
    assert evaluate_threshold(Operator.LTE, 3.51, 3.5) == ComplianceStatus.BREACH


def test_agreement_pads_cik_and_rejects_duplicate_ids():
    kwargs = dict(
        company_name="Acme Corp", cik="12345", agreement_title="Credit Agreement",
        filing_accession="0001234567-25-000123", filing_date="2025-03-01",
        exhibit_url="https://www.sec.gov/Archives/edgar/data/12345/example.htm",
    )
    a = Agreement(**kwargs, covenants=[make_leverage()])
    assert a.cik == "0000012345"
    with pytest.raises(ValidationError):
        Agreement(**kwargs, covenants=[make_leverage(), make_leverage()])


def test_result_needs_value_unless_indeterminate():
    with pytest.raises(ValidationError):
        ComplianceResult(
            covenant_id="x", period_end="2025-09-30", threshold=3.5,
            operator="<=", status="compliant",
        )
    ComplianceResult(
        covenant_id="x", period_end="2025-09-30", threshold=3.5,
        operator="<=", status="indeterminate",
    )
