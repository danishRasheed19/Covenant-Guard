# CovenantGuard: Project Plan

Assumes ~10 hrs/week for 8-10 weeks. Every phase ends in something demoable.

## Scope (locked for V1)

| Decision | V1 choice |
|---|---|
| Companies | 8-10 US public companies (non-financial) |
| Covenant types | Max total leverage, min interest coverage, min liquidity |
| Financial data | SEC XBRL "company facts" API |
| Ground truth | Covenant levels + actual results disclosed in 10-K/10-Q debt notes |
| Out of scope | Custom EBITDA add-backs, negative covenant baskets, multi-borrower syndicated structures, scanned/PDF exhibits |

**Core principle:** the LLM reads text and produces structure. Plain code does all arithmetic.

## Stack
Python, FastAPI, LangGraph, Postgres + pgvector (hybrid: full-text + vector), small local embedding model, Streamlit, Langfuse (optional), Docker (optional). $0 budget: free-tier LLM API + on-disk caching. See `docs/decisions.md`.

## Phase 0: Setup (Week 1)
- [ ] Repo, environment, decision log (`docs/decisions.md`)
- [ ] Fill `data/company_tracker.csv` (see `docs/company_selection.md`)
- [ ] Set `SEC_USER_AGENT` env var (name + email, required by SEC)

## Phase 1: Data pipeline + ground truth (Weeks 1-2)
- [ ] EDGAR client (respect rate limits)
- [ ] HTML exhibit -> clean text, preserving section numbers and headings
- [ ] Structure-aware chunking: split by Article/Section, keep section path as metadata
- [ ] Set up Postgres + pgvector (see `docker-compose.yml` or a native install)
- [ ] Index Article I (definitions) separately from the covenant sections
- [ ] **Hand-label covenants** into `eval/labeled/*.json` using `schema.Agreement` (2-3 agreements/week, start now)

## Phase 2: Extraction (Weeks 2-4)
- [ ] Retrieve financial-covenant section (hybrid search + section metadata)
- [ ] Extraction agent -> `Covenant` objects via structured output
- [ ] Definition resolver: fetch each referenced defined term from Article I
- [ ] Validators: schema check + `citation.quote_in(source_text)`
- [ ] First accuracy numbers vs labels (expect them to be ugly)

## Phase 3: Calculation engine (Weeks 3-5)
- [ ] XBRL client (company facts)
- [ ] Transparent defined-term -> XBRL-tag mapping, per company, every assumption written down
- [ ] `engine/calculator.py`: covenant + financials -> `ComplianceResult` (no LLM)
- [ ] Unit tests hand-checked against companies' reported ratios

## Phase 4: Orchestration (Weeks 5-6)
```
Ingest -> Extraction -> Definition resolver -> Verifier
       -> Calculation (deterministic) -> Interpretation -> Report/alert
```
- [ ] LangGraph flow with persisted audit log (every step's input/output)
- [ ] Verifier: quote exists in source, numbers match clause, else -> human review
- [ ] Interpretation agent: explains ambiguity/assumptions instead of silently guessing

## Phase 5: Evaluation (Weeks 6-7)
Report metrics separately; a blended "accuracy" hides problems.

| Metric | Measured how |
|---|---|
| Extraction precision / recall | Extracted covenants vs hand labels |
| Threshold accuracy | Exact match on value, operator, step-downs |
| Citation accuracy | Cited clause actually contains the covenant |
| Calculation accuracy | Computed ratio vs ratio the company reported |
| Verifier effectiveness | Seeded errors caught vs missed |

- [ ] Ablations: with/without verifier; with/without definition resolution
- [ ] Error analysis: quantify gap caused by custom add-backs
- [ ] `eval/run_eval.py` runs everything with one command

## Phase 6: Product layer (Weeks 7-9)
- [ ] FastAPI: upload/select agreement, run extraction, fetch status; API-key auth + org field
- [ ] Monitoring loop: poll EDGAR for new 10-Q/10-K, recompute, alert on low headroom (default 15%) or breach
- [ ] Streamlit dashboard: portfolio table, covenant drill-down (clause, inputs, verification), review queue
- [ ] Token/cost tracking per agreement

## Phase 7: Packaging (Weeks 9-10)
- [ ] README: pitch, architecture diagram, eval table, honest limitations, "what a commercial version needs"
- [ ] 2-3 min demo video
- [ ] Blog post: "the LLM never does arithmetic" + the definitions problem
- [ ] Docker Compose; optional free-tier deploy
- [ ] Competitive landscape section (be honest about incumbents; differentiate on open, measured, auditable)

## Repo layout
```
covenantguard/
├── ingestion/     edgar_client, parser, chunker, indexer
├── schema/        covenant.py  (done)
├── extraction/    extractor, definition_resolver
├── financials/    xbrl_client, metric_mapping
├── engine/        calculator.py + tests
├── agents/        verifier, interpreter
├── orchestration/ graph.py
├── monitoring/    edgar_poller, alerting
├── eval/          labeled/, run_eval.py
├── api/           FastAPI
├── app/           Streamlit
├── scripts/       xbrl_tag_check.py
├── data/          company_tracker.csv
└── docs/          PLAN.md, company_selection.md, decisions.md
```

## Risks

| Risk | Mitigation |
|---|---|
| Hand-labeling drags | Label 2-3 agreements/week from Phase 1; cap V1 at 8-10 |
| Custom EBITDA breaks numbers | Flag via `requires_approximation`, quantify the gap, report it |
| Messy/scanned exhibits | Only pick companies with clean HTML exhibits |
| Scope creep | Park ideas in a V2 list |
| LLM cost | Cache extraction results; process each agreement once |

## Definition of done
- Eval table covering extraction, citation, and calculation metrics
- Monitoring loop that detects a new filing and updates status
- Dashboard demo + recorded walkthrough
- README stating limitations plainly

**Stretch:** amendment tracking (thresholds change), negative-covenant baskets, compliance-certificate parser.
