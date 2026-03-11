from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .factions import FactionId
from .wilderness import hex_distance


@dataclass
class Contract:
    """A simple faction contract/quest.

    Contracts can be simple travel objectives or POI-specific missions.
    """

    cid: str
    faction_id: FactionId
    kind: str  # 'escort' | 'clear_lair' | 'scout' | 'secure_entrance' | 'relic_shrine' | 'strike_stronghold'
    title: str
    desc: str
    target_hex: Tuple[int, int]
    reward_gp: int
    rep_success: int
    rep_fail: int
    deadline_day: int

    # Optional POI targeting / context
    target_poi_type: Optional[str] = None
    target_poi_id: Optional[str] = None
    target_enemy_faction: Optional[str] = None

    accepted_day: Optional[int] = None
    status: str = "offered"  # offered | accepted | completed | failed

    # Progress flags
    visited_target: bool = False

    def to_dict(self) -> dict:
        return {
            "cid": self.cid,
            "faction_id": self.faction_id,
            "kind": self.kind,
            "title": self.title,
            "desc": self.desc,
            "target_hex": [int(self.target_hex[0]), int(self.target_hex[1])],
            "target_poi_type": self.target_poi_type,
            "target_poi_id": self.target_poi_id,
            "target_enemy_faction": self.target_enemy_faction,
            "reward_gp": int(self.reward_gp),
            "rep_success": int(self.rep_success),
            "rep_fail": int(self.rep_fail),
            "deadline_day": int(self.deadline_day),
            "accepted_day": None if self.accepted_day is None else int(self.accepted_day),
            "status": self.status,
            "visited_target": bool(self.visited_target),
        }

    @staticmethod
    def from_dict(d: dict) -> "Contract":
        th = d.get("target_hex") or [0, 0]
        return Contract(
            cid=str(d.get("cid", "K?")),
            faction_id=str(d.get("faction_id", "F?")),
            kind=str(d.get("kind", "scout")),
            title=str(d.get("title", "Contract")),
            desc=str(d.get("desc", "")),
            target_hex=(int(th[0]), int(th[1])),
            reward_gp=int(d.get("reward_gp", 0)),
            rep_success=int(d.get("rep_success", 0)),
            rep_fail=int(d.get("rep_fail", 0)),
            deadline_day=int(d.get("deadline_day", 0)),
            target_poi_type=d.get("target_poi_type"),
            target_poi_id=d.get("target_poi_id"),
            target_enemy_faction=d.get("target_enemy_faction"),
            accepted_day=d.get("accepted_day", None),
            status=str(d.get("status", "offered")),
            visited_target=bool(d.get("visited_target", False)),
        )


def _pick_known_or_frontier_hex(
    rng: Any,
    world_hexes: Dict[str, Any],
    min_dist: int = 2,
    max_dist: int = 10,
) -> Tuple[int, int]:
    """Pick a hex. Prefer already-generated (discovered or not), else pick a ring location."""
    coords = []
    for hx in world_hexes.values():
        try:
            q, r = int(hx.q), int(hx.r)
        except Exception:
            continue
        d = hex_distance((0, 0), (q, r))
        if min_dist <= d <= max_dist:
            coords.append((q, r))
    if coords:
        return rng.choice(coords)
    # Frontier fallback: random ring
    dist = rng.randint(min_dist, max_dist)
    # crude ring sample
    q = dist
    r = 0
    # random walk around ring
    for _ in range(rng.randint(0, dist * 6)):
        dq, dr = rng.choice([(0, -1), (1, -1), (1, 0), (0, 1), (-1, 1), (-1, 0)])
        q += dq
        r += dr
    return (q, r)


def _faction_type(f: Any) -> str:
    # Factions may be stored as dataclasses or plain dicts in saves.
    if f is None:
        return "guild"
    if hasattr(f, "ftype"):
        return str(getattr(f, "ftype"))
    return str(getattr(f, "ftype", None) or f.get("ftype") or f.get("type") or f.get("ftype", "guild"))


def _pick_hex_with_poi(
    rng: Any,
    world_hexes: Dict[str, Any],
    poi_type: str,
    prefer_faction_id: Optional[FactionId] = None,
    avoid_faction_id: Optional[FactionId] = None,
) -> Tuple[int, int] | None:
    """Pick a known hex that has a POI of a given type. Optionally prefer/avoid faction ownership."""
    candidates: list[Tuple[int, int, int]] = []  # (score,q,r)
    for k, hx in (world_hexes or {}).items():
        if not isinstance(hx, dict):
            continue
        poi = hx.get("poi")
        if not (isinstance(poi, dict) and poi.get("type") == poi_type):
            continue
        q, r = (int(hx.get("q", 0)), int(hx.get("r", 0))) if ("q" in hx and "r" in hx) else tuple(map(int, k.split(",")))
        fid = str((poi.get("faction_id") or hx.get("faction_id") or ""))
        if prefer_faction_id and fid != prefer_faction_id:
            # still allow but less preferred
            score = 1
        else:
            score = 3
        if avoid_faction_id and fid == avoid_faction_id:
            score = 0
        if score <= 0:
            continue
        # Bias toward nearer POIs (contracts are playable early)
        dist = hex_distance((0, 0), (q, r))
        score += max(0, 8 - dist)
        candidates.append((score, q, r))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    top = candidates[: min(10, len(candidates))]
    _, q, r = rng.choice(top)
    return (q, r)


def _poi_id_at_hex(world_hexes: Dict[str, Any], q: int, r: int, expected_type: Optional[str] = None) -> Optional[str]:
    hx = (world_hexes or {}).get(f"{int(q)},{int(r)}")
    if not isinstance(hx, dict):
        return None
    poi = hx.get("poi") if isinstance(hx.get("poi"), dict) else None
    if not isinstance(poi, dict):
        return None
    if expected_type and str(poi.get("type") or "") != str(expected_type):
        return None
    return str(poi.get("id") or f"hex:{int(q)},{int(r)}:{str(poi.get('type') or 'poi')}")


def generate_contracts(
    rng: Any,
    factions: Dict[FactionId, Any],
    world_hexes: Dict[str, Any],
    campaign_day: int,
    n: int = 3,
) -> list[Contract]:
    """Generate a small set of contracts, rotating weekly.

    Phase 2+: contracts may target specific POIs (shrines, dungeon entrances, strongholds).
    """
    fac_ids = list(factions.keys())
    rng.shuffle(fac_ids)
    contracts: list[Contract] = []

    for i in range(n):
        fid = fac_ids[i % len(fac_ids)] if fac_ids else "G1"
        f = factions.get(fid)
        ftype = _faction_type(f)

        # Choose a contract kind based on faction type
        if ftype == "religious":
            kind = rng.choice(["relic_shrine", "scout"])
        elif ftype == "guild":
            kind = rng.choice(["escort", "secure_entrance", "scout"])
        else:
            # monster / wildcard
            kind = rng.choice(["strike_stronghold", "clear_lair", "scout"])

        # Determine target
        target: Tuple[int, int]
        target_poi_type: Optional[str] = None
        target_poi_id: Optional[str] = None
        target_enemy_faction: Optional[str] = None

        if kind == "relic_shrine":
            target_poi_type = "shrine"
            picked = _pick_hex_with_poi(rng, world_hexes, "shrine", prefer_faction_id=fid)
            target = picked if picked else _pick_known_or_frontier_hex(rng, world_hexes)
        elif kind == "secure_entrance":
            target_poi_type = "dungeon_entrance"
            picked = _pick_hex_with_poi(rng, world_hexes, "dungeon_entrance", prefer_faction_id=fid)
            target = picked if picked else _pick_known_or_frontier_hex(rng, world_hexes)
        elif kind == "strike_stronghold":
            # Strike an enemy lair/entrance
            enemy_ids = [x for x in fac_ids if x != fid]
            target_enemy_faction = rng.choice(enemy_ids) if enemy_ids else None
            target_poi_type = rng.choice(["lair", "dungeon_entrance"])
            picked = _pick_hex_with_poi(rng, world_hexes, target_poi_type, prefer_faction_id=target_enemy_faction)
            target = picked if picked else _pick_known_or_frontier_hex(rng, world_hexes)
        elif kind == "clear_lair":
            target_poi_type = "lair"
            picked = _pick_hex_with_poi(rng, world_hexes, "lair")
            target = picked if picked else _pick_known_or_frontier_hex(rng, world_hexes)
        else:
            target = _pick_known_or_frontier_hex(rng, world_hexes)

        if target_poi_type:
            target_poi_id = _poi_id_at_hex(world_hexes, int(target[0]), int(target[1]), expected_type=target_poi_type)

        dist = hex_distance((0, 0), target)
        reward = 25 + dist * 10
        deadline = campaign_day + max(2, 3 + dist // 2)

        # Text + rep settings
        if kind == "escort":
            title = "Escort a caravan"
            desc = f"Escort a small caravan to hex {target}. Return to town before day {deadline}."
            rep_s, rep_f = 10, -10
        elif kind == "secure_entrance":
            title = "Secure a dungeon entrance"
            desc = f"Find the entrance at hex {target} and secure it (seal, ward, or fortify). Return before day {deadline}."
            reward += 30
            rep_s, rep_f = 12, -12
        elif kind == "relic_shrine":
            title = "Recover a shrine relic"
            desc = f"Travel to the shrine at hex {target} and recover a sacred relic. Return before day {deadline}."
            reward += 25
            rep_s, rep_f = 12, -12
        elif kind == "strike_stronghold":
            title = "Strike an enemy stronghold"
            enemy_name = target_enemy_faction or "an enemy"
            desc = f"Strike the stronghold near hex {target} held by {enemy_name}. Return before day {deadline}."
            reward += 50
            rep_s, rep_f = 15, -15
        elif kind == "clear_lair":
            title = "Clear a lair"
            desc = f"Clear the lair reported near hex {target}. Return to town before day {deadline}."
            reward += 40
            rep_s, rep_f = 15, -15
        else:
            title = "Scout the frontier"
            desc = f"Travel to hex {target} and report what you find. Return before day {deadline}."
            rep_s, rep_f = 8, -8

        contracts.append(
            Contract(
                cid=f"C{campaign_day}-{i}-{rng.randint(1000,9999)}",
                faction_id=fid,
                kind=kind,
                title=title,
                desc=desc,
                target_hex=target,
                reward_gp=reward,
                deadline_day=deadline,
                rep_success=rep_s,
                rep_fail=rep_f,
                target_poi_type=target_poi_type,
                target_poi_id=target_poi_id,
                target_enemy_faction=target_enemy_faction,
            )
        )

    return contracts
