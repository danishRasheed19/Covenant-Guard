from dataclasses import dataclass, field


@dataclass
class LLMResult:
    data: object                    # the parsed JSON answer
    text: str                       # the raw answer text
    usage: dict = field(default_factory=dict)   # token counts as reported by the provider
    model: str = ""
    provider: str = ""
    cached: bool = False            # True when the answer came from the disk cache (no API call made)
    seconds: float = 0.0
