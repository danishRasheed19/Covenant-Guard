"""Tests for the label consistency checks in scripts/validate_labels.py."""

import copy
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "validate_labels", Path(__file__).resolve().parent.parent / "scripts" / "validate_labels.py"
)
validate_labels = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate_labels)

EBITDA = {
    "term": "Consolidated EBITDA",
    "definition": "means net income plus interest, taxes, depreciation and amortization.",
    "section": "Section 1.01",
    "has_custom_adjustments": True,
    "adjustment_notes": ["Standard add-backs"],
}


def _covenant(cid, ebitda):
    return {
        "covenant_id": cid, "covenant_type": "max_total_leverage", "operator": "<=", "unit": "ratio",
        "threshold_schedule": [{"value": 4.0}],
        "numerator_term": "Indebtedness", "denominator_term": "Consolidated EBITDA",
        "definitions": [ebitda],
        "citation": {"section": "Section 7.11", "quote": "Permit the Leverage Ratio to exceed 4.00:1.00."},
    }


def _agreement(**overrides):
    data = {
        "company_name": "Acme Corp", "cik": "123", "agreement_title": "Credit Agreement",
        "agreement_date": "2026-05-06", "filing_accession": "0000000123-26-000001", "filing_date": "2026-05-08",
        "exhibit_url": "https://www.sec.gov/x.htm",
        "covenants": [_covenant("a-1", dict(EBITDA)), _covenant("a-2", dict(EBITDA))],
    }
    data.update(overrides)
    return data


def _check(tmp_path, data):
    path = tmp_path / "label.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return validate_labels.check_file(path, None)


def test_clean_label_passes(tmp_path):
    assert _check(tmp_path, _agreement()) == ([], [])


def test_same_term_with_different_flag_fails(tmp_path):
    data = _agreement()
    data["covenants"][1]["definitions"][0]["has_custom_adjustments"] = False
    problems, _ = _check(tmp_path, data)
    assert any("has_custom_adjustments differs" in p for p in problems)


def test_same_term_with_different_text_fails(tmp_path):
    data = _agreement()
    data["covenants"][1]["definitions"][0]["definition"] = "means something else entirely."
    problems, _ = _check(tmp_path, data)
    assert any("definition text differs" in p for p in problems)


def test_whitespace_and_quote_style_differences_are_not_flagged(tmp_path):
    data = _agreement()
    data["covenants"][1]["definitions"][0]["definition"] = "means net income  plus interest, taxes,\ndepreciation and amortization."
    assert _check(tmp_path, data) == ([], [])


def test_different_notes_only_warn(tmp_path):
    data = _agreement()
    data["covenants"][1]["definitions"][0]["adjustment_notes"] = ["Other wording"]
    problems, warnings = _check(tmp_path, data)
    assert problems == [] and any("adjustment_notes differ" in w for w in warnings)


def test_filing_before_agreement_fails(tmp_path):
    problems, _ = _check(tmp_path, _agreement(filing_date="2025-05-06"))
    assert any("is before agreement_date" in p for p in problems)


def test_working_notes_fail(tmp_path):
    data = _agreement()
    data["covenants"][0]["ambiguities"] = ["Threshold checked by the fill script"]
    data["covenants"][1]["definitions"][0]["adjustment_notes"] = ["My summary; confirm when you paste"]
    problems, _ = _check(tmp_path, data)
    assert sum("working note" in p for p in problems) == 2


def test_trailing_twelve_months_is_a_valid_basis(tmp_path):
    data = _agreement()
    for c in data["covenants"]:
        c["test_frequency"], c["measurement_basis"] = "monthly", "trailing_twelve_months"
    assert _check(tmp_path, data) == ([], [])
