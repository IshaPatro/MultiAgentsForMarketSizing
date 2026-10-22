"""Guard every long-term memory operation by agent namespace."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

PORTFOLIO = "portfolio_theses"


class MemoryLeakError(PermissionError):
    """A node attempted to access another agent's long-term memory."""


@dataclass(frozen=True)
class MemoryView:
    """An agent may read and write only its own namespace (agent, ...) and read the shared one."""

    store: object
    agent: str
    on_leak: Callable[[str, dict], None] | None = None

    def _check(self, namespace: tuple[str, ...], write: bool = False) -> None:
        own = len(namespace) >= 2 and namespace[0] == self.agent
        shared = len(namespace) >= 2 and namespace[0] == "shared_static" and not write
        if own or shared:
            return
        if self.on_leak:
            self.on_leak("leak_attempt", {"agent": self.agent, "namespace": list(namespace), "write": write})
        raise MemoryLeakError(f"{self.agent} cannot access namespace {namespace}")

    def get(self, namespace: tuple[str, ...], key: str):
        self._check(namespace)
        return self.store.get(namespace, key)

    def search(self, namespace: tuple[str, ...], *, query: str | None = None, limit: int = 10):
        self._check(namespace)
        return self.store.search(namespace, query=query, limit=limit)

    def put(self, namespace: tuple[str, ...], key: str, value: dict) -> None:
        self._check(namespace, write=True)
        self.store.put(namespace, key, value)

    def delete(self, namespace: tuple[str, ...], key: str) -> None:
        self._check(namespace, write=True)
        self.store.delete(namespace, key)


def vc_memories(view: MemoryView | None, company_id: str, limit: int = 3, query: str | None = None) -> str:
    """Notes about OTHER companies the VC has seen.

    The company being pitched is excluded: notes from its own earlier runs would prime the VC with a previous
    debate's conclusions, so repeated runs (seed-stability checks, routed vs opus_low comparisons) would no
    longer be independent.
    """
    if view is None:
        return ""
    # The store can only filter on equality, so over-fetch and drop the current company here.
    found = view.search(("vc", PORTFOLIO), query=query, limit=limit * 4)
    items = [i for i in found if i.value.get("company_id") != company_id][:limit]
    if not items:
        return ""
    lines = [f"- {i.value['company_id']}: {i.value['summary']}" for i in items]
    return "\n\nVC notes from earlier meetings:\n" + "\n".join(lines)


def remember_run(view: MemoryView, run_id: str, company_id: str, claims: list[dict]) -> None:
    supported = [c["text"] for c in claims if c["verdict"] == "supported"][:3]
    if not supported:
        return
    view.put(("vc", PORTFOLIO), f"{company_id}:{run_id}",
             {"company_id": company_id, "run_id": run_id, "summary": "; ".join(supported),
              "supported_claims": supported})
