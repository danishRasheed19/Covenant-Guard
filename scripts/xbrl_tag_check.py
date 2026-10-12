#!/usr/bin/env python3
"""Report which candidate us-gaap tags each company uses in its XBRL filings.

Usage:
    export SEC_USER_AGENT="Your Name your.email@example.com"
    python scripts/xbrl_tag_check.py AAPL MSFT ...

Uses only the standard library. Requires network access.
Missing tags are simply reported as missing; the candidate list is a starting point,
not an exhaustive or authoritative tag inventory.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SLEEP_SECONDS = 0.25  # stay well under SEC's published rate limit

TAG_GROUPS = {
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
        "LineOfCredit",
        "SecuredDebt",
        "NotesPayable",
    ],
    "ebitda_parts": [
        "OperatingIncomeLoss",
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "AmortizationOfIntangibleAssets",
    ],
    "interest": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestPaidNet",
    ],
    "liquidity": [
        "LineOfCreditFacilityRemainingBorrowingCapacity",
        "LineOfCreditFacilityMaximumBorrowingCapacity",
    ],
}


def get_json(url: str, user_agent: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def summarize_tag(facts: dict, tag: str) -> str:
    node = facts.get("facts", {}).get("us-gaap", {}).get(tag)
    if not node:
        return "missing"
    entries = node.get("units", {}).get("USD", [])
    if not entries:
        return "present (no USD values)"
    latest = max(entries, key=lambda e: e.get("end", ""))
    return f"{len(entries)} values, latest end {latest.get('end')} ({latest.get('form')})"


def main() -> int:
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        print('Set SEC_USER_AGENT, e.g. export SEC_USER_AGENT="Your Name you@example.com"')
        return 1
    tickers = [t.upper() for t in sys.argv[1:]]
    if not tickers:
        print("Usage: python scripts/xbrl_tag_check.py TICKER [TICKER ...]")
        return 1

    mapping = get_json(TICKER_URL, user_agent)
    cik_by_ticker = {row["ticker"].upper(): int(row["cik_str"]) for row in mapping.values()}

    for ticker in tickers:
        print(f"\n=== {ticker} ===")
        cik = cik_by_ticker.get(ticker)
        if cik is None:
            print("  ticker not found in SEC ticker list")
            continue
        time.sleep(SLEEP_SECONDS)
        try:
            facts = get_json(FACTS_URL.format(cik=cik), user_agent)
        except urllib.error.HTTPError as err:
            print(f"  could not fetch company facts (HTTP {err.code})")
            continue
        print(f"  {facts.get('entityName')} (CIK {cik:010d})")
        for group, tags in TAG_GROUPS.items():
            print(f"  [{group}]")
            for tag in tags:
                print(f"    {tag}: {summarize_tag(facts, tag)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
