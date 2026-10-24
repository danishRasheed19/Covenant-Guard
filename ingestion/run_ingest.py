"""Download (or read) an agreement, parse it, chunk it, and optionally check it against a label file.

Run from the repo root:

    python -m ingestion.run_ingest --label eval/labeled/ashland_2022_ar_credit_agreement.json
    python -m ingestion.run_ingest --url https://www.sec.gov/Archives/edgar/data/... --slug my_agreement
    python -m ingestion.run_ingest --html-file saved_page.html --slug my_agreement   (no network needed)

Outputs (git-ignored):
    data/processed/<slug>.txt            the clean text
    data/processed/<slug>.chunks.jsonl   one chunk per line

With --label, it also checks that each labeled section number, defined term, pasted definition
and pasted quote can be found in the parsed text. Exit code is 1 if anything is missing.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from ingestion.chunker import chunk_document
from ingestion.edgar_client import REPO_ROOT, decode, fetch
from ingestion.parser import html_to_text
from ingestion.textnorm import contains, squash

PROCESSED_DIR = REPO_ROOT / "data" / "processed"


def _is_placeholder(value: str) -> bool:
    return value.startswith("PASTE") or value.startswith("FILL_IN")


def check_against_label(label: dict, text: str, chunks: list) -> list:
    """Return report lines; lines starting with MISS are problems."""
    report = []
    sections = {c.section_number for c in chunks if c.kind == "section"}
    terms = {squash(c.term) for c in chunks if c.kind == "definition"}

    for covenant in label.get("covenants", []):
        cid = covenant["covenant_id"]
        match = re.search(r"(\d+\.\d+)", covenant["citation"]["section"])
        if match:
            found = match.group(1) in sections
            report.append(f"{'OK  ' if found else 'MISS'} {cid}: section {match.group(1)} {'found' if found else 'not found as a section heading'}")
        quote = covenant["citation"]["quote"]
        if not _is_placeholder(quote):
            found = contains(text, quote)
            report.append(f"{'OK  ' if found else 'MISS'} {cid}: citation quote {'found' if found else 'not found in parsed text'}")
        for definition in covenant.get("definitions", []):
            name = definition["term"]
            if _is_placeholder(name):
                continue
            found = squash(name) in terms
            report.append(f"{'OK  ' if found else 'MISS'} {cid}: definition chunk for '{name}' {'found' if found else 'not found'}")
            body = definition["definition"]
            if not _is_placeholder(body):
                found = contains(text, body)
                report.append(f"{'OK  ' if found else 'MISS'} {cid}: pasted definition of '{name}' {'matches' if found else 'does not match the parsed text'}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--label", type=Path, help="label JSON; its exhibit_url is downloaded and checked")
    parser.add_argument("--url", help="SEC exhibit URL")
    parser.add_argument("--html-file", type=Path, help="a saved HTML file (no network needed)")
    parser.add_argument("--slug", help="name for the output files")
    parser.add_argument("--keep-struck", action="store_true", help="keep struck-through text")
    parser.add_argument("--no-cache", action="store_true", help="re-download even if cached")
    args = parser.parse_args()

    label = json.loads(args.label.read_text(encoding="utf-8")) if args.label else None
    if args.html_file:
        html = decode(args.html_file.read_bytes())
        slug = args.slug or args.html_file.stem
    else:
        url = args.url or (label or {}).get("exhibit_url")
        if not url:
            print("Give --label, --url, or --html-file.")
            return 2
        html = decode(fetch(url, use_cache=not args.no_cache))
        slug = args.slug or (args.label.stem if args.label else Path(url).stem)

    parsed = html_to_text(html, drop_struck=not args.keep_struck)
    chunks = chunk_document(parsed.text, slug)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / f"{slug}.txt").write_text(parsed.text, encoding="utf-8")
    with (PROCESSED_DIR / f"{slug}.chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")

    kinds = Counter(c.kind for c in chunks)
    print(f"{slug}: {len(parsed.text):,} characters, {len(chunks)} chunks {dict(kinds)}")
    print(f"struck-through characters dropped: {parsed.struck_chars_dropped:,}")
    print(f"wrote {PROCESSED_DIR / (slug + '.txt')} and {slug}.chunks.jsonl")

    if label:
        report = check_against_label(label, parsed.text, chunks)
        print("\nChecks against the label file:")
        print("\n".join(report) if report else "  (nothing to check)")
        return 1 if any(line.startswith("MISS") for line in report) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
