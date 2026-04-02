"""Explore node — analyses classes within one service and produces a migration plan."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from microconv.prompts import EXPLORE_PROMPT
from microconv.state import MicroserviceState

logger = logging.getLogger(__name__)


def explore(state: MicroserviceState, *, model, tools) -> dict:
    """Read source files, build class_dependency_graph, and produce migration plan."""
    service_name = state["service_name"]
    spec = state["spec"]

    prompt = EXPLORE_PROMPT.format(
        service_name=service_name,
        related_service_deps=json.dumps(state["related_service_deps"], indent=2, ensure_ascii=False),
        required_interfaces=json.dumps(state["required_interfaces"], indent=2, ensure_ascii=False),
        my_exposed_interfaces=json.dumps(state["my_exposed_interfaces"], indent=2, ensure_ascii=False),
        classes=json.dumps(spec.get("classes", []), indent=2, ensure_ascii=False),
    )

    llm_with_tools = model.bind_tools(tools)
    messages: list = [SystemMessage(content=prompt), HumanMessage(content=f"Explore and plan migration for {service_name}")]

    max_iterations = 20
    for _ in range(max_iterations):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tc in response.tool_calls:
            matching = [t for t in tools if t.name == tc["name"]]
            if not matching:
                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(content=f"Tool {tc['name']} not found", tool_call_id=tc["id"]))
                continue
            result = matching[0].invoke(tc["args"])
            from langchain_core.messages import ToolMessage
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    parsed = _extract_json(response.content)
    class_dep_graph = parsed.get("class_dependency_graph", {})
    plan = parsed.get("plan", {"steps": []})

    logger.info("Explore complete for %s: %d steps planned", service_name, len(plan.get("steps", [])))

    return {
        "messages": messages,
        "class_dependency_graph": class_dep_graph,
        "plan": plan,
        "phase": "edit",
    }


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        end = next((i for i, l in enumerate(lines) if l.strip() == "```"), len(lines))
        text = "\n".join(lines[:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {}
