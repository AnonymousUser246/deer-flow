"""Verify node — compilation check + interface consistency check."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from microconv.prompts import VERIFY_PROMPT
from microconv.state import MicroserviceState

logger = logging.getLogger(__name__)


def verify(state: MicroserviceState, *, model, tools) -> dict:
    """Run compilation and interface consistency checks."""
    service_name = state["service_name"]
    spec = state.get("spec") or {}
    service_path = spec.get("target_path", f"/mnt/user-data/workspace/output/{service_name}")

    prompt = VERIFY_PROMPT.format(
        service_name=service_name,
        service_path=service_path,
        my_exposed_interfaces=json.dumps(state["my_exposed_interfaces"], indent=2, ensure_ascii=False),
        required_interfaces=json.dumps(state["required_interfaces"], indent=2, ensure_ascii=False),
    )

    llm_with_tools = model.bind_tools(tools)
    messages: list = [SystemMessage(content=prompt), HumanMessage(content=f"Verify migration for {service_name}")]

    max_iterations = 10
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

    verification = parsed.get("verification_result", {
        "compile_success": False,
        "compile_errors": ["Unable to parse verification output"],
        "interface_issues": [],
        "verdict": "fail_compile",
    })
    corrections = parsed.get("interface_corrections")

    verdict = verification.get("verdict", "fail_compile")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    if verdict == "pass":
        status = "success"
        next_phase = "done"
    elif retry_count >= max_retries:
        status = "max_retries_exceeded"
        next_phase = "done"
    else:
        status = "running"
        next_phase = "edit" if verdict == "fail_compile" else "explore"

    new_retry = retry_count + (0 if verdict == "pass" else 1)

    logger.info("Verify %s: verdict=%s, retry=%d/%d", service_name, verdict, new_retry, max_retries)

    return {
        "messages": messages,
        "verification_result": verification,
        "interface_corrections": corrections,
        "retry_count": new_retry,
        "phase": next_phase,
        "status": status,
        "error": None if verdict == "pass" else json.dumps(verification.get("compile_errors", [])),
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
