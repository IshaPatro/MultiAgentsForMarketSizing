import io
import json

import pytest

from debate.config import load_models
from debate.llm.client import LLMError
from debate.llm.jev import JevClient, build_request, parse_claims
from debate.llm.router import ModelRouter, configure_profile
from debate.nodes.factcheck import _prompt
from debate.state import ClaimExtraction, VerdictBatch


class FakeClient:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["schema"](claims=[], relies_on_later_knowledge=False, later_knowledge_reason=""), None


def test_jev_takes_only_the_verdict_tier_and_opus_baseline_changes_every_generative_call():
    cfg = configure_profile(load_models(), "routed", "jev-latest")
    claude, jev = FakeClient(), FakeClient()
    router = ModelRouter(claude, jev)
    for role, tier_name in (("fact_check", "verdict"), ("claim_extract", "fast"), ("ceo", "debate"),
                            ("fact_check_hard", "check_hard")):
        router.generate(role=role, tier=cfg.tiers[tier_name], system="s", user="u", schema=ClaimExtraction)
    assert [c["role"] for c in jev.calls] == ["fact_check"]           # a classifier cannot write claims
    assert [c["role"] for c in claude.calls] == ["claim_extract", "ceo", "fact_check_hard"]
    assert cfg.prices["jev:jev-latest"].input == 0.042 and cfg.prices["jev:jev-latest"].output == 0

    baseline = configure_profile(load_models(), "opus_low", "jev-latest")
    assert all(baseline.tiers[t].model == "claude-opus-5-5" and baseline.tiers[t].effort == "low"
               for t in ("fast", "verdict", "debate", "check_hard"))
    assert configure_profile(load_models(), "routed").tiers["verdict"].model == "claude-haiku-5-5"


class _Chunk:
    def __init__(self, chunk_id, text):
        self.chunk_id, self.text = chunk_id, text


class _Retrieval:
    def __init__(self, chunks):
        self.chunks = chunks


ITEMS = [{"ref": "C1", "speaker": "cfo", "text": "Biobot raised a $20 million Series A.",
          "retrieval": _Retrieval([_Chunk("a1", "Biobot announced a $20 million Series A.\nLed by Thursday."),
                                   _Chunk("a2", "Unrelated note about manholes.")])},
         {"ref": "C2", "speaker": "devil", "text": "No patent exists.", "retrieval": _Retrieval([])}]


def test_verdict_prompt_round_trips_into_jev_requests():
    claims = parse_claims(_prompt(ITEMS))
    assert [(c["ref"], c["speaker"], c["claim"], [cid for cid, _ in c["chunks"]]) for c in claims] == [
        ("C1", "CFO", "Biobot raised a $20 million Series A.", ["a1", "a2"]), ("C2", "DEVIL", "No patent exists.", [])]
    assert "Led by Thursday." in claims[0]["chunks"][0][1]
    request = build_request("jev-latest", claims[0])
    assert request["model"] == "jev-latest" and request["questions"]["verdict"]["type"] == "choice"
    assert set(request["questions"]) == {"covered", "verdict", "e0", "e1"} and "[a2]" in request["questions"]["e1"]["instructions"]


def _jev_reply(choice, confidence, noul, covered=0.95):
    answers = {"covered": {"type": "noul", "noul": covered},
               "verdict": {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {}}}
    answers |= {f"e{n}": {"type": "noul", "noul": p} for n, p in enumerate(noul)}
    return json.dumps({"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 20}}).encode()


def test_jev_client_turns_answers_into_verdicts_and_cites_only_relevant_chunks():
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, request.get_header("Authorization"), json.loads(request.data)))
        return io.BytesIO(_jev_reply("supported", 0.97, [0.96, 0.1], 0.95) if len(seen) == 1
                          else _jev_reply("unverifiable", 0.9, [], 0.02))

    cfg = configure_profile(load_models(), "routed", "jev-latest")
    parsed, usage = JevClient("secret", "https://jev.test/v1/systemone", opener).generate(
        role="fact_check", tier=cfg.tiers["verdict"], system="S", user=_prompt(ITEMS), schema=VerdictBatch)
    first, second = parsed.verdicts
    assert (first.ref, first.verdict, first.evidence_ids) == ("C1", "supported", ["a1"])
    assert first.confidence == pytest.approx(0.97 * 0.95, abs=1e-3)       # verdict confidence x "the info is present"
    assert (second.verdict, second.evidence_ids) == ("unverifiable", [])
    assert second.confidence == pytest.approx(0.9 * 0.98, abs=1e-3)       # confidently silent: no escalation needed
    assert seen[0][0] == "https://jev.test/v1/systemone" and seen[0][1] == "Bearer secret"
    assert usage.input_tokens == 200 and usage.output_tokens == 40
    assert usage.cost_usd == pytest.approx(200 * 0.042 / 1e6)               # input tokens only; output is free


def test_jev_client_rejects_other_schemas_and_reports_bad_replies():
    cfg = configure_profile(load_models(), "routed", "jev-latest")
    with pytest.raises(ValueError, match="only produce fact-check verdicts"):
        JevClient("k", opener=lambda r, timeout: None).generate(
            role="claim_extract", tier=cfg.tiers["fast"], system="S", user="U", schema=ClaimExtraction)
    with pytest.raises(LLMError, match="C1 failed"):
        JevClient("k", opener=lambda r, timeout: io.BytesIO(b'{"answers": {}}')).generate(
            role="fact_check", tier=cfg.tiers["verdict"], system="S", user=_prompt(ITEMS), schema=VerdictBatch)


def test_jev_disagreement_between_coverage_and_verdict_gets_low_confidence_so_it_escalates():
    replies = iter([_jev_reply("unverifiable", 1.0, [0.2, 0.1], 0.9),    # says "not covered" but evidence is present
                    _jev_reply("supported", 1.0, [0.9, 0.1], 0.1)])        # says "supported" but nothing covers it
    cfg = configure_profile(load_models(), "routed", "jev-latest")
    parsed, _ = JevClient("k", opener=lambda r, timeout: io.BytesIO(next(replies))).generate(
        role="fact_check", tier=cfg.tiers["verdict"], system="S", user=_prompt(ITEMS), schema=VerdictBatch)
    assert all(v.confidence < cfg.limits.escalate_below for v in parsed.verdicts)


def test_jev_moderator_route_is_priced_on_input_tokens():
    from debate.state import RouteChoice
    reply = json.dumps({"model": "jev-1.14.0", "answers": {"executive": {"type": "choice", "choice": "cto", "confidence": 0.9}},
                        "usage": {"input_tokens": 1000, "output_tokens": 30}}).encode()
    cfg = configure_profile(load_models(), "routed", "jev-latest")
    parsed, usage = JevClient("k", opener=lambda r, timeout: io.BytesIO(reply)).generate(
        role="route", tier=cfg.tiers["route"], system="S", user="Question: who owns the patents?", schema=RouteChoice)
    assert parsed.executive == "cto" and usage.cost_usd == pytest.approx(1000 * 0.042 / 1e6)


def test_jev_retries_transient_failures_and_gives_up_on_client_errors():
    from urllib.error import HTTPError
    from debate.state import RouteChoice
    ok = json.dumps({"model": "jev-1.14.0", "answers": {"executive": {"type": "choice", "choice": "cfo", "confidence": 0.9}},
                     "usage": {"input_tokens": 10, "output_tokens": 2}}).encode()
    attempts, waits = [], []

    def flaky(request, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise HTTPError(request.full_url, 429 if len(attempts) == 1 else 503, "busy", {}, None)
        return io.BytesIO(ok)

    cfg = configure_profile(load_models(), "routed", "jev-latest")
    parsed, _ = JevClient("k", opener=flaky, sleep=waits.append).generate(
        role="route", tier=cfg.tiers["route"], system="S", user="Question: q", schema=RouteChoice)
    assert parsed.executive == "cfo" and len(attempts) == 3 and waits == [1, 2]

    def bad_request(request, timeout):
        raise HTTPError(request.full_url, 400, "bad", {}, None)
    with pytest.raises(LLMError):
        JevClient("k", opener=bad_request, sleep=waits.append).generate(
            role="route", tier=cfg.tiers["route"], system="S", user="Question: q", schema=RouteChoice)
    assert waits == [1, 2]                                   # a 400 is not retried
