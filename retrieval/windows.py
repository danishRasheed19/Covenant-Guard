"""Cut long chunk text into small overlapping windows ("children") for embedding.

Small embedding models read about 512 tokens (roughly 2,000 characters) and ignore the rest, but
sections run to 6,000 characters and definitions to about 10,000. Each window is embedded on its
own; search returns the whole chunk ("parent") that the best window belongs to.
"""

WINDOW_CHARS = 1200
WINDOW_OVERLAP = 200


def make_windows(text: str, size: int = WINDOW_CHARS, overlap: int = WINDOW_OVERLAP) -> list:
    """Split `text` into windows of at most `size` characters, overlapping by about `overlap`.

    Windows end at whitespace when there is one in the last 40% of the window, so words are not cut.
    Text that already fits in one window comes back as a single window.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    windows, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind(" ", start + int(size * 0.6), end), text.rfind("\n", start + int(size * 0.6), end))
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            windows.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
        while start < len(text) and not text[start - 1].isspace():   # start at a word boundary
            start += 1
    return windows
