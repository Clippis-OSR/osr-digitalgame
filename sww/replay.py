from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReplayLog:
    """In-memory command log for deterministic-ish replay/debugging.

    We record:
    - initial seeds
    - each dispatched command (type + args)
    - lightweight time markers

    If RNG usage stays stable, re-applying the same command sequence from the
    same seeds yields identical results.
    """

    dice_seed: int
    wilderness_seed: int
    commands: list[dict[str, Any]] = field(default_factory=list)
    rng_events: list[dict[str, Any]] = field(default_factory=list)
    ai_events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, entry: dict[str, Any]) -> None:
        self.commands.append(entry)

    def log_rng(self, name: str, data: dict[str, Any]) -> None:
        self.rng_events.append({"name": name, **data})

    def log_ai(self, name: str, data: dict[str, Any]) -> None:
        self.ai_events.append({"name": name, **data})

    def to_dict(self) -> dict[str, Any]:
        return {
            "dice_seed": int(self.dice_seed),
            "wilderness_seed": int(self.wilderness_seed),
            "commands": list(self.commands),
            "rng_events": list(self.rng_events),
            "ai_events": list(self.ai_events),
        }
