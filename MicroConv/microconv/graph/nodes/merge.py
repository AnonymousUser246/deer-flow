"""Merge node — collects subgraph results and updates global memory."""

from __future__ import annotations

import logging

from microconv.memory import EditList, ServiceInterfaces, ServiceTopology
from microconv.state import GlobalState

logger = logging.getLogger(__name__)


def merge(state: GlobalState) -> dict:
    """Merge sub-agent results back into global memory."""
    results = state.get("results", [])
    topology = ServiceTopology.from_dict(state["service_topology"])
    interfaces = ServiceInterfaces.from_dict(state["service_interfaces"])
    edit_list = EditList.from_list(state.get("edit_list", []))

    succeeded = 0
    failed = 0

    for result in results:
        svc = result.get("service_name", "unknown")
        status = result.get("status", "failed")

        if status == "success":
            local_edits = result.get("local_edits", [])
            edit_list.extend_from_dicts(local_edits)

            corrections = result.get("interface_corrections")
            if corrections:
                interfaces.update_from_corrections(svc, corrections)

            succeeded += 1
        else:
            failed += 1
            logger.warning("Service %s migration %s: %s", svc, status, result.get("error", ""))

    logger.info("Merge complete: %d succeeded, %d failed", succeeded, failed)

    return {
        "edit_list": edit_list.to_list(),
        "service_topology": topology.to_dict(),
        "service_interfaces": interfaces.to_dict(),
        "phase": "check_complete",
        "results": [],
    }


def check_complete(state: GlobalState) -> str:
    """Route to END or back to plan for another iteration."""
    results = state.get("results", [])
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 3)

    has_failures = any(r.get("status") != "success" for r in results)

    if not has_failures:
        return "done"
    if iteration >= max_iterations:
        logger.warning("Max iterations (%d) reached with failures remaining", max_iterations)
        return "done"
    return "retry"
