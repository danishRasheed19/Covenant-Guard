"""Load ingestion output (data/processed/<slug>.chunks.jsonl) into Postgres.

    python -m retrieval.load --slug mec_2026_third_amendment
    python -m retrieval.load --all
    python -m retrieval.load --all --embedder hash        (no model download; for a quick test)

Loading a document again replaces it. The text that is embedded and indexed for each window is
"<section path>\n<window text>", so a window from Section 9.15 carries its heading with it.
"""

import argparse
import json
from pathlib import Path

from retrieval.db import EMBEDDING_DIM, REPO_ROOT, connect, ensure_schema
from retrieval.embedder import get_embedder
from retrieval.windows import WINDOW_CHARS, WINDOW_OVERLAP, make_windows

PROCESSED_DIR = REPO_ROOT / "data" / "processed"
CHUNK_FIELDS = ("chunk_id", "doc_id", "kind", "article", "section_number", "section_title", "term", "part", "path", "text")


def vector_literal(vector: list) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


def read_chunks(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_children(chunk: dict, size: int = WINDOW_CHARS, overlap: int = WINDOW_OVERLAP) -> list:
    prefix = chunk["path"]
    return [f"{prefix}\n{window}" if prefix else window for window in make_windows(chunk["text"], size, overlap)]


def load_document(conn, chunks: list, embedder, *, size: int = WINDOW_CHARS, overlap: int = WINDOW_OVERLAP,
                  batch_size: int = 32) -> tuple:
    """Replace one document in the database. Returns (number of chunks, number of windows)."""
    doc_ids = {c["doc_id"] for c in chunks}
    if len(doc_ids) != 1:
        raise ValueError(f"expected chunks from exactly one document, got {sorted(doc_ids)}")
    doc_id = doc_ids.pop()

    children = [(c["chunk_id"], i, text) for c in chunks for i, text in enumerate(build_children(c, size, overlap))]
    vectors = []
    for start in range(0, len(children), batch_size):
        vectors.extend(embedder.embed_documents([text for _, _, text in children[start:start + batch_size]]))

    chunk_rows = [tuple(c[field] for field in CHUNK_FIELDS) for c in chunks]
    child_rows = [(chunk_id, ordinal, text, vector_literal(vector))
                  for (chunk_id, ordinal, text), vector in zip(children, vectors)]
    with conn.transaction():
        conn.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))
        conn.execute(
            "INSERT INTO documents (doc_id, n_chunks, embedding_model, embedding_dim) VALUES (%s, %s, %s, %s)",
            (doc_id, len(chunks), embedder.name, embedder.dim),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunks (chunk_id, doc_id, kind, article, section_number, section_title, term, part, path, text) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", chunk_rows)
            cur.executemany(
                "INSERT INTO child_chunks (chunk_id, ordinal, text, embedding) VALUES (%s, %s, %s, %s::vector)", child_rows)
    conn.commit()
    return len(chunks), len(children)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--slug", action="append", default=[], help="document slug (repeatable)")
    parser.add_argument("--all", action="store_true", help="load every data/processed/*.chunks.jsonl")
    parser.add_argument("--embedder", default="bge-small", help="bge-small (default) or hash")
    args = parser.parse_args()

    paths = sorted(PROCESSED_DIR.glob("*.chunks.jsonl")) if args.all else [PROCESSED_DIR / f"{s}.chunks.jsonl" for s in args.slug]
    if not paths:
        print("Give --slug NAME or --all.")
        return 2
    missing = [p for p in paths if not p.exists()]
    if missing:
        print("Missing (run ingestion first): " + ", ".join(str(p) for p in missing))
        return 2

    embedder = get_embedder(args.embedder)
    with connect() as conn:
        ensure_schema(conn, EMBEDDING_DIM)
        for path in paths:
            n_chunks, n_children = load_document(conn, read_chunks(path), embedder)
            print(f"{path.name.removesuffix('.chunks.jsonl')}: {n_chunks} chunks, {n_children} windows ({embedder.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
