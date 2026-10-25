"""Attach the agreement's own definitions to an extracted covenant, with plain code (no LLM).

The model only NAMES the defined terms a covenant uses. This module then:
  1. looks each name up EXACTLY in the agreement (`lookup(term)` -> definition text and section),
  2. reads each definition for further defined terms and follows them (a definition such as Adjusted EBITDA
     points to EBITDA, which points to Consolidated Net Income),
  3. returns the verbatim definitions as `schema.DefinedTerm` objects.

Following links is limited to `max_depth` levels and, below the first level, to terms whose names look
financial (EBITDA, Income, Indebtedness, ...), because almost every definition mentions Person, Lien, Loan
Documents and similar words nobody needs here. Both limits are plain constants so the evaluation can tune them.
"""

import re

from schema import DefinedTerm

# Terms every agreement defines but that never change how a covenant is calculated.
GENERIC = {t.lower() for t in [
    "Agreement", "Borrower", "Borrowers", "Co-Borrower", "Co-Borrowers", "Company", "Parent", "Holdings", "Loan Parties",
    "Loan Party", "Guarantor", "Guarantors", "Subsidiary", "Subsidiaries", "Restricted Subsidiary", "Restricted Subsidiaries",
    "Person", "Persons", "Closing Date", "Effective Date", "Maturity Date", "Termination Date", "Expiration Date", "Fiscal Quarter",
    "Fiscal Quarters", "Fiscal Year", "Fiscal Years", "Consolidated", "GAAP", "SEC", "Obligations", "Secured Obligations",
    "Loan Documents", "Credit Documents", "Loans", "Lender", "Lenders", "Agent", "Administrative Agent", "Required Lenders",
    "Law", "Laws", "Lien", "Liens", "Governmental Authority", "Business Day", "Dollars", "Equity Interest", "Equity Interests"]}

FINANCIAL_WORDS = re.compile(
    r"EBITDA|Income|Indebtedness|Debt|Interest Expense|Cash|Liquidity|Availability|Charges|Payments|Expenditures|"
    r"Revenue|Earnings|Net Worth|Capital Lease|Fixed Charge|Service|Taxes", re.I)

ADJUSTMENT_WORDS = re.compile(
    r"\bplus\b|\bminus\b|add(?:ed)?[- ]?backs?|exclud|\bexcept\b|to the extent|non-?cash|pro forma|extraordinary|"
    r"non-?recurring|run[- ]rate|unrealized|\bless\b", re.I)


def is_generic(term: str) -> bool:
    return term.strip().lower() in GENERIC


def terms_in(text: str, known_terms: list, *, skip: str = "") -> list:
    """Defined terms (from `known_terms`) that appear in `text` as whole capitalised words, longest first."""
    found = []
    for term in sorted(set(known_terms), key=len, reverse=True):
        if term.lower() == skip.lower() or len(term) < 3:
            continue
        if re.search(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", text):
            found.append(term)
            text = text.replace(term, " ")        # a long term hides the shorter terms inside it
    return found


def strip_leading_term(term: str, text: str) -> str:
    """Definition chunks start with the term itself (`"Leverage Ratio" means ...`); keep only `means ...`."""
    cleaned = text.strip()
    match = re.match(r"^[\"'“”]?" + re.escape(term) + r"[\"'“”]?\s*", cleaned, re.I)
    return cleaned[match.end():] if match else cleaned


def resolve_definitions(names: list, lookup, known_terms: list, *, max_depth: int = 4) -> list:
    """Breadth-first closure. `lookup(term)` returns (definition_text, section) or None when not defined.

    Level 1 = the names the model gave (generic ones skipped). Deeper levels = terms found inside definitions,
    kept only if they look financial. Each term appears once, in discovery order."""
    out, seen = [], set()
    level = [n for n in names if n and not is_generic(n)]
    for depth in range(1, max_depth + 1):
        next_level = []
        for name in level:
            key = name.strip().lower()
            if key in seen:
                continue
            found = lookup(name)
            if not found:
                continue
            seen.add(key)
            text, section = found
            body = strip_leading_term(name, text)
            out.append(DefinedTerm(term=name.strip(), definition=body, section=section,
                                   has_custom_adjustments=bool(ADJUSTMENT_WORDS.search(body)), adjustment_notes=[]))
            for inner in terms_in(body, known_terms, skip=name):
                if not is_generic(inner) and FINANCIAL_WORDS.search(inner):
                    next_level.append(inner)
        level = next_level
    return out


def db_lookup(conn, doc_id: str):
    """Build `lookup` and the list of all defined terms from the loaded Postgres document."""
    from retrieval.search import find_definition

    known = [r[0] for r in conn.execute(
        "SELECT DISTINCT term FROM chunks WHERE doc_id = %s AND kind = 'definition' AND term <> ''", (doc_id,)).fetchall()]

    def lookup(term: str):
        hits = find_definition(conn, doc_id, term)
        if not hits:
            return None
        section = re.search(r"\d+\.\d+", hits[0].path)
        return " ".join(h.text for h in hits), f"Section {section.group()}" if section else "Section 1.01"
    return lookup, known
