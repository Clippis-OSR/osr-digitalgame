from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .blueprint import Point, Rect, Room
from .rng_streams import RNGStream


@dataclass
class _Region:
    x: int
    y: int
    w: int
    h: int
    depth: int


def _split_region(r: _Region, rng: RNGStream, *, min_w: int, min_h: int) -> Tuple[_Region, _Region] | None:
    # Decide split orientation based on aspect ratio; tie-break with RNG.
    can_split_h = r.h >= 2 * min_h
    can_split_v = r.w >= 2 * min_w
    if not can_split_h and not can_split_v:
        return None

    if can_split_h and can_split_v:
        # prefer splitting along longer axis
        if r.w > r.h:
            vertical = True
        elif r.h > r.w:
            vertical = False
        else:
            vertical = rng.rng.random() < 0.5
    else:
        vertical = can_split_v

    if vertical:
        # choose split x within [min_w, w-min_w]
        split = rng.rng.randint(min_w, r.w - min_w)
        a = _Region(r.x, r.y, split, r.h, r.depth + 1)
        b = _Region(r.x + split, r.y, r.w - split, r.h, r.depth + 1)
        return a, b
    else:
        split = rng.rng.randint(min_h, r.h - min_h)
        a = _Region(r.x, r.y, r.w, split, r.depth + 1)
        b = _Region(r.x, r.y + split, r.w, r.h - split, r.depth + 1)
        return a, b


def generate_rooms_bsp(
    *,
    width: int,
    height: int,
    depth: int,
    rng: RNGStream,
    max_splits: int = 7,
    min_region_w: int = 10,
    min_region_h: int = 10,
    margin: int = 1,
) -> Tuple[Room, ...]:
    """Generate rooms using a deterministic BSP approach.

    Returns rooms with stable IDs assigned by sorting (y, x, w, h).
    """
    root = _Region(0, 0, int(width), int(height), 0)
    regions: List[_Region] = [root]

    # Split phase
    for _ in range(int(max_splits)):
        # Choose a region to split: largest area first (deterministic ordering)
        regions.sort(key=lambda r: (-(r.w * r.h), r.y, r.x, r.w, r.h))
        target = regions[0]
        split = _split_region(target, rng, min_w=min_region_w, min_h=min_region_h)
        if not split:
            break
        regions.pop(0)
        regions.extend(list(split))

    # Carve a room within each leaf region
    rooms: List[Room] = []
    for reg in regions:
        # Room size ranges with margin
        max_w = max(1, reg.w - 2 * margin)
        max_h = max(1, reg.h - 2 * margin)
        # Ensure minimum usable room
        if max_w < 4 or max_h < 4:
            continue

        rw = rng.rng.randint(4, max_w)
        rh = rng.rng.randint(4, max_h)
        rx = rng.rng.randint(reg.x + margin, reg.x + reg.w - margin - rw)
        ry = rng.rng.randint(reg.y + margin, reg.y + reg.h - margin - rh)
        bounds = Rect(rx, ry, rw, rh)
        cx = rx + rw // 2
        cy = ry + rh // 2
        area = rw * rh
        tags = []
        if area >= 120:
            tags.append("large")
        if area <= 30:
            tags.append("small")
        # room_id assigned later
        rooms.append(
            Room(
                room_id="",
                bounds=bounds,
                centroid=Point(cx, cy),
                tags=tuple(tags),
                depth_hint=int(depth),
            )
        )

    # Assign stable IDs
    rooms.sort(key=lambda r: (r.bounds.y, r.bounds.x, r.bounds.w, r.bounds.h))
    out: List[Room] = []
    for i, r in enumerate(rooms):
        out.append(
            Room(
                room_id=f"R{i:04d}",
                bounds=r.bounds,
                centroid=r.centroid,
                tags=r.tags,
                depth_hint=r.depth_hint,
                floor_type=r.floor_type,
                notes=r.notes,
                role=r.role,
                theme_id=r.theme_id,
                faction=r.faction,
            )
        )
    return tuple(out)
