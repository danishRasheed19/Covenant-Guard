"""Hybrid search over the loaded chunks, returning parents.

    python -m retrieval.search "minimum interest coverage ratio" --doc mec_2026_third_amendment
    python -m retrieval.search "Consolidated EBITDA" --kind definition --k 3
    python -m retrieval.search "financial covenants" --section 9.15 --mode fts

How it works
  1. Vector search over the small windows (cosine distance) -> a ranked list of windows.
  2. Full-text search over the same windows (Postgres tsvector, words OR-ed) -> a second ranked list.
  3. Reciprocal Rank Fusion: each window scores sum(1 / (60 + rank)) over the lists it appears in.
  4. A parent chunk scores the best of its windows; the top k parents are returned WHOLE, so a
     definition or section reaches the LLM complete.
Filters (document, chunk kind, section number, text in the section path) apply to both searches.
`mode` can be hybrid, vector or fts, which is how the ablations are run.
"""

import argparse
import re
from dataclasses import dataclass
from typing import Optional

from retrieval.db import connect
from retrieval.embedder import get_embedder
from retrieval.load import vector_literal

MODES = ("hybrid", "vector", "fts")


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    kind: str
    section_number: str
    term: str
    path: str
    text: str
    score: float
    vector_rank: Optional[int]
    text_rank: Optional[int]


def tsquery_from(text: str) -> str:
    """'minimum interest coverage' -> 'minimum | interest | coverage' (any word may match)."""
    tokens = []
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if token not in tokens:
            tokens.append(token)
    return " | ".join(tokens)


def rrf(ranked_lists: list, k: int = 60) -> dict:
    """Reciprocal Rank Fusion. Each list is ids, best first. Returns {id: fused score}."""
    scores = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def _as_list(value):
    return None if value is None else ([value] if isinstance(value, str) else list(value))


def _filters(doc_id, kinds, section, path_contains) -> tuple:
    conditions, params = [], []
    if doc_id:
        conditions.append("p.doc_id = ANY(%s)")
        params.append(_as_list(doc_id))
    if kinds:
        conditions.append("p.kind = ANY(%s)")
        params.append(_as_list(kinds))
    if section:
        conditions.append("p.section_number = %s")
        params.append(section)
    if path_contains:
        conditions.append("p.path ILIKE %s")
        params.append(f"%{path_contains}%")
    return conditions, params


def _check_embedding_model(conn, embedder, doc_id) -> None:
    if doc_id:
        rows = conn.execute("SELECT DISTINCT embedding_model FROM documents WHERE doc_id = ANY(%s)", (_as_list(doc_id),)).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT embedding_model FROM documents").fetchall()
    models = {row[0] for row in rows}
    if models - {embedder.name}:
        raise ValueError(f"documents were loaded with {sorted(models)} but the query embedder is {embedder.name!r}; reload them")


def hybrid_search(conn, embedder, query: str, *, k: int = 5, mode: str = "hybrid", doc_id=None, kinds=None,
                  section: str = None, path_contains: str = None, candidates: int = 50, rrf_k: int = 60) -> list:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    _check_embedding_model(conn, embedder, doc_id)
    conditions, params = _filters(doc_id, kinds, section, path_contains)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    lists, child_chunk = {}, {}
    if mode in ("hybrid", "vector"):
        sql = ("SELECT c.child_id, c.chunk_id FROM child_chunks c JOIN chunks p ON p.chunk_id = c.chunk_id "
               f"{where} ORDER BY c.embedding <=> %s::vector LIMIT %s")
        rows = conn.execute(sql, params + [vector_literal(embedder.embed_query(query)), candidates]).fetchall()
        lists["vector"] = [r[0] for r in rows]
        child_chunk.update({r[0]: r[1] for r in rows})
    if mode in ("hybrid", "fts"):
        tsq = tsquery_from(query)
        if tsq:
            text_conditions = conditions + ["c.tsv @@ q"]
            sql = ("SELECT c.child_id, c.chunk_id FROM child_chunks c JOIN chunks p ON p.chunk_id = c.chunk_id "
                   "CROSS JOIN to_tsquery('english', %s) q WHERE " + " AND ".join(text_conditions) +
                   " ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT %s")
            rows = conn.execute(sql, [tsq] + params + [candidates]).fetchall()
            lists["fts"] = [r[0] for r in rows]
            child_chunk.update({r[0]: r[1] for r in rows})

    fused = rrf(list(lists.values()), rrf_k)
    parent_score, best_rank = {}, {}
    for name, ranked in lists.items():
        for rank, child in enumerate(ranked, start=1):
            parent = child_chunk[child]
            best_rank[(name, parent)] = min(rank, best_rank.get((name, parent), rank))
    for child, score in fused.items():
        parent = child_chunk[child]
        parent_score[parent] = max(score, parent_score.get(parent, 0.0))
    top = sorted(parent_score, key=lambda p: -parent_score[p])[:k]
    if not top:
        return []

    rows = conn.execute(
        "SELECT chunk_id, doc_id, kind, section_number, term, path, text FROM chunks WHERE chunk_id = ANY(%s)", (top,)
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    return [Hit(chunk_id=p, doc_id=by_id[p][1], kind=by_id[p][2], section_number=by_id[p][3], term=by_id[p][4],
                path=by_id[p][5], text=by_id[p][6], score=parent_score[p],
                vector_rank=best_rank.get(("vector", p)), text_rank=best_rank.get(("fts", p))) for p in top]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--doc", action="append", help="document slug (repeatable)")
    parser.add_argument("--kind", action="append", choices=["section", "definition", "attachment", "preamble"])
    parser.add_argument("--section", help="section number, e.g. 9.15")
    parser.add_argument("--path-contains", help="text that must appear in the section path")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--mode", choices=MODES, default="hybrid")
    parser.add_argument("--embedder", default="bge-small")
    args = parser.parse_args()
    with connect() as conn:
        hits = hybrid_search(conn, get_embedder(args.embedder), args.query, k=args.k, mode=args.mode, doc_id=args.doc,
                             kinds=args.kind, section=args.section, path_contains=args.path_contains)
    for rank, hit in enumerate(hits, start=1):
        print(f"{rank}. [{hit.kind}] {hit.doc_id}  {hit.path}  (score {hit.score:.4f}, vector#{hit.vector_rank}, text#{hit.text_rank})")
        print("   " + re.sub(r"\s+", " ", hit.text)[:160])
    if not hits:
        print("no results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
