from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .blueprint import (
    ConnectivityEdge,
    ConnectivityGraph,
    ConnectivityNode,
    Corridor,
    Point,
    Room,
)
from .rng_streams import RNGStream


@dataclass(frozen=True)
class ConnectivityBuildResult:
    corridors: tuple[Corridor, ...]
    graph: ConnectivityGraph
    room_node: dict[str, str]  # room_id -> node_id
    corridor_node: dict[str, str]  # corridor_id -> node_id


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _l_corridor(a: Point, b: Point, rng: RNGStream) -> Tuple[Point, ...]:
    # L-shape via a bend point; choose orientation deterministically via RNG.
    if rng.rng.random() < 0.5:
        mid = Point(b.x, a.y)
    else:
        mid = Point(a.x, b.y)
    # Keep polyline as {a, mid, b} but avoid duplicate points
    pts = [a]
    if mid != a and mid != b:
        pts.append(mid)
    pts.append(b)
    return tuple(pts)


class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.r[ra] < self.r[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        if self.r[ra] == self.r[rb]:
            self.r[ra] += 1
        return True


def build_connectivity(
    rooms: tuple[Room, ...],
    *,
    rng: RNGStream,
    loop_chance: float = 0.15,
) -> ConnectivityBuildResult:
    """Build a connected corridor network over rooms.

    - Create a complete graph over room centroids.
    - Compute an MST (Kruskal) to guarantee connectivity.
    - Add extra edges with probability loop_chance.
    - For each selected edge, carve an L-shaped corridor polyline.

    Returns corridors plus a connectivity graph with nodes for rooms and corridors.
    """

    if len(rooms) < 2:
        # Degenerate; still return a graph
        nodes = tuple(
            ConnectivityNode(node_id=f"N{i:04d}", kind="room", ref_id=r.room_id)
            for i, r in enumerate(rooms)
        )
        return ConnectivityBuildResult(
            corridors=tuple(),
            graph=ConnectivityGraph(nodes=nodes, edges=tuple(), loops_added=0),
            room_node={r.room_id: f"N{i:04d}" for i, r in enumerate(rooms)},
            corridor_node={},
        )

    # Build weighted edge list between rooms
    room_list = list(rooms)
    edges: List[Tuple[float, int, int]] = []
    for i in range(len(room_list)):
        for j in range(i + 1, len(room_list)):
            w = _dist(room_list[i].centroid, room_list[j].centroid)
            edges.append((w, i, j))
    edges.sort(key=lambda t: (t[0], t[1], t[2]))

    dsu = _DSU(len(room_list))
    selected: List[Tuple[int, int]] = []
    non_mst: List[Tuple[int, int]] = []
    # Kruskal
    for _, i, j in edges:
        if dsu.union(i, j):
            selected.append((i, j))
        else:
            non_mst.append((i, j))

    loops_added = 0
    # Add loops
    for i, j in non_mst:
        if rng.rng.random() < float(loop_chance):
            selected.append((i, j))
            loops_added += 1

    # Create corridors
    corridors: List[Corridor] = []
    corridor_links: List[Tuple[str, str, str]] = []  # (room_id_a, room_id_b, corridor_id)
    for k, (i, j) in enumerate(selected):
        a = room_list[i]
        b = room_list[j]
        cid = f"C{k:04d}"
        poly = _l_corridor(a.centroid, b.centroid, rng)
        corridors.append(Corridor(corridor_id=cid, polyline=poly, tags=tuple()))
        corridor_links.append((a.room_id, b.room_id, cid))

    # Build connectivity nodes: rooms first, then corridors
    nodes: List[ConnectivityNode] = []
    room_node: Dict[str, str] = {}
    for i, r in enumerate(room_list):
        nid = f"N{i:04d}"
        room_node[r.room_id] = nid
        nodes.append(ConnectivityNode(node_id=nid, kind="room", ref_id=r.room_id))

    corridor_node: Dict[str, str] = {}
    base = len(nodes)
    for i, c in enumerate(corridors):
        nid = f"N{base + i:04d}"
        corridor_node[c.corridor_id] = nid
        nodes.append(ConnectivityNode(node_id=nid, kind="corridor", ref_id=c.corridor_id))

    # Graph edges: connect each corridor node to its two room nodes.
    g_edges: List[ConnectivityEdge] = []
    for idx, (ra, rb, cid) in enumerate(corridor_links):
        cn = corridor_node[cid]
        a = room_node[ra]
        b = room_node[rb]
        # Two edges corridor<->room; use via corridor. Doors will refine later.
        g_edges.append(
            ConnectivityEdge(edge_id=f"E{len(g_edges):04d}", a=a, b=cn, via="corridor", via_id=cid, weight=1.0)
        )
        g_edges.append(
            ConnectivityEdge(edge_id=f"E{len(g_edges):04d}", a=b, b=cn, via="corridor", via_id=cid, weight=1.0)
        )

    graph = ConnectivityGraph(nodes=tuple(nodes), edges=tuple(g_edges), loops_added=int(loops_added))
    return ConnectivityBuildResult(
        corridors=tuple(corridors),
        graph=graph,
        room_node=room_node,
        corridor_node=corridor_node,
    )
