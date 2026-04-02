"""Plan node — produces microservice_specs from topology and interfaces."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from microconv.prompts import PLAN_PROMPT
from microconv.state import GlobalState

logger = logging.getLogger(__name__)


def plan(state: GlobalState, *, model) -> dict:
    """Produce microservice_specs based on the current topology and interfaces."""
    prompt = PLAN_PROMPT.format(
        service_topology=json.dumps(state["service_topology"], indent=2, ensure_ascii=False),
        service_interfaces=json.dumps(state["service_interfaces"], indent=2, ensure_ascii=False),
        target_services=json.dumps(state.get("target_services")),
    )

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content="Please produce the microservice specs."),
    ]
    response = model.invoke(messages)

    raw = response.content.strip()
    specs = _extract_json_array(raw)

    logger.info("Plan complete: %d microservice specs generated", len(specs))

    return {
        "messages": [response],
        "microservice_specs": specs,
        "phase": "dispatch",
    }


def _extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        end = next((i for i, l in enumerate(lines) if l.strip() == "```"), len(lines))
        text = "\n".join(lines[:end])
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        return [result]
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    logger.warning("Failed to parse specs from LLM output")
    return []
