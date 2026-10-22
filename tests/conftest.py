import pytest

from debate.config import load_company, load_models
from debate.graph import build_graph
from debate.llm.scripted import ScriptedClient, default_responder
from debate.nodes.agents import Deps
from debate.rag.retriever import Chunk, Retrieval


@pytest.fixture
def cfg():
    return load_models()


def chunk(cid="c1", text="Boston has 50,427 manholes."):
    return Chunk(cid, text, 11, "Gauging the Market", "case", 0.03)


class FakeRetriever:
    """Returns fixed chunks and remembers what it was asked (query, company, k)."""

    def __init__(self, chunks=None):
        self.chunks = chunks if chunks is not None else [chunk()]
        self.calls = []

    def search(self, query, company_id, k):
        self.calls.append((query, company_id, k))
        return Retrieval(query=query, chunks=list(self.chunks)[:k])


def initial_state():
    return {"run_id": "t", "company_id": "biobot", "round": 0, "next_speaker": "ceo", "turns": [],
            "claims": [], "checked_through": 0, "fact_note": "", "decision": None,
            "agenda_covered": [], "follow_up": None}


def make_run(cfg, responder=default_responder, rounds=3, retriever=None):
    cfg.limits.max_rounds = rounds
    llm = ScriptedClient(cfg, responder)
    events, claims, costs = [], [], []
    # fact_check_cost is bookkeeping, not an anomaly: keep it out of `events` so tests can assert "no events".
    def on_event(t, p):
        (costs if t == "fact_check_cost" else events).append((t, p))
    deps = Deps(llm=llm, cfg=cfg, company=load_company("biobot"), sinks=[],
                event_sinks=[on_event], claim_sinks=[claims.extend])
    if retriever is not None:
        deps.retriever = retriever
    graph = build_graph(deps)
    state = graph.invoke(initial_state(), {"recursion_limit": 80})
    deps.events, deps.recorded_claims, deps.fact_check_costs = events, claims, costs
    return state, llm, deps


def calls_for(llm, role):
    return [c for c in llm.calls if c["role"] == role]


def pg_available():
    try:
        from debate.rag import db
        db.connect().close()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_pg = pytest.mark.skipif(not pg_available(), reason="Postgres not reachable (docker compose up -d)")
