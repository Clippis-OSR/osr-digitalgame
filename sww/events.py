"""Lightweight event log.

This is the first step toward a more event-driven architecture.

Today the game is primarily an imperative loop (UI -> Game methods). We add an
event stream that the UI, scripted runner, tests, and future tooling can
observe *without* changing gameplay.

Events are NOT (yet) persisted in save files. They are meant for:
- journaling
- debugging
- deterministic test assertions

Later refactor steps can promote selected events into a persisted "journal".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Event:
    seq: int
    name: str
    data: Dict[str, Any]
    campaign_day: int
    day_watch: int
    dungeon_turn: int
    room_id: int
    hex_q: int
    hex_r: int


class EventLog:
    def __init__(self) -> None:
        self._seq = 0
        self.events: list[Event] = []
        self._subs_all: list[callable[[Event], None]] = []
        self._subs_by_name: dict[str, list[callable[[Event], None]]] = {}

    def subscribe_all(self, fn):
        self._subs_all.append(fn)

    def subscribe(self, name: str, fn):
        self._subs_by_name.setdefault(name, []).append(fn)

    def emit(
        self,
        name: str,
        *,
        data: Optional[Dict[str, Any] | Any] = None,
        campaign_day: int = 1,
        day_watch: int = 0,
        dungeon_turn: int = 0,
        room_id: int = 0,
        party_hex: tuple[int, int] = (0, 0),
    ) -> Event:
        self._seq += 1
        q, r = party_hex
        if data is None:
            payload = {}
        else:
            # Allow dataclass payloads for typed events.
            try:
                import dataclasses
                if dataclasses.is_dataclass(data):
                    payload = dataclasses.asdict(data)
                else:
                    payload = dict(data) if isinstance(data, dict) else {"value": data}
            except Exception:
                payload = dict(data) if isinstance(data, dict) else {"value": data}

        ev = Event(
            seq=self._seq,
            name=name,
            data=dict(payload),
            campaign_day=int(campaign_day),
            day_watch=int(day_watch),
            dungeon_turn=int(dungeon_turn),
            room_id=int(room_id),
            hex_q=int(q),
            hex_r=int(r),
        )
        self.events.append(ev)
        for fn in self._subs_all:
            fn(ev)
        for fn in self._subs_by_name.get(name, []):
            fn(ev)
        return ev
