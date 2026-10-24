"""Embedding models behind one small interface, so the model can be swapped.

* `HashEmbedder`: deterministic bag-of-words hashing. No download, no model. Used by tests and as a
  quick offline fallback; it only matches shared words, so it is NOT a semantic model.
* `FastEmbedEmbedder`: a real local model through `fastembed` (ONNX, no PyTorch). Default is
  BAAI/bge-small-en-v1.5 (384 dimensions, about 512 tokens). The first use downloads the model.
  Not yet tested against the real model.

`embedder.name` is stored with each loaded document; searching with a different model than the one
that loaded the document is refused, because vectors from different models are not comparable.
"""

import hashlib
import math
import re

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class HashEmbedder:
    def __init__(self, dim: int = 384):
        self.dim = dim
        self.name = f"hash-{dim}"

    def _embed(self, text: str) -> list:
        vector = [0.0] * self.dim
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm else vector

    def embed_documents(self, texts: list) -> list:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list:
        return self._embed(text)


class FastEmbedEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dim: int = 384, query_prefix: str = BGE_QUERY_PREFIX):
        self.model_name, self.dim, self.query_prefix = model_name, dim, query_prefix
        self.name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as err:
                raise RuntimeError("Install fastembed (pip install fastembed) or use --embedder hash") from err
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_documents(self, texts: list) -> list:
        return [[float(x) for x in vector] for vector in self._load().embed(texts)]

    def embed_query(self, text: str) -> list:
        return [float(x) for x in next(iter(self._load().embed([self.query_prefix + text])))]


def get_embedder(name: str):
    if name == "hash":
        return HashEmbedder()
    if name in ("bge-small", "BAAI/bge-small-en-v1.5"):
        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedder {name!r} (use 'bge-small' or 'hash')")
