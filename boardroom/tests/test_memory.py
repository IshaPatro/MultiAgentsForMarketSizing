import uuid

import pytest
from conftest import needs_pg

from debate.memory.setup import persistence
from debate.memory.views import PORTFOLIO, MemoryLeakError, MemoryView, remember_run, vc_memories


class FakeStore:
    def __init__(self):
        self.items = {}

    def put(self, ns, key, value):
        self.items[ns, key] = value

    def get(self, ns, key):
        value = self.items.get((ns, key))
        return None if value is None else type("Item", (), {"value": value, "key": key})()

    def search(self, ns, query=None, limit=10):
        return [type("Item", (), {"value": v, "key": k})() for (n, k), v in self.items.items() if n == ns][:limit]

    def delete(self, ns, key):
        self.items.pop((ns, key), None)


def test_memory_view_blocks_cross_agent_reads_and_writes():
    events = []
    store = FakeStore()
    vc = MemoryView(store, "vc", lambda kind, payload: events.append((kind, payload)))
    ns = ("vc", PORTFOLIO)
    vc.put(ns, "one", {"company_id": "acme", "summary": "Evidence-backed note"})
    assert vc.get(ns, "one").value["summary"] == "Evidence-backed note"
    assert "Evidence-backed note" in vc_memories(vc, "other_company")
    for forbidden in (("ceo", "private"), ("devil", PORTFOLIO)):
        with pytest.raises(MemoryLeakError):
            vc.search(forbidden)
        with pytest.raises(MemoryLeakError):
            vc.put(forbidden, "x", {})
    with pytest.raises(MemoryLeakError):
        vc.put(("shared_static", "glossary"), "x", {})      # shared memory is read-only
    vc.search(("shared_static", "glossary"))
    assert len(events) == 5 and all(kind == "leak_attempt" for kind, _ in events)


def test_only_supported_claims_become_vc_memory():
    view = MemoryView(FakeStore(), "vc")
    ns = ("vc", PORTFOLIO)
    remember_run(view, "r1", "biobot", [{"text": "unsupported", "verdict": "unverifiable"}])
    assert view.search(ns) == []
    remember_run(view, "r1", "biobot", [{"text": "A supported fact", "verdict": "supported"}])
    assert view.get(ns, "biobot:r1").value["supported_claims"] == ["A supported fact"]


def test_the_vc_does_not_recall_earlier_debates_about_the_company_being_pitched():
    """A completed Biobot run must not prime the next Biobot run (or the opus_low comparison run)."""
    view = MemoryView(FakeStore(), "vc")
    remember_run(view, "routed-1", "biobot", [{"text": "Biobot note", "verdict": "supported"}])
    remember_run(view, "r2", "acme", [{"text": "Acme note", "verdict": "supported"}])
    recalled = vc_memories(view, "biobot", query="Biobot Analytics")
    assert "Acme note" in recalled and "Biobot note" not in recalled
    assert vc_memories(MemoryView(FakeStore(), "vc"), "biobot") == ""


@needs_pg
def test_postgres_memory_survives_reopening_and_rejects_other_agents():
    ns = ("vc", PORTFOLIO)
    key = "test:" + uuid.uuid4().hex
    summary = "wastewater opioid measurement for municipal public health"
    with persistence() as (_, store):
        MemoryView(store, "vc").put(ns, key, {"company_id": "test", "summary": summary})
    with persistence() as (_, store):
        vc = MemoryView(store, "vc")
        assert vc.get(ns, key).value["summary"] == summary
        assert key in [item.key for item in vc.search(ns, query="municipal opioid wastewater", limit=10)]
        with pytest.raises(MemoryLeakError):
            MemoryView(store, "ceo").get(ns, key)
        vc.delete(ns, key)
