"""Lightweight event log and persisted player-facing journal events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


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


@dataclass(frozen=True)
class PlayerEvent:
    type: str
    category: str
    day: int
    watch: int
    title: str
    payload: Dict[str, Any]
    refs: Dict[str, Any]
    visibility: str = "journal"
    schema: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": str(self.type or ""),
            "category": str(self.category or ""),
            "day": int(self.day or 1),
            "watch": int(self.watch or 0),
            "title": str(self.title or ""),
            "payload": dict(self.payload or {}),
            "refs": dict(self.refs or {}),
            "visibility": str(self.visibility or "journal"),
            "schema": int(self.schema or 1),
        }


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def normalize_player_event(raw: Dict[str, Any] | PlayerEvent | None) -> Dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, PlayerEvent):
        return raw.to_dict()
    if not isinstance(raw, dict):
        return None
    return {
        "type": str(raw.get("type") or ""),
        "category": str(raw.get("category") or ""),
        "day": _safe_int(raw.get("day", 1), 1),
        "watch": _safe_int(raw.get("watch", 0), 0),
        "title": str(raw.get("title") or ""),
        "payload": dict(raw.get("payload") or {}),
        "refs": dict(raw.get("refs") or {}),
        "visibility": str(raw.get("visibility") or "journal"),
        "schema": _safe_int(raw.get("schema", 1), 1),
    }


def append_player_event(
    event_history: list[dict[str, Any]],
    *,
    event_type: str,
    category: str,
    day: int,
    watch: int,
    title: str,
    payload: Optional[Dict[str, Any]] = None,
    refs: Optional[Dict[str, Any]] = None,
    visibility: str = "journal",
) -> Dict[str, Any]:
    entry = normalize_player_event(
        PlayerEvent(
            type=str(event_type or ""),
            category=str(category or ""),
            day=_safe_int(day, 1),
            watch=_safe_int(watch, 0),
            title=str(title or ""),
            payload=dict(payload or {}),
            refs=dict(refs or {}),
            visibility=str(visibility or "journal"),
            schema=1,
        )
    )
    if entry is None:
        entry = {
            "type": str(event_type or ""),
            "category": str(category or ""),
            "day": _safe_int(day, 1),
            "watch": _safe_int(watch, 0),
            "title": str(title or ""),
            "payload": dict(payload or {}),
            "refs": dict(refs or {}),
            "visibility": str(visibility or "journal"),
            "schema": 1,
        }
    event_history.append(entry)
    return entry


def project_discoveries_from_events(event_history: Iterable[Dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, str]] = set()
    for ev in (event_history or []):
        if str((ev or {}).get("type") or "") != "discovery.recorded":
            continue
        p = dict((ev or {}).get("payload") or {})
        row = {
            "day": _safe_int((ev or {}).get("day", p.get("day", 1)), 1),
            "watch": _safe_int((ev or {}).get("watch", p.get("watch", 0)), 0),
            "q": _safe_int(p.get("q", 0), 0),
            "r": _safe_int(p.get("r", 0), 0),
            "terrain": str(p.get("terrain") or ""),
            "kind": str(p.get("kind") or "poi"),
            "name": str(p.get("name") or "Unknown"),
            "note": str(p.get("note") or ""),
        }
        sig = (int(row["q"]), int(row["r"]), str(row["kind"]), str(row["name"]))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(row)
    return out


def project_rumors_from_events(event_history: Iterable[Dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in (event_history or []):
        if str((ev or {}).get("type") or "") != "rumor.learned":
            continue
        p = dict((ev or {}).get("payload") or {})
        out.append(dict(p))
    return out


def project_clues_from_events(event_history: Iterable[Dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for ev in (event_history or []):
        if str((ev or {}).get("type") or "") != "clue.found":
            continue
        p = dict((ev or {}).get("payload") or {})
        text = str(p.get("text") or "").strip()
        lvl = _safe_int(p.get("level", 0), 0)
        sig = (text, lvl)
        if not text or sig in seen:
            continue
        seen.add(sig)
        out.append(dict(p))
    return out


def project_district_notes_from_events(event_history: Iterable[Dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in (event_history or []):
        if str((ev or {}).get("type") or "") != "district.noted":
            continue
        p = dict((ev or {}).get("payload") or {})
        if not p:
            p = {
                "day": _safe_int((ev or {}).get("day", 1), 1),
                "watch": _safe_int((ev or {}).get("watch", 0), 0),
                "text": str((ev or {}).get("title") or ""),
            }
        out.append(p)
    return out


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
