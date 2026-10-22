#!/usr/bin/env python3
"""Print a quick health report for a processed agreement (reads data/processed/<slug>.chunks.jsonl).

Usage (from the repo root):
    python scripts/inspect_chunks.py ashland_2022_ar_credit_agreement
    python scripts/inspect_chunks.py ashland_2022_ar_credit_agreement --show 7.11
    python scripts/inspect_chunks.py ashland_2022_ar_credit_agreement --show "EXHIBIT C"

It reports chunk counts and sizes, the longest chunks, what landed in "preamble", gaps in section
numbering (reserved sections also show up as gaps), and definitions that look too short, too long
or duplicated. --show prints the text of a section so you can read what was captured.
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
MAX_CHARS = 6000   # keep in sync with ingestion.chunker.MAX_CHARS


def load(slug: str) -> list:
    path = PROCESSED / f"{slug}.chunks.jsonl"
    if not path.exists():
        sys.exit(f"Not found: {path}. Run python -m ingestion.run_ingest first.")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("slug")
    parser.add_argument("--show", metavar="SECTION",
                        help='print the text of a section or attachment, e.g. 7.11 or "EXHIBIT C"')
    args = parser.parse_args()
    chunks = load(args.slug)

    print(f"{args.slug}: {len(chunks)} chunks\n")
    print("Sizes by kind (characters):")
    for kind in ("definition", "section", "attachment", "preamble"):
        sizes = [len(c["text"]) for c in chunks if c["kind"] == kind]
        if sizes:
            print(f"  {kind:<11} n={len(sizes):<4} min={min(sizes):<6} median={int(statistics.median(sizes)):<6} max={max(sizes)}")

    print("\n10 longest chunks:")
    for c in sorted(chunks, key=lambda c: len(c["text"]), reverse=True)[:10]:
        print(f"  {len(c['text']):>6}  {c['kind']:<10} {c['path'][:90]}")

    print("\nPreamble chunks (cover/recitals, or an article's lead-in before its first section):")
    for c in (c for c in chunks if c["kind"] == "preamble"):
        print(f"  [{c['article'][:30] or 'no article'}] {len(c['text']):>5} chars: {c['text'][:110]!r}")
    tiny = [c for c in chunks if c["kind"] == "preamble" and len(c["text"]) < 40]
    if tiny:
        print(f"  WARNING: {len(tiny)} preamble chunk(s) under 40 chars, probably page or TOC debris")

    attachments = defaultdict(list)
    for c in chunks:
        if c["kind"] == "attachment":
            attachments[(c["section_number"], c["section_title"])].append(c)
    print(f"\nAttachments: {len(attachments)} distinct, {sum(len(v) for v in attachments.values())} chunks")
    for (label, title), parts in attachments.items():
        print(f"  {label:<14} {len(parts):>2} part(s)  {title[:70] or '(no title found)'}")
    nested = [c for c in chunks if c["kind"] == "attachment" and c["article"]]
    if nested:
        print(f"  WARNING: {len(nested)} attachment chunk(s) still carry an article, e.g. {nested[0]['path'][:80]!r}")

    by_major = defaultdict(set)
    for c in chunks:
        match = re.fullmatch(r"(\d+)\.(\d+)", c["section_number"])
        if c["kind"] == "section" and match:
            by_major[int(match.group(1))].add(int(match.group(2)))
    print("\nGaps in section numbering ([Reserved] sections are chunks, so a gap is a missed heading):")
    any_gap = False
    for major in sorted(by_major):
        minors = by_major[major]
        missing = [m for m in range(min(minors), max(minors) + 1) if m not in minors]
        if missing:
            any_gap = True
            print(f"  Article {major}: found {min(minors)}-{max(minors)}, missing {', '.join(f'{major}.{m:02d}' for m in missing)}")
    if not any_gap:
        print("  none")

    definitions = [c for c in chunks if c["kind"] == "definition"]
    short = [c for c in definitions if len(c["text"]) < 50]
    long_ = [c for c in definitions if len(c["text"]) > 8000]
    duplicates = [t for t, n in Counter(c["term"].lower() for c in definitions).items() if n > 1]
    def listed(items, fmt, limit=10):
        shown = "".join(f"\n    {fmt(x)}" for x in items[:limit])
        more = f"\n    ... and {len(items) - limit} more" if len(items) > limit else ""
        return shown + more

    print(f"\nDefinitions: {len(definitions)} total")
    print(f"  shorter than 50 characters: {len(short)}" + listed(short, lambda c: f"{c['term']!r}: {c['text']!r}"))
    print(f"  longer than 8000 characters: {len(long_)}" + listed(long_, lambda c: f"{c['term']!r}: {len(c['text'])} chars"))
    print(f"  terms defined more than once: {len(duplicates)}" + listed(duplicates, repr))

    oversized = [c for c in chunks if c["kind"] != "definition" and len(c["text"]) > MAX_CHARS]
    print(f"\nNon-definition chunks over {MAX_CHARS} chars: {len(oversized)}"
          + listed(oversized, lambda c: f"{len(c['text'])}  {c['path'][:80]}"))

    if args.show:
        print(f"\n--- {args.show} ---")
        hits = [c for c in chunks if c["section_number"].lower() == args.show.lower()
                and c["kind"] in ("section", "attachment")]
        for c in hits:
            print(f"[{c['path']}] part {c['part']}\n{c['text']}\n")
        if not hits:
            print("not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
