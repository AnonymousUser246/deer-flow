"""Global state for the Main Agent graph."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import add_messages


class GlobalState(TypedDict):
    """Main Agent state — holds the macro-level view of the entire migration."""

    messages: Annotated[list, add_messages]

    # --- Input ---
    monolith_path: str
    target_services: list[str] | None

    # --- Main Agent Memory ---
    service_topology: dict      # ServiceTopology serialized
    service_interfaces: dict    # ServiceInterfaces serialized
    edit_list: list[dict]       # EditList serialized

    # --- Task dispatch ---
    microservice_specs: list[dict]
    results: Annotated[list, operator.add]

    # --- Control ---
    phase: str
    iteration: int
    max_iterations: int
