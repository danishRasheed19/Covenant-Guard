"""Collect the passages the LLM will read. Pure logic plus one database adapter.

`gather_evidence` runs several retrieval queries, takes results round-robin (so each kind of covenant
and the generic "financial covenants" query all get a turn), removes duplicates and stops at a size
limit. It receives a `retrieve(query, k)` function, so tests can use a fake and production uses the
Postgres retrieval from Phase 1b.
"""

from dataclasses import dataclass

QUERIES = [
    "financial covenants",
    "maximum total leverage ratio financial covenant",
    "minimum interest coverage ratio financial covenant",
    "minimum liquidity financial covenant",
]


@dataclass
class Evidence:
    chunk_id: str
    path: str
    text: str
    section_number: str = ""
    kind: str = "section"


def gather_evidence(retrieve, *, queries=QUERIES, per_query: int = 3, max_chunks: int = 8,
                    max_chars: int = 60000, expand=None) -> list:
    """`retrieve(query, k)` returns Evidence objects best-first. `expand(evidence_list)` may add the other
    parts of a long section (applied only to the best hit of each query). "[Reserved]" sections are dropped. The first chunk is always kept; later ones stop when max_chars is reached."""
    results = [retrieve(q, per_query) for q in queries]
    picked, seen = [], set()
    for rank in range(per_query):
        for result in results:
            if rank < len(result) and result[rank].chunk_id not in seen:
                seen.add(result[rank].chunk_id)
                picked.append(result[rank])
    picked = [item for item in picked if "reserved" not in item.path.lower()][:max_chunks]
    if expand:
        # Only the best hit of each query is expanded to all parts of its section. Expanding every hit pulled in
        # whole unrelated sections (one agreement's Section 7.01 became 8 parts and 57,000 characters).
        best = {result[0].chunk_id for result in results if result}
        expanded = []
        for item in picked:
            expanded.extend(expand([item]) if item.chunk_id in best else [item])
        picked = [x for i, x in enumerate(expanded) if x.chunk_id not in {y.chunk_id for y in expanded[:i]}]
    kept, total = [], 0
    for item in picked:
        if kept and total + len(item.text) > max_chars:
            continue
        kept.append(item)
        total += len(item.text)
    return kept


def db_retriever(conn, embedder, doc_id: str):
    from retrieval.search import hybrid_search

    def retrieve(query: str, k: int) -> list:
        hits = hybrid_search(conn, embedder, query, k=k, mode="hybrid", doc_id=doc_id)
        return [Evidence(h.chunk_id, h.path, h.text, h.section_number, h.kind) for h in hits]
    return retrieve


def db_expander(conn, doc_id: str):
    """Replace each section hit by ALL parts of that section (long sections are stored as several chunks)."""
    def expand(items: list) -> list:
        out, seen = [], set()
        for item in items:
            group = [item]
            if item.kind == "section" and item.section_number:
                rows = conn.execute(
                    "SELECT chunk_id, path, text, section_number, kind FROM chunks "
                    "WHERE doc_id = %s AND kind = 'section' AND section_number = %s ORDER BY part",
                    (doc_id, item.section_number)).fetchall()
                group = [Evidence(*r) for r in rows] or [item]
            for member in group:
                if member.chunk_id not in seen:
                    seen.add(member.chunk_id)
                    out.append(member)
        return out
    return expand
