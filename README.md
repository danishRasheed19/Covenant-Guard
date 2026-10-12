# CovenantGuard

Extracts financial covenants from public credit agreements, computes compliance from SEC XBRL data with a deterministic engine (no LLM math), and monitors for new filings.

Status: planning / Phase 0. See `docs/PLAN.md`.

```bash
pip install -r requirements.txt
pytest
python -m schema.covenant   # prints the Covenant JSON schema
```
