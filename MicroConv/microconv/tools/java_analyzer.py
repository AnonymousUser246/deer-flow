"""Java source code analysis helpers (MVP: LLM-driven, no AST parsing)."""

from __future__ import annotations


def find_java_files(project_path: str) -> list[str]:
    """Recursively find all .java files under project_path."""
    from pathlib import Path

    return sorted(str(p) for p in Path(project_path).rglob("*.java"))


def find_pom_files(project_path: str) -> list[str]:
    """Find all pom.xml files under project_path."""
    from pathlib import Path

    return sorted(str(p) for p in Path(project_path).rglob("pom.xml"))
