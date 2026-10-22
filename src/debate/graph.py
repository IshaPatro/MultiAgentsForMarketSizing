"""LangGraph wiring for the debate and the final VC decision.

  vc_open -> ceo (pitch) -> devil -> fact_check -> [round < max?] -> vc_question -> route -> ceo|cfo|cto -> devil -> ...
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from debate.nodes.agents import Deps, devil_node, executive_node, route_node, vc_open_node, vc_question_node
from debate.nodes.factcheck import fact_check_node
from debate.nodes.decision import decision_node
from debate.state import DebateState

EXECUTIVES = ("ceo", "cfo", "cto")


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver | None = None,
                interrupt_after: list[str] | None = None):
    max_rounds = deps.cfg.limits.max_rounds
    g = StateGraph(DebateState)

    g.add_node("vc_open", vc_open_node(deps))
    g.add_node("vc_question", vc_question_node(deps))
    g.add_node("route", route_node(deps))
    g.add_node("devil", devil_node(deps))
    g.add_node("fact_check", fact_check_node(deps))
    g.add_node("vc_decision", decision_node(deps))
    for role in EXECUTIVES:
        g.add_node(role, executive_node(deps, role))

    g.add_edge(START, "vc_open")
    g.add_edge("vc_open", "ceo")  # the opening asks the CEO for the pitch
    g.add_edge("vc_question", "route")
    g.add_conditional_edges("route", lambda s: s["next_speaker"], {r: r for r in EXECUTIVES})
    for role in EXECUTIVES:
        g.add_edge(role, "devil")
    # `round` counts completed cycles; stop once max_rounds cycles (pitch included) have run.
    g.add_edge("devil", "fact_check")
    g.add_conditional_edges("fact_check", lambda s: "vc_decision" if s["round"] >= max_rounds else "vc_question",
                            {"vc_decision": "vc_decision", "vc_question": "vc_question"})
    g.add_edge("vc_decision", END)
    return g.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
