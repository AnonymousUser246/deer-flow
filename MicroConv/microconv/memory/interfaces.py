"""ServiceInterfaces — API contract definitions held by the Main Agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class InterfaceEndpoint:
    """A single exposed API endpoint of a service."""

    name: str
    type: str = "REST"
    method: str = ""
    path: str = ""
    request: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "method": self.method,
            "path": self.path,
            "request": dict(self.request),
            "response": dict(self.response),
        }

    @classmethod
    def from_dict(cls, data: dict) -> InterfaceEndpoint:
        return cls(
            name=data["name"],
            type=data.get("type", "REST"),
            method=data.get("method", ""),
            path=data.get("path", ""),
            request=data.get("request", {}),
            response=data.get("response", {}),
        )


@dataclass
class ServiceInterfaces:
    """All services' exposed API contracts."""

    interfaces: dict[str, list[InterfaceEndpoint]] = field(default_factory=dict)

    def get_interfaces_for(self, service_name: str) -> list[InterfaceEndpoint]:
        return self.interfaces.get(service_name, [])

    def update_from_corrections(self, service_name: str, corrections: dict) -> None:
        """Apply interface corrections reported by a sub-agent."""
        if not corrections:
            return
        updated = corrections.get("exposed", [])
        if updated:
            self.interfaces[service_name] = [InterfaceEndpoint.from_dict(ep) for ep in updated]

    def to_dict(self) -> dict:
        return {k: [ep.to_dict() for ep in eps] for k, eps in self.interfaces.items()}

    @classmethod
    def from_dict(cls, data: dict) -> ServiceInterfaces:
        interfaces: dict[str, list[InterfaceEndpoint]] = {}
        for svc, eps in data.items():
            interfaces[svc] = [InterfaceEndpoint.from_dict(ep) if isinstance(ep, dict) else ep for ep in eps]
        return cls(interfaces=interfaces)
