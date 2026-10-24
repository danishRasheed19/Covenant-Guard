"""Section headings in other styles: "SECTION 9.15", glued titles, two-line headings, table cells."""

from ingestion.chunker import chunk_document, section_heading
from ingestion.parser import html_to_text

PROSE = ("This Credit Agreement is entered into as of June 28, 2023, by and among the Borrower, the "
         "Guarantors, the Lenders from time to time party hereto and the Administrative Agent named herein.")


def sections(chunks):
    return {c.section_number: c for c in chunks if c.kind == "section"}


def definition_terms(chunks):
    return {c.term for c in chunks if c.kind == "definition"}


def test_uppercase_section_headings_with_a_toc():
    text = "\n".join([
        "CREDIT AGREEMENT", "TABLE OF CONTENTS",
        "ARTICLE I DEFINITIONS 1", "SECTION 1.1 Defined Terms 1",
        "ARTICLE IX NEGATIVE COVENANTS 40", "SECTION 9.15 Financial Covenants 41",
        PROSE,
        "ARTICLE I", "DEFINITIONS",
        "SECTION 1.1 Defined Terms.",
        "“Closing Date” means June 28, 2023.",
        "“Consolidated EBITDA” means, for any period, net income plus interest expense.",
        "ARTICLE IX", "NEGATIVE COVENANTS",
        "SECTION 9.15Financial Covenants.",
        "(a) Consolidated Total Leverage Ratio. As of the last day of any fiscal quarter, permit the ratio to exceed 4.00 to 1.00.",
    ])
    chunks = chunk_document(text, "doc")
    found = sections(chunks)
    assert set(found) == {"9.15"}                      # 1.1 holds only definitions, so it has no section chunk
    assert {c.section_number for c in chunks if c.kind == "definition"} == {"1.1"}
    assert found["9.15"].section_title == "Financial Covenants"
    assert "ARTICLE IX" in found["9.15"].path
    assert "Consolidated Total Leverage Ratio" in found["9.15"].text
    assert definition_terms(chunks) == {"Closing Date", "Consolidated EBITDA"}
    assert not all(c.kind == "preamble" for c in chunks)


def test_glued_title_without_a_toc():
    text = "\n".join([
        PROSE, "ARTICLE IX", "SECTION 9.15Financial Covenants.", "The Borrower shall maintain the ratios below.",
        "SECTION 9.16.Fiscal Year.", "The fiscal year shall not change.",
    ])
    found = sections(chunk_document(text, "doc"))
    assert set(found) == {"9.15", "9.16"}
    assert found["9.15"].section_title == "Financial Covenants"
    assert found["9.16"].section_title == "Fiscal Year"


def test_heading_split_over_two_lines():
    text = "\n".join([
        PROSE, "ARTICLE IX", "SECTION 9.15", "Financial Covenants.",
        "The Borrower shall maintain the ratios below.", "SECTION 9.16", "Fiscal Year",
        "The fiscal year shall not change.",
    ])
    found = sections(chunk_document(text, "doc"))
    assert set(found) == {"9.15", "9.16"}
    assert found["9.15"].section_title == "Financial Covenants"
    assert "maintain the ratios" in found["9.15"].text
    assert found["9.16"].section_title == "Fiscal Year"


def test_uppercase_heading_in_a_table_row_end_to_end():
    html = (
        "<html><body><p>" + PROSE + "</p><p>ARTICLE IX</p><p>NEGATIVE COVENANTS</p>"
        "<table><tr><td>SECTION 9.15</td><td>Financial Covenants.</td></tr></table>"
        "<table><tr><td>(a)</td><td>Consolidated Total Leverage Ratio. As of the last day of any fiscal quarter.</td></tr></table>"
        "</body></html>"
    )
    parsed = html_to_text(html)
    assert "SECTION 9.15 Financial Covenants." in parsed.text.split("\n")
    assert "(a) Consolidated Total Leverage Ratio." in parsed.text
    found = sections(chunk_document(parsed.text, "doc"))
    assert set(found) == {"9.15"}


def test_existing_styles_and_non_headings_are_unchanged():
    text = "\n".join([
        PROSE, "ARTICLE VII", "Section 7.11. Financial Covenants. The Borrower shall maintain the ratios.",
        "3.50 to 1.00", "Section 7.11 requires the Borrower to deliver a certificate.",
        "Notes 4.00X", "7.12 Fiscal Year. The fiscal year shall not change.",
    ])
    found = sections(chunk_document(text, "doc"))
    assert set(found) == {"7.11", "7.12"}
    assert "3.50 to 1.00" in found["7.11"].text
    assert "Section 7.11 requires" in found["7.11"].text


def test_section_heading_helper():
    assert section_heading("SECTION 9.15Financial Covenants.").group(1) == "9.15"
    assert section_heading("Section 7.11. Financial Covenants.").group(2) == "Financial Covenants"
    assert section_heading("3.50X") is None
    assert section_heading("1.00ABC") is None
