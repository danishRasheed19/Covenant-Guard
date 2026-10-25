import json

import pytest

from extraction.draft import Draft, draft_to_covenant
from extraction.evidence import Evidence, gather_evidence
from extraction.extract import extract_covenants
from extraction.verify import number_in_text, verify_covenant
from llm.types import LLMResult
from schema import ReviewStatus, Unit

QUOTE = "The Borrower shall not permit the Total Leverage Ratio as of the last day of any fiscal quarter to exceed 3.50 to 1.00."
SECTION_TEXT = "Section 7.11 Financial Covenants. " + QUOTE + " For fiscal quarters ending on or after June 30, 2026 the ratio is 3.00 to 1.00."


def step(value, effective_from=None, condition=None):
    return {"effective_from": effective_from, "value": value, "condition": condition}


def draft_item(**over):
    base = dict(covenant_type="max_total_leverage", operator="<=", unit="ratio",
                threshold_steps=[step(3.5), step(3.0, "2026-04-01")],
                numerator_term="Total Debt", denominator_term="EBITDA", measure_term=None,
                test_frequency="quarterly", measurement_basis="trailing_four_quarters",
                section="Section 7.11", quote=QUOTE, defined_terms_used=["Total Debt", "EBITDA"], ambiguities=[])
    base.update(over)
    return base


class FakeClient:
    model = "fake"

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def generate_json(self, prompt, schema, *, system=None, temperature=0.0, use_cache=True):
        self.prompts.append(prompt)
        data = self.answers.pop(0)
        return LLMResult(data=data, text=json.dumps(data), model="fake", provider="fake")


EVIDENCE = [Evidence("c1", "ARTICLE VII > 7.11 Financial Covenants", SECTION_TEXT, "7.11")]


# ---------- draft conversion ----------
def test_valid_draft_becomes_covenant():
    covenant, notes = draft_to_covenant(Draft.model_validate({"covenants": [draft_item()]}).covenants[0], "x-lev-001")
    assert covenant.threshold_on(__import__("datetime").date(2026, 6, 30)) == 3.0
    assert notes["defined_terms_used"] == ["Total Debt", "EBITDA"]


def test_conditional_step_is_moved_to_ambiguities():
    item = Draft.model_validate({"covenants": [draft_item(threshold_steps=[step(3.5), step(4.5, None, "during the Adjustment Period")])]}).covenants[0]
    covenant, _ = draft_to_covenant(item, "x-lev-001")
    assert len(covenant.threshold_schedule) == 1
    assert "Conditional threshold 4.5" in covenant.ambiguities[0]


def test_schema_rule_violations_are_readable_errors():
    bad_operator = Draft.model_validate({"covenants": [draft_item(operator=">=")]}).covenants[0]
    with pytest.raises(ValueError, match="must use operator"):
        draft_to_covenant(bad_operator, "x")
    bad_date = Draft.model_validate({"covenants": [draft_item(threshold_steps=[step(3.5), step(3.0, "June 2026")])]}).covenants[0]
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        draft_to_covenant(bad_date, "x")
    only_conditional = Draft.model_validate({"covenants": [draft_item(threshold_steps=[step(3.5, None, "if X")])]}).covenants[0]
    with pytest.raises(ValueError, match="no unconditional"):
        draft_to_covenant(only_conditional, "x")


# ---------- verification ----------
def covenant_from(**over):
    return draft_to_covenant(Draft.model_validate({"covenants": [draft_item(**over)]}).covenants[0], "x-lev-001")[0]


def test_number_matching():
    assert number_in_text(3.5, Unit.RATIO, "to exceed 3.50 to 1.00")
    assert not number_in_text(3.5, Unit.RATIO, "to exceed 13.50 to 1.00")
    assert not number_in_text(3.5, Unit.RATIO, "to exceed 3.55 to 1.00")
    assert number_in_text(50_000_000, Unit.USD, "at least $50,000,000 of Liquidity")
    assert number_in_text(50_000_000, Unit.USD, "at least $50 million")


def test_verifier_statuses():
    good = verify_covenant(covenant_from(), SECTION_TEXT, SECTION_TEXT)
    assert good.status == ReviewStatus.AUTO_VERIFIED
    invented_quote = verify_covenant(covenant_from(quote="The Borrower must keep leverage low at all times always."), SECTION_TEXT, SECTION_TEXT)
    assert invented_quote.status == ReviewStatus.REJECTED and "quote" in invented_quote.problems[0]
    invented_number = verify_covenant(covenant_from(threshold_steps=[step(3.5), step(2.0, "2026-04-01")]), SECTION_TEXT, SECTION_TEXT)
    assert invented_number.status == ReviewStatus.REJECTED
    with_ambiguity = verify_covenant(covenant_from(ambiguities=["equity cure"]), SECTION_TEXT, SECTION_TEXT)
    assert with_ambiguity.status == ReviewStatus.AUTO_VERIFIED and with_ambiguity.notes == ["equity cure"]


def test_section_number_is_found_in_chunk_metadata():
    cov = covenant_from(section="Section 8.1")
    assert verify_covenant(cov, SECTION_TEXT, SECTION_TEXT).warnings          # not in the text alone
    assert not verify_covenant(cov, SECTION_TEXT, SECTION_TEXT, "8.1 ARTICLE VIII > 8.1 Maximum Leverage").warnings


def test_unscheduled_conditional_step_forces_review():
    item = Draft.model_validate({"covenants": [draft_item(threshold_steps=[step(3.5), step(4.5, None, "during the Adjustment Period")])]}).covenants[0]
    cov, _ = draft_to_covenant(item, "x-lev-001")
    assert verify_covenant(cov, SECTION_TEXT, SECTION_TEXT).status == ReviewStatus.NEEDS_REVIEW


# ---------- agent ----------
def test_extract_happy_path_sets_review_status():
    client = FakeClient({"covenants": [draft_item()]})
    result = extract_covenants(client, EVIDENCE, id_prefix="x", source_text=SECTION_TEXT)
    assert result.errors == [] and len(result.outcomes) == 1
    outcome = result.outcomes[0]
    assert outcome.covenant.covenant_id == "x-lev-001"
    assert outcome.covenant.review_status == ReviewStatus.AUTO_VERIFIED
    assert result.api_calls == 1 and "Financial Covenants" in client.prompts[0]


def test_one_repair_call_fixes_a_schema_error():
    client = FakeClient({"covenants": [draft_item(operator=">=")]}, {"covenants": [draft_item()]})
    result = extract_covenants(client, EVIDENCE, id_prefix="x", source_text=SECTION_TEXT)
    assert len(client.prompts) == 2 and "must use operator" in client.prompts[1]
    assert len(result.outcomes) == 1 and result.errors == []


def test_unfixable_draft_is_reported_not_hidden():
    client = FakeClient({"covenants": [draft_item(operator=">=")]}, {"covenants": [draft_item(operator=">=")]})
    result = extract_covenants(client, EVIDENCE, id_prefix="x", source_text=SECTION_TEXT)
    assert result.outcomes == [] and len(result.errors) == 1


def test_empty_answer_and_id_numbering():
    assert extract_covenants(FakeClient({"covenants": []}), EVIDENCE, id_prefix="x", source_text="").outcomes == []
    two = FakeClient({"covenants": [draft_item(), draft_item(threshold_steps=[step(3.5)])]})
    ids = [o.covenant.covenant_id for o in extract_covenants(two, EVIDENCE, id_prefix="x", source_text=SECTION_TEXT).outcomes]
    assert ids == ["x-lev-001", "x-lev-002"]


# ---------- evidence ----------
def test_gather_evidence_round_robin_dedup_and_limits():
    def ev(i):
        return Evidence(f"c{i}", f"p{i}", "t" * 100)

    results = {"q1": [ev(1), ev(2), ev(3)], "q2": [ev(1), ev(4), ev(5)]}
    got = gather_evidence(lambda q, k: results[q][:k], queries=["q1", "q2"], per_query=3, max_chunks=4)
    assert [e.chunk_id for e in got] == ["c1", "c2", "c4", "c3"]
    capped = gather_evidence(lambda q, k: results[q][:k], queries=["q1", "q2"], per_query=3, max_chunks=10, max_chars=250)
    assert [e.chunk_id for e in capped] == ["c1", "c2"]


def test_only_best_hit_is_expanded_and_reserved_is_dropped():
    def ev(i, path="p"):
        return Evidence(f"c{i}", f"{path}{i}", "t" * 10, f"{i}.0")

    results = {"q1": [ev(1), ev(2)], "q2": [ev(3), ev(4, "8.2 [Reserved] ")]}
    calls = []

    def expand(items):
        calls.append([x.chunk_id for x in items])
        return items + [Evidence(f"{items[0].chunk_id}b", "part", "t", "")]

    got = gather_evidence(lambda q, k: results[q][:k], queries=["q1", "q2"], per_query=2, expand=expand)
    assert calls == [["c1"], ["c3"]]
    assert [e.chunk_id for e in got] == ["c1", "c1b", "c3", "c3b", "c2"]
