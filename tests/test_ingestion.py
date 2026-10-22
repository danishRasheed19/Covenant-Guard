import json
import os

import pytest

from ingestion import edgar_client
from ingestion.chunker import chunk_document, is_toc_row
from ingestion.parser import html_to_text
from ingestion.run_ingest import check_against_label
from ingestion.textnorm import normalize

SAMPLE_HTML = """
<html><head><title>x</title><style>p {color:red}</style></head><body>
<div style="display:none">hidden xbrl header</div>
<p>FINANCING AGREEMENT dated as of June 24, 2024</p>
<table>
<tr><td>ARTICLE I</td><td>DEFINITIONS</td><td>1</td></tr>
<tr><td>1.01</td><td>Defined Terms</td><td>1</td></tr>
<tr><td>7.03</td><td>Financial Covenants</td><td>136</td></tr>
</table>
<p>ARTICLE I</p>
<p>DEFINITIONS</p>
<p><b>1.01 Defined Terms.</b> As used in this Agreement:</p>
<p>&ldquo; <b>Availability</b>&rdquo; means, at any time, the difference between (a) the commitment and (b) the outstanding principal.</p>
<p>&ldquo;<b>Total Leverage Ratio</b>&rdquo; means the ratio of (a) debt to (b) Consolidated EBITDA.</p>
<p>Notwithstanding the foregoing, the ratio is measured as follows.</p>
<p>&ldquo;Consolidated EBITDA&rdquo; means net income plus (a) <s>old addback</s> new addback.</p>
<p>1.02 Other Terms. Terms not defined have their GAAP meaning.</p>
<p>136</p>
<p>ARTICLE VII</p>
<p>NEGATIVE COVENANTS</p>
<p><b>7.03 Financial Covenants.</b> (a) Total Leverage Ratio. Permit the Total Leverage Ratio to be greater than the ratio below:</p>
<table>
<tr><td>Fiscal Quarter End</td><td>Total Leverage Ratio</td></tr>
<tr><td>September 30, 2024</td><td>6.20:1.00</td></tr>
<tr><td><span style="text-decoration: line-through">September 30, 2025</span></td><td>5.50:1.00</td></tr>
</table>
<p>SCHEDULE 1.01(A) EXISTING LIENS</p>
<p>None.</p>
<script>var x = 1;</script>
</body></html>
"""


def test_parser_drops_struck_text_and_hidden_content():
    parsed = html_to_text(SAMPLE_HTML)
    assert "old addback" not in parsed.text
    assert "new addback" in parsed.text
    assert "September 30, 2025" not in parsed.text  # struck via CSS
    assert "hidden xbrl header" not in parsed.text
    assert "var x" not in parsed.text
    assert parsed.struck_chars_dropped > 0
    kept = html_to_text(SAMPLE_HTML, drop_struck=False)
    assert "old addback" in kept.text


def test_parser_tables_quotes_and_page_numbers():
    text = html_to_text(SAMPLE_HTML).text
    assert "September 30, 2024 | 6.20:1.00" in text
    assert "\u201cAvailability\u201d means" in text      # no space inside the curly quotes
    assert "\n136\n" not in text                         # bare page number removed


def test_toc_rows_detected():
    assert is_toc_row("7.03 | Financial Covenants | 136")
    assert is_toc_row("ARTICLE I | DEFINITIONS | 1")
    assert not is_toc_row("7.03 Financial Covenants. (a) Total Leverage Ratio.")
    assert not is_toc_row("September 30, 2024 | 6.20:1.00")


def test_chunker_structure():
    text = html_to_text(SAMPLE_HTML).text
    chunks = chunk_document(text, "sample")
    definitions = {c.term: c for c in chunks if c.kind == "definition"}
    assert set(definitions) == {"Availability", "Total Leverage Ratio", "Consolidated EBITDA"}
    assert "Notwithstanding the foregoing" in definitions["Total Leverage Ratio"].text  # continuation paragraph kept
    assert "old addback" not in definitions["Consolidated EBITDA"].text

    covenant = next(c for c in chunks if c.section_number == "7.03")
    assert covenant.kind == "section" and covenant.section_title == "Financial Covenants"
    assert covenant.article.startswith("ARTICLE VII") and "NEGATIVE COVENANTS" in covenant.article
    assert "6.20:1.00" in covenant.text
    assert covenant.path.endswith("7.03 Financial Covenants")

    assert any(c.kind == "attachment" and c.section_number == "SCHEDULE 1.01(A)" for c in chunks)
    # the table of contents must not become chunks
    assert not any("| 136" in c.text for c in chunks)
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_long_section_is_split_but_definitions_are_not():
    body = "\n".join(f"({chr(97 + i)}) clause number {i} " + "x" * 400 for i in range(10))
    text = "ARTICLE VII\nNEGATIVE COVENANTS\n7.03 Financial Covenants.\n" + body
    chunks = chunk_document(text, "d", max_chars=1500)
    parts = [c for c in chunks if c.section_number == "7.03"]
    assert len(parts) > 1 and [c.part for c in parts] == list(range(1, len(parts) + 1))


def test_check_against_label():
    parsed = html_to_text(SAMPLE_HTML)
    chunks = chunk_document(parsed.text, "sample")
    label = {
        "covenants": [{
            "covenant_id": "c1",
            "citation": {"section": "Section 7.03", "quote": "PASTE: later"},
            "definitions": [
                {"term": "Availability", "definition": "\u201cAvailability\u201d means, at any time, the difference between (a) the commitment and (b) the outstanding principal."},
                {"term": "Not In Document", "definition": "PASTE: later"},
            ],
        }]
    }
    report = check_against_label(label, parsed.text, chunks)
    assert any(line.startswith("OK") and "section 7.03" in line for line in report)
    assert any(line.startswith("OK") and "pasted definition of 'Availability'" in line for line in report)
    assert any(line.startswith("MISS") and "Not In Document" in line for line in report)


def test_normalize_ignores_quote_style_and_inner_spaces():
    assert normalize("\u201c Availability\u201d  means") == normalize('"Availability" means')


def test_client_refuses_non_sec_urls_and_placeholder_agent():
    with pytest.raises(edgar_client.EdgarError):
        edgar_client.fetch("https://example.com/file.htm")
    with pytest.raises(edgar_client.EdgarError):
        edgar_client.fetch("http://www.sec.gov/file.htm")
    previous = os.environ.get("SEC_USER_AGENT")
    os.environ["SEC_USER_AGENT"] = "Your Name your.email@example.com"
    try:
        with pytest.raises(edgar_client.EdgarError):
            edgar_client.get_user_agent()
        os.environ["SEC_USER_AGENT"] = "Jane Doe jane@university.edu"
        assert edgar_client.get_user_agent() == "Jane Doe jane@university.edu"
    finally:
        if previous is None:
            os.environ.pop("SEC_USER_AGENT", None)
        else:
            os.environ["SEC_USER_AGENT"] = previous


def test_cache_path_is_stable():
    url = "https://www.sec.gov/Archives/edgar/data/1/2/ex10-1.htm"
    assert edgar_client.cache_path(url) == edgar_client.cache_path(url)
    assert edgar_client.cache_path(url).name.endswith("ex10-1.htm")
