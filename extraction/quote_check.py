"""Explain WHY a quote was rejected: show where it stops matching the agreement text.

    python -m extraction.quote_check --slug regis_2024_credit_agreement

For every covenant in data/extracted/<slug>.json whose quote is not found, this prints how much of the quote
matches the source (comparison ignores case, quote style and whitespace, like the verifier) and the next
characters from the quote and from the source at the point where they differ.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ingestion.textnorm import squash  # noqa: E402


def divergence(quote: str, source: str, context: int = 80) -> dict:
    """Longest prefix of the quote found in the source (binary search; a prefix match is monotonic)."""
    q, s = squash(quote), squash(source)
    if not q:
        return {"found": False, "matched": 0, "total": 0, "quote_next": "", "source_next": ""}
    if q in s:
        return {"found": True, "matched": len(q), "total": len(q), "quote_next": "", "source_next": ""}
    low, high = 0, len(q)
    while low < high:
        mid = (low + high + 1) // 2
        low, high = (mid, high) if q[:mid] in s else (low, mid - 1)
    start = s.find(q[:low]) if low else -1
    source_next = s[start + low:start + low + context] if start >= 0 else ""
    return {"found": False, "matched": low, "total": len(q), "quote_next": q[low:low + context], "source_next": source_next}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    source = (REPO_ROOT / "data" / "processed" / f"{args.slug}.txt").read_text(encoding="utf-8")
    data = json.loads((REPO_ROOT / "data" / "extracted" / f"{args.slug}.json").read_text(encoding="utf-8"))
    for cov in data["covenants"]:
        result = divergence(cov["citation"]["quote"], source)
        if result["found"]:
            print(f"{cov['covenant_id']}: quote found")
            continue
        print(f"{cov['covenant_id']}: quote NOT found. {result['matched']} of {result['total']} characters match.")
        print(f"  quote continues : {result['quote_next']!r}")
        print(f"  source continues: {result['source_next']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
