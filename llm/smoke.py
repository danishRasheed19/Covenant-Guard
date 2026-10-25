"""Live check of the client:  python -m llm.smoke   (run it twice: the second run should say cached=True)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel  # noqa: E402

from llm import LLMError, get_client  # noqa: E402


class Found(BaseModel):
    ratio_name: str
    operator: str
    threshold: float
    quote: str


TEXT = ("Section 7.11 Financial Covenants. The Borrower shall not permit the Total Leverage Ratio as of the "
        "last day of any fiscal quarter to exceed 3.50 to 1.00.")


def main() -> int:
    try:
        client = get_client()
        result = client.generate_json(f"Extract the financial covenant. Quote the sentence exactly.\n\nTEXT:\n{TEXT}", Found,
                                      system="You extract facts from contracts. Answer only with JSON.")
    except LLMError as err:
        print(f"FAILED: {err}")
        return 1
    found = Found.model_validate(result.data)
    print(f"model={result.model} cached={result.cached} seconds={result.seconds:.1f} api_calls={client.calls}")
    print("usage:", result.usage)
    print("parsed:", found)
    print("quote is exact:", found.quote in TEXT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
