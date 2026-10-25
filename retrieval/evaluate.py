"""Measure retrieval quality against the hand-labeled agreements.

    python -m retrieval.evaluate                      (all label files whose document is loaded)
    python -m retrieval.evaluate --label eval/labeled/mec_2026_third_amendment.json
    python -m retrieval.evaluate --show-misses

For each label file (its name must equal the document slug) it asks retrieval the questions the
extractor will ask, and checks whether the right chunk comes back:

  * covenant questions:   "where is the leverage covenant?"  Right answer = any chunk that contains
                          the label's citation quote.
  * definition questions: "Consolidated EBITDA" (just the term).  Right answer = any chunk that contains
                          the start of the label's definition text.

Each question is run in three modes (hybrid, vector, fts) so you can see whether combining the two
searches helps. Metrics: hit@1 / hit@3 / hit@5 (right chunk within the top N) and MRR (mean of
1/rank of the first right chunk; 1.0 is perfect).

Questions never use the quote or the definition text, so the test is not rigged. Label text that is
still a placeholder (PASTE / FILL_IN) is skipped.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ingestion.textnorm import contains  # noqa: E402

MODES = ("hybrid", "hybrid_plain", "vector", "fts", "hybrid+article", "lookup")
# hybrid_plain = hybrid without the heading bonus. hybrid+article = top 3 plus the sibling sections of the
# top hit's article (a bigger context, so its size is printed). lookup = exact term lookup for definition
# questions (falls back to hybrid), hybrid for covenant questions.
TOP_K = 10
DEFINITION_PREFIX_CHARS = 150

COVENANT_QUERIES = {
    "max_total_leverage": [
        "maximum total leverage ratio financial covenant",
        "Borrower shall not permit the leverage ratio to exceed",
    ],
    "min_interest_coverage": [
        "minimum interest coverage ratio financial covenant",
        "Borrower shall not permit the interest coverage ratio to be less than",
    ],
    "min_liquidity": [
        "minimum liquidity financial covenant",
        "Borrower shall maintain minimum liquidity or availability of not less than",
    ],
    "other_financial": [
        "financial covenants",
        "financial covenant ratio shall not be less than or exceed",
    ],
}


def is_placeholder(text: str) -> bool:
    return "PASTE" in text or "FILL_IN" in text


def path_of(chunks: list, chunk_id: str) -> str:
    return next((c["path"] for c in chunks if c["chunk_id"] == chunk_id), "?")


def gold_chunk_ids(chunks: list, needle: str) -> list:
    """Ids of chunks whose text contains `needle` (whitespace/quote tolerant)."""
    return [c["chunk_id"] for c in chunks if contains(c["text"], needle)]


def reciprocal_rank(result_ids: list, gold: set) -> float:
    for rank, chunk_id in enumerate(result_ids, start=1):
        if chunk_id in gold:
            return 1.0 / rank
    return 0.0


def build_questions(label: dict, chunks: list) -> tuple:
    """Return (questions, problems). A question is {kind, text, gold}. problems lists unusable label text."""
    questions, problems, seen_terms = [], [], set()
    for cov in label.get("covenants", []):
        quote = cov["citation"]["quote"]
        if is_placeholder(quote):
            problems.append(f"{cov['covenant_id']}: citation quote is a placeholder, skipped")
        else:
            gold = gold_chunk_ids(chunks, quote)
            if not gold:
                problems.append(f"{cov['covenant_id']}: citation quote is in no loaded chunk (label or ingestion problem)")
            else:
                for query in COVENANT_QUERIES.get(cov["covenant_type"], COVENANT_QUERIES["other_financial"]):
                    questions.append({"kind": "covenant", "name": cov["covenant_id"], "text": query, "gold": set(gold),
                                      "gold_paths": [path_of(chunks, g) for g in gold[:3]]})
        for term in cov.get("definitions", []):
            name = term["term"]
            if name in seen_terms:
                continue
            seen_terms.add(name)
            if is_placeholder(term["definition"]):
                problems.append(f"definition '{name}': placeholder text, skipped")
                continue
            gold = gold_chunk_ids(chunks, term["definition"][:DEFINITION_PREFIX_CHARS])
            if not gold:
                problems.append(f"definition '{name}': text is in no loaded chunk (label or ingestion problem)")
                continue
            questions.append({"kind": "definition", "name": name, "text": name, "gold": set(gold),
                              "gold_paths": [path_of(chunks, g) for g in gold[:3]]})
    return questions, problems


def summarize(rows: list) -> dict:
    """rows: [{'rr': float, 'rank': int|None}]. Returns hit@1/3/5 and MRR."""
    n = len(rows)
    if not n:
        return {"n": 0, "hit@1": 0.0, "hit@3": 0.0, "hit@5": 0.0, "mrr": 0.0, "size": 0.0}
    def hit(limit):
        return sum(1 for r in rows if r["rank"] is not None and r["rank"] <= limit) / n
    return {"n": n, "hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "mrr": sum(r["rr"] for r in rows) / n,
            "size": sum(r.get("size", 0) for r in rows) / n}


def run_questions(conn, embedder, doc_id: str, questions: list, mode: str) -> list:
    from retrieval.search import find_definition, hybrid_search
    rows = []
    for q in questions:
        if mode == "hybrid+article":
            hits = hybrid_search(conn, embedder, q["text"], k=3, mode="hybrid", doc_id=doc_id, expand_article=1)
        elif mode == "lookup":
            hits = find_definition(conn, doc_id, q["name"]) if q["kind"] == "definition" else []
            if not hits:
                hits = hybrid_search(conn, embedder, q["text"], k=TOP_K, mode="hybrid", doc_id=doc_id)
        else:
            hits = hybrid_search(conn, embedder, q["text"], k=TOP_K, mode=mode, doc_id=doc_id)
        ids = [h.chunk_id for h in hits]
        rank = next((i for i, c in enumerate(ids, start=1) if c in q["gold"]), None)
        rows.append({**q, "rr": reciprocal_rank(ids, q["gold"]), "rank": rank, "top": hits[:1], "size": len(ids)})
    return rows


def main() -> int:
    from retrieval.db import connect
    from retrieval.embedder import get_embedder

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--label", action="append", type=Path, help="label file (default: all in eval/labeled)")
    parser.add_argument("--embedder", default="bge-small")
    parser.add_argument("--show-misses", action="store_true", help="list every question whose right chunk is not in the top 5")
    args = parser.parse_args()

    labels = args.label or sorted((REPO_ROOT / "eval" / "labeled").glob("*.json"))
    embedder = get_embedder(args.embedder)
    all_rows = {mode: [] for mode in MODES}
    with connect() as conn:
        for path in labels:
            doc_id = path.stem
            if not conn.execute("SELECT 1 FROM documents WHERE doc_id = %s", (doc_id,)).fetchone():
                print(f"{doc_id}: not loaded, skipped (python -m retrieval.load --slug {doc_id})")
                continue
            label = json.loads(path.read_text(encoding="utf-8"))
            chunks = [dict(chunk_id=r[0], kind=r[1], path=r[2], text=r[3]) for r in conn.execute(
                "SELECT chunk_id, kind, path, text FROM chunks WHERE doc_id = %s", (doc_id,)).fetchall()]
            questions, problems = build_questions(label, chunks)
            print(f"\n{doc_id}: {len(questions)} questions")
            for problem in problems:
                print(f"  note: {problem}")
            for mode in MODES:
                rows = run_questions(conn, embedder, doc_id, questions, mode)
                all_rows[mode].extend(rows)
                if args.show_misses and mode in ("hybrid", "lookup", "hybrid+article"):
                    for r in rows:
                        if r["rank"] is None or r["rank"] > 5:
                            top = r["top"][0].path if r["top"] else "nothing"
                            print(f"  miss [{mode}] {r['kind']} '{r['name']}' query={r['text']!r} rank={r['rank']} top1={top}")
                            print(f"       right answer is in: {' | '.join(r['gold_paths'])}")

    print("\n=== overall ===")
    print(f"{'mode':15} {'n':>4} {'hit@1':>7} {'hit@3':>7} {'hit@5':>7} {'MRR':>7} {'chunks returned':>16}")
    for mode in MODES:
        s = summarize(all_rows[mode])
        print(f"{mode:15} {s['n']:>4} {s['hit@1']:>7.2f} {s['hit@3']:>7.2f} {s['hit@5']:>7.2f} {s['mrr']:>7.2f} {s['size']:>16.1f}")
    for kind in ("covenant", "definition"):
        print(f"\n--- {kind} questions ---")
        for mode in MODES:
            s = summarize([r for r in all_rows[mode] if r["kind"] == kind])
            print(f"{mode:15} {s['n']:>4} {s['hit@1']:>7.2f} {s['hit@3']:>7.2f} {s['hit@5']:>7.2f} {s['mrr']:>7.2f} {s['size']:>16.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
