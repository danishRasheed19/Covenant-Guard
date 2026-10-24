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
