"""Main Agent graph — analyze → plan → dispatch → merge → check_complete."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from microconv.graph.microservice_graph import build_microservice_subgraph
from microconv.graph.nodes.dispatch import dispatch
from microconv.graph.nodes.merge import check_complete, merge
from microconv.state import GlobalState

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.tools import BaseTool


def build_main_graph(model: BaseChatModel, tools: list[BaseTool]):
    """Build the full main graph with embedded microservice subgraph.

    Topology:
        analyze → plan → dispatch(Send×N) → [subgraph] → merge → check_complete
                                                                    ↓
                                                            done → END
                                                            retry → plan
    """
    from microconv.graph.nodes.analyze import analyze as analyze_fn
    from microconv.graph.nodes.plan import plan as plan_fn

    bound_analyze = partial(analyze_fn, model=model, tools=tools)
    bound_plan = partial(plan_fn, model=model)

    subgraph = build_microservice_subgraph(model=model, tools=tools)

    mg = StateGraph(GlobalState)

    mg.add_node("analyze", bound_analyze)
    mg.add_node("plan", bound_plan)
    mg.add_node("process_microservice", subgraph)
    mg.add_node("merge", merge)

    mg.set_entry_point("analyze")
    mg.add_edge("analyze", "plan")
    mg.add_conditional_edges("plan", dispatch)
    mg.add_edge("process_microservice", "merge")
    mg.add_conditional_edges("merge", check_complete, {
        "done": END,
        "retry": "plan",
    })

    return mg.compile(checkpointer=MemorySaver())
