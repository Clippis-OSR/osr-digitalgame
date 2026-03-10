"""Ports (interfaces) for dependency inversion.

This project is moving toward a cleaner architecture where the simulation
(the "engine") does not import or depend on terminal I/O.

These Protocols define the *minimum* surface area the engine needs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, Optional, List


@runtime_checkable
class UIProtocol(Protocol):
    """Minimal UI surface the game depends on."""

    auto_combat: bool
    auto_style: str

    def hr(self) -> None: ...

    def title(self, s: str) -> None: ...

    def prompt(self, s: str, default: Optional[str] = None) -> str: ...

    def choose(self, title: str, options: List[str]) -> int: ...

    def log(self, msg: str) -> None: ...

    def wrap(self, msg: str, width: int = 72) -> None: ...
