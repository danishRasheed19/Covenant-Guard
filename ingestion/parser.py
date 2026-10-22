"""Turn an SEC exhibit's HTML into clean, structured plain text (standard library only).

What it does
------------
* One line per paragraph; table rows become one line with cells joined by " | ".
  Exception: a two-cell row whose first cell is only an enumerator ("3.1", "(a)", "(iv)") is a
  hanging indent built with a table, so it becomes "3.1 text" like an ordinary paragraph.
* Drops struck-through text (<s>, <strike>, <del>, or CSS line-through), so a "conformed copy"
  of an amended agreement reads as the current text instead of merging deleted and inserted words.
* Ignores scripts, styles, <head>, and anything hidden with display:none (inline XBRL headers).
* Removes lines that are only a page number (arabic or lowercase roman, e.g. "-iv-"), lone "Page"
  column headers from the table of contents, and the stray space after an opening curly quote.

Limits: a table row containing only a bare number or a lowercase roman numeral is treated as a
page number and dropped. Verified on three real credit agreements plus synthetic tests.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

VOID = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr", "source", "track", "embed", "param"}
BLOCK = {
    "p", "div", "li", "ul", "ol", "table", "thead", "tbody", "tfoot", "h1", "h2", "h3", "h4", "h5", "h6",
    "center", "blockquote", "pre", "section", "article", "header", "footer", "address", "dl", "dt", "dd",
}
SKIP_TAGS = {"script", "style", "head", "title", "noscript"}
STRIKE_TAGS = {"s", "strike", "del"}
CELLS = {"td", "th"}

_WHITESPACE = re.compile(r"[ \t\r\n\f\v\u00a0\u2009\u200a\u200b]+")
# A line that is only a page marker: "12", "- 12 -", or a lowercase roman numeral like "-iv-"
# (front-matter pages such as the table of contents). Lowercase only, so a heading "I" survives.
_PAGE_NUMBER = re.compile(r"^\s*[-\u2013]?\s*(?:\d{1,3}|[ivxl]{1,7})\s*[-\u2013]?\s*$")
# A cell that holds only a paragraph enumerator: "3.1", "Section 3.1", "1.", "(a)", "(iv)", "(A)", "(1)".
# Word-generated legal HTML often builds hanging indents as a two-cell table row: number | text.
_ENUMERATOR_CELL = re.compile(
    r"^(?:(?:Section\s+)?\d{1,2}\.\d{1,2}\.?|\d{1,2}\.|\((?:[a-z]{1,2}|[ivxlcdm]{1,6}|[A-Z]{1,2}|\d{1,3})\))$"
)
# The "Page" column header that table-of-contents tables repeat at the top of every page.
_PAGE_HEADER = re.compile(r"^\s*page\s*$", re.IGNORECASE)


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


@dataclass
class ParsedDocument:
    text: str
    struck_chars_dropped: int


class _Extractor(HTMLParser):
    def __init__(self, drop_struck: bool):
        super().__init__(convert_charrefs=True)
        self.drop_struck = drop_struck
        self.stack = []            # entries: (tag, skipped, struck)
        self.skip_depth = 0
        self.struck_depth = 0
        self.buffer = []
        self.paragraphs = []
        self.row_cells = None
        self.in_cell = False
        self.struck_chars = 0

    # ---- helpers ---------------------------------------------------------- #
    def _flush_paragraph(self):
        text = _collapse("".join(self.buffer))
        self.buffer = []
        if text:
            self.paragraphs.append(text)

    def _break(self):
        if self.in_cell:
            self.buffer.append(" ")
        else:
            self._flush_paragraph()

    def _end_cell(self):
        text = _collapse("".join(self.buffer))
        self.buffer = []
        self.in_cell = False
        if text:
            if self.row_cells is not None:
                self.row_cells.append(text)
            else:
                self.paragraphs.append(text)

    def _end_row(self):
        if self.in_cell:
            self._end_cell()
        if self.row_cells:
            cells = self.row_cells
            if len(cells) == 2 and _ENUMERATOR_CELL.match(cells[0]):
                # Hanging-indent layout, not a data table: "(a) | text" reads as "(a) text".
                self.paragraphs.append(f"{cells[0]} {cells[1]}")
            else:
                self.paragraphs.append(" | ".join(cells))
        self.row_cells = None

    # ---- HTMLParser hooks ------------------------------------------------- #
    def handle_starttag(self, tag, attrs):
        style = (dict(attrs).get("style") or "").lower().replace(" ", "")
        if tag == "br":
            self._break()
            return
        if tag in VOID:
            return
        struck = self.drop_struck and (tag in STRIKE_TAGS or "line-through" in style)
        skipped = tag in SKIP_TAGS or "display:none" in style or struck
        self.stack.append((tag, skipped, struck))
        if skipped:
            self.skip_depth += 1
        if struck:
            self.struck_depth += 1

        if tag == "tr":
            self._flush_paragraph()
            self.row_cells = []
        elif tag in CELLS:
            if self.in_cell:
                self._end_cell()
            self._flush_paragraph()
            self.in_cell = True
        elif tag in BLOCK:
            self._break()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        while len(self.stack) > index:
            _, skipped, struck = self.stack.pop()
            if skipped:
                self.skip_depth -= 1
            if struck:
                self.struck_depth -= 1

        if tag in CELLS:
            self._end_cell()
        elif tag == "tr":
            self._end_row()
        elif tag in BLOCK:
            self._break()

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.buffer.append(data)
        elif self.struck_depth > 0:
            self.struck_chars += len(data.strip())


def html_to_text(html: str, drop_struck: bool = True) -> ParsedDocument:
    extractor = _Extractor(drop_struck)
    extractor.feed(html)
    extractor.close()
    extractor._end_row()
    extractor._flush_paragraph()

    lines = [line for line in extractor.paragraphs if not (_PAGE_NUMBER.match(line) or _PAGE_HEADER.match(line))]
    text = "\n".join(lines)
    text = re.sub("([\u201c\u2018])[ \t]+", r"\1", text)
    text = re.sub("[ \t]+([\u201d])", r"\1", text)
    return ParsedDocument(text=text, struck_chars_dropped=extractor.struck_chars)
