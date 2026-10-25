"""Check an extracted covenant against the source text with plain code (no LLM).

problems  -> the covenant cannot be trusted: it is marked `rejected`
warnings  -> worth a human look: marked `needs_review`
notes     -> information only (the extractor's ambiguities, such as an equity cure); they do NOT change the status
Nothing found -> `auto_verified`.
"""

import re
from dataclasses import dataclass, field

from schema import Covenant, ReviewStatus, Unit


@dataclass
class Verification:
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def status(self) -> ReviewStatus:
        if self.problems:
            return ReviewStatus.REJECTED
        if self.warnings:
            return ReviewStatus.NEEDS_REVIEW
        return ReviewStatus.AUTO_VERIFIED


def number_forms(value: float, unit: Unit) -> list:
    forms = {f"{value:.2f}", f"{value:.1f}", f"{value:g}"}
    if unit == Unit.USD:
        forms |= {f"{value:,.0f}", f"{value:,.2f}"}
        if value >= 1e6:
            forms |= {f"{value / 1e6:g} million", f"{value / 1e6:.1f} million"}
    return sorted(forms, key=len, reverse=True)


def number_in_text(value: float, unit: Unit, text: str) -> bool:
    for form in number_forms(value, unit):
        if re.search(r"(?<![\d.,])" + re.escape(form) + r"(?!\d)", text):
            return True
    return False


def verify_covenant(covenant: Covenant, source_text: str, evidence_text: str, evidence_meta: str = "") -> Verification:
    """`evidence_meta` is the section numbers and headings of the evidence chunks. A chunk's section number lives
    there, not in its text, so the section check looks in both."""
    result = Verification()
    if not covenant.citation.quote_in(source_text):
        result.problems.append("the quote does not appear in the agreement text")
    for step in covenant.threshold_schedule:
        if not number_in_text(step.value, covenant.unit, evidence_text):
            result.problems.append(f"threshold {step.value:g} does not appear in the evidence given to the model")
    section_number = re.search(r"\d+\.\d+", covenant.citation.section)
    if section_number and section_number.group() not in evidence_text and section_number.group() not in evidence_meta:
        result.warnings.append(f"cited section {section_number.group()} was not found in the evidence")
    for text in covenant.ambiguities:
        if "not scheduled" in text:     # a threshold level was left out of the schedule: a human must look
            result.warnings.append(text)
        else:
            result.notes.append(text)
    if len(covenant.threshold_schedule) == 1 and len(covenant.citation.quote) < 20:
        result.warnings.append("very short quote")
    return result
