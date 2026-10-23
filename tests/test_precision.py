"""Answers are not length-limited: the prompts ask for precision, and nothing is rewritten or cut."""
from conftest import calls_for, make_run

from debate.llm.scripted import default_responder
from debate.state import Statement

LONG = " ".join(["word"] * 400) + "."


def test_long_answers_are_kept_whole_with_one_model_call(cfg):
    def responder(role, system, user, schema, n):
        if role == "ceo":
            return Statement(point=LONG, assumptions=[" ".join(["assume"] * 80)])
        return default_responder(role, system, user, schema, n)

    state, llm, _ = make_run(cfg, responder, rounds=1)
    ceo = next(t for t in state["turns"] if t["speaker"] == "ceo")
    assert ceo["content"] == LONG and ceo["retries"] == 0 and ceo["trimmed"] is False
    assert len(ceo["assumptions"][0].split()) == 80
    assert len(calls_for(llm, "ceo")) == 1                       # no "rewrite it shorter" request


def test_prompts_ask_for_precision_not_a_word_count(cfg):
    _, llm, _ = make_run(cfg, rounds=1)
    system = calls_for(llm, "ceo")[0]["system"]
    assert "be precise" in system and "words" not in system.split("STYLE")[1].split("\n")[0]
    assert "at most" not in system
