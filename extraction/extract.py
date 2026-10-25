"""The extraction agent: evidence -> LLM draft -> strict Covenant -> verification.

    extract_covenants(client, evidence, id_prefix="mec-2026", source_text=text)

One LLM call finds all covenants in the evidence. Drafts that break a schema rule get ONE repair
call that shows the model its own answer and the exact errors. Nothing is guessed in code: the
verifier decides afterwards whether each covenant is rejected, needs review, or is auto-verified.
"""

import json
from dataclasses import dataclass, field

from extraction.draft import CODES, Draft, draft_to_covenant
from extraction.prompts import SYSTEM, build_prompt, build_repair_prompt
from extraction.verify import Verification, verify_covenant
from schema import Covenant


@dataclass
class Outcome:
    covenant: Covenant
    verification: Verification
    defined_terms_used: list = field(default_factory=list)


@dataclass
class ExtractionResult:
    outcomes: list
    errors: list = field(default_factory=list)       # drafts that could not be converted even after repair
    api_calls: int = 0
    cached_calls: int = 0
    evidence_ids: list = field(default_factory=list)


def _convert_all(draft: Draft, id_prefix: str) -> tuple:
    converted, errors, counters = [], [], {}
    for index, item in enumerate(draft.covenants, start=1):
        code = CODES[item.covenant_type]
        counters[code] = counters.get(code, 0) + 1
        covenant_id = f"{id_prefix}-{code}-{counters[code]:03d}"
        try:
            covenant, notes = draft_to_covenant(item, covenant_id)
            converted.append((covenant, notes))
        except ValueError as err:
            errors.append(f"covenants[{index - 1}] ({item.covenant_type}): {err}")
    return converted, errors


def extract_covenants(client, evidence: list, *, id_prefix: str, source_text: str, max_repairs: int = 1) -> ExtractionResult:
    evidence_text = "\n".join(e.text for e in evidence)
    evidence_meta = "\n".join(f"{e.section_number} {e.path}" for e in evidence)
    result = ExtractionResult(outcomes=[], evidence_ids=[e.chunk_id for e in evidence])
    prompt = build_prompt(evidence)

    def ask(text: str) -> Draft:
        answer = client.generate_json(text, Draft, system=SYSTEM)
        result.cached_calls += int(answer.cached)
        result.api_calls += int(not answer.cached)
        return Draft.model_validate(answer.data), answer.text

    draft, raw = ask(prompt)
    converted, errors = _convert_all(draft, id_prefix)
    for _ in range(max_repairs):
        if not errors:
            break
        draft, raw = ask(build_repair_prompt(prompt, raw, errors))
        converted, errors = _convert_all(draft, id_prefix)

    result.errors = errors
    for covenant, notes in converted:
        verification = verify_covenant(covenant, source_text, evidence_text, evidence_meta)
        covenant = covenant.model_copy(update={"review_status": verification.status})
        result.outcomes.append(Outcome(covenant, verification, notes["defined_terms_used"]))
    return result


def outcome_to_dict(outcome: Outcome) -> dict:
    return {**json.loads(outcome.covenant.model_dump_json()),
            "verification": {"problems": outcome.verification.problems, "warnings": outcome.verification.warnings,
                             "notes": outcome.verification.notes},
            "defined_terms_used": outcome.defined_terms_used}
