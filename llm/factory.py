"""Build a client from settings in the environment or in .env (repo root)."""

import os
from pathlib import Path

from llm.cache import DiskCache
from llm.errors import LLMError

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_setting(name: str, default: str = None, env_file: Path = REPO_ROOT / ".env"):
    if os.environ.get(name):
        return os.environ[name].strip()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return default


def get_client(provider: str = None, model: str = None, *, cache: bool = True, **options):
    """Settings: LLM_PROVIDER (default gemini), LLM_MODEL, GEMINI_API_KEY, LLM_TIMEOUT, LLM_CACHE_DIR."""
    provider = (provider or read_setting("LLM_PROVIDER", "gemini")).lower()
    model = model or read_setting("LLM_MODEL")
    disk = DiskCache(read_setting("LLM_CACHE_DIR", str(REPO_ROOT / "data" / "llm_cache"))) if cache else None
    if provider == "gemini":
        from llm.gemini import GeminiClient
        timeout = float(read_setting("LLM_TIMEOUT", "120"))
        options.setdefault("timeout", timeout)
        return GeminiClient(read_setting("GEMINI_API_KEY"), model, cache=disk, **options)
    raise LLMError(f"unknown LLM_PROVIDER {provider!r} (only 'gemini' is implemented)")
