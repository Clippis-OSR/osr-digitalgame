from __future__ import annotations

from dataclasses import dataclass


Tile = str  # "floor" | "wall" | "cover" | "rubble" | doors


@dataclass
class GridMap:
    width: int
    height: int
    tiles: list[list[Tile]]

    @classmethod
    def empty(cls, width: int, height: int, tile: Tile = "floor") -> "GridMap":
        return cls(width=width, height=height, tiles=[[tile for _ in range(width)] for _ in range(height)])

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> Tile:
        if not self.in_bounds(x, y):
            return "wall"
        return self.tiles[y][x]

    def set(self, x: int, y: int, tile: Tile) -> None:
        if self.in_bounds(x, y):
            self.tiles[y][x] = tile


    def is_door(self, x: int, y: int) -> bool:
        t = self.get(x, y)
        return t.startswith("door_")

    def door_state(self, x: int, y: int) -> str | None:
        t = self.get(x, y)
        if not t.startswith("door_"):
            return None
        return t[len("door_"):]

    def blocks_los(self, x: int, y: int) -> bool:
        t = self.get(x, y)
        if t in ("wall","wall_secret"):
            return True
        # Closed/locked/stuck/spiked doors block line of sight.
        if t in ("door_closed", "door_locked", "door_stuck", "door_spiked"):
            return True
        return False
    def blocks_movement(self, x: int, y: int) -> bool:
        t = self.get(x, y)
        if t in ("wall","wall_secret"):
            return True
        if t in ("door_closed", "door_locked", "door_stuck", "door_spiked"):
            return True
        return False

    def move_cost(self, x: int, y: int) -> int:
        """Movement cost for entering a tile (1 normal, 2 difficult).

        Walls are treated as impassable elsewhere.
        """
        t = self.get(x, y)
        if t == "rubble":
            return 2
        return 1


    def is_cover(self, x: int, y: int) -> bool:
        return self.get(x, y) == "cover"


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def bresenham_line(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Return cells on the line from a to b (inclusive) using Bresenham."""
    x0, y0 = a
    x1, y1 = b
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    out: list[tuple[int, int]] = []
    while True:
        out.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return out


def has_line_of_sight(gm: GridMap, a: tuple[int, int], b: tuple[int, int]) -> bool:
    """LOS is blocked by walls. Cover does not block LOS in the prototype."""
    pts = bresenham_line(a, b)
    # Skip the first/last cell (standing in a wall is handled elsewhere).
    for (x, y) in pts[1:-1]:
        if gm.blocks_los(x, y):
            return False
    return True
