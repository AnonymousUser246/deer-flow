"""ServiceTopology — service-level dependency graph held by the Main Agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ServiceInfo:
    """Macro-level info about one microservice."""

    name: str
    description: str = ""
    classes: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    used_interfaces: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "classes": list(self.classes),
            "depends_on": list(self.depends_on),
            "used_interfaces": {k: list(v) for k, v in self.used_interfaces.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> ServiceInfo:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            classes=data.get("classes", []),
            depends_on=data.get("depends_on", []),
            used_interfaces=data.get("used_interfaces", {}),
        )


@dataclass
class ServiceTopology:
    """Service-level dependency topology — the Main Agent's macro view."""

    services: dict[str, ServiceInfo] = field(default_factory=dict)
    shared_entities: list[str] = field(default_factory=list)
    output_base_path: str = ""

    def get_dependents(self, service_name: str) -> dict[str, list[str]]:
        """Return services that depend on *service_name* and which interfaces they use."""
        result: dict[str, list[str]] = {}
        for svc_name, svc_info in self.services.items():
            if service_name in svc_info.depends_on:
                result[svc_name] = svc_info.used_interfaces.get(service_name, [])
        return result

    def to_dict(self) -> dict:
        return {
            "services": {k: v.to_dict() for k, v in self.services.items()},
            "shared_entities": list(self.shared_entities),
            "output_base_path": self.output_base_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ServiceTopology:
        services = {}
        for k, v in data.get("services", {}).items():
            services[k] = ServiceInfo.from_dict(v) if isinstance(v, dict) else v
        return cls(
            services=services,
            shared_entities=data.get("shared_entities", []),
            output_base_path=data.get("output_base_path", ""),
        )
