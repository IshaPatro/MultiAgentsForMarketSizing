"""ClaudeClient against a fake SDK object: checks the request shape and the usage/cost mapping."""
from types import SimpleNamespace

import pytest

from debate.config import load_models
from debate.llm.claude import ClaudeClient
from debate.llm.client import LLMError
from debate.state import Statement


class FakeMessages:
    def __init__(self, **resp):
        self.resp, self.kwargs = resp, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(**self.resp)


def fake_response(**over):
    base = dict(
        stop_reason="end_turn", stop_details=None, parsed_output=Statement(point="ok"),
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                              cache_read_input_tokens=500, cache_creation_input_tokens=0),
    )
    base.update(over)
    return base


def client_with(**resp):
    cfg = load_models()
    messages = FakeMessages(**fake_response(**resp))
    return ClaudeClient(cfg, SimpleNamespace(messages=messages)), cfg, messages


def test_request_shape_and_cost():
    client, cfg, messages = client_with()
    parsed, usage = client.generate(role="ceo", tier=cfg.tiers["debate"], system="SYS", user="U", schema=Statement)
    k = messages.kwargs
    assert k["model"] == "claude-sonnet-5-5"
    assert k["output_format"] is Statement
    assert k["output_config"] == {"effort": "low"}
    assert k["system"][0]["cache_control"] == {"type": "ephemeral"}   # frozen system prompt first
    assert "temperature" not in k and "top_p" not in k                # rejected on this model
    assert parsed.point == "ok"
    expected = (1000 * 2.00 + 200 * 10.00 + 500 * 0.20) / 1e6
    assert usage.cost_usd == pytest.approx(expected)
    assert usage.cache_read_tokens == 500


@pytest.mark.parametrize("over", [{"stop_reason": "refusal"}, {"stop_reason": "max_tokens"}, {"parsed_output": None}])
def test_unusable_replies_raise(over):
    client, cfg, _ = client_with(**over)
    with pytest.raises(LLMError):
        client.generate(role="ceo", tier=cfg.tiers["debate"], system="S", user="U", schema=Statement)
