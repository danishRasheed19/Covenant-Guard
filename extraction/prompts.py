"""Prompt text for covenant extraction. Kept in one place so it is easy to read, review and version."""

SYSTEM = """You read excerpts of a credit agreement and extract its FINANCIAL MAINTENANCE COVENANTS into JSON.
You only read and copy; you never calculate. Use only the evidence given. If something is not in the evidence, do not guess."""

INSTRUCTIONS = """What to extract
- A financial maintenance covenant is a ratio or amount the Borrower must satisfy at regular test dates (usually the end of each fiscal quarter, sometimes monthly).
- Do NOT extract: interest-rate pricing grid levels, tests that only apply when the Borrower wants to do something (incur debt, make an acquisition, pay a dividend), conditions to borrowing, or definitions on their own.
- If the evidence contains no such covenant, return an empty list.

For each covenant
- covenant_type: max_total_leverage (debt-to-EBITDA style maximum, incl. total or net leverage), min_interest_coverage (EBITDA-to-interest style minimum), min_liquidity (minimum cash or cash plus availability, in dollars), or other_financial (anything else, such as fixed charge coverage or debt service coverage).
- operator: "<=" for a maximum, ">=" for a minimum. unit: "ratio" (3.50 to 1.00 means value 3.5) or "usd".
- threshold_steps: every level and when it starts. The first step has effective_from null, UNLESS the agreement says testing starts at a given period ("commencing with the month ending April 30, 2026", "beginning with the fiscal quarter ending June 30, 2026"): then the first step's effective_from is the FIRST DAY of that period (2026-04-01 in both examples). For a later step, effective_from is the FIRST DAY of the first fiscal quarter tested at the new level, as YYYY-MM-DD (the day after the previous test date). Example: "for the fiscal quarter ending June 30, 2026 and each quarter thereafter" starts 2026-04-01. If a level depends on an event or a defined period instead of a date, put that condition in `condition` (and mention it in ambiguities) and do not invent a date; otherwise condition is null.
- numerator_term / denominator_term: for ratios, the defined terms as written in the agreement (for example "Consolidated Total Indebtedness", "Consolidated EBITDA"). For a ratio whose numerator is a formula, copy the formula in words as written. measure_term: for usd covenants only (for example "Liquidity"). Use null for fields that do not apply.
- test_frequency and measurement_basis: as stated in the agreement (point_in_time for liquidity, trailing_four_quarters for most ratios).
- section: the section reference where the covenant is stated (for example "Section 9.15(a)").
- quote: ONE passage copied EXACTLY, character for character, from the evidence, that states the covenant and its level. It must be a single contiguous passage of at least 10 characters. Do not change, shorten inside, or reformat the words.
- defined_terms_used: every capitalised defined term the covenant relies on, exactly as written.
- ambiguities: anything unclear, conditional, cure-related (equity cure), or that you could not resolve. Use an empty list if there is none.

Return JSON only."""


def build_prompt(evidence: list) -> str:
    blocks = [f"=== EVIDENCE {i} | {e.chunk_id} | {e.path} ===\n{e.text}" for i, e in enumerate(evidence, start=1)]
    return INSTRUCTIONS + "\n\nEVIDENCE\n\n" + "\n\n".join(blocks)


def build_repair_prompt(base_prompt: str, previous_json: str, errors: list) -> str:
    return (base_prompt + "\n\nYour previous answer was rejected by validation:\n- " + "\n- ".join(errors)
            + "\n\nPrevious answer:\n" + previous_json
            + "\n\nReturn the complete corrected JSON answer. Fix only what the errors describe.")
