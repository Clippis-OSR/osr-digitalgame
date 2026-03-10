from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional


SchemaVersion = str


@dataclass(frozen=True)
class Point:
    x: int
    y: int

    def to_dict(self) -> dict[str, int]:
        return {"x": int(self.x), "y": int(self.y)}


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict[str, int]:
        return {"x": int(self.x), "y": int(self.y), "w": int(self.w), "h": int(self.h)}


@dataclass(frozen=True)
class Room:
    room_id: str
    bounds: Rect
    centroid: Point
    tags: tuple[str, ...] = field(default_factory=tuple)
    depth_hint: int = 0
    floor_type: Optional[str] = None
    notes: Optional[str] = None
    role: Optional[str] = None
    theme_id: Optional[str] = None
    faction: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "room_id": self.room_id,
            "bounds": self.bounds.to_dict(),
            "centroid": self.centroid.to_dict(),
            "tags": list(self.tags),
        }
        if self.depth_hint:
            d["depth_hint"] = int(self.depth_hint)
        if self.floor_type:
            d["floor_type"] = str(self.floor_type)
        if self.notes:
            d["notes"] = str(self.notes)
        if self.role:
            d["role"] = str(self.role)
        if self.theme_id:
            d["theme_id"] = str(self.theme_id)
        if self.faction:
            d["faction"] = str(self.faction)
        return d


@dataclass(frozen=True)
class Corridor:
    corridor_id: str
    polyline: tuple[Point, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
    width: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "corridor_id": self.corridor_id,
            "polyline": [p.to_dict() for p in self.polyline],
            "width": int(self.width),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class EdgeEndpoint:
    kind: Literal["room", "corridor"]
    id: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id}


DoorType = Literal["normal", "stuck", "locked", "barred", "secret"]
Orientation = Literal["N", "S", "E", "W"]


@dataclass(frozen=True)
class DoorLock:
    difficulty: int

    def to_dict(self) -> dict[str, Any]:
        return {"difficulty": int(self.difficulty)}


@dataclass(frozen=True)
class DoorSecret:
    dc: int

    def to_dict(self) -> dict[str, Any]:
        return {"dc": int(self.dc)}


@dataclass(frozen=True)
class Door:
    door_id: str
    pos: Point
    orientation: Orientation
    connects: tuple[EdgeEndpoint, EdgeEndpoint]
    door_type: DoorType
    tags: tuple[str, ...] = field(default_factory=tuple)
    lock: Optional[DoorLock] = None
    secret: Optional[DoorSecret] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "door_id": self.door_id,
            "pos": self.pos.to_dict(),
            "orientation": self.orientation,
            "connects": [c.to_dict() for c in self.connects],
            "door_type": self.door_type,
            "tags": list(self.tags),
        }
        if self.lock is not None:
            d["lock"] = self.lock.to_dict()
        if self.secret is not None:
            d["secret"] = self.secret.to_dict()
        return d


@dataclass(frozen=True)
class Stairs:
    stairs_id: str
    pos: Point
    direction: Literal["up", "down"]
    connects_level_id: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    connects_stairs_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stairs_id": self.stairs_id,
            "pos": self.pos.to_dict(),
            "direction": self.direction,
            "connects_level_id": self.connects_level_id,
            "tags": list(self.tags),
        }
        if self.connects_stairs_id:
            d["connects_stairs_id"] = self.connects_stairs_id
        return d


@dataclass(frozen=True)
class Feature:
    feature_id: str
    feature_type: str
    pos: Point
    tags: tuple[str, ...] = field(default_factory=tuple)
    room_id: Optional[str] = None
    params: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "pos": self.pos.to_dict(),
            "tags": list(self.tags),
        }
        if self.room_id:
            d["room_id"] = self.room_id
        if self.params:
            d["params"] = self.params
        return d


@dataclass(frozen=True)
class ConnectivityNode:
    node_id: str
    kind: Literal["room", "corridor"]
    ref_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "kind": self.kind, "ref_id": self.ref_id}


ViaType = Literal["corridor", "door", "stairs", "open_boundary"]


@dataclass(frozen=True)
class ConnectivityEdge:
    edge_id: str
    a: str
    b: str
    via: ViaType
    via_id: Optional[str] = None
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "edge_id": self.edge_id,
            "a": self.a,
            "b": self.b,
            "via": self.via,
            "weight": float(self.weight),
        }
        if self.via_id is not None:
            d["via_id"] = self.via_id
        return d


@dataclass(frozen=True)
class ConnectivityGraph:
    nodes: tuple[ConnectivityNode, ...]
    edges: tuple[ConnectivityEdge, ...]
    loops_added: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "loops_added": int(self.loops_added),
        }


@dataclass(frozen=True)
class DungeonBlueprint:
    schema_version: SchemaVersion
    level_id: str
    seed: str
    rng_streams: dict[str, str]
    width: int
    height: int
    cell_size: int
    origin: Point
    rooms: tuple[Room, ...]
    corridors: tuple[Corridor, ...]
    doors: tuple[Door, ...]
    stairs: tuple[Stairs, ...]
    features: tuple[Feature, ...]
    connectivity: ConnectivityGraph
    context: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        # Stable ordering: everything already stored as tuples in deterministic order.
        return {
            "schema_version": self.schema_version,
            "level_id": self.level_id,
            "seed": self.seed,
            "rng_streams": dict(self.rng_streams),
            "bounds": {"width": int(self.width), "height": int(self.height)},
            "grid": {"cell_size": int(self.cell_size), "origin": self.origin.to_dict()},
            "rooms": [r.to_dict() for r in self.rooms],
            "corridors": [c.to_dict() for c in self.corridors],
            "doors": [d.to_dict() for d in self.doors],
            "stairs": [s.to_dict() for s in self.stairs],
            "features": [f.to_dict() for f in self.features],
            "connectivity": self.connectivity.to_dict(),
            **({"context": self.context} if self.context else {}),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def stable_hash_dict(obj: Any) -> str:
    """Canonical hash for arbitrary JSON-serializable objects."""
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _pairwise(it: Iterable[Any]) -> Iterable[tuple[Any, Any]]:
    it = iter(it)
    prev = next(it, None)
    for cur in it:
        if prev is None:
            break
        yield prev, cur
        prev = cur
