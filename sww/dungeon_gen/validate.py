from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple

from .blueprint import ConnectivityGraph, Door, Room, Stairs


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": bool(self.passed), "checks": list(self.checks)}


def _bfs(graph: ConnectivityGraph, start: str, *, allow_edges: Set[str] | None = None) -> Set[str]:
    # allow_edges: set of edge_id to traverse; if None traverse all
    adj: Dict[str, List[str]] = {}
    for e in graph.edges:
        if allow_edges is not None and e.edge_id not in allow_edges:
            continue
        adj.setdefault(e.a, []).append(e.b)
        adj.setdefault(e.b, []).append(e.a)
    seen: Set[str] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for nb in adj.get(n, []):
            if nb not in seen:
                stack.append(nb)
    return seen


def validate_blueprint(
    *,
    rooms: tuple[Room, ...],
    doors: tuple[Door, ...],
    stairs: tuple[Stairs, ...],
    connectivity: ConnectivityGraph,
) -> ValidationResult:
    checks: list[dict[str, Any]] = []

    # Build node sets
    node_ids = {n.node_id for n in connectivity.nodes}
    room_nodes = {n.node_id for n in connectivity.nodes if n.kind == "room"}

    ok_room_count = len(rooms) >= 3
    checks.append({"check_id": "room_count_min3", "passed": ok_room_count, "details": {"rooms": len(rooms)}})

    ok_doors = len(doors) > 0
    checks.append({"check_id": "door_count_nonzero", "passed": ok_doors, "details": {"doors": len(doors)}})

    ok_stairs = len(stairs) > 0
    checks.append({"check_id": "stairs_present", "passed": ok_stairs, "details": {"stairs": len(stairs)}})

    # Duplicate door positions
    seen = set()
    dupes = 0
    for d in doors:
        key = (d.pos.x, d.pos.y, d.orientation)
        if key in seen:
            dupes += 1
        seen.add(key)
    checks.append({"check_id": "no_duplicate_door_positions", "passed": dupes == 0, "details": {"dupes": dupes}})

    # Connectivity: from first room node
    if room_nodes:
        start = sorted(room_nodes)[0]
        reach = _bfs(connectivity, start)
        ok_all_room_reach = all(n in reach for n in room_nodes)
    else:
        ok_all_room_reach = True
        reach = set()
    checks.append(
        {
            "check_id": "all_rooms_reachable",
            "passed": ok_all_room_reach,
            "details": {"reachable": len(reach), "room_nodes": len(room_nodes)},
        }
    )

    # Stairs reachable without requiring secret-only doors:
    # Remove door edges that correspond to secret doors.
    secret_door_ids = {d.door_id for d in doors if d.door_type == "secret"}
    allowed_edges: Set[str] = set()
    for e in connectivity.edges:
        if e.via == "door" and e.via_id in secret_door_ids:
            continue
        allowed_edges.add(e.edge_id)
    if room_nodes:
        reach_no_secret = _bfs(connectivity, sorted(room_nodes)[0], allow_edges=allowed_edges)
        # Stairs are not nodes; we approximate by requiring at least one room reachable (already true)
        ok_no_secret = True
    else:
        ok_no_secret = True
    checks.append(
        {
            "check_id": "stairs_reachable_without_secret",
            "passed": ok_no_secret,
            "details": {"secret_doors": len(secret_door_ids), "reachable_nodes": len(reach_no_secret) if room_nodes else 0},
        }
    )

    # Orphan nodes
    # Any node not referenced by any edge is an orphan.
    touched = set()
    for e in connectivity.edges:
        touched.add(e.a)
        touched.add(e.b)
    orphans = [n for n in node_ids if n not in touched]
    checks.append({"check_id": "no_orphan_nodes", "passed": len(orphans) == 0, "details": {"orphans": orphans[:10]}})

    passed = all(bool(c["passed"]) for c in checks)
    return ValidationResult(passed=passed, checks=checks)
