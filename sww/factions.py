from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .wilderness import DIRECTIONS, hex_distance, neighbors


FactionId = str


@dataclass
class ConflictClock:
    """A simple progress clock between two factions.

    When progress reaches `threshold`, the clock triggers an event and resets.
    """

    cid: str
    a: FactionId
    b: FactionId
    name: str
    progress: int = 0
    threshold: int = 6

    def to_dict(self) -> dict:
        return {
            "cid": self.cid,
            "a": self.a,
            "b": self.b,
            "name": self.name,
            "progress": int(self.progress),
            "threshold": int(self.threshold),
        }

    @staticmethod
    def from_dict(d: dict) -> "ConflictClock":
        return ConflictClock(
            cid=str(d.get("cid", "C?")),
            a=str(d.get("a", "A")),
            b=str(d.get("b", "B")),
            name=str(d.get("name", "Conflict")),
            progress=int(d.get("progress", 0)),
            threshold=int(d.get("threshold", 6)),
        )


@dataclass
class Faction:
    fid: FactionId
    name: str
    ftype: str  # 'monster' | 'guild' | 'religious'
    alignment: str
    home_hex: Tuple[int, int]
    controlled_hexes: List[Tuple[int, int]]
    rep: int = 0  # -100..100
    # Keywords used to bias encounters toward suitable monsters.
    encounter_keywords: List[str] | None = None
    # Special flags
    is_town_guild: bool = False

    def to_dict(self) -> dict:
        return {
            "fid": self.fid,
            "name": self.name,
            "ftype": self.ftype,
            "alignment": self.alignment,
            "home_hex": [int(self.home_hex[0]), int(self.home_hex[1])],
            "controlled_hexes": [[int(q), int(r)] for (q, r) in self.controlled_hexes],
            "rep": int(self.rep),
            "encounter_keywords": list(self.encounter_keywords or []),
            "is_town_guild": bool(self.is_town_guild),
        }

    @staticmethod
    def from_dict(d: dict) -> "Faction":
        home = d.get("home_hex") or [0, 0]
        ch = d.get("controlled_hexes") or []
        return Faction(
            fid=str(d.get("fid", "F?")),
            name=str(d.get("name", "Faction")),
            ftype=str(d.get("ftype", "monster")),
            alignment=str(d.get("alignment", "Neutrality")),
            home_hex=(int(home[0]), int(home[1])),
            controlled_hexes=[(int(x[0]), int(x[1])) for x in ch if isinstance(x, (list, tuple)) and len(x) >= 2],
            rep=int(d.get("rep", 0)),
            encounter_keywords=list(d.get("encounter_keywords") or []),
            is_town_guild=bool(d.get("is_town_guild", False)),
        )


def _ring_at_distance(dist: int) -> List[Tuple[int, int]]:
    """Return axial coordinates exactly `dist` from (0,0)."""
    if dist <= 0:
        return [(0, 0)]
    # Walk a ring in axial coords.
    results: List[Tuple[int, int]] = []
    # Start at (dist, 0) and walk the 6 directions dist steps each.
    q, r = dist, 0
    dirs = ["NW", "N", "NE", "SE", "S", "SW"]
    for d in dirs:
        dq, dr = DIRECTIONS[d]
        for _ in range(dist):
            results.append((q, r))
            q += dq
            r += dr
    return results


def random_hex_in_band(rng: Any, min_dist: int, max_dist: int) -> Tuple[int, int]:
    dist = rng.randint(min_dist, max_dist)
    ring = _ring_at_distance(dist)
    return rng.choice(ring)


def generate_static_core_factions(rng: Any) -> Dict[FactionId, Faction]:
    """Generate 5 static core factions.

    Composition: 2 monster, 1 religious, 1 town guild, 1 wildcard.
    Home hexes are assigned later by the game (so we don't depend on world generation here).
    """
    monsters = [
        ("The Blacktusk Goblins", ["goblin", "hobgoblin", "orc", "kobold"]),
        ("The Red Fang Raiders", ["bandit", "berserker", "human"]),
        ("The Mireclaw Tribe", ["lizard", "lizard man", "gnoll", "orc"]),
        ("The Stone-Eye Ogres", ["ogre", "troll", "giant"]),
        ("The Serpent Cult", ["cult", "human", "snake"]),
    ]
    religions = [
        ("Order of the Dawn", "Law"),
        ("Ashen Brotherhood", "Neutrality"),
        ("Wardens of the Green", "Neutrality"),
    ]
    guilds = [
        ("Merchant Consortium", "Law"),
        ("Caravan League", "Law"),
        ("The Free Traders", "Neutrality"),
    ]

    rng.shuffle(monsters)
    rng.shuffle(religions)
    rng.shuffle(guilds)

    f: Dict[FactionId, Faction] = {}
    # Two monsters
    for i in range(2):
        name, kws = monsters[i]
        fid = f"M{i+1}"
        f[fid] = Faction(
            fid=fid,
            name=name,
            ftype="monster",
            alignment=rng.choice(["Chaos", "Neutrality"]),
            home_hex=(0, 0),
            controlled_hexes=[],
            rep=0,
            encounter_keywords=kws,
        )

    # Religious
    rname, ralign = religions[0]
    f["R1"] = Faction(
        fid="R1",
        name=rname,
        ftype="religious",
        alignment=ralign,
        home_hex=(0, 0),
        controlled_hexes=[],
        rep=0,
        encounter_keywords=["cleric", "human"],
    )

    # Town guild
    gname, galign = guilds[0]
    f["G1"] = Faction(
        fid="G1",
        name=gname,
        ftype="guild",
        alignment=galign,
        home_hex=(0, 0),
        controlled_hexes=[(0, 0)],
        rep=0,
        encounter_keywords=["human", "bandit"],
        is_town_guild=True,
    )

    # Wildcard
    wildcard_pool = [
        ("monster", "The Iron-Wolf Band", ["bandit", "human", "wolf"]),
        ("religious", "Keepers of the Quiet Spring", ["cleric", "human"]),
        ("guild", "Arcane Circle", ["wizard", "human"]),
    ]
    wtype, wname, wkws = rng.choice(wildcard_pool)
    fid = "W1"
    f[fid] = Faction(
        fid=fid,
        name=wname,
        ftype=wtype,
        alignment=rng.choice(["Law", "Neutrality", "Chaos"]),
        home_hex=(0, 0),
        controlled_hexes=[],
        rep=0,
        encounter_keywords=wkws,
    )

    return f


def assign_territories(
    rng: Any,
    factions: Dict[FactionId, Faction],
    min_dist: int = 3,
    max_dist: int = 8,
) -> None:
    """Assign home hexes and a small territory cluster for each non-town faction."""

    used: set[Tuple[int, int]] = {(0, 0)}
    # Town guild stays at (0,0)
    for fac in factions.values():
        if fac.is_town_guild:
            fac.home_hex = (0, 0)
            continue

        # Find a unique home hex
        for _ in range(200):
            pos = random_hex_in_band(rng, min_dist, max_dist)
            if pos not in used:
                used.add(pos)
                fac.home_hex = pos
                break

        # Territory radius (1..2)
        radius = 1 if rng.random() < 0.55 else 2
        territory: set[Tuple[int, int]] = {fac.home_hex}
        frontier = {fac.home_hex}
        for _ in range(radius):
            new_frontier: set[Tuple[int, int]] = set()
            for p in frontier:
                for _d, np in neighbors(p).items():
                    territory.add(np)
                    new_frontier.add(np)
            frontier = new_frontier
        fac.controlled_hexes = sorted(list(territory), key=lambda x: (hex_distance((0, 0), x), x[0], x[1]))


def clamp_rep(v: int) -> int:
    return max(-100, min(100, int(v)))


def generate_static_conflict_clocks(factions: Dict[FactionId, Faction]) -> Dict[str, ConflictClock]:
    """Create a small set of static conflict clocks.

    Phase 2 keeps this deliberately simple: a few predictable rivalries.
    """
    monster_ids = [f.fid for f in factions.values() if f.ftype == "monster"]
    guild_ids = [f.fid for f in factions.values() if f.ftype == "guild"]
    rel_ids = [f.fid for f in factions.values() if f.ftype == "religious"]

    clocks: Dict[str, ConflictClock] = {}

    def _add(a: str, b: str, name: str):
        cid = f"{a}_vs_{b}"
        clocks[cid] = ConflictClock(cid=cid, a=a, b=b, name=name, progress=0, threshold=6)

    # Monsters pressure town trade
    if monster_ids and guild_ids:
        _add(monster_ids[0], guild_ids[0], "Raids on the roads")
    if len(monster_ids) > 1 and guild_ids:
        _add(monster_ids[1], guild_ids[0], "Caravans under threat")

    # Cult/tribes vs holy order
    if monster_ids and rel_ids:
        _add(monster_ids[-1], rel_ids[0], "Desecration of shrines")

    # Wildcard stirs trouble with someone (pick the first non-self)
    if "W1" in factions:
        for other in factions:
            if other != "W1":
                _add("W1", other, "Shadow games")
                break

    return clocks
