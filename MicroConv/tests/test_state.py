"""Tests for state definitions — verify TypedDict keys are consistent with design."""

from microconv.state import GlobalState, MicroserviceState


def test_global_state_has_expected_keys():
    expected = {
        "messages", "monolith_path", "target_services",
        "service_topology", "service_interfaces", "edit_list",
        "microservice_specs", "results",
        "phase", "iteration", "max_iterations",
    }
    assert set(GlobalState.__annotations__.keys()) == expected


def test_microservice_state_has_expected_keys():
    expected = {
        "messages", "service_name", "spec",
        "related_service_deps", "required_interfaces", "my_exposed_interfaces",
        "class_dependency_graph",
        "phase", "plan", "retry_count", "max_retries",
        "local_edits", "interface_corrections", "verification_result",
        "status", "error",
    }
    assert set(MicroserviceState.__annotations__.keys()) == expected
