import pytest

from retrieval.evaluate import build_questions, gold_chunk_ids, is_placeholder, reciprocal_rank, summarize

CHUNKS = [
    dict(chunk_id="c1", kind="section", path="9.15", text="The Borrower shall not permit the Consolidated Total Leverage Ratio to exceed 4.00 to 1.00."),
    dict(chunk_id="c2", kind="definition", path="d", text='"Consolidated EBITDA" means, for any period, Consolidated Net Income plus interest expense.'),
    dict(chunk_id="c3", kind="section", path="x", text="Unrelated reporting covenant."),
]


def label(quote="The Borrower shall not  permit the Consolidated Total Leverage Ratio to exceed 4.00 to 1.00.", definition='"Consolidated EBITDA" means, for any period, Consolidated Net Income'):
    return {"covenants": [{
        "covenant_id": "x-lev", "covenant_type": "max_total_leverage",
        "citation": {"section": "9.15", "quote": quote},
        "definitions": [{"term": "Consolidated EBITDA", "definition": definition}],
    }]}


def test_gold_is_whitespace_tolerant():
    assert gold_chunk_ids(CHUNKS, "shall not permit  the consolidated total leverage ratio") == ["c1"]


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a"], {"z"}) == 0.0


def test_summarize():
    s = summarize([{"rr": 1.0, "rank": 1}, {"rr": 0.25, "rank": 4}, {"rr": 0.0, "rank": None}])
    assert s["n"] == 3 and s["hit@1"] == pytest.approx(1 / 3) and s["hit@3"] == pytest.approx(1 / 3)
    assert s["hit@5"] == pytest.approx(2 / 3) and s["mrr"] == pytest.approx(1.25 / 3)
    assert summarize([])["n"] == 0


def test_build_questions():
    questions, problems = build_questions(label(), CHUNKS)
    assert problems == []
    kinds = [q["kind"] for q in questions]
    assert kinds.count("covenant") == 2 and kinds.count("definition") == 1
    assert all(q["gold"] == {"c1"} for q in questions if q["kind"] == "covenant")
    assert [q for q in questions if q["kind"] == "definition"][0]["gold"] == {"c2"}


def test_placeholders_and_missing_text_are_reported_not_asked():
    assert is_placeholder("PASTE from data") and not is_placeholder("real text")
    questions, problems = build_questions(label(quote="PASTE here", definition="not in any chunk"), CHUNKS)
    assert questions == []
    assert len(problems) == 2
