class LLMError(Exception):
    """A call to the model failed. `status` is the HTTP status (0 = network error or timeout)."""

    def __init__(self, message: str, status: int = None):
        super().__init__(message)
        self.status = status


class LLMRateLimited(LLMError):
    """The provider kept answering 429 (too many requests, or the free daily quota is used up)."""


class LLMParseError(LLMError):
    """The model answered, but the answer was not valid JSON (for example it was cut off)."""
