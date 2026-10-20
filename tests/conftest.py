import pytest

from debate.config import load_company, load_era, load_models
from debate.graph import build_graph
from debate.llm.scripted import ScriptedClient, default_responder
from debate.nodes.agents import Deps


@pytest.fixture
def cfg():
    return load_models()


def make_run(cfg, responder=default_responder, rounds=3):
    cfg.limits.max_rounds = rounds
    llm = ScriptedClient(cfg, responder)
    turns = []
    deps = Deps(llm=llm, cfg=cfg, company=load_company("biobot"), era=load_era("pre_covid"), sinks=[turns.append])
    graph = build_graph(deps)
    state = graph.invoke({"run_id": "t", "company_id": "biobot", "era": {}, "round": 0, "next_speaker": "ceo", "turns": []},
                         {"recursion_limit": 60})
    return state, llm, turns, deps
