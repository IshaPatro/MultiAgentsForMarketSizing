"""Claim extraction (implementation.md section 10).

One System-1 call splits a turn (point + shown maths + assumptions + estimates) into atomic, labelled claims.
The fact-checker verifies the fact/number claims; forecasts and opinions are recorded but not verified.
"""
from __future__ import annotations

from debate.state import ClaimExtraction, ExtractedClaim, Usage

EXTRACTION_SYSTEM = (
    "You audit one statement from a simulated venture-capital meeting.\n"
    "Split the statement into atomic claims (each self-contained) and label each: fact, number, forecast or opinion. "
    "Claims about what someone said or did NOT say in this meeting are opinion, not fact. "
    "Statements of uncertainty such as 'I cannot verify X from the evidence shown' are opinion, not "
    "claims that X does not exist. Extract external facts separately from these statements.\n"
    "Text after 'CALCULATION:' is the speaker's arithmetic: label its result number. Text after 'ASSUMPTION:' "
    "is a stated assumption: label it forecast or opinion, never fact. Text after 'ESTIMATE (...) CALCULATION:' is "
    "one sizing method's arithmetic: label its result number."
)


def extract_claims(deps, text: str) -> tuple[list[ExtractedClaim], Usage]:
    parsed, usage = deps.call("claim_extract", "fast", EXTRACTION_SYSTEM, f"Statement:\n{text}", ClaimExtraction)
    return parsed.claims, usage
