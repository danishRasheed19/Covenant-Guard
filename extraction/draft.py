"""The shape the LLM is asked to fill, and the conversion into the strict `Covenant` schema.

The model fills a deliberately SIMPLE draft (closed lists of choices, dates as text, no cross-field
rules). Code then converts it into `schema.Covenant`, which enforces every rule (operator/unit match
the type, ordered schedule, ratio needs numerator and denominator, ...). Anything that fails is
reported as readable errors so the model can be asked to repair its answer once.
"""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from schema import Covenant

CODES = {"max_total_leverage": "lev", "min_interest_coverage": "icr", "min_liquidity": "liq", "other_financial": "oth"}


class DraftStep(BaseModel):
    effective_from: Optional[str]      # "YYYY-MM-DD", or null for the first level
    value: float
    condition: Optional[str]           # non-date condition ("during the Covenant Adjustment Period"), else null


class DraftCovenant(BaseModel):
    covenant_type: Literal["max_total_leverage", "min_interest_coverage", "min_liquidity", "other_financial"]
    operator: Literal["<=", ">="]
    unit: Literal["ratio", "usd"]
    threshold_steps: list[DraftStep]
    numerator_term: Optional[str]
    denominator_term: Optional[str]
    measure_term: Optional[str]
    test_frequency: Literal["monthly", "quarterly", "annual", "other"]
    measurement_basis: Literal["trailing_four_quarters", "trailing_twelve_months", "point_in_time", "other"]
    section: str
    quote: str
    defined_terms_used: list[str]
    ambiguities: list[str]


class Draft(BaseModel):
    covenants: list[DraftCovenant]


def _parse_date(text: Optional[str]) -> Optional[date]:
    if text is None or not str(text).strip():
        return None
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError as err:
        raise ValueError(f"effective_from {text!r} is not a YYYY-MM-DD date") from err


def draft_to_covenant(draft: DraftCovenant, covenant_id: str) -> tuple:
    """Return (Covenant, notes). Raises ValueError with a readable message if the draft breaks a schema rule.

    Steps that carry a non-date `condition` are NOT put in the schedule (a dated schedule cannot express
    them); they become ambiguities so a reviewer sees them.
    """
    ambiguities = list(draft.ambiguities)
    steps = []
    for step in draft.threshold_steps:
        if step.condition and step.condition.strip():
            ambiguities.append(f"Conditional threshold {step.value:g} not scheduled (condition: {step.condition.strip()}).")
            continue
        steps.append({"effective_from": _parse_date(step.effective_from), "value": step.value})
    if not steps:
        raise ValueError("no unconditional threshold step was found")
    try:
        covenant = Covenant.model_validate({
            "covenant_id": covenant_id,
            "covenant_type": draft.covenant_type,
            "operator": draft.operator,
            "unit": draft.unit,
            "threshold_schedule": steps,
            "numerator_term": draft.numerator_term or None,
            "denominator_term": draft.denominator_term or None,
            "measure_term": draft.measure_term or None,
            "test_frequency": draft.test_frequency,
            "measurement_basis": draft.measurement_basis,
            "citation": {"section": draft.section, "quote": draft.quote},
            "ambiguities": ambiguities,
        })
    except ValidationError as err:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors())
        raise ValueError(problems) from err
    return covenant, {"defined_terms_used": [t for t in draft.defined_terms_used if t and t.strip()]}
