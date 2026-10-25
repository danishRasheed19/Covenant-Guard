"""Phase 2 LLM access: one small interface, a disk cache, and a Gemini adapter."""

from llm.errors import LLMError, LLMParseError, LLMRateLimited
from llm.factory import get_client
from llm.types import LLMResult

__all__ = ["LLMError", "LLMParseError", "LLMRateLimited", "LLMResult", "get_client"]
