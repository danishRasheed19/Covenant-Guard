"""Extract the covenants of one loaded agreement.

    python -m extraction.run --slug mec_2026_third_amendment

Needs: the document loaded into Postgres (python -m retrieval.load --slug ...), data/processed/<slug>.txt,
and the LLM settings in .env. Writes data/extracted/<slug>.json and prints a short summary.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from extraction.definitions import db_lookup, resolve_definitions  # noqa: E402
from extraction.evidence import db_expander, db_retriever, gather_evidence  # noqa: E402
from extraction.extract import extract_covenants, outcome_to_dict  # noqa: E402
from llm import LLMError, get_client  # noqa: E402
from retrieval.db import connect  # noqa: E402
from retrieval.embedder import get_embedder  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--embedder", default="bge-small")
    parser.add_argument("--id-prefix", help="prefix for covenant ids (default: the slug)")
    args = parser.parse_args()

    text_path = REPO_ROOT / "data" / "processed" / f"{args.slug}.txt"
    if not text_path.exists():
        print(f"Missing {text_path} (run ingestion first).")
        return 2
    source_text = text_path.read_text(encoding="utf-8")
    try:
        client = get_client()
        with connect() as conn:
            evidence = gather_evidence(db_retriever(conn, get_embedder(args.embedder), args.slug),
                                       expand=db_expander(conn, args.slug))
            print(f"evidence: {len(evidence)} chunks, {sum(len(e.text) for e in evidence):,} characters")
            for e in evidence:
                print(f"  {e.chunk_id}  {e.path}")
            result = extract_covenants(client, evidence, id_prefix=args.id_prefix or args.slug, source_text=source_text)
            lookup, known_terms = db_lookup(conn, args.slug)
            for o in result.outcomes:      # definitions come from exact lookup in the agreement, never from the model
                o.covenant = o.covenant.model_copy(update={"definitions": resolve_definitions(o.defined_terms_used, lookup, known_terms)})
    except LLMError as err:
        print(f"LLM call failed: {err}")
        return 1

    out_dir = REPO_ROOT / "data" / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {"slug": args.slug, "model": client.model, "evidence": result.evidence_ids,
           "covenants": [outcome_to_dict(o) for o in result.outcomes], "unconverted_errors": result.errors}
    (out_dir / f"{args.slug}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nmodel {client.model}: {result.api_calls} API call(s), {result.cached_calls} from cache")
    for o in result.outcomes:
        c = o.covenant
        steps = ", ".join(f"{s.effective_from or 'start'}: {s.value:g}" for s in c.threshold_schedule)
        print(f"\n[{c.review_status.value}] {c.covenant_id}  {c.covenant_type.value} {c.operator.value}  ({c.citation.section})")
        print(f"  schedule: {steps}")
        print(f"  ratio: {c.numerator_term} / {c.denominator_term}" if c.unit.value == "ratio" else f"  measure: {c.measure_term}")
        for p in o.verification.problems:
            print(f"  PROBLEM: {p}")
        for w in o.verification.warnings:
            print(f"  warning: {w}")
    for e in result.errors:
        print(f"\nCould not convert: {e}")
    print(f"\nSaved data/extracted/{args.slug}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
