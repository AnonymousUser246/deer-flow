"""EditList — global edit transaction log held by the Main Agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileAction:
    """A single file operation within a transaction."""

    file: str
    action_type: str  # "create" | "modify" | "delete" | "move"
    description: str = ""

    def to_dict(self) -> dict:
        return {"file": self.file, "action_type": self.action_type, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> FileAction:
        return cls(file=data["file"], action_type=data["action_type"], description=data.get("description", ""))


@dataclass
class AtomicActionSet:
    """A group of file operations that form a logical transaction."""

    transaction_id: str
    service_name: str
    target: str
    actions: list[FileAction] = field(default_factory=list)
    status: str = "committed"

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "service_name": self.service_name,
            "target": self.target,
            "actions": [a.to_dict() for a in self.actions],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AtomicActionSet:
        return cls(
            transaction_id=data["transaction_id"],
            service_name=data["service_name"],
            target=data["target"],
            actions=[FileAction.from_dict(a) for a in data.get("actions", [])],
            status=data.get("status", "committed"),
        )


@dataclass
class EditList:
    """Append-only log of all committed edit transactions."""

    entries: list[AtomicActionSet] = field(default_factory=list)

    def append(self, action_set: AtomicActionSet) -> None:
        self.entries.append(action_set)

    def extend_from_dicts(self, dicts: list[dict]) -> None:
        for d in dicts:
            self.entries.append(AtomicActionSet.from_dict(d))

    def get_by_service(self, service_name: str) -> list[AtomicActionSet]:
        return [e for e in self.entries if e.service_name == service_name]

    def to_list(self) -> list[dict]:
        return [e.to_dict() for e in self.entries]

    @classmethod
    def from_list(cls, data: list[dict]) -> EditList:
        return cls(entries=[AtomicActionSet.from_dict(d) for d in data])
