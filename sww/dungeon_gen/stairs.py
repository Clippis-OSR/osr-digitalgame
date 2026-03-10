from __future__ import annotations

import math
from typing import List, Tuple

from .blueprint import ConnectivityEdge, ConnectivityGraph, Point, Room, Stairs
from .rng_streams import RNGStream


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def place_stairs(
    rooms: tuple[Room, ...],
    *,
    level_id: str,
    depth: int,
    rng: RNGStream,
) -> tuple[Stairs, ...]:
    """Place stairs in far-apart rooms.

    P4.9.0/1 simplification: use Euclidean farthest pair.
    """

    if not rooms:
        return tuple()

    # Determine farthest pair
    far: Tuple[int, int] = (0, 0)
    best = -1.0
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            d = _dist(rooms[i].centroid, rooms[j].centroid)
            if d > best:
                best = d
                far = (i, j)

    a = rooms[far[0]]
    b = rooms[far[1]]

    stairs: List[Stairs] = []

    if depth <= 1:
        # Level 1: only down
        stairs.append(
            Stairs(
                stairs_id="S0000",
                pos=a.centroid,
                direction="down",
                connects_level_id=f"{level_id}:depth2",
                tags=("primary",),
            )
        )
    else:
        stairs.append(
            Stairs(
                stairs_id="S0000",
                pos=a.centroid,
                direction="up",
                connects_level_id=f"{level_id}:depth{depth-1}",
                tags=("primary",),
            )
        )
        stairs.append(
            Stairs(
                stairs_id="S0001",
                pos=b.centroid,
                direction="down",
                connects_level_id=f"{level_id}:depth{depth+1}",
                tags=("primary",),
            )
        )

    # Tie-break if both in same room (degenerate)
    if len(stairs) == 2 and stairs[0].pos == stairs[1].pos:
        # Nudge second to another room deterministically
        idx = rng.rng.randint(0, len(rooms) - 1)
        stairs[1] = Stairs(
            stairs_id=stairs[1].stairs_id,
            pos=rooms[idx].centroid,
            direction=stairs[1].direction,
            connects_level_id=stairs[1].connects_level_id,
            tags=stairs[1].tags,
            connects_stairs_id=stairs[1].connects_stairs_id,
        )

    return tuple(stairs)


def inject_stairs_into_connectivity(
    graph: ConnectivityGraph,
    rooms: tuple[Room, ...],
    *,
    room_node: dict[str, str],
    stairs: tuple[Stairs, ...],
) -> ConnectivityGraph:
    """Add stairs as edges anchored to the room containing the stair pos.

    In this milestone we simply connect the room node to itself via 'stairs'
    for auditability; the game layer can interpret stairs via the stairs list.
    """

    # Find nearest room for each stair by centroid distance.
    def nearest_room_id(p: Point) -> str:
        best = None
        best_d = 1e18
        for r in rooms:
            d = (r.centroid.x - p.x) ** 2 + (r.centroid.y - p.y) ** 2
            if d < best_d:
                best_d = d
                best = r.room_id
        return best or rooms[0].room_id

    edges = list(graph.edges)
    for st in stairs:
        rid = nearest_room_id(st.pos)
        n = room_node.get(rid)
        if not n:
            continue
        edges.append(
            ConnectivityEdge(
                edge_id=f"E{len(edges):04d}",
                a=n,
                b=n,
                via="stairs",
                via_id=st.stairs_id,
                weight=0.0,
            )
        )
    return ConnectivityGraph(nodes=graph.nodes, edges=tuple(edges), loops_added=graph.loops_added)
