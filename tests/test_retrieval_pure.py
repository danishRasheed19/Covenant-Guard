import pytest

from retrieval.embedder import HashEmbedder
from retrieval.load import build_children, vector_literal
from retrieval.search import rrf, tsquery_from
from retrieval.windows import make_windows


def test_short_text_is_one_window():
    assert make_windows("hello world") == ["hello world"]
    assert make_windows("   ") == []


def test_windows_overlap_and_cover_everything():
    words = [f"w{i}" for i in range(600)]
    text = " ".join(words)
    windows = make_windows(text, size=300, overlap=60)
    assert len(windows) > 3
    assert all(len(w) <= 300 for w in windows)
    seen = set()
    for w in windows:
        seen.update(w.split())
    assert seen == set(words)                      # nothing lost, no word cut in half
    for a, b in zip(windows, windows[1:]):
        assert set(a.split()) & set(b.split())     # consecutive windows overlap


def test_bad_window_arguments():
    with pytest.raises(ValueError):
        make_windows("x", size=100, overlap=100)


def test_rrf_rewards_agreement():
    scores = rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] > scores["c"] > scores["d"] or scores["c"] == scores["d"]
    assert max(scores, key=scores.get) in ("a", "b")


def test_tsquery_dedupes_and_ignores_punctuation():
    assert tsquery_from("Interest coverage, interest!") == "interest | coverage"
    assert tsquery_from("???") == ""


def test_hash_embedder_is_deterministic_and_normalized():
    e = HashEmbedder(64)
    a, b = e.embed_query("interest coverage ratio"), e.embed_query("interest coverage ratio")
    assert a == b and len(a) == 64
    assert sum(x * x for x in a) == pytest.approx(1.0)
    close = sum(x * y for x, y in zip(a, e.embed_query("coverage ratio interest test")))
    far = sum(x * y for x, y in zip(a, e.embed_query("governing law new york")))
    assert close > far


def test_build_children_prefixes_path():
    chunk = {"path": "Article IX > Section 9.15", "text": "word " * 1000}
    kids = build_children(chunk, size=500, overlap=50)
    assert len(kids) > 2 and all(k.startswith("Article IX > Section 9.15\n") for k in kids)


def test_vector_literal():
    assert vector_literal([0.5, -1.0]) == "[0.500000,-1.000000]"


# ---- heading match ----
from retrieval.search import heading_score, heading_tokens


def test_heading_tokens_drop_filler_and_plurals():
    assert heading_tokens("Financial Covenants") == {"financial", "covenant"}
    assert heading_tokens("Interest on the Loans") == {"interest", "loan"}
    assert heading_tokens("Reserved Class") == {"reserved", "class"}      # 'ss' words keep their s


def test_heading_score_prefers_exact_term():
    q = heading_tokens("Indebtedness")
    exact = heading_score(q, "Indebtedness", "Definitions", "ARTICLE I DEFINITIONS")
    longer = heading_score(q, "Permitted Indebtedness", "Definitions", "ARTICLE I DEFINITIONS")
    assert exact == 1.0 and 0 < longer < exact


def test_heading_score_section_title_beats_article_title():
    q = heading_tokens("financial covenants")
    section = heading_score(q, "", "Financial Covenants", "ARTICLE VII NEGATIVE COVENANTS")
    sibling = heading_score(q, "", "Reserved", "ARTICLE VIII FINANCIAL COVENANTS")
    assert section == 1.0 and sibling == pytest.approx(0.5)               # roman numeral not counted
    assert heading_score(q, "", "Interest Rate Limitation", "ARTICLE X MISCELLANEOUS") == 0.0


def test_long_clause_query_still_finds_a_short_matching_title():
    q = heading_tokens("Borrower shall not permit the leverage ratio to exceed")
    assert heading_score(q, "", "Maximum Leverage Ratio", "ARTICLE VIII FINANCIAL COVENANTS") >= 0.3
