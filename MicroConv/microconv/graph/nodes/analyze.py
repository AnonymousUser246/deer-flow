"""Analyze node — scans the monolith and builds ServiceTopology + ServiceInterfaces."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from microconv.prompts import ANALYZE_PROMPT
from microconv.state import GlobalState

logger = logging.getLogger(__name__)


def analyze(state: GlobalState, *, model, tools) -> dict:
    """Scan the monolith project and produce initial service_topology and service_interfaces.

    This node uses an LLM agent loop: it sends the analyze prompt, lets the LLM
    call tools (ls, read_file) to explore the codebase, then parses the final
    structured JSON output.
    """
    monolith_path = state["monolith_path"]
    prompt = ANALYZE_PROMPT.format(monolith_path=monolith_path)

    llm_with_tools = model.bind_tools(tools)

    messages = [SystemMessage(content=prompt)]
    if state.get("messages"):
        messages.extend(state["messages"])
    else:
        messages.append(HumanMessage(content=f"Please analyse the monolith at {monolith_path}"))

    max_iterations = 15
    for _ in range(max_iterations):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        if not response.tool_calls:
            break

        for tool_call in response.tool_calls:
            matching = [t for t in tools if t.name == tool_call["name"]]
            if not matching:
                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(content=f"Tool {tool_call['name']} not found", tool_call_id=tool_call["id"]))
                continue
            tool = matching[0]
            result = tool.invoke(tool_call["args"])
            from langchain_core.messages import ToolMessage
            messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))

    raw = response.content
    parsed = _extract_json(raw)

    service_topology = parsed.get("service_topology", {"services": {}, "shared_entities": [], "output_base_path": ""})
    service_interfaces = parsed.get("service_interfaces", {})

    logger.info("Analyze complete: %d services identified", len(service_topology.get("services", {})))

    return {
        "messages": messages,
        "service_topology": service_topology,
        "service_interfaces": service_interfaces,
        "phase": "plan",
    }


def _extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from LLM text output."""
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
    logger.warning("Failed to parse JSON from LLM output, returning empty dict")
    return {}
