"""Chunker and parser tests built from the layout of a real credit agreement (Ashland 2022).

The text below is synthetic and short, but it reproduces the structures that broke the first
version: a table of contents with roman-numeral page footers and "Page" headers, a schedules and
exhibits list, a 155-character section title, a section heading with a double period, an
article lead-in sentence, and exhibits that follow Article X.
"""

from ingestion.chunker import MAX_CHARS, chunk_document
from ingestion.parser import html_to_text

RECITAL = (
    "This AMENDED AND RESTATED CREDIT AGREEMENT is entered into as of July 22, 2022, among the Borrowers, "
    "the Lenders from time to time party hereto, and the Administrative Agent, and amends and restates the Existing Agreement."
)
LONG_TITLE = (
    "Guaranteed Cash Management Agreements, Guaranteed Foreign Line of Credit Agreements, "
    "Guaranteed Letter of Credit Agreements and Guaranteed Hedge Agreements"
)

ASHLAND_LIKE = "\n".join([
    "EX-10.1 2 ex10-1.htm AMENDED AND RESTATED CREDIT AGREEMENT",
    "Exhibit 10.1",
    "Execution Copy",
    "AMENDED AND RESTATED CREDIT AGREEMENT",
    "TABLE OF CONTENTS",
    "Section | Page",
    "ARTICLE I | DEFINITIONS AND ACCOUNTING TERMS | 1",
    "1.01 | Defined Terms | 1",
    "ARTICLE VII | NEGATIVE COVENANTS | 90",
    "7.11 | Financial Covenants | 99",
    "ARTICLE IX",
    "ADMINISTRATIVE AGENT",
    f"9.13 | {LONG_TITLE.upper()} | 117",
    "ARTICLE X | MISCELLANEOUS | 120",
    "10.19 | CURRENCY INDEMNITY FOR JUDGMENTS | 136",
    "SCHEDULES",
    "1.01 | Unrestricted Subsidiaries",
    "2.01 | Commitments and Applicable Percentages",
    "EXHIBITS",
    "C | Form of Compliance Certificate",
    RECITAL,
    "The parties hereto agree as follows:",
    "ARTICLE I",
    "DEFINITIONS AND ACCOUNTING TERMS",
    "1.01 Defined Terms. As used in this Agreement, the following terms shall have the meanings set forth below:",
    "“Closing Date” means January 10, 2020.",
    "“Consolidated Net Leverage Ratio” means the ratio of Consolidated Funded Indebtedness to Consolidated EBITDA.",
    "ARTICLE VII",
    "NEGATIVE COVENANTS",
    "From and after the Closing Date, so long as any Lender shall have any Commitment hereunder, the Borrowers shall not:",
    "7.11 Financial Covenants.",
    "(a) Consolidated Net Leverage Ratio. Permit the Consolidated Net Leverage Ratio to be greater than 4.00:1.00.",
    "ARTICLE IX",
    "ADMINISTRATIVE AGENT",
    "9.12 Withholding Tax. The Administrative Agent may withhold.",
    f"9.13 {LONG_TITLE}. Except to the extent specifically provided for in Section 10.01, no Hedge Bank shall have any right.",
    "ARTICLE X",
    "MISCELLANEOUS",
    "10.18 Electronic Execution. Signatures may be electronic.",
    "10.19 Currency Indemnity for Judgments.. If it becomes necessary to convert into a Judgment Currency, the rate applies.",
    "10.20 Governing Law. New York law governs.",
    "EXHIBIT C",
    "FORM OF COMPLIANCE CERTIFICATE",
    "Financial Statement Date: ________",
    "1.1 Consolidated Net Leverage Ratio. Funded Indebtedness divided by EBITDA.",
    "ARTICLE I",
    "SCHEDULE 2.01",
    "COMMITMENTS AND APPLICABLE PERCENTAGES",
    "Lender | Commitment",
])


def _by(chunks, **filters):
    return [c for c in chunks if all(getattr(c, k) == v for k, v in filters.items())]


def test_no_toc_debris_in_any_chunk():
    chunks = chunk_document(ASHLAND_LIKE, "ash")
    all_text = "\n".join(c.text for c in chunks)
    assert "Unrestricted Subsidiaries" not in all_text          # schedule list from the TOC
    assert "| 117" not in all_text and "| 99" not in all_text   # TOC rows
    assert "Section | Page" not in all_text


def test_cover_and_recitals_are_preamble_without_article():
    preambles = _by(chunk_document(ASHLAND_LIKE, "ash"), kind="preamble", article="")
    text = "\n".join(c.text for c in preambles)
    assert "Execution Copy" in text      # cover page kept
    assert RECITAL in text               # parties and date kept, not skipped with the TOC


def test_article_lead_in_is_preamble_under_its_article():
    lead_ins = _by(chunk_document(ASHLAND_LIKE, "ash"), kind="preamble", article="ARTICLE VII NEGATIVE COVENANTS")
    assert len(lead_ins) == 1 and lead_ins[0].text.startswith("From and after the Closing Date")


def test_financial_covenant_section_path_and_text():
    [section] = _by(chunk_document(ASHLAND_LIKE, "ash"), kind="section", section_number="7.11")
    assert section.path == "ARTICLE VII NEGATIVE COVENANTS > 7.11 Financial Covenants"
    assert "greater than 4.00:1.00" in section.text


def test_long_section_title_is_detected():
    [section] = _by(chunk_document(ASHLAND_LIKE, "ash"), kind="section", section_number="9.13")
    assert section.section_title == LONG_TITLE
    assert section.text.startswith("Except to the extent")


def test_double_period_section_heading_is_detected():
    chunks = chunk_document(ASHLAND_LIKE, "ash")
    [section] = _by(chunks, kind="section", section_number="10.19")
    assert section.section_title == "Currency Indemnity for Judgments"
    assert section.text.startswith("If it becomes necessary")
    # and its text is no longer swallowed by 10.18
    [previous] = _by(chunks, kind="section", section_number="10.18")
    assert "Judgment Currency" not in previous.text


def test_definitions_still_split_per_term():
    terms = [c.term for c in _by(chunk_document(ASHLAND_LIKE, "ash"), kind="definition")]
    assert terms == ["Closing Date", "Consolidated Net Leverage Ratio"]


def test_attachments_are_top_level_with_titles():
    attachments = _by(chunk_document(ASHLAND_LIKE, "ash"), kind="attachment")
    assert {c.section_number for c in attachments} == {"EXHIBIT C", "SCHEDULE 2.01"}
    assert all(c.article == "" for c in attachments)
    [exhibit_c] = [c for c in attachments if c.section_number == "EXHIBIT C"]
    assert exhibit_c.path == "EXHIBIT C FORM OF COMPLIANCE CERTIFICATE"
    assert exhibit_c.text.startswith("FORM OF COMPLIANCE CERTIFICATE")


def test_headings_inside_exhibits_stay_in_the_exhibit():
    chunks = chunk_document(ASHLAND_LIKE, "ash")
    [exhibit_c] = _by(chunks, kind="attachment", section_number="EXHIBIT C")
    assert "1.1 Consolidated Net Leverage Ratio" in exhibit_c.text
    assert "ARTICLE I" in exhibit_c.text
    assert not _by(chunks, kind="section", section_number="1.1")


def test_toc_followed_directly_by_article_one():
    """No recital paragraph: the TOC must end when Article I repeats."""
    text = "\n".join([
        "CREDIT AGREEMENT",
        "TABLE OF CONTENTS",
        "ARTICLE I | DEFINITIONS | 1",
        "1.01 | Defined Terms | 1",
        "ARTICLE I",
        "DEFINITIONS",
        "1.01 Defined Terms. As used herein:",
        "“Borrower” means Acme Corp.",
    ])
    chunks = chunk_document(text, "x")
    [definition] = _by(chunks, kind="definition")
    assert definition.term == "Borrower" and definition.article == "ARTICLE I DEFINITIONS"


def test_document_without_toc():
    text = "\n".join([
        "CREDIT AGREEMENT dated as of June 1, 2024 among Acme Corp. and the Lenders party hereto.",
        "ARTICLE VIII",
        "FINANCIAL COVENANTS",
        "8.1 Maximum Leverage Ratio. The Borrower shall not permit the Leverage Ratio to exceed 6.00 to 1.00.",
    ])
    chunks = chunk_document(text, "x")
    assert _by(chunks, kind="preamble")[0].text.startswith("CREDIT AGREEMENT")
    [section] = _by(chunks, kind="section")
    assert section.path == "ARTICLE VIII FINANCIAL COVENANTS > 8.1 Maximum Leverage Ratio"


def test_chunks_never_exceed_max_chars():
    paragraph = "The Borrower shall deliver the certificate. " * 40       # ~1,760 chars
    giant = "This clause has no sentence breaks at all " * 300           # ~12,600 chars, no ". "
    text = "\n".join(["ARTICLE II", "THE CREDITS", "2.03 Letters of Credit."] + [paragraph] * 10 + [giant])
    chunks = _by(chunk_document(text, "x"), kind="section")
    assert len(chunks) > 3
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    joined = "".join(c.text.replace("\n", "") for c in chunks).replace(" ", "")
    assert joined == "".join(([paragraph] * 10 + [giant])).replace(" ", "")   # nothing lost


def test_parser_drops_roman_page_footers_and_page_headers():
    html = (
        "<p>ARTICLE III</p><p>-iv-</p><p>Page</p><p>- 12 -</p>"
        "<p>(i) the first clause</p><p>I</p><p>Pages of the Agreement</p>"
    )
    lines = html_to_text(html).text.split("\n")
    assert lines == ["ARTICLE III", "(i) the first clause", "I", "Pages of the Agreement"]


# Amendment No. 1 that carries the full conformed agreement as an annex (Regis 2024 layout).
AMENDMENT_WRAPPER = "\n".join([
    "AMENDMENT NO. 1 TO FINANCING AGREEMENT",
    "This AMENDMENT NO. 1 TO FINANCING AGREEMENT is entered into as of December 19, 2024, by and among the Borrower, "
    "the Guarantors, the Lenders party hereto, and the Collateral Agent.",
    "1.1 Amendments to Financing Agreement. The Financing Agreement is amended as set forth in the conformed copy attached as Annex A.",
    "1.2 Conditions to Effectiveness. This Amendment shall become effective upon satisfaction of the conditions below.",
    "ANNEX A",
    "FINANCING AGREEMENT (conformed through Amendment No. 1)",
    "TABLE OF CONTENTS",
    "ARTICLE I | DEFINITIONS; CERTAIN TERMS | 1",
    "1.01 | Definitions | 1",
    "ARTICLE VII | NEGATIVE COVENANTS | 120",
    "7.03 | Financial Covenants | 136",
    "ARTICLE I",
    "DEFINITIONS; CERTAIN TERMS",
    "1.01 Definitions. As used in this Agreement, the following terms have the following meanings:",
    "“Liquidity” means, as of any date of determination, the sum of unrestricted cash and Availability.",
    "ARTICLE VII",
    "NEGATIVE COVENANTS",
    "7.03 Financial Covenants. (a) Minimum Liquidity. Permit Liquidity at any time to be less than $10,000,000.",
    "EXHIBIT A",
    "FORM OF NOTICE OF BORROWING",
    "ARTICLE I",
    "1.1 Request. The Borrower requests a Loan.",
])


def test_amendment_annex_does_not_swallow_the_conformed_agreement():
    chunks = chunk_document(AMENDMENT_WRAPPER, "rgs")
    [covenant] = _by(chunks, kind="section", section_number="7.03")
    assert covenant.path == "ARTICLE VII NEGATIVE COVENANTS > 7.03 Financial Covenants"
    assert "less than $10,000,000" in covenant.text
    [liquidity] = _by(chunks, kind="definition")
    assert liquidity.term == "Liquidity" and liquidity.article == "ARTICLE I DEFINITIONS; CERTAIN TERMS"


def test_amendment_toc_is_skipped_and_its_own_sections_kept():
    chunks = chunk_document(AMENDMENT_WRAPPER, "rgs")
    assert "| 136" not in "\n".join(c.text for c in chunks)
    assert _by(chunks, kind="section", section_number="1.2")           # the amendment's own section


def test_exhibit_after_the_conformed_body_keeps_its_form_headings():
    chunks = chunk_document(AMENDMENT_WRAPPER, "rgs")
    [exhibit] = _by(chunks, kind="attachment", section_number="EXHIBIT A")
    assert "1.1 Request" in exhibit.text                                 # not a new section 1.1
    assert {c.section_number for c in _by(chunks, kind="attachment")} == {"ANNEX A", "EXHIBIT A"}


def test_article_title_wrapped_over_two_lines():
    """Regis Article IV: the title wraps, and the second line used to become a 22-char preamble."""
    text = "\n".join([
        "ARTICLE IV",
        "APPLICATION OF PAYMENTS; DEFAULTING LENDERS; JOINT AND SEVERAL",
        "LIABILITY OF BORROWERS",
        "4.01 Apportionment, Application and Reversal of Payments. Payments shall be apportioned ratably.",
    ])
    chunks = chunk_document(text, "x")
    assert not _by(chunks, kind="preamble")
    [section] = _by(chunks, kind="section")
    assert section.article == (
        "ARTICLE IV APPLICATION OF PAYMENTS; DEFAULTING LENDERS; JOINT AND SEVERAL LIABILITY OF BORROWERS"
    )


def test_toc_title_with_glued_digit():
    """Regis prints "TABLE OF CONTENTS1"; rows without page numbers after it must still be skipped."""
    text = "\n".join([
        "TABLE OF CONTENTS1",
        "Exhibit A | Form of Notice of Borrowing",
        "ARTICLE I",
        "DEFINITIONS",
        "1.01 Definitions. As used herein:",
    ])
    chunks = chunk_document(text, "x")
    assert "Notice of Borrowing" not in "\n".join(c.text for c in chunks)


# ---- Hanging indents built as two-cell tables (number cell | text cell) --------------------------- #

def _row(*cells):
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def test_parser_joins_hanging_indent_rows():
    html = "<table>" + _row("3.1", "Increased Costs. If any Change in Law shall:") + _row("(a)", "impose a reserve;") \
        + _row("(iv)", "a fourth item;") + _row("(A)", "an upper item;") + _row("(1)", "a numbered item;") + "</table>"
    assert html_to_text(html).text.split("\n") == [
        "3.1 Increased Costs. If any Change in Law shall:", "(a) impose a reserve;",
        "(iv) a fourth item;", "(A) an upper item;", "(1) a numbered item;",
    ]


def test_parser_keeps_real_tables_as_tables():
    html = "<table>" + _row("September 30, 2024", "6.20:1.00") + _row("1.1", "Certain Definitions", "1") \
        + _row("Level", "(a)") + _row("(a)", "4.00:1.00", "3.00%") + "</table>"
    assert html_to_text(html).text.split("\n") == [
        "September 30, 2024 | 6.20:1.00",     # threshold schedule: first cell is a date
        "1.1 | Certain Definitions | 1",      # TOC row: three cells
        "Level | (a)",                        # enumerator in the second cell only
        "(a) | 4.00:1.00 | 3.00%",            # pricing grid: three cells
    ]


def test_table_layout_headings_become_sections_end_to_end():
    html = (
        "<p>ARTICLE VIII</p><p>FINANCIAL COVENANTS</p><table>"
        + _row("8.1", "Maximum Leverage Ratio. The Loan Parties shall maintain a Leverage Ratio not exceeding:")
        + _row("(a)", "6.00 to 1.00 through September 30, 2027;")
        + _row("8.3", "Minimum Debt Service Coverage Ratio. The Loan Parties shall maintain a ratio of at least 1.25.")
        + "</table>"
    )
    chunks = chunk_document(html_to_text(html).text, "x")
    [leverage] = _by(chunks, kind="section", section_number="8.1")
    assert leverage.path == "ARTICLE VIII FINANCIAL COVENANTS > 8.1 Maximum Leverage Ratio"
    assert "(a) 6.00 to 1.00" in leverage.text
    assert _by(chunks, kind="section", section_number="8.3")
    assert not _by(chunks, kind="preamble")


def test_definition_clauses_in_table_layout_read_as_plain_text():
    html = (
        "<p>ARTICLE I</p><p>CERTAIN DEFINITIONS</p><p>1.1 Certain Definitions. In this Agreement:</p>"
        "<p>“Indebtedness” means all of the following:</p><table>"
        + _row("(a)", "all obligations for borrowed money;") + _row("(b)", "all Capital Leases.") + "</table>"
    )
    [definition] = _by(chunk_document(html_to_text(html).text, "x"), kind="definition")
    assert definition.text.endswith("(a) all obligations for borrowed money;\n(b) all Capital Leases.")


# ---- A schedule titled in the article heading style --------------------------------------------- #

def test_article_style_schedule_title_stays_in_the_attachment():
    text = "\n".join([
        "ARTICLE I", "DEFINITIONS", "1.1 Definitions. Terms used herein.",
        "ARTICLE XII", "GUARANTY", "12.1 Guaranty. Each Guarantor guarantees.",
        "SCHEDULE 1.1(B)", "COMMITMENTS OF LENDERS AND ADDRESSES FOR NOTICES",
        "ARTICLE XIII", "- Commitments of Lenders",
        "Lender | Amount of Commitment",
        "ARTICLE XIV", "Addresses for Notices to Administrative Agent:",
    ])
    chunks = chunk_document(text, "x")
    assert not [c for c in chunks if c.article.startswith(("ARTICLE XIII", "ARTICLE XIV"))]
    [schedule] = _by(chunks, kind="attachment")
    assert "Lender | Amount of Commitment" in schedule.text and "Addresses for Notices" in schedule.text


def test_reserved_section_is_its_own_heading():
    text = "\n".join([
        "ARTICLE VIII", "FINANCIAL COVENANTS",
        "8.1 Maximum Leverage Ratio. Not more than 6.00:1.00.",
        "8.2 [Reserved].",
        "Section 8.3. [RESERVED]",
        "8.4 Minimum Debt Service Coverage Ratio. At least 1.25:1.00.",
    ])
    chunks = chunk_document(text, "x")
    [leverage] = _by(chunks, kind="section", section_number="8.1")
    assert "[Reserved]" not in leverage.text
    titles = {c.section_number: c.section_title for c in chunks}
    assert titles["8.4"] == "Minimum Debt Service Coverage Ratio"
    # Reserved sections get a placeholder chunk, so a numbering gap always means a missed heading.
    assert titles["8.2"].lower() == titles["8.3"].lower() == "[reserved]"


# ---- Section titles recovered from the document's own table of contents -------------------------- #

TOC_THEN_BODY = "\n".join([
    "TABLE OF CONTENTS",
    "ARTICLE II. | CREDIT FACILITIES | 39",
    "2.9 | Notes | 49",
    "ARTICLE V. | REPRESENTATIONS AND WARRANTIES | 74",
    "5.11 | MARGIN STOCK | 76",
    "5.12 | Full Disclosure | 76",
    "ARTICLE II",
    "CREDIT FACILITIES",
    "2.9 Notes.The obligation of the Borrower to repay the Loans shall be evidenced by Notes.",
    "ARTICLE V",
    "REPRESENTATIONS AND WARRANTIES",
    "5.11 Margin Stock None of the Loan Parties engages in the business of extending credit for margin stock.",
    "5.12 Full Disclosure. Nothing herein is misleading.",
])


def test_heading_without_any_period_is_split_using_the_toc_title():
    [section] = _by(chunk_document(TOC_THEN_BODY, "x"), kind="section", section_number="5.11")
    assert section.section_title == "Margin Stock"          # body casing kept, though the TOC is upper case
    assert section.text.startswith("None of the Loan Parties")


def test_heading_with_no_space_after_the_period():
    [section] = _by(chunk_document(TOC_THEN_BODY, "x"), kind="section", section_number="2.9")
    assert section.section_title == "Notes" and section.text.startswith("The obligation")


def test_no_space_after_period_also_works_without_a_toc():
    text = "\n".join(["ARTICLE III", "YIELD PROTECTION", "3.5 Indemnity.Upon demand of any Lender, the Borrower pays."])
    [section] = _by(chunk_document(text, "x"), kind="section")
    assert (section.section_number, section.section_title) == ("3.5", "Indemnity")
    assert section.text.startswith("Upon demand")


def test_toc_title_must_match_the_whole_word():
    """TOC says "Notes"; a body line "2.9 Notesheet ..." must not be split inside a word."""
    text = "\n".join(["TABLE OF CONTENTS", "ARTICLE II | LOANS | 39", "2.9 | Notes | 49", "ARTICLE II", "LOANS",
                      "2.9 Notesheet Requirements. Each Lender keeps a notesheet."])
    [section] = _by(chunk_document(text, "x"), kind="section", section_number="2.9")
    assert section.section_title == "Notesheet Requirements"   # fallback pattern, not a mid-word TOC split
