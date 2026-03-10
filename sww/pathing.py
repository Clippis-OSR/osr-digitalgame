from __future__ import annotations

import heapq

from .grid_map import GridMap


def bfs_movement_range(gm: GridMap, start: tuple[int, int], max_cost: int, blocked: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """Return tiles reachable within max_cost using 4-dir movement.

    P4.5: supports weighted movement costs via Dijkstra. The name is kept for
    backwards compatibility.
    Returns a dict tile->cost.
    """
    sx, sy = start
    if not gm.in_bounds(sx, sy):
        return {}
    dist: dict[tuple[int, int], int] = {(sx, sy): 0}
    pq: list[tuple[int, int, int]] = [(0, sx, sy)]
    while pq:
        cost, x, y = heapq.heappop(pq)
        if cost != dist.get((x, y), 10**9):
            continue
        if cost > max_cost:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not gm.in_bounds(nx, ny):
                continue
            if gm.blocks_movement(nx, ny):
                continue
            if (nx, ny) in blocked and (nx, ny) != (sx, sy):
                continue
            step = int(gm.move_cost(nx, ny))
            nc = cost + step
            if nc > max_cost:
                continue
            if nc < dist.get((nx, ny), 10**9):
                dist[(nx, ny)] = nc
                heapq.heappush(pq, (nc, nx, ny))
    return dist


def bfs_shortest_path(gm: GridMap, start: tuple[int, int], goal: tuple[int, int], blocked: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Shortest path from start to goal (4-dir), respecting weighted move costs.

    Name kept for backwards compatibility. Uses Dijkstra; tie-breakers are stable.
    Path is returned as a list of tiles excluding the start and including the goal.
    """
    if start == goal:
        return []
    sx, sy = start
    gx, gy = goal
    if not gm.in_bounds(gx, gy) or gm.blocks_movement(gx, gy):
        return []
    if goal in blocked:
        return []

    dist: dict[tuple[int, int], int] = {(sx, sy): 0}
    prev: dict[tuple[int, int], tuple[int, int] | None] = {(sx, sy): None}
    pq: list[tuple[int, int, int]] = [(0, sx, sy)]
    while pq:
        cost, x, y = heapq.heappop(pq)
        if (x, y) == (gx, gy):
            break
        if cost != dist.get((x, y), 10**9):
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not gm.in_bounds(nx, ny):
                continue
            if gm.blocks_movement(nx, ny):
                continue
            if (nx, ny) in blocked and (nx, ny) != (gx, gy):
                continue
            step = int(gm.move_cost(nx, ny))
            nc = cost + step
            if nc < dist.get((nx, ny), 10**9):
                dist[(nx, ny)] = nc
                prev[(nx, ny)] = (x, y)
                heapq.heappush(pq, (nc, nx, ny))

    if (gx, gy) not in prev:
        return []

    # Reconstruct.
    cur = (gx, gy)
    path: list[tuple[int, int]] = []
    while cur != (sx, sy):
        path.append(cur)
        p = prev.get(cur)
        if p is None:
            break
        cur = p
    path.reverse()
    return path


# Alias for clarity in P4.5 controller.

def dijkstra_shortest_path(gm: GridMap, start: tuple[int,int], goal: tuple[int,int], blocked: set[tuple[int,int]]):
    return bfs_shortest_path(gm, start, goal, blocked)
