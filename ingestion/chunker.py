"""Split parsed agreement text into structure-aware chunks (standard library only).

Chunk kinds
-----------
definition  one defined term (all of its paragraphs), taken from a "Defined Terms" section
section     the text of one numbered section, split into parts if it is long
attachment  a schedule, exhibit, annex or appendix
preamble    text before the first article or section heading

Every chunk carries its article, section number, section title, and a readable path such as
"ARTICLE VII NEGATIVE COVENANTS > 7.03 Financial Covenants", so retrieval can filter by section.
Table-of-contents rows are skipped. Not yet tested against real SEC files.
"""

import re
from dataclasses import asdict, dataclass

MAX_CHARS = 6000

ARTICLE = re.compile(r"^ARTICLE\s+([IVXLCDM]+|\d+)\b[\s.:\-\u2013\u2014]*(.*)$", re.IGNORECASE)
SECTION = re.compile(r"^(?:Section\s+)?(\d{1,2}\.\d{1,2})\.?\s+([A-Z][^.\n]{1,100}?)(?:\.(?:\s+(.*))?)?$")
ATTACHMENT = re.compile(r"^(SCHEDULE|EXHIBIT|ANNEX|APPENDIX)\s+([A-Z0-9][A-Z0-9.()\-]*)(?=\s|$)(.*)$")
DEFINITION = re.compile(
    r"^[\u201c\"]\s*([^\u201c\u201d\"]{1,120}?)\s*[\u201d\"]"
    r"(?:\s+(?:or|and)\s+[\u201c\"][^\u201c\u201d\"]{1,120}?[\u201d\"])*"
    r"\s*(?:means|has the meaning|have the meanings|shall mean|shall have the meaning)\b",
    re.IGNORECASE,
)
TOC_ROW = re.compile(
    r"^(?:ARTICLE\s+[IVXLCDM\d]+|(?:Section\s+)?\d{1,2}\.\d{1,2}|SCHEDULE|EXHIBIT|ANNEX)\b.*"
    r"(?:\|\s*|\.{2,}\s*|\s)\d{1,3}\s*$",
    re.IGNORECASE,
)


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


def is_toc_row(line: str) -> bool:
    return len(line) < 200 and bool(TOC_ROW.match(line))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


class _Block:
    def __init__(self, kind, article, number, title, term=""):
        self.kind, self.article, self.number, self.title, self.term = kind, article, number, title, term
        self.paragraphs = []


def chunk_document(text: str, doc_id: str, max_chars: int = MAX_CHARS) -> list:
    lines = [line for line in text.split("\n") if line.strip()]
    chunks = []
    used_ids = {}
    state = {"article": "", "number": "", "title": "", "in_definitions": False, "block": None}

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
            groups = [block.paragraphs]
            key = f"def-{_slug(block.term)}"
        else:
            groups, current, size = [], [], 0
            for paragraph in block.paragraphs:
                if current and size + len(paragraph) > max_chars:
                    groups.append(current)
                    current, size = [], 0
                current.append(paragraph)
                size += len(paragraph)
            if current:
                groups.append(current)
            key = {"section": block.number or "sec", "attachment": block.number, "preamble": "pre"}[block.kind]
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

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if is_toc_row(line):
            continue

        article_match = ARTICLE.match(line)
        if article_match and (line == line.upper() or not article_match.group(2)):
            flush()
            title = article_match.group(2).strip()
            if not title and i < len(lines):
                following = lines[i].strip()
                if following.isupper() and len(following) < 80 and not SECTION.match(following) and not is_toc_row(following):
                    title = following
                    i += 1
            state.update(article=f"ARTICLE {article_match.group(1).upper()} {title}".strip(),
                         number="", title="", in_definitions=False)
            continue

        section_match = SECTION.match(line)
        if section_match:
            number, title, rest = section_match.group(1), section_match.group(2).strip(), section_match.group(3)
            state.update(number=number, title=title,
                         in_definitions=bool(re.search(r"definition|defined terms", title, re.IGNORECASE)))
            start("section", number, title)
            if rest:
                state["block"].paragraphs.append(rest)
            continue

        attachment_match = ATTACHMENT.match(line)
        if attachment_match and len(line) < 120:
            label = f"{attachment_match.group(1)} {attachment_match.group(2)}"
            state.update(number=label, title=attachment_match.group(3).strip(" .-\u2013\u2014"), in_definitions=False)
            start("attachment", label, state["title"])
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
