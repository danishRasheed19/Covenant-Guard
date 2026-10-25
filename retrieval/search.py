"""Hybrid search over the loaded chunks, returning parents.

    python -m retrieval.search "minimum interest coverage ratio" --doc mec_2026_third_amendment
    python -m retrieval.search "Consolidated EBITDA" --kind definition --k 3
    python -m retrieval.search "financial covenants" --section 9.15 --mode fts

How it works
  1. Vector search over the small windows (cosine distance) -> a ranked list of windows.
  2. Full-text search over the same windows (Postgres tsvector, words OR-ed) -> a second ranked list.
     Windows containing the query as an exact phrase (words adjacent, in order) rank above windows that
     only share some of the words, so "Consolidated EBITDA" finds that definition, not every definition.
  3. Reciprocal Rank Fusion: each window scores sum(1 / (60 + rank)) over the lists it appears in.
  4. A parent chunk scores the best of its windows; the top k parents are returned WHOLE, so a
     definition or section reaches the LLM complete.
  5. Heading match (hybrid mode only): chunks whose defined term or section title resembles the query
     (or, at half strength, whose article title does) get an extra RRF bonus. This is what makes
     "Financial Covenants" find the section titled "Financial Covenants".
Extras
  * expand_article=N adds the sibling sections of the top N hits' articles (appended after the hits),
    for when the right section sits next to the one that matched.
  * find_definition(conn, doc, term) is an exact lookup of a defined term (no ranking involved).
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

MODES = ("hybrid", "hybrid_plain", "vector", "fts")   # hybrid_plain = hybrid without the heading bonus
HEADING_MIN_SCORE = 0.3
HEADING_CANDIDATES = 10
MAX_SIBLINGS = 8
_HEADING_STOP = {"the", "of", "and", "a", "an", "to", "in", "for", "on", "or", "not", "be", "by", "is", "as", "at",
                 "with", "any", "that", "this", "such", "all", "no", "than", "article", "section"}


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
    heading_rank: Optional[int] = None
    expanded: bool = False          # True for sibling sections added by expand_article


def tsquery_from(text: str) -> str:
    """'minimum interest coverage' -> 'minimum | interest | coverage' (any word may match)."""
    tokens = []
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if token not in tokens:
            tokens.append(token)
    return " | ".join(tokens)


def heading_tokens(text: str) -> set:
    """Lowercase words of a heading, minus filler words, with a crude plural strip ('covenants' -> 'covenant')."""
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if token in _HEADING_STOP:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _overlap_score(query_tokens: set, heading: set) -> float:
    """coverage x (0.5 + 0.5 x precision): 1.0 for an exact match, lower when the heading has words the
    query lacks (coverage) or the query has many words the heading lacks (precision)."""
    if not query_tokens or not heading:
        return 0.0
    shared = len(query_tokens & heading)
    return (shared / len(heading)) * (0.5 + 0.5 * shared / len(query_tokens))


def heading_score(query_tokens: set, term: str, section_title: str, article: str) -> float:
    """How well a chunk's heading matches the query. The defined term / section title counts fully;
    the article title (roman numeral stripped) counts half."""
    own = _overlap_score(query_tokens, heading_tokens(term or section_title))   # a defined term, else the section title
    article_name = re.sub(r"^\s*article\s+[ivxlcdm\d]+\b", "", article, flags=re.IGNORECASE)
    return max(own, 0.5 * _overlap_score(query_tokens, heading_tokens(article_name)))


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
                  section: str = None, path_contains: str = None, candidates: int = 50, rrf_k: int = 60,
                  expand_article: int = 0) -> list:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    _check_embedding_model(conn, embedder, doc_id)
    conditions, params = _filters(doc_id, kinds, section, path_contains)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    lists, child_chunk = {}, {}
    if mode in ("hybrid", "hybrid_plain", "vector"):
        sql = ("SELECT c.child_id, c.chunk_id FROM child_chunks c JOIN chunks p ON p.chunk_id = c.chunk_id "
               f"{where} ORDER BY c.embedding <=> %s::vector LIMIT %s")
        rows = conn.execute(sql, params + [vector_literal(embedder.embed_query(query)), candidates]).fetchall()
        lists["vector"] = [r[0] for r in rows]
        child_chunk.update({r[0]: r[1] for r in rows})
    if mode in ("hybrid", "hybrid_plain", "fts"):
        tsq = tsquery_from(query)
        if tsq:
            text_conditions = conditions + ["c.tsv @@ q"]
            sql = ("SELECT c.child_id, c.chunk_id FROM child_chunks c JOIN chunks p ON p.chunk_id = c.chunk_id "
                   "CROSS JOIN to_tsquery('english', %s) q WHERE " + " AND ".join(text_conditions) +
                   " ORDER BY (c.tsv @@ phraseto_tsquery('english', %s)) DESC, ts_rank_cd(c.tsv, q) DESC LIMIT %s")
            rows = conn.execute(sql, [tsq] + params + [query, candidates]).fetchall()
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
    heading_rank = {}
    if mode == "hybrid":
        query_tokens = heading_tokens(query)
        rows = conn.execute(f"SELECT p.chunk_id, p.term, p.section_title, p.article FROM chunks p {where} ORDER BY p.chunk_id",
                            params).fetchall()
        scored = sorted(((heading_score(query_tokens, r[1], r[2], r[3]), r[0]) for r in rows), key=lambda x: -x[0])
        for rank, (score, chunk_id) in enumerate([x for x in scored if x[0] >= HEADING_MIN_SCORE][:HEADING_CANDIDATES], start=1):
            heading_rank[chunk_id] = rank
            parent_score[chunk_id] = parent_score.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    top = sorted(parent_score, key=lambda p: -parent_score[p])[:k]
    if not top:
        return []

    rows = conn.execute(
        "SELECT chunk_id, doc_id, kind, section_number, term, path, text FROM chunks WHERE chunk_id = ANY(%s)", (top,)
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    hits = [Hit(chunk_id=p, doc_id=by_id[p][1], kind=by_id[p][2], section_number=by_id[p][3], term=by_id[p][4],
                path=by_id[p][5], text=by_id[p][6], score=parent_score[p],
                vector_rank=best_rank.get(("vector", p)), text_rank=best_rank.get(("fts", p)),
                heading_rank=heading_rank.get(p)) for p in top]
    if expand_article > 0:
        hits = hits + _siblings(conn, hits[:expand_article], exclude={h.chunk_id for h in hits})
    return hits


def _siblings(conn, seeds: list, exclude: set) -> list:
    """Other sections in the same article as each seed hit (not already returned, 'Reserved' stubs skipped)."""
    out, seen = [], set(exclude)
    for seed in seeds:
        if seed.kind != "section":
            continue
        row = conn.execute("SELECT article FROM chunks WHERE chunk_id = %s", (seed.chunk_id,)).fetchone()
        if not row or not row[0]:
            continue
        rows = conn.execute(
            "SELECT chunk_id, doc_id, kind, section_number, term, path, text FROM chunks "
            "WHERE doc_id = %s AND article = %s AND kind = 'section' AND section_title NOT ILIKE '%%reserved%%' "
            "ORDER BY chunk_id", (seed.doc_id, row[0])).fetchall()
        for r in rows:
            if r[0] not in seen and len(out) < MAX_SIBLINGS:
                seen.add(r[0])
                out.append(Hit(chunk_id=r[0], doc_id=r[1], kind=r[2], section_number=r[3], term=r[4], path=r[5],
                               text=r[6], score=0.0, vector_rank=None, text_rank=None, expanded=True))
    return out


def find_definition(conn, doc_id: str, term: str) -> list:
    """Exact lookup of a defined term (case and spacing ignored). Returns its chunks (long definitions are
    split into parts), or [] if the document has no such term -- the caller can then fall back to search."""
    wanted = re.sub(r"\s+", " ", term.strip().strip("\"'\u201c\u201d").lower())
    rows = conn.execute(
        "SELECT chunk_id, doc_id, kind, section_number, term, path, text FROM chunks "
        "WHERE doc_id = %s AND kind = 'definition' AND term <> '' ORDER BY chunk_id", (doc_id,)).fetchall()
    return [Hit(chunk_id=r[0], doc_id=r[1], kind=r[2], section_number=r[3], term=r[4], path=r[5], text=r[6],
                score=1.0, vector_rank=None, text_rank=None)
            for r in rows if re.sub(r"\s+", " ", r[4].strip().lower()) == wanted]


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
    parser.add_argument("--expand", type=int, default=0, help="also return sibling sections of the top N hits' articles")
    parser.add_argument("--define", action="store_true", help="treat the query as a defined term: exact lookup (needs one --doc)")
    args = parser.parse_args()
    with connect() as conn:
        if args.define:
            if not args.doc or len(args.doc) != 1:
                parser.error("--define needs exactly one --doc")
            hits = find_definition(conn, args.doc[0], args.query)
        else:
            hits = hybrid_search(conn, get_embedder(args.embedder), args.query, k=args.k, mode=args.mode, doc_id=args.doc,
                                 kinds=args.kind, section=args.section, path_contains=args.path_contains,
                                 expand_article=args.expand)
    for rank, hit in enumerate(hits, start=1):
        tag = " [sibling]" if hit.expanded else ""
        print(f"{rank}. [{hit.kind}]{tag} {hit.doc_id}  {hit.path}  (score {hit.score:.4f}, vector#{hit.vector_rank}, text#{hit.text_rank}, heading#{hit.heading_rank})")
        print("   " + re.sub(r"\s+", " ", hit.text)[:160])
    if not hits:
        print("no results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
