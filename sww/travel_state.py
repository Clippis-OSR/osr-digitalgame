from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TOWN_HEX = (0, 0)
DUNGEON_ENTRANCE_HEX = (0, 1)


@dataclass
class TravelState:
    """Minimal P6.2 overworld travel ownership model.

    Keeps state intentionally small so it is stable for save/load and easy to
    evolve with future wilderness content.
    """

    location: str = "town"  # town | wilderness | dungeon
    travel_turns: int = 0
    route_progress: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": str(self.location or "town"),
            "travel_turns": int(self.travel_turns or 0),
            "route_progress": int(self.route_progress or 0),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TravelState":
        if not isinstance(data, dict):
            return cls()
        location = str(data.get("location") or "town").strip().lower()
        if location not in ("town", "wilderness", "dungeon"):
            location = "town"
        try:
            travel_turns = int(data.get("travel_turns", 0) or 0)
        except Exception:
            travel_turns = 0
        try:
            route_progress = int(data.get("route_progress", 0) or 0)
        except Exception:
            route_progress = 0
        return cls(location=location, travel_turns=max(0, travel_turns), route_progress=max(0, route_progress))

