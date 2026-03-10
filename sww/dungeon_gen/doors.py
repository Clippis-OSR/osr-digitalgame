from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .blueprint import (
    Door,
    DoorLock,
    DoorSecret,
    EdgeEndpoint,
    Orientation,
    Point,
    Rect,
    Room,
    ConnectivityEdge,
    ConnectivityGraph,
)
from .connectivity import ConnectivityBuildResult
from .rng_streams import RNGStream


def _door_type_from_d20(roll: int) -> str:
    if roll <= 12:
        return "normal"
    if roll <= 15:
        return "stuck"
    if roll <= 17:
        return "locked"
    if roll == 18:
        return "barred"
    return "secret"


def _door_weight(door_type: str) -> float:
    return {
        "normal": 1.0,
        "stuck": 1.2,
        "locked": 2.0,
        "barred": 3.0,
        "secret": 2.5,
    }[door_type]


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _door_on_room_boundary(room: Room, toward: Point) -> Tuple[Point, Orientation]:
    """Pick a boundary point on the room in the direction of `toward`.

    We place the door on the room wall cell just outside the room.
    """
    r: Rect = room.bounds
    cx, cy = room.centroid.x, room.centroid.y
    dx = 0 if toward.x == cx else (1 if toward.x > cx else -1)
    dy = 0 if toward.y == cy else (1 if toward.y > cy else -1)
    # Prefer axis with stronger delta.
    if abs(toward.x - cx) >= abs(toward.y - cy):
        dy = 0
    else:
        dx = 0

    if dx > 0:
        # east wall
        x = r.x + r.w
        y = _clamp(cy, r.y, r.y + r.h - 1)
        return Point(x, y), "E"
    if dx < 0:
        # west wall
        x = r.x - 1
        y = _clamp(cy, r.y, r.y + r.h - 1)
        return Point(x, y), "W"
    if dy > 0:
        # south wall
        y = r.y + r.h
        x = _clamp(cx, r.x, r.x + r.w - 1)
        return Point(x, y), "S"
    # north wall
    y = r.y - 1
    x = _clamp(cx, r.x, r.x + r.w - 1)
    return Point(x, y), "N"


@dataclass(frozen=True)
class DoorsResult:
    doors: tuple[Door, ...]
    connectivity: ConnectivityGraph
    roll_log: list[dict[str, Any]]
    adjusted_secrets: list[str]


def place_doors(
    rooms: tuple[Room, ...],
    conn: ConnectivityBuildResult,
    *,
    rng: RNGStream,
    depth: int,
) -> DoorsResult:
    """Place doors between room and corridor endpoints.

    P4.9.1 chooses one door per room<->corridor edge, positioned on the
    appropriate room boundary in the direction of the corridor.
    """

    room_by_id: Dict[str, Room] = {r.room_id: r for r in rooms}
    corridor_by_id = {c.corridor_id: c for c in conn.corridors}

    # Identify room<->corridor adjacency from base connectivity edges.
    candidates: List[Tuple[int, int, str, str, Point, Orientation]] = []
    # (y,x, room_id, corridor_id, pos, orientation)

    for e in conn.graph.edges:
        if e.via != "corridor":
            continue
        # One endpoint should be room node, other corridor node
        a_node = next((n for n in conn.graph.nodes if n.node_id == e.a), None)
        b_node = next((n for n in conn.graph.nodes if n.node_id == e.b), None)
        if not a_node or not b_node:
            continue
        if {a_node.kind, b_node.kind} != {"room", "corridor"}:
            continue
        room_id = a_node.ref_id if a_node.kind == "room" else b_node.ref_id
        corridor_id = a_node.ref_id if a_node.kind == "corridor" else b_node.ref_id
        room = room_by_id.get(room_id)
        corr = corridor_by_id.get(corridor_id)
        if not room or not corr:
            continue
        # Find corridor point "away" from room centroid
        toward = None
        for p in corr.polyline:
            if p != room.centroid:
                toward = p
                break
        if toward is None:
            toward = corr.polyline[-1]
        pos, orient = _door_on_room_boundary(room, toward)
        candidates.append((pos.y, pos.x, room_id, corridor_id, pos, orient))

    # Deduplicate by (x,y,orientation) keeping deterministic first
    candidates.sort(key=lambda t: (t[0], t[1], t[5], t[2], t[3]))
    seen = set()
    uniq: List[Tuple[str, str, Point, Orientation]] = []
    for y, x, room_id, corridor_id, pos, orient in candidates:
        key = (x, y, orient)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((room_id, corridor_id, pos, orient))

    roll_log: List[dict[str, Any]] = []
    doors_tmp: List[Tuple[str, str, Point, Orientation, str, DoorLock | None, DoorSecret | None]] = []

    for i, (room_id, corridor_id, pos, orient) in enumerate(uniq):
        d20 = int(rng.roll("1d20"))
        dt = _door_type_from_d20(d20)

        lock = None
        secret = None

        roll_log.append(
            {
                "step": "door.type",
                "stream": rng.name,
                "dice": "1d20",
                "result": d20,
                "table_id": f"door_type_depth_{int(depth)}",
                "choice": dt,
                "ref": f"D{i:04d}",
            }
        )

        if dt in ("locked", "barred"):
            # simple depth scaling
            base = int(depth) * 2
            extra = int(rng.roll("1d4"))
            diff = base + extra
            if dt == "barred":
                diff += 3
            lock = DoorLock(difficulty=diff)
            roll_log.append(
                {
                    "step": "door.lock",
                    "stream": rng.name,
                    "dice": "1d4",
                    "result": extra,
                    "table_id": f"door_lock_depth_{int(depth)}",
                    "choice": str(diff),
                    "ref": f"D{i:04d}",
                }
            )

        if dt == "secret":
            # OSR default 1-in-6 search chance represented as dc=1
            secret = DoorSecret(dc=1)
            roll_log.append(
                {
                    "step": "door.secret",
                    "stream": rng.name,
                    "dice": "fixed",
                    "result": 1,
                    "table_id": "secret_dc",
                    "choice": "1",
                    "ref": f"D{i:04d}",
                }
            )

        doors_tmp.append((room_id, corridor_id, pos, orient, dt, lock, secret))

    # Assign stable IDs by (y,x,orientation)
    doors_tmp.sort(key=lambda t: (t[2].y, t[2].x, t[3], t[0], t[1]))
    doors: List[Door] = []
    adjusted: List[str] = []
    for idx, (room_id, corridor_id, pos, orient, dt, lock, secret) in enumerate(doors_tmp):
        door_id = f"D{idx:04d}"
        doors.append(
            Door(
                door_id=door_id,
                pos=pos,
                orientation=orient,
                connects=(EdgeEndpoint("room", room_id), EdgeEndpoint("corridor", corridor_id)),
                door_type=dt,  # may be adjusted later
                tags=tuple(),
                lock=lock,
                secret=secret,
            )
        )

    # Safety adjustment: ensure not all doors adjacent to the first stair room are secret.
    # (Full path-based secret validation comes later; this prevents the worst "all secret exits" cases.)
    if rooms and doors:
        # pick "entry" room as first room (stable) for now
        entry_room = rooms[0].room_id
        incident = [d for d in doors if any(ep.kind == "room" and ep.id == entry_room for ep in d.connects)]
        if incident and all(d.door_type == "secret" for d in incident):
            # convert first to normal
            d0 = incident[0]
            new_d0 = Door(
                door_id=d0.door_id,
                pos=d0.pos,
                orientation=d0.orientation,
                connects=d0.connects,
                door_type="normal",
                tags=d0.tags,
                lock=None,
                secret=None,
            )
            doors = [new_d0 if d.door_id == d0.door_id else d for d in doors]
            adjusted.append(d0.door_id)
            roll_log.append(
                {
                    "step": "door.secret.adjusted",
                    "stream": rng.name,
                    "dice": "policy",
                    "result": 0,
                    "table_id": "secret_safety",
                    "choice": "secret->normal",
                    "ref": d0.door_id,
                }
            )

    # Inject door weights into connectivity graph by replacing corridor edges linking rooms to corridors.
    door_by_pair: Dict[Tuple[str, str], Door] = {}
    for d in doors:
        room_id = next(ep.id for ep in d.connects if ep.kind == "room")
        corridor_id = next(ep.id for ep in d.connects if ep.kind == "corridor")
        door_by_pair[(room_id, corridor_id)] = d

    new_edges: List[ConnectivityEdge] = []
    for e in conn.graph.edges:
        if e.via != "corridor":
            new_edges.append(e)
            continue
        a_node = next((n for n in conn.graph.nodes if n.node_id == e.a), None)
        b_node = next((n for n in conn.graph.nodes if n.node_id == e.b), None)
        if not a_node or not b_node or {a_node.kind, b_node.kind} != {"room", "corridor"}:
            new_edges.append(e)
            continue
        room_id = a_node.ref_id if a_node.kind == "room" else b_node.ref_id
        corridor_id = a_node.ref_id if a_node.kind == "corridor" else b_node.ref_id
        d = door_by_pair.get((room_id, corridor_id))
        if not d:
            new_edges.append(e)
            continue
        new_edges.append(
            ConnectivityEdge(
                edge_id=e.edge_id,
                a=e.a,
                b=e.b,
                via="door",
                via_id=d.door_id,
                weight=_door_weight(d.door_type),
            )
        )

    connectivity = ConnectivityGraph(nodes=conn.graph.nodes, edges=tuple(new_edges), loops_added=conn.graph.loops_added)
    return DoorsResult(doors=tuple(doors), connectivity=connectivity, roll_log=roll_log, adjusted_secrets=adjusted)
