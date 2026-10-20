"""LangGraph wiring for the Phase 1 debate (no RAG, no fact-checker, no decision yet).

  vc_open -> ceo (pitch) -> devil -> [round < max?] -> vc_question -> ceo|cfo|cto -> devil -> ...
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from debate.nodes.agents import Deps, devil_node, executive_node, vc_open_node, vc_question_node
from debate.state import DebateState

EXECUTIVES = ("ceo", "cfo", "cto")


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
    max_rounds = deps.cfg.limits.max_rounds
    g = StateGraph(DebateState)

    g.add_node("vc_open", vc_open_node(deps))
    g.add_node("vc_question", vc_question_node(deps))
    g.add_node("devil", devil_node(deps))
    for role in EXECUTIVES:
        g.add_node(role, executive_node(deps, role))

    g.add_edge(START, "vc_open")
    g.add_edge("vc_open", "ceo")  # the opening asks the CEO for the pitch
    g.add_conditional_edges("vc_question", lambda s: s["next_speaker"], {r: r for r in EXECUTIVES})
    for role in EXECUTIVES:
        g.add_edge(role, "devil")
    # `round` counts completed cycles; stop once max_rounds cycles (pitch included) have run.
    g.add_conditional_edges("devil", lambda s: END if s["round"] >= max_rounds else "vc_question",
                            {END: END, "vc_question": "vc_question"})
    return g.compile(checkpointer=checkpointer)
