"""Who answers the VC's question: a classification the router makes, separately from writing the question.

Optimized runs ask JEV (a `choice` question over the three executives); the all-Opus baseline asks Opus. Follow-ups
are not routed: they stay with the executive who gave the answer being probed.
"""
from __future__ import annotations

from debate.state import Executive

EXEC_ROLES: dict[str, str] = {
    "ceo": "The CEO: vision, market opportunity, customers and who pays, go-to-market and sales, team, partnerships, "
           "company-level risks.",
    "cfo": "The CFO: market size and TAM maths, pricing, unit economics, margins, revenue model, budgets, funding "
           "and use of funds.",
    "cto": "The CTO: technology, sampling hardware, lab and data science, intellectual property and patents, "
           "defensibility against competitors, scalability.",
}

SYSTEM = (
    "You route questions in a venture-capital meeting. Given the VC's question (and its agenda topic), pick the single executive best placed "
    "to answer it:\n" + "\n".join(f"- {k}: {v}" for k, v in EXEC_ROLES.items()) +
    "\nReturn the executive key and a reason of at most 12 words."
)

SENTENCE = {"ceo": "CEO", "cfo": "CFO", "cto": "CTO"}


def announcement(executive: Executive, follow_up: bool) -> str:
    who = SENTENCE[executive]
    return (f"This follow-up stays with the {who}, who gave the answer being probed." if follow_up
            else f"This question will be taken up by the {who}.")
