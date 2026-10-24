"""Split parsed agreement text into structure-aware chunks (standard library only).

Chunk kinds
-----------
definition  one defined term (all of its paragraphs), taken from a "Defined Terms" section
section     the text of one numbered section, split into parts if it is long
attachment  a schedule, exhibit, annex or appendix (top level, never inside an article)
preamble    text that is not inside a numbered section: the cover page and recitals before
            Article I, or an article's lead-in sentence before its first section (for example
            "So long as any Lender shall have any Commitment hereunder ...")

Every chunk carries its article, section number, section title, and a readable path such as
"ARTICLE VII NEGATIVE COVENANTS > 7.11 Financial Covenants" or "EXHIBIT C FORM OF COMPLIANCE
CERTIFICATE", so retrieval can filter by section.

Table of contents
-----------------
Credit agreements open with a table of contents that repeats every article and section heading,
followed by a list of schedules and exhibits. Treating those lines as headings files TOC debris
under the wrong article, so the whole TOC region is skipped:

* it starts at a "TABLE OF CONTENTS" line or the first TOC row (a heading ending in a page number)
  seen before the first body article (an amendment's own text may come first);
* it ends at the first long prose line (the "This Agreement is entered into ..." paragraph), or
  when an article heading that already appeared in the TOC appears again (the body's Article I).

Attachments
-----------
Once a schedule/exhibit/annex heading appears after a section, following lines belong to it, even
"1.1 ..." or "ARTICLE I" lines that are part of an exhibit form. A new body article ends attachment
mode only when (1) no article has been seen yet or its number is higher than any seen so far, and
(2) a section carrying that article's number follows it (ARTICLE I -> 1.01). This handles an
amendment whose annex holds the full conformed agreement, while a schedule titled in the article
heading style ("ARTICLE XIII" over a commitments table) stays an attachment.

Verified on three real credit agreements (one with a table-layout body, one an amendment with a
conformed copy) plus synthetic tests in tests/test_chunker_layout.py.
"""

import re
from dataclasses import asdict, dataclass

MAX_CHARS = 6000
TOC_PROSE_MIN_CHARS = 150      # a line this long inside the TOC region is body prose, not a TOC entry

ARTICLE = re.compile(r"^ARTICLE\s+([IVXLCDM]+|\d+)\b[\s.:\-–—]*(.*)$", re.IGNORECASE)
# Fallback heading pattern, used when the document's TOC does not know the section number.
# Title: up to 200 chars without a period (long titles occur in syndicated agreements).
# Terminator: one or more periods (typos like "Judgments.." occur), then optional whitespace,
# since some filings drop the space after the title ("Notes.The obligation ...").
# A title is either text starting with a capital, or "[Reserved]" (placeholder sections are common).
# The word "Section" may be in any case ("SECTION 9.15" is common).
SECTION = re.compile(
    r"^(?:(?i:section)\s+)?(\d{1,2}\.\d{1,2})\.?\s+([A-Z][^.\n]{1,200}?|\[(?i:reserved)\])(?:\.+\s*(.*))?$"
)
# Title glued to the number or to its period ("SECTION 9.15Financial Covenants.", "Section 9.15.Notes").
# Only accepted after the word "Section", so a bare "3.50X" in a table is never a heading.
SECTION_GLUED = re.compile(
    r"^(?i:section)\s+(\d{1,2}\.\d{1,2})\.?([A-Z][^.\n]{1,200}?|\[(?i:reserved)\])(?:\.+\s*(.*))?$"
)
# The heading split over two lines: "SECTION 9.15" alone, then the title on the next line.
SECTION_BARE = re.compile(r"^(?i:section)\s+(\d{1,2}\.\d{1,2})\.?$")
SECTION_TITLE_LINE = re.compile(r"^([A-Z][^.\n]{1,200}?|\[(?i:reserved)\])(?:\.+\s*(.*))?$")
SECTION_NUMBER = re.compile(r"^(?:(?i:section)\s+)?(\d{1,2})\.(\d{1,2})\.?\s*")
# A TOC row for a section: "5.11 | Margin Stock | 76" or "5.11 Margin Stock ......... 76".
TOC_SECTION_ENTRY = re.compile(
    r"^(?:Section\s+)?(\d{1,2})\.(\d{1,2})\.?\s*(?:\|\s*)?(.+?)\s*(?:\|\s*|\.{2,}\s*|\s)\d{1,3}\s*$",
    re.IGNORECASE,
)
ATTACHMENT = re.compile(r"^(SCHEDULE|EXHIBIT|ANNEX|APPENDIX)\s+([A-Z0-9][A-Z0-9.()\-]*)(?=\s|$)(.*)$")
DEFINITION = re.compile(
    r"^[“\"]\s*([^“”\"]{1,120}?)\s*[”\"]"
    r"(?:\s+(?:or|and)\s+[“\"][^“”\"]{1,120}?[”\"])*"
    r"\s*(?:means|has the meaning|have the meanings|shall mean|shall have the meaning)\b",
    re.IGNORECASE,
)
TOC_ROW = re.compile(
    r"^(?:ARTICLE\s+[IVXLCDM\d]+|(?:Section\s+)?\d{1,2}\.\d{1,2}|SCHEDULE|EXHIBIT|ANNEX)\b.*"
    r"(?:\|\s*|\.{2,}\s*|\s)\d{1,3}\s*$",
    re.IGNORECASE,
)
# The heading may have a page number or footnote mark glued on ("TABLE OF CONTENTS1").
TOC_TITLE = re.compile(r"^(?:TABLE OF CONTENTS|CONTENTS)\s*\d{0,3}$", re.IGNORECASE)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    kind: str
    article: str
    section_number: str
    section_title: str
    term: str
    part: int
    path: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def section_heading(line: str):
    """Match object (groups: number, title, rest of line) if `line` is a one-line section heading."""
    return SECTION.match(line) or SECTION_GLUED.match(line)


def is_toc_row(line: str) -> bool:
    return len(line) < 200 and bool(TOC_ROW.match(line))


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _article_number(label: str) -> int:
    """'VII' -> 7, '12' -> 12."""
    label = label.upper()
    if label.isdigit():
        return int(label)
    total = 0
    for current, following in zip(label, label[1:] + " "):
        value = _ROMAN[current]
        total += -value if _ROMAN.get(following, 0) > value else value
    return total


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _split_long(paragraph: str, max_chars: int) -> list:
    """Split one paragraph that is longer than max_chars, preferring sentence ends ("; " or ". ")."""
    pieces = []
    while len(paragraph) > max_chars:
        window = paragraph[:max_chars]
        cut = max(window.rfind(". "), window.rfind("; "))
        cut = cut + 1 if cut > max_chars // 2 else max_chars   # no good boundary: hard cut
        pieces.append(paragraph[:cut].rstrip())
        paragraph = paragraph[cut:].lstrip()
    if paragraph:
        pieces.append(paragraph)
    return pieces


def _group(paragraphs: list, max_chars: int) -> list:
    """Pack paragraphs into groups whose joined text ("\\n".join) is at most max_chars."""
    groups, current, size = [], [], 0
    for paragraph in paragraphs:
        for piece in _split_long(paragraph, max_chars):
            added = len(piece) + (1 if current else 0)   # +1 for the joining newline
            if current and size + added > max_chars:
                groups.append(current)
                current, size, added = [], 0, len(piece)
            current.append(piece)
            size += added
    if current:
        groups.append(current)
    return groups


class _Block:
    def __init__(self, kind, article, number, title, term=""):
        self.kind, self.article, self.number, self.title, self.term = kind, article, number, title, term
        self.paragraphs = []


def chunk_document(text: str, doc_id: str, max_chars: int = MAX_CHARS) -> list:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    chunks = []
    used_ids = {}
    state = {
        "article": "", "number": "", "title": "", "in_definitions": False, "block": None,
        "toc": "before",          # before -> inside -> done
        "toc_articles": set(),    # article labels seen in the TOC, e.g. {"I", "II"}
        "body_started": False,    # a real section heading has been seen
        "in_attachments": False,  # inside schedules/exhibits/annexes
        "max_article": 0,         # highest body article number seen so far
        "toc_titles": {},         # (major, minor) -> section title, read from the table of contents
    }

    def make_id(key: str, part: int) -> str:
        base = f"{doc_id}:{key}:{part}"
        used_ids[base] = used_ids.get(base, 0) + 1
        return base if used_ids[base] == 1 else f"{base}-{used_ids[base]}"

    def path_for(block) -> str:
        pieces = [block.article, f"{block.number} {block.title}".strip()]
        if block.term:
            pieces.append(block.term)
        return " > ".join(p for p in pieces if p)

    def flush():
        block = state["block"]
        state["block"] = None
        if block is None or not block.paragraphs:
            return
        if block.kind == "definition":
            groups = [block.paragraphs]          # definitions are never split
            key = f"def-{_slug(block.term)}"
        else:
            groups = _group(block.paragraphs, max_chars)
            key = {"section": block.number or "sec", "attachment": _slug(block.number), "preamble": "pre"}[block.kind]
        for part, group in enumerate(groups, start=1):
            chunks.append(
                Chunk(
                    chunk_id=make_id(key, part), doc_id=doc_id, kind=block.kind, article=block.article,
                    section_number=block.number, section_title=block.title, term=block.term,
                    part=part, path=path_for(block), text="\n".join(group),
                )
            )

    def start(kind, number="", title="", term=""):
        flush()
        state["block"] = _Block(kind, state["article"], number, title, term)

    def is_title_line(line: str) -> bool:
        return (line.isupper() and len(line) < 120 and not section_heading(line) and not SECTION_BARE.match(line)
                and not ATTACHMENT.match(line) and not ARTICLE.match(line) and not is_toc_row(line))

    def heading_title(i: int, max_lines: int = 3) -> tuple:
        """All-caps short lines right after a bare heading ("ARTICLE IV", "EXHIBIT C") form its title.
        Long titles wrap onto several lines, so up to max_lines are joined.
        Returns (title, index of the first line after the title)."""
        parts = []
        while i < len(lines) and len(parts) < max_lines and is_title_line(lines[i]):
            parts.append(lines[i])
            i += 1
        return " ".join(parts), i

    def remember_toc_entry(line: str):
        match = TOC_SECTION_ENTRY.match(line)
        if match:
            title = match.group(3).strip().rstrip(".").strip()
            if title:
                state["toc_titles"][(int(match.group(1)), int(match.group(2)))] = title

    def match_section(line: str):
        """Return (number, title, rest) if the line is a section heading, else None.

        First choice: the document's own TOC. If the line starts with a section number whose TOC title
        follows it, the title ends exactly there, whatever punctuation follows ("5.11 Margin Stock None
        of the ..." has no period at all). Fallback: the SECTION pattern."""
        number_match = SECTION_NUMBER.match(line)
        if number_match:
            key = (int(number_match.group(1)), int(number_match.group(2)))
            toc_title = state["toc_titles"].get(key)
            if toc_title:
                words = r"\s*".join(re.escape(w) for w in toc_title.split())
                title_match = re.compile(words + r"(?![A-Za-z0-9])", re.IGNORECASE).match(line, number_match.end())
                if title_match:
                    number = f"{number_match.group(1)}.{number_match.group(2)}"
                    rest = line[title_match.end():].lstrip(" .:;-\u2013\u2014")
                    return number, title_match.group(0), rest
        match = section_heading(line)
        if match:
            return match.group(1), match.group(2).strip(), match.group(3)
        return None

    def has_own_sections(i: int, number: int, window: int = 80) -> bool:
        """True if a section numbered "<number>.x" follows before the next article or attachment heading.
        A real body article always contains its own numbered sections; a schedule or exhibit title that
        happens to use the article heading style does not."""
        for following in lines[i:i + window]:
            match = section_heading(following)
            if match:
                return int(match.group(1).split(".")[0]) == number
            if ATTACHMENT.match(following) or ARTICLE.match(following):
                return False
        return False

    def in_toc(line: str) -> bool:
        """Update the TOC state for this line; return True if the line belongs to the TOC (skip it)."""
        if state["toc"] == "before":
            if not state["max_article"] and (TOC_TITLE.match(line) or is_toc_row(line)):
                state["toc"] = "inside"
            else:
                return False
        if state["toc"] != "inside":
            return False
        article_match = ARTICLE.match(line)
        if article_match and not is_toc_row(line) and article_match.group(1).upper() in state["toc_articles"]:
            state["toc"] = "done"            # the body repeats a TOC article heading: body starts here
            return False
        if len(line) >= TOC_PROSE_MIN_CHARS and not is_toc_row(line):
            state["toc"] = "done"            # first real paragraph after the TOC (parties, recitals)
            return False
        if article_match:
            state["toc_articles"].add(article_match.group(1).upper())
        return True

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if in_toc(line) or is_toc_row(line):
            remember_toc_entry(line)
            continue

        attachment_match = ATTACHMENT.match(line)
        # Before the first section heading, "EXHIBIT 10.1" is the SEC filing stamp on the cover, not an attachment.
        if attachment_match and len(line) < 120 and state["body_started"]:
            label = f"{attachment_match.group(1)} {attachment_match.group(2)}"
            title = attachment_match.group(3).strip(" .-:|–—")
            title_line = ""
            if not title:
                title_line, i = heading_title(i)
                title = title_line
            # Attachments are top-level parts of the document: drop the article context.
            state.update(article="", number=label, title=title, in_definitions=False, in_attachments=True)
            start("attachment", label, title)
            if title_line:
                state["block"].paragraphs.append(title_line)   # keep the title in the text for search
            continue

        article_match = ARTICLE.match(line)
        is_article_heading = bool(article_match) and (line == line.upper() or not article_match.group(2))
        if state["in_attachments"] and is_article_heading:
            # A new body article ends attachment mode. This happens when an amendment's annex holds the
            # conformed agreement (annex first, then Article I). A form inside an exhibit that restarts
            # at "ARTICLE I" after the body's Article X is not higher than X, so it stays in the exhibit.
            number = _article_number(article_match.group(1))
            if (not state["max_article"] or number > state["max_article"]) and has_own_sections(i, number):
                state["in_attachments"] = False

        if state["in_attachments"]:
            # Inside exhibits, "1.1 Assignment" or "ARTICLE I" belong to the exhibit form, not the agreement.
            if state["block"] is None:
                start("attachment", state["number"], state["title"])
            state["block"].paragraphs.append(line)
            continue

        if is_article_heading:
            flush()
            state["max_article"] = max(state["max_article"], _article_number(article_match.group(1)))
            title = article_match.group(2).strip()
            if not title:
                title, i = heading_title(i)
            state.update(article=f"ARTICLE {article_match.group(1).upper()} {title}".strip(),
                         number="", title="", in_definitions=False)
            continue

        section_match = match_section(line)
        bare_match = SECTION_BARE.match(line) if not section_match else None
        if bare_match and i < len(lines):
            title_match = SECTION_TITLE_LINE.match(lines[i])
            if title_match and not ARTICLE.match(lines[i]) and not ATTACHMENT.match(lines[i]):
                section_match = (bare_match.group(1), title_match.group(1).strip(), title_match.group(2))
                i += 1
        if section_match:
            number, title, rest = section_match
            state.update(number=number, title=title, body_started=True,
                         in_definitions=bool(re.search(r"definition|defined terms", title, re.IGNORECASE)))
            start("section", number, title)
            if rest:
                state["block"].paragraphs.append(rest)
            elif title.lower() == "[reserved]":
                # Keep a placeholder chunk so the section numbering has no false gaps.
                state["block"].paragraphs.append(title)
            continue

        definition_match = DEFINITION.match(line)
        if state["in_definitions"] and definition_match:
            start("definition", state["number"], state["title"], term=definition_match.group(1).strip())
            state["block"].paragraphs.append(line)
            continue

        if state["block"] is None:
            kind = "section" if state["number"] else "preamble"
            start(kind, state["number"], state["title"])
        state["block"].paragraphs.append(line)

    flush()
    return chunks
