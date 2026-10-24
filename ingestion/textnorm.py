"""Text normalization: the single place that decides when two pieces of text "match".

Used to compare label text, LLM-extracted quotes and parsed document text.
`schema.covenant` re-exports `normalize` as `normalize_text`, so there is only one
implementation.

Two levels:
* `normalize`: lowercase, unify quotes and dashes, collapse whitespace runs to one space.
* `squash`: `normalize`, then remove ALL whitespace. Source documents sometimes lack
  spaces ("providedthat") and an LLM will insert them, so quote checks use this level.
  A fabricated quote still fails because the letters have to match.
"""

import re

_QUOTE_MAP = str.maketrans(
    {
        "‘": "'", "’": "'",
        "“": '"', "”": '"',
        "–": "-", "—": "-",
        " ": " ", "​": "",
    }
)


def normalize(text: str) -> str:
    """Lowercase; unify quotes and dashes; drop spaces just inside curly quotes; collapse whitespace."""
    text = re.sub("([“‘])[ \t ]+", r"\1", text)
    text = re.sub("[ \t ]+([”’])", r"\1", text)
    return re.sub(r"\s+", " ", text.translate(_QUOTE_MAP)).strip().lower()


def squash(text: str) -> str:
    """`normalize`, then remove every whitespace character."""
    return re.sub(r"\s+", "", normalize(text))


def contains(haystack: str, needle: str) -> bool:
    """True if `needle` appears in `haystack`, ignoring case, quote style and all whitespace.

    An empty (or whitespace-only) needle never matches.
    """
    squashed = squash(needle)
    return bool(squashed) and squashed in squash(haystack)
