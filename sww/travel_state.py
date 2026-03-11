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
    encounter_clock_ticks: int = 0
    encounter_next_check_tick: int = 1

    def advance_clock(self, *, travel_turns: int = 0, watch_turns: int = 0, encounter_ticks: int = 0) -> None:
        self.travel_turns = max(0, int(self.travel_turns) + max(0, int(travel_turns)))
        self.wilderness_clock_turns = max(0, int(self.wilderness_clock_turns) + max(0, int(watch_turns)))
        self.encounter_clock_ticks = max(0, int(self.encounter_clock_ticks) + max(0, int(encounter_ticks)))
        if int(self.encounter_next_check_tick or 0) <= 0:
            self.encounter_next_check_tick = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": str(self.location or "town"),
            "travel_turns": int(self.travel_turns or 0),
            "route_progress": int(self.route_progress or 0),
            "wilderness_clock_turns": int(self.wilderness_clock_turns or 0),
            "encounter_clock_ticks": int(self.encounter_clock_ticks or 0),
            "encounter_next_check_tick": int(self.encounter_next_check_tick or 1),
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
            encounter_clock_ticks = int(data.get("encounter_clock_ticks", 0) or 0)
        except Exception:
            encounter_clock_ticks = 0
        try:
            encounter_next_check_tick = int(data.get("encounter_next_check_tick", 1) or 1)
        except Exception:
            encounter_next_check_tick = 1
        return cls(
            location=location,
            travel_turns=max(0, travel_turns),
            route_progress=max(0, route_progress),
            wilderness_clock_turns=max(0, wilderness_clock_turns),
            encounter_clock_ticks=max(0, encounter_clock_ticks),
            encounter_next_check_tick=max(1, encounter_next_check_tick),
        )

