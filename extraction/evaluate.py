"""Score extracted covenants against the hand-labelled ones. Plain code, no LLM, no database.

    python -m extraction.evaluate                 # every slug that has both data/extracted/<slug>.json and eval/labeled/<slug>.json
    python -m extraction.evaluate --slug via_2026_credit_agreement
    python -m extraction.evaluate --json results.json

What is compared, per labelled covenant (matched to an extracted one of the same type, closest first value):
  fields      type, operator, unit, test frequency, measurement basis
  thresholds  the value in force on every TEST DATE (month-ends or quarter-ends) from the label's first date to 2032.
              Test dates, not step dates, because labels and the extractor differ in convention: a step may start on
              the first day of a quarter or on its last day, and both give the same threshold at the test date.
  quote       the verifier found the quote in the agreement and no problem was recorded
  terms       recall of the label's defined terms among (a) the terms the model named and (b) the final definitions
Extracted covenants matching no label count as extras (false positives).
"""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["covenant_type", "operator", "unit", "test_frequency", "measurement_basis"]


def _d(text):
    return date.fromisoformat(text) if text else None


def month_ends(start: date, end: date) -> list:
    out, year, month = [], start.year, start.month
    while True:
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        if last > end:
            return out
        if last >= start:
            out.append(last)
        year, month = nxt.year, nxt.month


def period_end_dates(frequency: str, start: date, end: date = date(2032, 12, 31)) -> list:
    if frequency == "monthly":
        return month_ends(start, end)
    return [d for d in month_ends(start, end) if d.month in (3, 6, 9, 12)]


def threshold_on(schedule: list, as_of: date):
    current = None
    for step in schedule:
        when = _d(step.get("effective_from"))
        if when is None or when <= as_of:
            current = step["value"]
        else:
            break
    return current


def threshold_agreement(label: dict, extracted: dict, start: date) -> tuple:
    dates = period_end_dates(label.get("test_frequency", "quarterly"), start)
    same = [d for d in dates if threshold_on(label["threshold_schedule"], d) == threshold_on(extracted["threshold_schedule"], d)]
    return len(same), len(dates)


def match_covenants(labels: list, extracted: list) -> tuple:
    """Pair each label with the unused extracted covenant of the same type whose first value is closest."""
    pairs, used = [], set()
    for lab in labels:
        options = [(abs(lab["threshold_schedule"][0]["value"] - e["threshold_schedule"][0]["value"]), i)
                   for i, e in enumerate(extracted) if i not in used and e["covenant_type"] == lab["covenant_type"]]
        if options:
            _, index = min(options)
            used.add(index)
            pairs.append((lab, extracted[index]))
        else:
            pairs.append((lab, None))
    extras = [e for i, e in enumerate(extracted) if i not in used]
    return pairs, extras


def _terms(items) -> set:
    return {str(t).strip().lower() for t in items}


def evaluate_agreement(label_doc: dict, extracted_doc: dict) -> dict:
    start_default = _d(label_doc.get("agreement_date")) or date(2020, 1, 1)
    pairs, extras = match_covenants(label_doc["covenants"], extracted_doc["covenants"])
    rows = []
    for lab, ext in pairs:
        row = {"label_id": lab["covenant_id"], "found": ext is not None}
        if ext is not None:
            row["fields_ok"] = {f: lab.get(f) == ext.get(f) for f in FIELDS}
            first = _d(lab["threshold_schedule"][0].get("effective_from")) or start_default
            row["threshold_ok"], row["threshold_total"] = threshold_agreement(lab, ext, first)
            row["quote_ok"] = not any("quote" in p for p in ext.get("verification", {}).get("problems", []))
            wanted = _terms(d["term"] for d in lab.get("definitions", []))
            named = _terms(ext.get("defined_terms_used", []))
            final = _terms(d["term"] for d in ext.get("definitions", []))
            row["terms_wanted"] = len(wanted)
            row["terms_named"] = len(wanted & named)
            row["terms_final"] = len(wanted & final)
            row["terms_missing_final"] = sorted(wanted - final)
            row["extra_final_terms"] = sorted(final - wanted)
            row["status"] = ext.get("review_status")
        rows.append(row)
    return {"rows": rows, "extras": [e["covenant_id"] for e in extras]}


def summarise(results: dict) -> dict:
    total = {"labelled": 0, "found": 0, "extras": 0, "fields_ok": 0, "fields_total": 0, "thr_ok": 0, "thr_total": 0,
             "quotes_ok": 0, "terms_wanted": 0, "terms_named": 0, "terms_final": 0, "auto_verified": 0, "needs_review": 0, "rejected": 0}
    for res in results.values():
        total["extras"] += len(res["extras"])
        for row in res["rows"]:
            total["labelled"] += 1
            if not row["found"]:
                continue
            total["found"] += 1
            total["fields_ok"] += sum(row["fields_ok"].values())
            total["fields_total"] += len(row["fields_ok"])
            total["thr_ok"] += row["threshold_ok"]
            total["thr_total"] += row["threshold_total"]
            total["quotes_ok"] += int(row["quote_ok"])
            for key in ("terms_wanted", "terms_named", "terms_final"):
                total[key] += row[key]
            if row["status"] in total:
                total[row["status"]] += 1
    return total


def print_report(results: dict) -> None:
    for slug, res in results.items():
        print(f"\n{slug}")
        for row in res["rows"]:
            if not row["found"]:
                print(f"  {row['label_id']}: NOT FOUND")
                continue
            bad = [f for f, ok in row["fields_ok"].items() if not ok]
            print(f"  {row['label_id']}: fields {'ok' if not bad else 'DIFFER ' + ','.join(bad)} | thresholds {row['threshold_ok']}/{row['threshold_total']} test dates"
                  f" | quote {'ok' if row['quote_ok'] else 'FAILED'} | terms named {row['terms_named']}/{row['terms_wanted']}, final {row['terms_final']}/{row['terms_wanted']}"
                  f" | {row['status']}")
            if row["terms_missing_final"]:
                print(f"      missing terms: {', '.join(row['terms_missing_final'])}")
            if row["extra_final_terms"]:
                print(f"      extra terms (not in label): {', '.join(row['extra_final_terms'])}")
        if res["extras"]:
            print(f"  EXTRA covenants (not in label): {', '.join(res['extras'])}")
    t = summarise(results)
    pct = lambda a, b: f"{a}/{b} ({a / b:.0%})" if b else "n/a"
    print("\nTOTAL")
    print(f"  covenants found      {pct(t['found'], t['labelled'])}   extras (false positives): {t['extras']}")
    print(f"  field agreement      {pct(t['fields_ok'], t['fields_total'])}")
    print(f"  threshold agreement  {pct(t['thr_ok'], t['thr_total'])} test dates")
    print(f"  quotes verified      {pct(t['quotes_ok'], t['found'])}")
    print(f"  terms named by model {pct(t['terms_named'], t['terms_wanted'])}")
    print(f"  terms after lookup   {pct(t['terms_final'], t['terms_wanted'])}")
    print(f"  review status        auto_verified {t['auto_verified']}, needs_review {t['needs_review']}, rejected {t['rejected']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--slug", action="append")
    parser.add_argument("--extracted-dir", default=str(REPO_ROOT / "data" / "extracted"))
    parser.add_argument("--labeled-dir", default=str(REPO_ROOT / "eval" / "labeled"))
    parser.add_argument("--json", help="also write the results to this file")
    args = parser.parse_args()
    ex_dir, lab_dir = Path(args.extracted_dir), Path(args.labeled_dir)
    slugs = args.slug or sorted(p.stem for p in ex_dir.glob("*.json") if (lab_dir / p.name).exists() and not p.stem.startswith("_"))
    if not slugs:
        print("No slug has both an extracted and a labeled file.")
        return 2
    results = {}
    for slug in slugs:
        results[slug] = evaluate_agreement(json.loads((lab_dir / f"{slug}.json").read_text(encoding="utf-8")),
                                           json.loads((ex_dir / f"{slug}.json").read_text(encoding="utf-8")))
    print_report(results)
    if args.json:
        Path(args.json).write_text(json.dumps({"agreements": results, "total": summarise(results)}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
