"""Dispatch node — fans out to N microservice subgraphs via Send()."""

from __future__ import annotations

import logging

from langgraph.types import Send

from microconv.memory import ServiceInterfaces, ServiceTopology
from microconv.state import GlobalState

logger = logging.getLogger(__name__)


def dispatch(state: GlobalState) -> list[Send]:
    """Create one Send() per microservice spec with filtered context."""
    topology = ServiceTopology.from_dict(state["service_topology"])
    interfaces = ServiceInterfaces.from_dict(state["service_interfaces"])

    sends: list[Send] = []
    for spec in state["microservice_specs"]:
        svc = spec["name"]
        svc_info = topology.services.get(svc)

        i_depend_on = svc_info.used_interfaces if svc_info else {}
        depend_on_me = topology.get_dependents(svc)

        required: dict[str, list[dict]] = {}
        for dep_svc in (svc_info.depends_on if svc_info else []):
            required[dep_svc] = [ep.to_dict() for ep in interfaces.get_interfaces_for(dep_svc)]

        my_exposed = {"exposed": [ep.to_dict() for ep in interfaces.get_interfaces_for(svc)]}

        sends.append(
            Send(
                "process_microservice",
                {
                    "messages": [],
                    "service_name": svc,
                    "spec": spec,
                    "related_service_deps": {
                        "my_service": svc,
                        "i_depend_on": i_depend_on,
                        "depend_on_me": depend_on_me,
                    },
                    "required_interfaces": required,
                    "my_exposed_interfaces": my_exposed,
                    "class_dependency_graph": {},
                    "phase": "explore",
                    "plan": None,
                    "retry_count": 0,
                    "max_retries": 3,
                    "local_edits": [],
                    "interface_corrections": None,
                    "verification_result": None,
                    "status": "running",
                    "error": None,
                },
            )
        )

    logger.info("Dispatching %d microservice subgraphs", len(sends))
    return sends
