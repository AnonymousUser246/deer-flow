"""ClassDependencyGraph — service-internal class-level dependency graph built by the Sub-agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClassNode:
    """Metadata for a single Java class within the service."""

    fqcn: str
    type: str = "unknown"  # "service" | "repository" | "entity" | "client" | "controller" | "config" | "util"
    package: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {"fqcn": self.fqcn, "type": self.type, "package": self.package, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict) -> ClassNode:
        return cls(fqcn=data["fqcn"], type=data.get("type", "unknown"), package=data.get("package", ""), note=data.get("note", ""))


@dataclass
class ClassEdge:
    """A dependency relationship between two classes."""

    source: str
    target: str
    type: str = "method_call"  # "injection" | "method_call" | "inheritance" | "generic_param"

    def to_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "type": self.type}

    @classmethod
    def from_dict(cls, data: dict) -> ClassEdge:
        return cls(source=data["source"], target=data["target"], type=data.get("type", "method_call"))


@dataclass
class ClassDependencyGraph:
    """Internal class-level dependency graph for one microservice."""

    nodes: dict[str, ClassNode] = field(default_factory=dict)
    edges: list[ClassEdge] = field(default_factory=list)

    def get_external_clients(self) -> list[ClassNode]:
        return [n for n in self.nodes.values() if n.type == "client"]

    def get_dependencies_of(self, class_name: str) -> list[ClassEdge]:
        return [e for e in self.edges if e.source == class_name]

    def to_dict(self) -> dict:
        return {
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClassDependencyGraph:
        nodes = {}
        for k, v in data.get("nodes", {}).items():
            nodes[k] = ClassNode.from_dict(v) if isinstance(v, dict) else v
        edges = [ClassEdge.from_dict(e) for e in data.get("edges", [])]
        return cls(nodes=nodes, edges=edges)
