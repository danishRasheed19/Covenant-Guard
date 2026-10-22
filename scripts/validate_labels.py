#!/usr/bin/env python3
"""Validate hand-labeled agreement files against the schema.

Usage (from the repo root):
    python scripts/validate_labels.py
    python scripts/validate_labels.py eval/labeled/some_file.json
    python scripts/validate_labels.py eval/labeled/some_file.json --source agreement.txt

With no file arguments, every .json file in eval/labeled/ is checked.

--source (optional) is a plain-text copy of the agreement. If given, every citation
quote and every definition must appear in it (tolerant of whitespace and curly quotes).

Beyond the schema, each file is checked for internal consistency:
* the same defined term must have the same definition text and has_custom_adjustments flag
  in every covenant of the agreement (notes that differ are only a warning);
* filing_date may not be earlier than agreement_date;
* no working notes left behind ("fill script", "when you paste", "TODO", "TBD").
These catch copy slips; whether a label matches the contract still needs a human read.

Exit code is 0 if everything is clean, 1 otherwise.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from schema import Agreement, normalize_text  # noqa: E402


def find_placeholders(obj, path=""):
    """Yield the location of any string still containing a FILL_IN / PASTE marker."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from find_placeholders(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from find_placeholders(value, f"{path}[{i}]")
    elif isinstance(obj, str) and ("FILL_IN" in obj or "PASTE" in obj):
        yield path


# Phrases that only make sense while a label is being drafted.
WORKING_NOTES = re.compile(r"fill script|when you paste|\bTODO\b|\bTBD\b", re.IGNORECASE)


def find_working_notes(obj, path=""):
    """Yield (location, phrase) for strings that still contain drafting notes."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from find_working_notes(value, f"{path}.{key}" if path else key)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from find_working_notes(value, f"{path}[{i}]")
    elif isinstance(obj, str):
        match = WORKING_NOTES.search(obj)
        if match:
            yield path, match.group(0)


def check_consistency(agreement):
    """Return (problems, warnings) for cross-field checks the schema cannot express."""
    problems, warnings = [], []
    if agreement.agreement_date and agreement.filing_date < agreement.agreement_date:
        problems.append(
            f"filing_date {agreement.filing_date} is before agreement_date {agreement.agreement_date}"
        )
    first_seen = {}   # normalized term -> (covenant_id, DefinedTerm)
    for covenant in agreement.covenants:
        for term in covenant.definitions:
            key = normalize_text(term.term)
            if key not in first_seen:
                first_seen[key] = (covenant.covenant_id, term)
                continue
            other_id, other = first_seen[key]
            where = f"'{term.term}' in {covenant.covenant_id} vs {other_id}"
            if normalize_text(term.definition) != normalize_text(other.definition):
                problems.append(f"{where}: definition text differs")
            if term.has_custom_adjustments != other.has_custom_adjustments:
                problems.append(f"{where}: has_custom_adjustments differs")
            if [normalize_text(n) for n in term.adjustment_notes] != [normalize_text(n) for n in other.adjustment_notes]:
                warnings.append(f"{where}: adjustment_notes differ")
    return problems, warnings


def check_file(path: Path, source_text):
    problems = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        return [f"invalid JSON at line {err.lineno}, column {err.colno}: {err.msg}"], []

    for location in find_placeholders(data):
        problems.append(f"placeholder still present at {location}")
    for location, phrase in find_working_notes(data):
        problems.append(f"working note '{phrase}' left at {location}")

    try:
        agreement = Agreement.model_validate(data)
    except ValidationError as err:
        for e in err.errors():
            loc = ".".join(str(part) for part in e["loc"])
            problems.append(f"schema: {loc}: {e['msg']}")
        return problems, []

    consistency_problems, warnings = check_consistency(agreement)
    problems.extend(consistency_problems)

    if source_text is not None:
        normalized_source = normalize_text(source_text)
        for covenant in agreement.covenants:
            if not covenant.citation.quote_in(source_text):
                problems.append(f"{covenant.covenant_id}: citation quote not found in source")
            for term in covenant.definitions:
                if normalize_text(term.definition) not in normalized_source:
                    problems.append(
                        f"{covenant.covenant_id}: definition of '{term.term}' not found in source"
                    )
    return problems, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--source", type=Path, help="plain-text copy of the agreement")
    args = parser.parse_args()

    files = args.files or sorted(Path("eval/labeled").glob("*.json"))
    if not files:
        print("No label files found.")
        return 1

    source_text = args.source.read_text(encoding="utf-8") if args.source else None

    failed = False
    for path in files:
        problems, warnings = check_file(path, source_text)
        if problems:
            failed = True
            print(f"\n{path}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"OK  {path}")
        for warning in warnings:
            print(f"  WARN {warning}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
