from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


# Axial coordinates (q, r)
# Directions are ordered to be stable for UI and random choices.
DIRECTIONS: Dict[str, Tuple[int, int]] = {
    "N": (0, -1),
    "NE": (1, -1),
    "SE": (1, 0),
    "S": (0, 1),
    "SW": (-1, 1),
    "NW": (-1, 0),
}


def hex_distance(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    """Axial hex distance."""
    aq, ar = a
    bq, br = b
    # Convert to cube coords (x=q, z=r, y=-x-z)
    ax, az = aq, ar
    ay = -ax - az
    bx, bz = bq, br
    by = -bx - bz
    return max(abs(ax - bx), abs(ay - by), abs(az - bz))


def neighbor(pos: Tuple[int, int], direction: str) -> Tuple[int, int]:
    dq, dr = DIRECTIONS[direction]
    return (pos[0] + dq, pos[1] + dr)


def neighbors(pos: Tuple[int, int]) -> Dict[str, Tuple[int, int]]:
    return {d: neighbor(pos, d) for d in DIRECTIONS}


def terrain_for_hex(rng: Any, dist_from_town: int) -> str:
    """Procedural terrain with a gentle difficulty ramp.

    Moderate/forgiving: more "clear" near town, mountains rare.
    """
    # Close to town, favor clear/road.
    if dist_from_town <= 1 and rng.random() < 0.20:
        return "road"
    if dist_from_town <= 2 and rng.random() < 0.10:
        return "road"

    roll = rng.random()
    # Base weights (sum ~1.0)
    # clear 0.40, forest 0.25, hills 0.18, swamp 0.10, mountain 0.07
    # Nudge mountains slightly upward far away, but keep rare.
    mountain_bonus = min(0.03, max(0.0, (dist_from_town - 6) * 0.002))
    clear_w = 0.40 - mountain_bonus
    forest_w = 0.25
    hills_w = 0.18
    swamp_w = 0.10
    mountain_w = 0.07 + mountain_bonus

    cut1 = clear_w
    cut2 = cut1 + forest_w
    cut3 = cut2 + hills_w
    cut4 = cut3 + swamp_w
    if roll < cut1:
        return "clear"
    if roll < cut2:
        return "forest"
    if roll < cut3:
        return "hills"
    if roll < cut4:
        return "swamp"
    return "mountain"


def _poi_chance(dist_from_town: int) -> float:
    """Chance a newly generated hex contains a point of interest.

    Moderate/forgiving: a little something near town, more as you push outward.
    """
    if dist_from_town <= 1:
        return 0.08
    if dist_from_town <= 3:
        return 0.10
    if dist_from_town <= 6:
        return 0.14
    return 0.18


def roll_poi(rng: Any, dist_from_town: int, terrain: str) -> Optional[dict[str, Any]]:
    """Create a persistent POI structure for a hex (or None)."""
    if rng.random() >= _poi_chance(dist_from_town):
        return None

    # Weight POI types.
    # Terrain shapes what you find, and distance increases the odds of lairs and dungeon entrances.
    base = {
        "ruins": 0.28,
        "shrine": 0.18,
        "lair": 0.16,
        "dungeon_entrance": 0.14,
        "abandoned_camp": 0.10,
        "caravan": 0.08,
        "outpost": 0.06,
    }

    # Distance ramp (moderate): more lairs/entrances as you push outward.
    base["dungeon_entrance"] += min(0.10, dist_from_town * 0.01)
    base["lair"] += min(0.12, dist_from_town * 0.015)
    # Keep total roughly stable by shaving ruins first.
    shave = min(base["ruins"] * 0.5, (base["dungeon_entrance"] - 0.16) + (base["lair"] - 0.18))
    base["ruins"] = max(0.20, base["ruins"] - shave)

    # Terrain nudges.
    if terrain in ("forest",):
        base["lair"] += 0.05
        base["ruins"] -= 0.03
        base["abandoned_camp"] += 0.02
    elif terrain in ("swamp",):
        base["shrine"] += 0.04
        base["lair"] += 0.04
        base["ruins"] -= 0.04
        base["abandoned_camp"] += 0.01
    elif terrain in ("hills",):
        base["ruins"] += 0.03
        base["lair"] += 0.02
        base["shrine"] -= 0.02
        base["outpost"] += 0.02
    elif terrain in ("mountain",):
        base["dungeon_entrance"] += 0.06
        base["ruins"] -= 0.04
        base["outpost"] += 0.02
    elif terrain in ("road", "clear"):
        base["ruins"] += 0.02
        base["caravan"] += 0.05
        base["outpost"] += 0.01

    # Clamp to avoid negative weights.
    for k in list(base.keys()):
        base[k] = max(0.02, float(base[k]))

    ruins_w = base["ruins"]
    shrine_w = base["shrine"]
    lair_w = base["lair"]
    dungeon_w = base["dungeon_entrance"]
    camp_w = base["abandoned_camp"]
    caravan_w = base["caravan"]
    outpost_w = base["outpost"]
    total = dungeon_w + lair_w + shrine_w + ruins_w + camp_w + caravan_w + outpost_w
    roll = rng.random() * total

    if roll < ruins_w:
        ruins_names = {
            "clear": ["Crumbling Ruins", "Abandoned Farmstead", "Collapsed Bridgehouse", "Burnt Mill"],
            "road": ["Roadside Watchtower", "Broken Tollhouse", "Ruined Inn", "Collapsed Bridgehouse"],
            "forest": ["Overgrown Manor", "Moss-Covered Cairns", "Stone Circle", "Forgotten Hunting Lodge"],
            "hills": ["Old Watchtower", "Hill Fort", "Broken Manor", "Tumbled Standing Stones"],
            "swamp": ["Sunken Causeway", "Half-Sunk Chapel", "Ruined Wharf", "Drowned Stones"],
            "mountain": ["Crumbling Outpost", "Collapsed Mine Works", "High Shrine Ruin", "Wind-Scoured Cairn"],
        }
        name = rng.choice(ruins_names.get(terrain, ruins_names["clear"]))
        return {
            "type": "ruins",
            "name": name,
            "discovered": False,
            "resolved": False,
            "notes": "A place of old stones and lingering secrets.",
            "seed": rng.randrange(1_000_000_000),
        }

    roll -= ruins_w
    if roll < shrine_w:
        shrine_names = {
            "clear": ["Wayside Shrine", "Stone Altar", "Old Boundary Marker", "Spring of Quiet Waters"],
            "road": ["Roadside Shrine", "Pilgrim's Marker", "Stone Altar", "Wayside Chapel"],
            "forest": ["Grove Shrine", "Hollow Oak Altar", "Stone Altar", "Quiet Spring"],
            "hills": ["Hilltop Shrine", "Wind Shrine", "Stone Altar", "Forgotten Chapel"],
            "swamp": ["Bog Shrine", "Lantern Post", "Blackwater Altar", "Whispering Spring"],
            "mountain": ["High Chapel", "Storm Cairn", "Stone Altar", "Shrine of Thin Air"],
        }
        name = rng.choice(shrine_names.get(terrain, shrine_names["clear"]))
        return {
            "type": "shrine",
            "name": name,
            "discovered": False,
            "resolved": False,
            "notes": "A place of calm (and perhaps a price).",
            "seed": rng.randrange(1_000_000_000),
        }

    roll -= shrine_w
    if roll < camp_w:
        camp_names = {
            "clear": ["Cold Campfire", "Abandoned Wagon Camp", "Forsaken Lean-To", "Scattered Bedrolls"],
            "road": ["Roadside Camp", "Broken Caravan Camp", "Ash Pit", "Wayfarers' Site"],
            "forest": ["Hunter's Camp", "Mossy Camp", "Woodcutter's Fire Ring", "Hidden Lean-To"],
            "hills": ["Ridge Camp", "Windbreak Camp", "Stone Fire Ring", "Collapsed Tents"],
            "swamp": ["Reed Camp", "Mud Camp", "Sodden Fire Ring", "Half-Sunk Camp"],
            "mountain": ["Goat Trail Camp", "Scree Camp", "Cold Fire Ring", "High Camp"],
        }
        name = rng.choice(camp_names.get(terrain, camp_names["clear"]))
        return {
            "type": "abandoned_camp",
            "name": name,
            "discovered": False,
            "resolved": False,
            "notes": "Signs of recent travelers, though no one remains.",
            "seed": rng.randrange(1_000_000_000),
        }

    roll -= camp_w
    if roll < caravan_w:
        caravan_names = {
            "clear": ["Merchant Caravan", "Pack Train", "Traveling Peddlers", "Westroad Traders"],
            "road": ["Road Caravan", "Guild Wagon Train", "Pilgrim Caravan", "Merchants on the Road"],
            "forest": ["Woodland Caravan", "Trappers' Train", "Traveling Traders", "Forest Road Merchants"],
            "hills": ["Hill Caravan", "Mule Train", "Surveyors' Train", "Traveling Traders"],
            "swamp": ["Fen Traders", "Flatboat Porters", "Reed Road Caravan", "Mud Track Traders"],
            "mountain": ["Mountain Train", "High Pass Traders", "Sure-Footed Caravan", "Goat Pack Train"],
        }
        name = rng.choice(caravan_names.get(terrain, caravan_names["clear"]))
        return {
            "type": "caravan",
            "name": name,
            "discovered": False,
            "resolved": False,
            "notes": "Travelers and merchants on the move.",
            "seed": rng.randrange(1_000_000_000),
        }

    roll -= caravan_w
    if roll < outpost_w:
        outpost_names = {
            "clear": ["Border Outpost", "Watch Post", "Guild Waystation", "Patrol Camp"],
            "road": ["Road Watch", "Toll Outpost", "Patrol Station", "Guild Waystation"],
            "forest": ["Forest Watch", "Wardens' Post", "Boundary Outpost", "Hidden Watch"],
            "hills": ["Hill Outpost", "Signal Tower", "Stone Watch", "Ridge Station"],
            "swamp": ["Causeway Post", "Fen Watch", "Raised Outpost", "Reed Tower"],
            "mountain": ["Pass Fort", "High Watch", "Cliff Outpost", "Stone Redoubt"],
        }
        name = rng.choice(outpost_names.get(terrain, outpost_names["clear"]))
        return {
            "type": "outpost",
            "name": name,
            "discovered": False,
            "resolved": False,
            "notes": "A faction holding meant to watch the roads and wilds.",
            "seed": rng.randrange(1_000_000_000),
        }

    roll -= outpost_w
    if roll < lair_w:
        lair_names = {
            "clear": ["Bandit Hideout", "Ravine Camp", "Beast Lair", "Burrow"],
            "road": ["Bandit Road Camp", "Hidden Ditch", "Ambush Hollow", "Beast Lair"],
            "forest": ["Overgrown Den", "Hunter's Camp", "Webbed Thicket", "Hollow"],
            "hills": ["Ravine Camp", "Rocky Den", "Hill Caves", "Bandit Hideout"],
            "swamp": ["Sodden Den", "Reed Camp", "Sinkhole Lair", "Bog Caves"],
            "mountain": ["Cave Mouth", "High Ledge Den", "Goat Paths Camp", "Rockfall Caves"],
        }
        name = rng.choice(lair_names.get(terrain, lair_names["clear"]))
        return {
            "type": "lair",
            "name": name,
            "discovered": False,
            "resolved": False,
            "notes": "Signs of habitation and danger.",
            "seed": rng.randrange(1_000_000_000),
        }

    # Dungeon entrance
    entrance_names = {
        "clear": ["Hidden Barrow", "Cairn Entrance", "Sealed Stone Door", "Mossy Stair"],
        "road": ["Warded Culvert", "Sealed Stone Door", "Collapsed Cellar Steps", "Old Mile-Stone Door"],
        "forest": ["Mossy Stair", "Root-Choked Door", "Hidden Barrow", "Thorn-Covered Descent"],
        "hills": ["Cairn Entrance", "Barrow Mound", "Sinkhole Descent", "Stone Door in a Slope"],
        "swamp": ["Sinkhole Descent", "Drowned Stair", "Mire Door", "Half-Submerged Arch"],
        "mountain": ["Crack in the Cliff", "Collapsed Mine Shaft", "High Gate", "Stair in the Scree"],
    }
    name = rng.choice(entrance_names.get(terrain, entrance_names["clear"]))
    return {
        "type": "dungeon_entrance",
        "name": name,
        "discovered": False,
        "resolved": False,
        "notes": "A way down into darkness.",
        "seed": rng.randrange(1_000_000_000),
        "dungeon_state": None,
    }


@dataclass
class Hex:
    q: int
    r: int
    terrain: str
    discovered: bool = False
    visited: int = 0
    poi: Optional[dict[str, Any]] = None
    lair: Optional[dict[str, Any]] = None
    faction_id: Optional[str] = None
    heat: int = 0  # patrol intensity / contested pressure

    def key(self) -> str:
        return f"{self.q},{self.r}"

    def to_dict(self) -> dict:
        return {
            "q": self.q,
            "r": self.r,
            "terrain": self.terrain,
            "discovered": bool(self.discovered),
            "visited": int(self.visited),
            "poi": self.poi,
            "lair": self.lair,
            "faction_id": self.faction_id,
            "heat": int(getattr(self, "heat", 0)),
        }

    @staticmethod
    def from_dict(d: dict) -> "Hex":
        return Hex(
            q=int(d.get("q", 0)),
            r=int(d.get("r", 0)),
            terrain=d.get("terrain", "clear"),
            discovered=bool(d.get("discovered", False)),
            visited=int(d.get("visited", 0)),
            poi=d.get("poi"),
            lair=d.get("lair"),
            faction_id=d.get("faction_id"),
            heat=int(d.get("heat", 0)),
        )


def ensure_hex(world: Dict[str, dict], pos: Tuple[int, int], rng: Any) -> Hex:
    """Get or generate a Hex at pos, storing it back into world as plain dict."""
    q, r = pos
    k = f"{q},{r}"
    if k in world:
        return Hex.from_dict(world[k])
    dist = hex_distance((0, 0), pos)
    terrain = terrain_for_hex(rng, dist)
    hx = Hex(q=q, r=r, terrain=terrain, discovered=False, visited=0)
    hx.poi = roll_poi(rng, dist, terrain)
    # P6.1: assign a stable POI id when a POI exists (used for dungeon instances)
    if hx.poi is not None and "id" not in hx.poi:
        hx.poi["id"] = f"hex:{q},{r}:{hx.poi.get('type','poi')}"
    world[k] = hx.to_dict()
    return hx


def force_poi(rng: Any, dist_from_town: int, terrain: str) -> dict[str, Any]:
    """Create a POI for a hex, guaranteed (used for rumors pointing beyond explored frontier)."""
    # Weight POI types (same logic as roll_poi but without the chance gate).
    base = {
        "ruins": 0.28,
        "shrine": 0.18,
        "lair": 0.16,
        "dungeon_entrance": 0.14,
        "abandoned_camp": 0.10,
        "caravan": 0.08,
        "outpost": 0.06,
    }

    base["dungeon_entrance"] += min(0.10, dist_from_town * 0.01)
    base["lair"] += min(0.12, dist_from_town * 0.015)
    shave = min(base["ruins"] * 0.5, (base["dungeon_entrance"] - 0.16) + (base["lair"] - 0.18))
    base["ruins"] = max(0.20, base["ruins"] - shave)

    if terrain in ("forest",):
        base["lair"] += 0.05
        base["ruins"] -= 0.03
        base["abandoned_camp"] += 0.02
    elif terrain in ("swamp",):
        base["shrine"] += 0.04
        base["lair"] += 0.04
        base["ruins"] -= 0.04
        base["abandoned_camp"] += 0.01
    elif terrain in ("hills",):
        base["ruins"] += 0.03
        base["lair"] += 0.02
        base["shrine"] -= 0.02
        base["outpost"] += 0.02
    elif terrain in ("mountain",):
        base["dungeon_entrance"] += 0.06
        base["ruins"] -= 0.04
        base["outpost"] += 0.02
    elif terrain in ("road", "clear"):
        base["ruins"] += 0.02
        base["caravan"] += 0.05
        base["outpost"] += 0.01

    for k in list(base.keys()):
        base[k] = max(0.02, float(base[k]))

    ruins_w = base["ruins"]
    shrine_w = base["shrine"]
    lair_w = base["lair"]
    dungeon_w = base["dungeon_entrance"]
    camp_w = base["abandoned_camp"]
    caravan_w = base["caravan"]
    outpost_w = base["outpost"]
    total = ruins_w + shrine_w + lair_w + dungeon_w + camp_w + caravan_w + outpost_w
    roll = rng.random() * total

    if roll < ruins_w:
        poi_type = "ruins"
    elif roll < ruins_w + shrine_w:
        poi_type = "shrine"
    elif roll < ruins_w + shrine_w + lair_w:
        poi_type = "lair"
    elif roll < ruins_w + shrine_w + lair_w + dungeon_w:
        poi_type = "dungeon_entrance"
    elif roll < ruins_w + shrine_w + lair_w + dungeon_w + camp_w:
        poi_type = "abandoned_camp"
    elif roll < ruins_w + shrine_w + lair_w + dungeon_w + camp_w + caravan_w:
        poi_type = "caravan"
    else:
        poi_type = "outpost"

    # Provide a minimal persistent POI structure; name/flavor is assigned by the game layer.
    return {
        "type": poi_type,
        "discovered": False,
        "rumored": False,
        "cleared": False,
        "note": "",
    }
