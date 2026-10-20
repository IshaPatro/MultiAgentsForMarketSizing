from conftest import make_run

from debate.guards import trim_to_words, word_count
from debate.state import Statement, VCQuestion
from debate.llm.scripted import default_responder

LONG = " ".join(["word"] * 120) + "."


def test_trim_prefers_sentence_boundary():
    text = "First sentence is short. " + " ".join(["filler"] * 100)
    out = trim_to_words(text, 60)
    assert word_count(out) <= 60


def test_over_long_reply_triggers_one_rewrite_and_is_accepted(cfg):
    seen = {}

    def responder(role, system, user, schema, n):
        if role == "ceo":
            seen["ceo_calls"] = seen.get("ceo_calls", 0) + 1
            if seen["ceo_calls"] == 1:
                return Statement(point=LONG)
            assert "Rewrite it in at most 60 words" in user
            return Statement(point="Short and sharp.")
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=1)
    ceo = next(t for t in state["turns"] if t["speaker"] == "ceo")
    assert ceo["content"] == "Short and sharp."
    assert ceo["retries"] == 1 and ceo["trimmed"] is False
    assert ceo["usage"]["input_tokens"] > 0  # usage of both attempts is summed


def test_still_too_long_after_retry_is_hard_trimmed(cfg):
    def responder(role, system, user, schema, n):
        if role == "devil":
            return Statement(point=LONG)
        return default_responder(role, system, user, schema, n)

    state, _, _ = make_run(cfg, responder, rounds=1)
    devil = next(t for t in state["turns"] if t["speaker"] == "devil")
    assert word_count(devil["content"]) <= 60
    assert devil["trimmed"] is True and devil["retries"] == 1


def test_all_turns_respect_the_limit_end_to_end(cfg):
    state, _, _ = make_run(cfg, rounds=3)
    assert all(word_count(t["content"]) <= cfg.limits.max_words_per_turn for t in state["turns"])
