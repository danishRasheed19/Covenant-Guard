"""Postgres connection and schema.

Three tables:
  documents     one row per loaded agreement (and which embedding model loaded it)
  chunks        the parents: one row per chunk from ingestion (section / definition / attachment / preamble)
  child_chunks  small overlapping windows of each chunk's text, with a vector and a full-text index

Search is exact (no ANN index): at a few thousand windows it is fast, and exact search keeps filtered
queries correct. Will add an HNSW index once the corpus grows.
"""

import os
from pathlib import Path

import psycopg

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DSN = "postgresql://covenantguard:covenantguard@localhost:5432/covenantguard" 
EMBEDDING_DIM = 384


def _dotenv_value(name: str):
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def get_dsn() -> str:
    """DATABASE_URL from the environment, else from .env, else the docker-compose default."""
    return os.environ.get("DATABASE_URL") or _dotenv_value("DATABASE_URL") or DEFAULT_DSN


def connect(dsn: str = None):
    return psycopg.connect(dsn or get_dsn())


def ensure_schema(conn, dim: int = EMBEDDING_DIM, *, create_extension: bool = True, vector_type: str = None) -> None:
    """Create the extension and tables if missing (safe to run repeatedly).

    `vector_type` exists only so tests can run against a database without pgvector.
    """
    vector_type = vector_type or f"vector({int(dim)})"
    if create_extension:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id          text PRIMARY KEY,
            n_chunks        integer NOT NULL,
            embedding_model text NOT NULL,
            embedding_dim   integer NOT NULL,
            loaded_at       timestamptz NOT NULL DEFAULT now()
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id       text PRIMARY KEY,
            doc_id         text NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
            kind           text NOT NULL,
            article        text NOT NULL,
            section_number text NOT NULL,
            section_title  text NOT NULL,
            term           text NOT NULL,
            part           integer NOT NULL,
            path           text NOT NULL,
            text           text NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_doc_section_idx ON chunks (doc_id, section_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_kind_idx ON chunks (kind)")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS child_chunks (
            child_id  bigserial PRIMARY KEY,
            chunk_id  text NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
            ordinal   integer NOT NULL,
            text      text NOT NULL,
            embedding {vector_type} NOT NULL,
            tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
            UNIQUE (chunk_id, ordinal)
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS child_chunks_tsv_idx ON child_chunks USING gin (tsv)")
    conn.commit()
