"""Disk cache for model answers.

Every call is stored under a key made from everything that can change the answer: provider, model,
system text, prompt, schema and temperature. Asking the same thing again reads the file instead of
calling the API, which keeps evaluation runs free, fast and repeatable. Failures are never cached.
"""

import hashlib
import json
import os
import time
from pathlib import Path


def cache_key(provider: str, model: str, system, prompt: str, schema: dict, temperature: float) -> str:
    payload = json.dumps(
        {"provider": provider, "model": model, "system": system or "", "prompt": prompt,
         "schema": schema, "temperature": temperature},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str):
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None            # unreadable file: behave as a miss, the next put overwrites it

    def put(self, key: str, record: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {**record, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, path)      # atomic: a crash never leaves a half-written cache file
