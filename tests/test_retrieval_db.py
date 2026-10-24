"""Needs the docker Postgres (with pgvector) running. Skipped otherwise."""
import psycopg
import pytest

from retrieval.db import connect, ensure_schema
from retrieval.embedder import HashEmbedder
from retrieval.load import load_document
from retrieval.search import hybrid_search


@pytest.fixture()
def conn():
    try:
        c = connect()
        c.execute("SELECT 1")
        ensure_schema(c, 384)
    except Exception as err:  # no server, or no pgvector
        pytest.skip(f"Postgres with pgvector not available: {err}")
    yield c
    c.execute("DELETE FROM documents WHERE doc_id LIKE 'test-%'")
    c.commit()
    c.close()


def chunk(doc, n, kind, number, title, text):
    return dict(chunk_id=f"{doc}:{n}", doc_id=doc, kind=kind, article="ARTICLE IX", section_number=number,
                section_title=title, term="", part=1, path=f"ARTICLE IX > Section {number} {title}", text=text)


def test_load_and_search(conn):
    emb = HashEmbedder()
    chunks = [
        chunk("test-a", 1, "section", "9.15", "Financial Covenants",
              "The Borrower shall not permit the Consolidated Interest Coverage Ratio to be less than 3.00 to 1.00."),
        chunk("test-a", 2, "section", "9.01", "Reporting", "The Borrower shall deliver annual audited financial statements."),
        chunk("test-a", 3, "definition", "", "", "Consolidated EBITDA means net income plus interest expense."),
    ]
    assert load_document(conn, chunks, emb) == (3, 3)
    for mode in ("hybrid", "fts", "vector"):
        hits = hybrid_search(conn, emb, "minimum interest coverage ratio", mode=mode, doc_id="test-a", k=2)
        assert hits[0].section_number == "9.15", mode
    only_defs = hybrid_search(conn, emb, "EBITDA", doc_id="test-a", kinds="definition")
    assert [h.kind for h in only_defs] == ["definition"]
    assert hybrid_search(conn, emb, "reporting", doc_id="test-a", section="9.01", mode="fts")[0].chunk_id == "test-a:2"
    load_document(conn, chunks[:1], emb)                       # reload replaces
    assert conn.execute("SELECT count(*) FROM chunks WHERE doc_id='test-a'").fetchone()[0] == 1


def test_wrong_embedder_refused(conn):
    load_document(conn, [chunk("test-b", 1, "section", "1.01", "X", "some text")], HashEmbedder(384))
    with pytest.raises(ValueError):
        hybrid_search(conn, HashEmbedder(128), "text", doc_id="test-b")
