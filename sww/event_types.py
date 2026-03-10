from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CommandDispatched:
    command: str
    status: str

@dataclass(frozen=True)
class StateDiff:
    changes: list[dict[str, Any]]

@dataclass(frozen=True)
class ValidationIssues:
    issues: list[str]
