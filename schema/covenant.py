"""CovenantGuard V1 schema.

This is the contract between the pipeline stages:

    extraction agent  --(Covenant)-->  verifier  --(Covenant)-->  calculation engine
                                                                        |
                                                            (ComplianceResult)

Design rules
------------
* The LLM only ever produces `Covenant` / `DefinedTerm` objects (text -> structure).
* The calculation engine consumes them and produces `ComplianceResult`. No LLM math.
* Every extracted field must be traceable: `SourceCitation.quote` must be a verbatim
  substring of the agreement (checked by `SourceCitation.quote_in`).
* `extra="forbid"` so malformed LLM output fails loudly instead of being silently dropped.

V1 scope: max total leverage, min interest coverage, min liquidity.
Anything else is captured as OTHER_FINANCIAL and stored, but not computed.
"""

import json
import re
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class CovenantType(str, Enum):
    MAX_TOTAL_LEVERAGE = "max_total_leverage"          # Debt / EBITDA <= X
    MIN_INTEREST_COVERAGE = "min_interest_coverage"    # EBITDA / Interest >= X
    MIN_LIQUIDITY = "min_liquidity"                    # Cash (+ availability) >= $X
    OTHER_FINANCIAL = "other_financial"                # stored, not computed in V1


class Operator(str, Enum):
    LTE = "<="
    GTE = ">="


class Unit(str, Enum):
    RATIO = "ratio"   # e.g. 3.5 means 3.5x
    USD = "usd"       # absolute dollars


class TestFrequency(str, Enum):
    __test__ = False  # stop pytest from trying to collect this as a test class
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    OTHER = "other"


class MeasurementBasis(str, Enum):
    TRAILING_FOUR_QUARTERS = "trailing_four_quarters"  # typical for leverage / coverage
    POINT_IN_TIME = "point_in_time"                    # typical for liquidity
    OTHER = "other"


class ReviewStatus(str, Enum):
    AUTO_VERIFIED = "auto_verified"    # passed all automated verifier checks
    NEEDS_REVIEW = "needs_review"      # default: a human should look
    HUMAN_APPROVED = "human_approved"
    REJECTED = "rejected"


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    BREACH = "breach"
    INDETERMINATE = "indeterminate"    # inputs missing / definition unmappable


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


_TEXT_MAP = str.maketrans(
    {
        "\u2018": "'", "\u2019": "'",      # curly single quotes
        "\u201c": '"', "\u201d": '"',      # curly double quotes
        "\u2013": "-", "\u2014": "-",      # en / em dash
        "\u00a0": " ",                      # non-breaking space
    }
)


def normalize_text(text: str) -> str:
    """Lowercase, unify quotes/dashes, collapse whitespace. Used for quote matching."""
    return re.sub(r"\s+", " ", text.translate(_TEXT_MAP)).strip().lower()


# --------------------------------------------------------------------------- #
# Extraction-side models
# --------------------------------------------------------------------------- #
class SourceCitation(_Strict):
    section: str = Field(..., description='Where it appears, e.g. "Section 7.11(a)".')
    quote: str = Field(
        ..., min_length=10,
        description="Verbatim text from the agreement that supports the extracted fields.",
    )

    def quote_in(self, source_text: str) -> bool:
        """True if the quote appears in `source_text` (whitespace/quote-style tolerant)."""
        return normalize_text(self.quote) in normalize_text(source_text)


class DefinedTerm(_Strict):
    """A defined term the covenant depends on (e.g. 'Consolidated EBITDA')."""

    term: str
    definition: str = Field(..., description="Verbatim definition text from the agreement.")
    section: str = Field(..., description='e.g. "Section 1.01".')
    has_custom_adjustments: bool = Field(
        False,
        description="True if the definition has add-backs/adjustments that public XBRL "
        "data cannot reproduce (restructuring, non-cash charges, pro forma, etc.).",
    )
    adjustment_notes: list[str] = Field(default_factory=list)


class ThresholdStep(_Strict):
    """One threshold level. A step stays in force until the next step's effective_from."""

    effective_from: Optional[date] = Field(
        None, description="None = in force from the agreement's effective date (first step only)."
    )
    value: float = Field(..., gt=0, description="Ratio (3.5 = 3.5x) or dollars, per `Covenant.unit`.")


_EXPECTED_SHAPE = {
    CovenantType.MAX_TOTAL_LEVERAGE: (Operator.LTE, Unit.RATIO),
    CovenantType.MIN_INTEREST_COVERAGE: (Operator.GTE, Unit.RATIO),
    CovenantType.MIN_LIQUIDITY: (Operator.GTE, Unit.USD),
}


class Covenant(_Strict):
    covenant_id: str = Field(..., description='Stable id, e.g. "acme-lev-001".')
    covenant_type: CovenantType
    operator: Operator
    unit: Unit
    threshold_schedule: list[ThresholdStep] = Field(..., min_length=1)

    # For ratio covenants: numerator_term / denominator_term. For USD covenants: measure_term.
    # Each is the *defined term as written in the agreement*, not an XBRL tag.
    numerator_term: Optional[str] = None
    denominator_term: Optional[str] = None
    measure_term: Optional[str] = None

    test_frequency: TestFrequency = TestFrequency.QUARTERLY
    measurement_basis: MeasurementBasis = MeasurementBasis.TRAILING_FOUR_QUARTERS

    definitions: list[DefinedTerm] = Field(default_factory=list)
    citation: SourceCitation
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Anything the extractor could not resolve; surfaced to the reviewer.",
    )
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW

    # ---- validation ------------------------------------------------------- #
    @field_validator("threshold_schedule")
    @classmethod
    def _check_schedule(cls, steps: list[ThresholdStep]) -> list[ThresholdStep]:
        for i, step in enumerate(steps):
            if i > 0 and step.effective_from is None:
                raise ValueError("only the first threshold step may omit effective_from")
        dates = [s.effective_from for s in steps if s.effective_from is not None]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("threshold steps must be in strictly increasing date order")
        return steps

    @model_validator(mode="after")
    def _check_consistency(self) -> "Covenant":
        expected = _EXPECTED_SHAPE.get(self.covenant_type)
        if expected is not None and (self.operator, self.unit) != expected:
            raise ValueError(
                f"{self.covenant_type.value} must use operator {expected[0].value} "
                f"and unit {expected[1].value}"
            )
        if self.unit == Unit.RATIO and not (self.numerator_term and self.denominator_term):
            raise ValueError("ratio covenants require numerator_term and denominator_term")
        if self.unit == Unit.USD and not self.measure_term:
            raise ValueError("usd covenants require measure_term")
        return self

    # ---- convenience ------------------------------------------------------ #
    def threshold_on(self, as_of: date) -> float:
        """Threshold in force on `as_of` (handles step-downs)."""
        current: Optional[ThresholdStep] = None
        for step in self.threshold_schedule:
            if step.effective_from is None or step.effective_from <= as_of:
                current = step
            else:
                break
        if current is None:
            raise ValueError(f"No threshold in effect on {as_of} for {self.covenant_id}")
        return current.value

    @property
    def requires_approximation(self) -> bool:
        """True if any referenced definition has adjustments public data can't reproduce."""
        return any(d.has_custom_adjustments for d in self.definitions)


class Agreement(_Strict):
    """One credit agreement (or amendment) and the covenants extracted from it."""

    company_name: str
    ticker: Optional[str] = None
    cik: str = Field(..., description="SEC Central Index Key; zero-padded to 10 digits.")
    agreement_title: str
    agreement_date: Optional[date] = None
    is_amendment: bool = False
    filing_accession: str = Field(..., description='e.g. "0001234567-25-000123".')
    filing_date: date
    exhibit_url: str
    covenants: list[Covenant] = Field(default_factory=list)

    @field_validator("cik")
    @classmethod
    def _pad_cik(cls, v: str) -> str:
        if not v.isdigit() or len(v) > 10:
            raise ValueError("cik must be 1-10 digits")
        return v.zfill(10)

    @model_validator(mode="after")
    def _unique_ids(self) -> "Agreement":
        ids = [c.covenant_id for c in self.covenants]
        if len(ids) != len(set(ids)):
            raise ValueError("covenant_id values must be unique within an agreement")
        return self


# --------------------------------------------------------------------------- #
# Calculation-side models (produced by engine/calculator.py, never by an LLM)
# --------------------------------------------------------------------------- #
class InputValue(_Strict):
    """One number that went into a calculation, with full provenance."""

    label: str = Field(..., description='e.g. "Total Debt".')
    xbrl_tags: list[str] = Field(..., description="Tag(s) summed/used to produce the value.")
    value: float
    period_end: date
    accession: str = Field(..., description="Filing the XBRL fact came from.")


class ComplianceResult(_Strict):
    covenant_id: str
    period_end: date
    computed_value: Optional[float] = None
    threshold: float
    operator: Operator
    headroom_pct: Optional[float] = None
    status: ComplianceStatus
    inputs: list[InputValue] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=list, description="Every mapping/approximation made, in plain English."
    )
    approximate: bool = Field(
        False, description="True if custom definitions could not be fully reproduced."
    )

    @model_validator(mode="after")
    def _check_value_present(self) -> "ComplianceResult":
        if self.status != ComplianceStatus.INDETERMINATE and self.computed_value is None:
            raise ValueError("computed_value is required unless status is indeterminate")
        return self


def evaluate_threshold(operator: Operator, value: float, threshold: float) -> ComplianceStatus:
    """Pass/fail. Equality passes for both <= and >= covenants."""
    ok = value <= threshold if operator == Operator.LTE else value >= threshold
    return ComplianceStatus.COMPLIANT if ok else ComplianceStatus.BREACH


def headroom_pct(operator: Operator, value: float, threshold: float) -> float:
    """Headroom as a % of the threshold. Negative means breach.

    Note: this is headroom on the *ratio itself*. Lenders often quote "EBITDA cushion"
    (how far EBITDA can fall before breach), which is a different number. Add that
    separately if you want it.
    """
    if threshold == 0:
        raise ValueError("threshold must be non-zero")
    if operator == Operator.LTE:
        return (threshold - value) / threshold * 100
    return (value - threshold) / threshold * 100


if __name__ == "__main__":
    print(json.dumps(Covenant.model_json_schema(), indent=2))
