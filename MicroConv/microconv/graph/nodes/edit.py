"""Edit node — executes the migration plan by calling sandbox tools."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from microconv.prompts import EDIT_PROMPT
from microconv.state import MicroserviceState

logger = logging.getLogger(__name__)


def edit(state: MicroserviceState, *, model, tools) -> dict:
    """Execute the migration plan step by step using sandbox tools."""
    service_name = state["service_name"]
    plan = state.get("plan") or {"steps": []}

    prompt = EDIT_PROMPT.format(
        service_name=service_name,
        plan=json.dumps(plan, indent=2, ensure_ascii=False),
        required_interfaces=json.dumps(state["required_interfaces"], indent=2, ensure_ascii=False),
        class_dependency_graph=json.dumps(state["class_dependency_graph"], indent=2, ensure_ascii=False),
    )

    llm_with_tools = model.bind_tools(tools)
    messages: list = [SystemMessage(content=prompt), HumanMessage(content=f"Execute migration plan for {service_name}")]

    max_iterations = 40
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
    completed = parsed.get("completed_actions", [])
    existing_edits = list(state.get("local_edits") or [])
    existing_edits.extend(completed)

    logger.info("Edit complete for %s: %d action sets committed", service_name, len(completed))

    return {
        "messages": messages,
        "local_edits": existing_edits,
        "phase": "verify",
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
