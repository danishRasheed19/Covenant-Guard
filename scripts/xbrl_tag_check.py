#!/usr/bin/env python3
"""Check which XBRL tags a company reports for covenant inputs (v2).

Usage (from the repo root):
    python scripts/xbrl_tag_check.py ASH RGS NUVR          # tickers
    python scripts/xbrl_tag_check.py 1606268               # or CIKs (all digits)
    python scripts/xbrl_tag_check.py ASH --at ASH=2022-09-30,2025-09-30
    python scripts/xbrl_tag_check.py ASH --refresh         # ignore the on-disk cache

What it reports
  1. Coverage: for each candidate us-gaap tag, how many USD values exist and the
     first and last period end. A tag that stops years ago is not usable for
     current periods.
  2. Extension tags: company-specific namespaces, if the SEC data contains any.
  3. With --at: the facts that end exactly on each date you name (your ground-truth
     dates), with their period length. This is the real test of whether a ratio can
     be recomputed for that date.

SEC_USER_AGENT is read from the environment, or from .env in the repo root.
Responses are cached in data/raw/xbrl/ for 24 hours. Standard library only.
A missing tag is only reported as missing: the candidate lists are a starting
point, not an exhaustive tag inventory.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "raw" / "xbrl"
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SLEEP_SECONDS = 0.25  # stay well under SEC's published rate limit
CACHE_MAX_AGE_HOURS = 24

TAG_GROUPS = {
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "RestrictedCashAndCashEquivalentsAtCarryingValue",
        "ShortTermInvestments",
    ],
    "debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
        "LineOfCredit",
        "LongTermLineOfCredit",
        "SecuredDebt",
        "NotesPayable",
        "LongTermNotesPayable",
        "DebtInstrumentCarryingAmount",
        "DebtInstrumentFaceAmount",
        "LongTermDebtAndCapitalLeaseObligations",
        "FinanceLeaseLiability",
        "FinanceLeaseLiabilityNoncurrent",
        "CapitalLeaseObligationsNoncurrent",
    ],
    "net_income": [  # every covenant EBITDA in the labels starts from net income
        "NetIncomeLoss",
        "ProfitLoss",
        "IncomeLossFromContinuingOperations",
        "IncomeTaxExpenseBenefit",
        "IncomeTaxesPaidNet",
    ],
    "ebitda_parts": [
        "OperatingIncomeLoss",
        "Depreciation",
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "AmortizationOfIntangibleAssets",
        "ShareBasedCompensation",
        "AssetImpairmentCharges",
        "ImpairmentOfLongLivedAssetsHeldForUse",
        "ImpairmentOfIntangibleAssetsExcludingGoodwill",
        "GoodwillImpairmentLoss",
        "GoodwillAndIntangibleAssetImpairment",
        "RestructuringCharges",
        "IncomeLossFromEquityMethodInvestments",
        "IncomeLossFromDiscontinuedOperationsNetOfTax",
    ],
    "interest": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestExpenseNonoperating",
        "InterestExpenseLongTermDebt",
        "InterestIncomeExpenseNet",
        "InterestIncomeExpenseNonoperatingNet",
        "InterestAndDebtExpense",
        "InvestmentIncomeInterest",
        "InterestPaidNet",
    ],
    "capex": [  # covenants that deduct capital expenditures (e.g. MEC interest coverage)
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "liquidity": [
        "LineOfCreditFacilityRemainingBorrowingCapacity",
        "LineOfCreditFacilityMaximumBorrowingCapacity",
        "LineOfCreditFacilityCurrentBorrowingCapacity",
        "LineOfCreditFacilityAmountOutstanding",
        "LetterOfCreditOutstandingAmount",
    ],
}

# Namespaces published by standards bodies. Anything else is a company extension.
STANDARD_TAXONOMIES = {"us-gaap", "dei", "srt", "ifrs-full", "invest", "country",
                       "currency", "exch", "naics", "sic", "stpr",
                       "ecd", "cyd", "rr", "ffd", "oef", "sec"}  # ecd = executive compensation
CUSTOM_KEYWORDS = re.compile(
    r"debt|borrow|credit|revolv|receivable|liquid|availab|ebitda|interest|covenant|loan",
    re.IGNORECASE,
)
MAX_CUSTOM_LINES = 40

FLOW_NOTE = (
    "  Note: a 10-K reports only the full year, never a standalone Q4. Trailing-twelve-month\n"
    "  figures at a 10-Q date = last full year + current year-to-date - prior-year\n"
    "  year-to-date (all three facts must exist). At a fiscal year end, the 12-month fact\n"
    "  is the trailing figure."
)


# --------------------------------------------------------------------------- #
# Setup helpers
# --------------------------------------------------------------------------- #
def find_user_agent():
    """SEC_USER_AGENT from the environment, else from .env in the repo root."""
    value = os.environ.get("SEC_USER_AGENT")
    if value:
        return value.strip().strip('"').strip("'")
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("SEC_USER_AGENT="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def fetch_json(url, user_agent, cache_path, refresh=False):
    """GET a JSON document, using a fresh on-disk copy when there is one."""
    if cache_path.exists() and not refresh:
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours < CACHE_MAX_AGE_HOURS:
            return json.loads(cache_path.read_text(encoding="utf-8"))
    time.sleep(SLEEP_SECONDS)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(raw)
    return json.loads(raw)


def norm_key(key):
    """Tickers are compared upper-case; CIKs without leading zeros."""
    key = key.strip()
    return str(int(key)) if key.isdigit() else key.upper()


def parse_at(values):
    """['ASH=2022-09-30,2025-09-30', ...] -> {'ASH': ['2022-09-30', '2025-09-30']}."""
    result = {}
    for item in values or []:
        key, sep, dates = item.partition("=")
        if not sep or not key.strip() or not dates.strip():
            raise ValueError(f"--at expects KEY=YYYY-MM-DD[,YYYY-MM-DD...], got {item!r}")
        parsed = []
        for text in dates.split(","):
            text = text.strip()
            try:
                date.fromisoformat(text)
            except ValueError:
                raise ValueError(f"{text!r} in {item!r} is not a valid YYYY-MM-DD date") from None
            parsed.append(text)
        result.setdefault(norm_key(key), []).extend(parsed)
    return result


# --------------------------------------------------------------------------- #
# Reading the facts
# --------------------------------------------------------------------------- #
def usd_entries(facts, taxonomy, tag):
    """None if the tag is absent, else its USD entries (possibly an empty list)."""
    node = facts.get("facts", {}).get(taxonomy, {}).get(tag)
    if node is None:
        return None
    return node.get("units", {}).get("USD", [])


def describe(entries):
    if entries is None:
        return "missing"
    if not entries:
        return "present (no USD values)"
    ends = sorted(e["end"] for e in entries if "end" in e)
    latest = max(entries, key=lambda e: e.get("end", ""))
    return f"{len(entries)} values, {ends[0]} to {ends[-1]} (latest from {latest.get('form')})"


def period_label(start, end):
    """'instant', '3mo', '6mo', '9mo', '12mo', or the day count."""
    if not start:
        return "instant"
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    for target, name in ((91, "3mo"), (182, "6mo"), (273, "9mo"), (365, "12mo")):
        if abs(days - target) <= 8:  # 52/53-week years and 14-week quarters
            return f"{name} ({start} to {end})"
    return f"{days}d ({start} to {end})"


def facts_at(entries, target):
    """Distinct facts whose period ends on `target`, shortest period first.

    The same fact is repeated in later filings as a comparative, so identical
    (start, value) pairs are shown once, attributed to the first filing listed.
    Different values for the same period (restatements) are all shown.
    """
    seen = {}
    for e in entries:
        if e.get("end") != target:
            continue
        seen.setdefault((e.get("start"), e["val"]), e)
    def length(start):
        return 0 if not start else (date.fromisoformat(target) - date.fromisoformat(start)).days

    ordered = sorted(seen.items(), key=lambda item: (length(item[0][0]), item[0][1]))
    return [(period_label(start, target), val, e.get("form"), e.get("accn"), e.get("filed"))
            for (start, val), e in ordered]


def scan_custom(facts):
    """Extension-namespace tags whose names look debt/interest/liquidity related."""
    namespaces = [ns for ns in facts.get("facts", {}) if ns not in STANDARD_TAXONOMIES]
    found = []
    for ns in namespaces:
        for tag, node in facts["facts"][ns].items():
            if CUSTOM_KEYWORDS.search(tag):
                entries = node.get("units", {}).get("USD", [])
                if entries:
                    found.append((f"{ns}:{tag}", describe(entries)))
    return namespaces, sorted(found)


def fmt_value(value):
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report_custom(facts):
    namespaces, found = scan_custom(facts)
    print("  [extension tags]")
    if not namespaces:
        print("    none in this data. The SEC company-facts data may omit company-specific")
        print("    tags, so this does NOT prove none exist. Check the filing's")
        print("    Financial_Report.xlsx or its R pages for custom debt/availability tags.")
        return
    print(f"    namespaces: {', '.join(namespaces)}")
    for name, text in found[:MAX_CUSTOM_LINES]:
        print(f"    {name}: {text}")
    if len(found) > MAX_CUSTOM_LINES:
        print(f"    ... and {len(found) - MAX_CUSTOM_LINES} more")


def report_at(facts, target):
    print(f"  [facts ending {target}]")
    absent = []
    for tags in TAG_GROUPS.values():
        for tag in tags:
            entries = usd_entries(facts, "us-gaap", tag)
            rows = facts_at(entries, target) if entries else []
            if not rows:
                absent.append(tag)
                continue
            for label, val, form, accn, filed in rows:
                print(f"    {tag}: {fmt_value(val)}  [{label}]  {form} {accn} filed {filed}")
    print(f"    no fact ending on this date: {', '.join(absent) if absent else '(none)'}")


def run_company(label, cik, user_agent, refresh, dates):
    facts = fetch_json(
        FACTS_URL.format(cik=cik), user_agent,
        CACHE_DIR / f"companyfacts_CIK{cik:010d}.json", refresh,
    )
    print(f"\n=== {label} ===")
    print(f"  {facts.get('entityName')} (CIK {cik:010d})")
    for group, tags in TAG_GROUPS.items():
        print(f"  [{group}]")
        for tag in tags:
            print(f"    {tag}: {describe(usd_entries(facts, 'us-gaap', tag))}")
    report_custom(facts)
    for target in dates:
        report_at(facts, target)
    if dates:
        print(FLOW_NOTE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("companies", nargs="+", help="tickers, or all-digit CIKs")
    parser.add_argument("--at", action="append", metavar="KEY=DATE[,DATE]",
                        help="show facts ending on these dates (KEY = ticker or CIK as given)")
    parser.add_argument("--refresh", action="store_true", help="ignore the on-disk cache")
    args = parser.parse_args(argv)

    user_agent = find_user_agent()
    if not user_agent:
        print('Set SEC_USER_AGENT in .env or the environment, e.g. "Your Name you@example.com"')
        return 1
    try:
        at_map = parse_at(args.at)
    except ValueError as err:
        print(f"Bad --at value: {err}")
        return 2

    ticker_map = None
    used_keys = set()
    for arg in args.companies:
        try:
            if arg.isdigit():
                cik = int(arg)
            else:
                if ticker_map is None:
                    rows = fetch_json(TICKER_URL, user_agent, CACHE_DIR / "company_tickers.json",
                                      args.refresh)
                    ticker_map = {r["ticker"].upper(): int(r["cik_str"]) for r in rows.values()}
                cik = ticker_map.get(arg.upper())
                if cik is None:
                    print(f"\n=== {arg.upper()} ===")
                    print("  ticker not found in SEC ticker list (try the CIK instead)")
                    continue
            dates = []
            for key in (norm_key(arg), str(cik)):
                if key in at_map:
                    used_keys.add(key)
                    dates = at_map[key]
            run_company(arg.upper(), cik, user_agent, args.refresh, dates)
        except urllib.error.HTTPError as err:
            print(f"\n=== {arg.upper()} ===\n  could not fetch data (HTTP {err.code})")
        except urllib.error.URLError as err:
            print(f"\n=== {arg.upper()} ===\n  network error: {err.reason}")

    for key in sorted(set(at_map) - used_keys):
        print(f"\nWarning: --at key {key!r} did not match any company argument")
    return 0


if __name__ == "__main__":
    sys.exit(main())
