"""The VC works through an agenda; every main question gets a follow-up; TAM answers carry up to 3 estimates."""
from conftest import calls_for, make_run

from debate.agenda import AGENDA, FOLLOW_UP_ANSWER
from debate.llm.scripted import default_responder
from debate.state import Estimate, Statement, VCQuestion
from debate.storage.sqlite_store import SqliteStore


def vc_turns(state):
    return [t for t in state["turns"] if t["speaker"] == "vc"][1:]  # skip the opening


def test_market_size_comes_first_goes_to_the_cfo_and_every_main_question_gets_a_follow_up(cfg):
    state, llm, _ = make_run(cfg, rounds=9)
    qs = vc_turns(state)
    assert len(qs) == 8
    assert [q["follow_up"] for q in qs] == [False, True] * 4                   # main, follow-up, main, ...
    assert qs[0]["topic"] == "market_size"
    notices = [t for t in state["turns"] if t["speaker"] == "moderator"][1:]   # one per VC question (skip the pitch)
    assert len(notices) == 8 and notices[0]["addressed_to"] == "cfo"           # market size goes to the CFO
    assert notices[0]["content"] == "This question will be taken up by the CFO."
    for main, follow in zip(notices[::2], notices[1::2]):                      # follow-up probes the same exec+topic
        assert (follow["topic"], follow["addressed_to"]) == (main["topic"], main["addressed_to"])
        assert follow["content"].startswith("This follow-up stays with")
    topics = [q["topic"] for q in qs[::2]]
    assert len(set(topics)) == 4 and set(topics) <= set(AGENDA)              # four distinct agenda topics
    vc_calls = calls_for(llm, "vc")                                         # ("vc_opening" is the pitch request)
    assert "FOLLOW-UP" not in vc_calls[0]["user"] and "FOLLOW-UP" in vc_calls[1]["user"]


def test_executives_get_the_topic_brief_on_main_questions_and_must_explain_on_follow_ups(cfg):
    _, llm, _ = make_run(cfg, rounds=3)
    cfo = calls_for(llm, "cfo")
    assert "THREE independent sizing approaches" in cfo[0]["user"]
    assert FOLLOW_UP_ANSWER in cfo[-1]["user"] and "THREE independent" not in cfo[-1]["user"]


def test_the_vc_can_choose_the_next_topic_and_a_bad_choice_falls_back(cfg):
    picks = iter(["technology_moat", "market_size"])  # second pick is already covered -> fallback

    def responder(role, system, user, schema, n):
        if role == "vc" and schema is VCQuestion and "not yet covered" in user:
            topic = next(picks)
            return VCQuestion(point="Next?", addressed_to="cto", topic=topic)
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=7)
    mains = [q for q in vc_turns(state) if not q["follow_up"]]
    assert [m["topic"] for m in mains] == ["market_size", "technology_moat", "customers"]
    assert state["agenda_covered"] == ["market_size", "technology_moat", "customers"]


def test_no_follow_up_is_left_pending_when_rounds_run_out(cfg):
    state, _, _ = make_run(cfg, rounds=4)
    assert [q["follow_up"] for q in vc_turns(state)] == [False, True, False]
    assert state["follow_up"] is None


def _tam(estimates):
    def responder(role, system, user, schema, n):
        if role == "cfo" and schema is Statement and "THREE independent" in user:
            return Statement(point="Between $0.5B and $6.5B.", estimates=estimates)
        return default_responder(role, system, user, schema, n)
    return responder


def test_three_estimates_are_checked_shown_to_later_speakers_and_a_wrong_one_is_flagged(cfg):
    ests = [Estimate(method="Budgets (top-down)", calculation="$110B x 20% = $22B; $22B x 4.6% = $1.012B",
                     assumptions=["4.6% recovery share"]),
            Estimate(method="Existing market", calculation="$138B x 53.9% = $7.4B", assumptions=["1% capture"]),
            Estimate(method="Bottom-up", calculation="542,500 sites x 12 = 6,510,000 samples; 6,510,000 x $1,000 = $6.51B")]
    state, llm, _ = make_run(cfg, _tam(ests), rounds=3)
    cfo = next(t for t in state["turns"] if t["speaker"] == "cfo")
    assert [e["calculation_ok"] for e in cfo["estimates"]] == [True, False, True]
    devil_prompt = calls_for(llm, "devil")[1]["user"]
    assert "estimate 1 - Budgets (top-down)" in devil_prompt and "estimate 2 - Existing market (does NOT add up)" in devil_prompt
    flagged = [c for c in state["claims"] if c["claim_id"].endswith("-est2")]
    assert flagged and flagged[0]["verdict"] == "miscalculated"
    follow_up_q = calls_for(llm, "vc")[1]["user"]                            # the VC sees all three before probing
    assert "estimate 3 - Bottom-up" in follow_up_q


def test_estimates_are_capped_at_three_and_need_maths(cfg):
    ests = [Estimate(method=f"M{i}", calculation=f"{i} x 2 = {i * 2}") for i in range(1, 5)]
    ests.insert(0, Estimate(method="No maths", calculation="  "))
    state, _, _ = make_run(cfg, _tam(ests), rounds=2)
    cfo = next(t for t in state["turns"] if t["speaker"] == "cfo")
    assert [e["method"] for e in cfo["estimates"]] == ["M1", "M2", "M3"]


def test_topic_follow_up_and_estimates_are_stored(cfg, tmp_path):
    state, _, _ = make_run(cfg, rounds=3)
    store = SqliteStore(tmp_path / "s.sqlite")
    store.start_run(run_id="t", company_id="biobot", thread_id="t", config={})
    from debate.state import Turn
    for t in state["turns"]:
        store.add_turn("t", Turn(**t))
    rows = {r["seq"]: r for r in store.turns("t")}
    follow = next(r for r in rows.values() if r["is_follow_up"])
    assert follow["topic"] == "market_size" and follow["speaker"] == "vc"
    cfo = next(r for r in rows.values() if r["speaker"] == "cfo")
    assert '"Beachhead budgets (top-down)"' in cfo["estimates_json"]       # dry-run CFO gives the 3 slide methods


def test_the_vc_asks_until_every_topic_is_covered_and_then_decides_by_itself(cfg):
    state, llm, _ = make_run(cfg, rounds=60)                                  # 60 is only a runaway guard
    assert set(state["agenda_covered"]) == set(AGENDA)                        # vision, raise, use of funds, team, ICP...
    assert state["vc_ready"] is True and state["round"] < 60                  # it stopped because it was ready
    assert state["turns"][-1]["speaker"] == "vc"                             # its closing remark ends the meeting
    assert state["decision"]
    assert all(t["addressed_to"] == "panel" for t in state["turns"] if t["speaker"] == "vc")


def test_the_vc_cannot_stop_early_and_its_own_follow_ups_stay_with_the_same_executive(cfg):
    asked = {"n": 0}

    def responder(role, system, user, schema, n):
        if role == "vc" and schema is VCQuestion:
            asked["n"] += 1
            if "set `follow_up` to true" in user and asked["n"] == 3:        # an extra, VC-chosen follow-up
                return VCQuestion(point="And the second input?", follow_up=True)
            return VCQuestion(point="Enough?", ready_to_decide=True)        # wants to stop: refused while topics remain
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=6)
    assert not state.get("vc_ready")                                          # topics remained, so it kept going
    qs = vc_turns(state)
    assert [q["follow_up"] for q in qs][:3] == [False, True, True]            # main, forced follow-up, chosen follow-up
    notices = [t for t in state["turns"] if t["speaker"] == "moderator"][1:]
    assert notices[1]["addressed_to"] == notices[2]["addressed_to"] == notices[0]["addressed_to"]
    assert notices[2]["content"].startswith("This follow-up stays with")
