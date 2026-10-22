"""Text normalization used when comparing pasted label text with parsed document text."""

import re

_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-",
        "\u00a0": " ", "\u200b": "",
    }
)


def normalize(text: str) -> str:
    """Lowercase; unify quotes and dashes; drop spaces just inside curly quotes; collapse whitespace."""
    text = re.sub("([\u201c\u2018])[ \t\u00a0]+", r"\1", text)
    text = re.sub("[ \t\u00a0]+([\u201d\u2019])", r"\1", text)
    return re.sub(r"\s+", " ", text.translate(_QUOTE_MAP)).strip().lower()
