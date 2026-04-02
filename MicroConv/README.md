# MicroConv

Monolith-to-Microservice code transformation agent harness built on LangGraph.

## Architecture

```
Main Agent (GlobalState)
├── analyze    — scan monolith, build service topology + interface contracts
├── plan       — produce microservice specs
├── dispatch   — fan out to N microservice subgraphs (Send × N)
├── merge      — collect results, update global memory
└── check      — retry failed services or finish

Microservice Subgraph (MicroserviceState) × N
├── explore    — read source code, build class dependency graph, plan migration
├── edit       — execute file operations (create/modify/move)
└── verify     — compile check + interface consistency check (retry on failure)
```

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Run tests
pytest

# Use in code
from microconv.config import get_model, get_tools
from microconv.graph import build_main_graph

model = get_model()
tools = get_tools()
graph = build_main_graph(model=model, tools=tools)

result = graph.invoke({
    "messages": [],
    "monolith_path": "/path/to/monolith",
    "target_services": None,
    "service_topology": {},
    "service_interfaces": {},
    "edit_list": [],
    "microservice_specs": [],
    "results": [],
    "phase": "analyze",
    "iteration": 0,
    "max_iterations": 3,
})
```

## DeerFlow Integration

Set `MICROCONV_USE_DEERFLOW=true` to use DeerFlow's LLM factory and sandbox tools
instead of the built-in standalone implementations.

## Design

See [docs/DESIGN.md](docs/DESIGN.md) for the full architecture specification.
