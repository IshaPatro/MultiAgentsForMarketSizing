"""Agents show their maths and assumptions; code re-checks the maths and the guard screens both."""
import pytest
from conftest import FakeRetriever, calls_for, make_run

from debate.llm.scripted import default_responder
from debate.numeric import check_calculation
from debate.state import ClaimExtraction, ExtractedClaim, Statement, Turn, Usage
from debate.storage.sqlite_store import SqliteStore


@pytest.mark.parametrize("text, expected", [
    ("4 cities x $110B budgets x 20% = $88B", True),
    ("4 cities x $110B budgets x 20% = $22B", False),
    ("$110B x 20% = ~$22B; $22B x 1% = $220M", True),          # chained steps, rounding marker
    ("71 cities x $200,000 per year = $14M", True),             # rounded result within tolerance
    ("71 cities x $200,000 per year = $20M", False),
    ("650,000 residents / 50,427 manholes = 12.9 residents per manhole", True),
    ("$500,000 + $250,000 x 2 = $1M", True),                    # precedence
    ("870 cities - 71 cities = 799 cities", True),
    ("$10-20M budget", None),                                   # a range is not a subtraction
    ("$5k/site per month", None),                               # a rate is not a division
    ("(2 + 3) x 4 = 20", None),                                 # no grouping: never guess
    ("We assume $50,000 per city", None),
])
def test_shown_calculations_are_rechecked_in_code(text, expected):
    assert check_calculation(text) is expected


def _cfo_responder(calc, assumptions, point="The beachhead alone is worth $88B in budgets."):
    def responder(role, system, user, schema, n):
        if role == "cfo" and schema is Statement:
            return Statement(point=point, calculation=calc, assumptions=assumptions)
        if role == "vc" and "Ask your next question" in user:
            from debate.state import VCQuestion
            return VCQuestion(point="What is the revenue maths?", addressed_to="cfo")
        return default_responder(role, system, user, schema, n)
    return responder


def test_maths_and_assumptions_are_stored_and_shown_to_later_speakers(cfg):
    calc, assumptions = "4 cities x $110B x 20% = $88B", ["cities spend 20% of budgets on public health"]
    state, llm, _ = make_run(cfg, _cfo_responder(calc, assumptions), rounds=2)
    cfo = next(t for t in state["turns"] if t["speaker"] == "cfo")
    assert cfo["calculation"] == calc and cfo["assumptions"] == assumptions and cfo["calculation_ok"] is True
    devil_prompt = [c for c in calls_for(llm, "devil")][-1]["user"]
    assert f"[maths: {calc}]" in devil_prompt and "[assumes: cities spend 20%" in devil_prompt
    assert "CALCULATION: " + calc in next(c["user"] for c in calls_for(llm, "claim_extract") if calc in c["user"])


def test_a_wrong_calculation_becomes_a_code_verified_miscalculation_the_vc_sees(cfg):
    state, llm, _ = make_run(cfg, _cfo_responder("4 cities x $110B x 20% = $22B", []), rounds=3)
    cfo = next(t for t in state["turns"] if t["speaker"] == "cfo")
    assert cfo["calculation_ok"] is False
    calc_claims = [c for c in state["claims"] if c["claim_id"].endswith("-calc")]
    assert calc_claims and calc_claims[0]["verdict"] == "miscalculated" and calc_claims[0]["checker_model"] == "python"
    last_vc = calls_for(llm, "vc")[-1]["user"]
    assert "does NOT add up" in last_vc and "miscalculated" in last_vc


def test_support_is_kept_whole_and_only_blanks_are_dropped(cfg):
    long = " ".join(["word"] * 80)
    state, _, _ = make_run(cfg, _cfo_responder(long, ["a", "b", "c", "d", " "]), rounds=2)
    cfo = next(t for t in state["turns"] if t["speaker"] == "cfo")
    assert len(cfo["calculation"].split()) == 80 and cfo["assumptions"] == ["a", "b", "c", "d"]


def test_sqlite_keeps_the_maths_columns(tmp_path):
    store = SqliteStore(tmp_path / "x.sqlite")
    store.start_run(run_id="r", company_id="c", thread_id="t", config={})
    store.add_turn("r", Turn(seq=1, round=1, speaker="cfo", addressed_to="vc", content="p", model="m", tier="t",
                             usage=Usage(), calculation="2 x 3 = 6", assumptions=["x"], calculation_ok=True))
    row = store.turns("r")[0]
    assert row["calculation"] == "2 x 3 = 6" and row["assumptions_json"] == '["x"]' and row["calculation_ok"] == 1
