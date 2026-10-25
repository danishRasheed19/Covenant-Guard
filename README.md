# CovenantGuard

Extracts financial covenants from public SEC credit agreements into a strict schema, computes compliance from SEC XBRL data with a deterministic engine (the LLM never does arithmetic), cites the exact clause behind every number, and monitors for new filings.

**Status:** Phase 1 (ingestion and retrieval) is done. Phase 2 (LLM extraction) is next. See `docs/PLAN.md`.

## How it works

```
SEC exhibit (HTML)
   -> ingestion:  download, parse, chunk by section / definition        (Phase 1a, done)
   -> retrieval:  Postgres + pgvector, hybrid search, parent chunks     (Phase 1b, done)
   -> extraction: LLM fills the Covenant schema, quotes verified        (Phase 2, next)
   -> engine:     deterministic compliance from XBRL company facts      (Phase 3)
   -> monitoring: new filings re-run the check                          (later)
```

Design rules:
- **The LLM never computes.** It only reads clauses and fills the schema. Ratios, thresholds and breach flags come from code.
- **Every extracted value carries a citation.** A quote must appear in the source text (whitespace and curly-quote tolerant) or it is rejected.
- **Unknown stays unknown.** Ambiguous clauses go into an `ambiguities` field, and an approximate input can never produce a hard breach.
- **Hand-labeled ground truth.** Each labeled agreement in `eval/labeled/` is the answer key for both retrieval and (later) extraction.

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
pytest
python -m schema.covenant       # prints the Covenant JSON schema
```

Put your real name and email in `.env` (`SEC_USER_AGENT=Your Name your@email.com`); the SEC requires it.

## Phase 1a: download, parse, chunk

```
# run on a labeled agreement (downloads its exhibit_url, parses, chunks, checks the label)
python -m ingestion.run_ingest --label eval/labeled/ashland_2022_ar_credit_agreement.json

# or parse a page you saved from your browser (no network needed)
python -m ingestion.run_ingest --html-file saved_page.html --slug my_agreement
```

Outputs go to `data/processed/` (git-ignored): `<slug>.txt` and `<slug>.chunks.jsonl`.

The chunker handles real-world formatting: `Section 9.15`, `SECTION 9.15`, glued titles, two-line headings, tables of contents and struck-through redlines.

## Phase 1b: retrieval

Postgres 16 with the pgvector extension runs in Docker. Documents are cut into small overlapping windows that are embedded and indexed. A search returns the whole parent chunk (a full section or definition) so the extractor never sees a fragment.

```
docker compose up -d                                    # start the database
python -m retrieval.load --all                          # embed and load every data/processed/*.chunks.jsonl
python -m retrieval.load --slug mec_2026_third_amendment   # or just one (loading again replaces it)

python -m retrieval.search "minimum interest coverage ratio" --doc mec_2026_third_amendment
python -m retrieval.search "Consolidated EBITDA" --doc mec_2026_third_amendment --define   # exact term lookup
python -m retrieval.evaluate --show-misses              # measure retrieval against the labels
```

Settings: the connection string defaults to the docker-compose database. If another Postgres already uses port 5432 on your machine (common on Windows), map the container to 5433 in `docker-compose.yml` and set `DATABASE_URL=postgresql://covenantguard:covenantguard@localhost:5433/covenantguard` in `.env`. The embedding model (`BAAI/bge-small-en-v1.5`, 384 dimensions, runs locally, no API key) downloads on first use. Data persists in the Docker volume between sessions.

How search works:
1. **Vector search** finds windows by meaning.
2. **Keyword search** (Postgres full text, exact-phrase matches first) finds windows by wording.
3. **Heading match** boosts chunks whose defined term or section title resembles the query ("Financial Covenants" finds the section with that title).
4. **Reciprocal Rank Fusion** merges the rankings; a parent scores as its best window.
5. `find_definition` gives an exact lookup of a defined term when the name is known.

### Retrieval results

Measured on 60 questions across 5 hand-labeled agreements: 22 "where is this covenant?" questions and 38 "what is this defined term?" questions. A question counts as answered when the chunk containing the labeled citation quote or definition is in the top N results. Questions never reuse the label text.

| Mode | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| Keyword only | 0.35 | 0.77 | 0.87 | 0.55 |
| Vector only | 0.78 | 0.87 | 0.90 | 0.83 |
| Hybrid, no heading match | 0.70 | 0.88 | 0.92 | 0.81 |
| **Hybrid with heading match** | **0.82** | **0.95** | **0.95** | **0.88** |

By question type (hit@1 / hit@3):

| | Covenants (22) | Definitions (38) |
|---|---|---|
| Vector only | 0.45 / 0.64 | 0.97 / 1.00 |
| Hybrid, no heading match | 0.59 / 0.73 | 0.76 / 0.97 |
| Hybrid with heading match | 0.59 / 0.86 | 0.95 / 1.00 |
| Exact term lookup | n/a | 1.00 / 1.00 |

What the numbers say:
- Combining vector and keyword search with a heading match beats either alone, and the heading match is what made hybrid pay off (0.88 to 0.95 hit@3 overall).
- Defined terms are best found by exact lookup, not search.
- The remaining covenant misses are generic clause-style queries whose wording does not overlap the section title. Phase 2 will send a topic query and a "financial covenants" query and merge the results.

Caveats: 60 questions on 5 agreements is a small sample; the labels were written by the project author; the keyword and vector parameters were not tuned beyond the changes above, to avoid overfitting. Treat the figures as a baseline to re-measure as agreements are added.

## Repository layout

```
schema/        Pydantic models: Covenant, Agreement, ComplianceResult
ingestion/     download, parse, chunk, text normalization
retrieval/     database, embedder, loader, search, evaluation
scripts/       validate_labels.py (checks label files against the schema and source text)
eval/labeled/  hand-labeled agreements (the ground truth)
tests/         pytest suite (database tests skip when Postgres is not running)
```

## Tests

```
pytest                     # everything; database tests skip if the container is down
pytest tests/test_retrieval_db.py -rs     # shows why a database test was skipped
```
