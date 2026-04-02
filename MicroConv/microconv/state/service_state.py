"""Per-microservice subgraph state."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph import add_messages


class MicroserviceState(TypedDict):
    """Sub-agent state — holds the micro-level view of a single service migration."""

    messages: Annotated[list, add_messages]

    # --- Identity ---
    service_name: str
    spec: dict

    # --- Filtered context from parent (read-only) ---
    related_service_deps: dict
    required_interfaces: dict
    my_exposed_interfaces: dict

    # --- Sub-agent internal memory (built during explore) ---
    class_dependency_graph: dict

    # --- Phase control ---
    phase: str
    plan: dict | None
    retry_count: int
    max_retries: int

    # --- Outputs (returned to parent) ---
    local_edits: list[dict]
    interface_corrections: dict | None
    verification_result: dict | None
    status: str
    error: str | None
