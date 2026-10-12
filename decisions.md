# Decision log

Short entries: what we chose, why, and what we gave up. Add a new entry whenever you change course.

## 2026-10-12: Project choice
- **Chose:** CovenantGuard (covenant extraction + compliance monitoring) over FinSight, AuditTrail, TermSheet.
- **Why:** objective eval (hand-labeled covenants, ratios checkable against 10-K disclosures), clear product shape, strong interview story.
- **Cost:** crowded commercial space (Lumonic, Hebbia, V7, Allvue, etc.), so we differentiate on being open, measured, and auditable, not on novelty.

## 2026-10-12: Scope (V1)
- **Chose:** 8-10 public non-financial companies; max total leverage, min interest coverage, min liquidity; clean HTML exhibits only.
- **Out of scope:** custom EBITDA add-backs (flagged and quantified instead), negative covenant baskets, syndicated multi-borrower structures, PDF/scanned exhibits.

## 2026-10-12: The LLM never does arithmetic
- **Chose:** LLM turns text into a typed `Covenant`; a deterministic engine computes ratios from XBRL.
- **Why:** keeps the LLM out of the parts that must be exactly right; makes every number traceable.

## 2026-10-12: Strict schema
- **Chose:** Pydantic with `extra="forbid"`, type/operator/unit consistency checks, and verbatim-quote verification.
- **Why:** malformed or hallucinated LLM output should fail loudly, not pass silently.

## 2026-10-12: $0 budget
- **Chose:** free-tier cloud LLM API (Gemini via AI Studio is the first candidate), no billing enabled; cache every LLM call on disk; provider-swappable client.
- **Why:** old laptop rules out useful local models; caching makes evals and reruns free.
- **To verify:** actual free-tier limits in AI Studio, and whether structured/JSON output works on the chosen model.

## 2026-10-12: Storage and retrieval: Postgres + pgvector
- **Chose:** Postgres with the pgvector extension. Hybrid retrieval = Postgres full-text search (keyword) + pgvector (embeddings), filtered by section metadata.
- **Considered:** SQLite + BM25 only. Reversed because pgvector is free and open source (cost was never the issue), Postgres + pgvector is a stronger signal for AI engineering roles, and one database handles keyword + vector search.
- **Cost:** more setup than SQLite, and embeddings are needed from the start (small local model, CPU-friendly; confirm it runs acceptably on the dev laptop).
- **Note:** Postgres full-text ranking is not true BM25. If keyword ranking quality matters, compare against `rank_bm25` in Python on this small corpus and report it in the eval.
- **Setup options:** native Postgres install, Docker (`docker-compose.yml` provided, untested), or a hosted free tier (verify current terms). Pick whichever the laptop handles best.

## 2026-10-12: Deployment
- **Chose:** run locally; README, screenshots, and a demo video instead of a cloud deploy.
- **Revisit if:** there's a budget or a free tier that's worth the setup time.
