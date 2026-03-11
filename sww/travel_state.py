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
    wilderness_clock_turns: int = 0
    condition_started_clock: int = 0
    travel_condition: str = "clear"

    def advance_clock(self, *, travel_turns: int = 0, watch_turns: int = 0, encounter_ticks: int = 0) -> None:
        """Advance lightweight wilderness clocks used by travel UX surfaces.

        The exact weighting is intentionally simple and deterministic.
        """
        dt = max(0, int(travel_turns or 0))
        dw = max(0, int(watch_turns or 0))
        de = max(0, int(encounter_ticks or 0))
        self.travel_turns = int(self.travel_turns or 0) + dt
        self.wilderness_clock_turns = int(self.wilderness_clock_turns or 0) + dt + dw + de

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": str(self.location or "town"),
            "travel_turns": int(self.travel_turns or 0),
            "route_progress": int(self.route_progress or 0),
            "wilderness_clock_turns": int(self.wilderness_clock_turns or 0),
            "condition_started_clock": int(self.condition_started_clock or 0),
            "travel_condition": str(self.travel_condition or "clear"),
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
        try:
            wilderness_clock_turns = int(data.get("wilderness_clock_turns", 0) or 0)
        except Exception:
            wilderness_clock_turns = 0
        try:
            condition_started_clock = int(data.get("condition_started_clock", 0) or 0)
        except Exception:
            condition_started_clock = 0
        travel_condition = str(data.get("travel_condition") or "clear").strip().lower()
        if travel_condition not in ("clear", "wind", "rain", "fog"):
            travel_condition = "clear"
        wilderness_clock_turns = max(0, wilderness_clock_turns)
        condition_started_clock = max(0, min(condition_started_clock, wilderness_clock_turns))
        return cls(
            location=location,
            travel_turns=max(0, travel_turns),
            route_progress=max(0, route_progress),
            wilderness_clock_turns=wilderness_clock_turns,
            condition_started_clock=condition_started_clock,
            travel_condition=travel_condition,
        )
