"""Smoke tests for graph construction — verifies graphs compile without errors."""

from unittest.mock import MagicMock


def _make_mock_model():
    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    return model


def _make_mock_tools():
    tool = MagicMock()
    tool.name = "bash"
    return [tool]


def test_microservice_subgraph_compiles():
    from microconv.graph.microservice_graph import build_microservice_subgraph

    graph = build_microservice_subgraph(model=_make_mock_model(), tools=_make_mock_tools())
    assert graph is not None


def test_main_graph_compiles():
    from microconv.graph.main_graph import build_main_graph

    graph = build_main_graph(model=_make_mock_model(), tools=_make_mock_tools())
    assert graph is not None
