# CovenantGuard

Extracts financial covenants from public credit agreements, computes compliance from SEC XBRL data with a deterministic engine (no LLM math), and monitors for new filings.

Status: planning / Phase 0. See `docs/PLAN.md`.

```bash
pip install -r requirements.txt
pytest
python -m schema.covenant   # prints the Covenant JSON schema
```

## Phase 1a: download, parse, chunk

```bash
# 1. put your real name and email in .env (SEC_USER_AGENT=...)
# 2. run on a labeled agreement (downloads its exhibit_url, parses, chunks, checks the label)
python -m ingestion.run_ingest --label eval/labeled/ashland_2022_ar_credit_agreement.json

# or parse a page you saved from your browser (no network needed)
python -m ingestion.run_ingest --html-file saved_page.html --slug my_agreement
```

Outputs go to `data/processed/` (git-ignored): `<slug>.txt` and `<slug>.chunks.jsonl`.

