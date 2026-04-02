"""Microservice subgraph — explore → edit → verify with conditional retry."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from microconv.state import MicroserviceState

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool


def _route_after_verify(state: MicroserviceState) -> str:
    phase = state.get("phase", "done")
    if phase == "done":
        return "end"
    if phase == "edit":
        return "edit"
    if phase == "explore":
        return "explore"
    return "end"


def build_microservice_subgraph(model: BaseChatModel, tools: list[BaseTool]):
    """Build the 3-phase microservice migration subgraph.

    Topology:
        explore → edit → verify → (pass: END | fail_compile: edit | fail_arch: explore)
    """
    from microconv.graph.nodes.edit import edit as edit_fn
    from microconv.graph.nodes.explore import explore as explore_fn
    from microconv.graph.nodes.verify import verify as verify_fn

    bound_explore = partial(explore_fn, model=model, tools=tools)
    bound_edit = partial(edit_fn, model=model, tools=tools)
    bound_verify = partial(verify_fn, model=model, tools=tools)

    sg = StateGraph(MicroserviceState)

    sg.add_node("explore", bound_explore)
    sg.add_node("edit", bound_edit)
    sg.add_node("verify", bound_verify)

    sg.set_entry_point("explore")
    sg.add_edge("explore", "edit")
    sg.add_edge("edit", "verify")
    sg.add_conditional_edges("verify", _route_after_verify, {
        "end": END,
        "edit": "edit",
        "explore": "explore",
    })

    return sg.compile()
