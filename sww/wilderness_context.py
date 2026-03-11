from __future__ import annotations

from typing import Any


def first_time_poi_resolution_key(poi: dict[str, Any], mode: str = "explore") -> str:
    """Return a stable canonical key used for first-time POI resolution gates."""
    p = poi if isinstance(poi, dict) else {}
    ptype = str(p.get("type") or "poi").strip().lower() or "poi"
    pid = str(p.get("id") or "").strip()
    if not pid:
        q = p.get("q")
        r = p.get("r")
        if q is not None and r is not None:
            pid = f"hex:{int(q)},{int(r)}:{ptype}"
        else:
            name = str(p.get("name") or "unknown").strip().lower().replace(" ", "_")
            pid = f"legacy:{ptype}:{name}"
    m = str(mode or "explore").strip().lower() or "explore"
    return f"{m}:{ptype}:{pid}"


def resolve_travel_context(game: Any) -> dict[str, Any]:
    """Read-only normalization of current wilderness travel context.

    Keeps wilderness decisions (encounters/discovery/hints/prep) aligned on one surface.
    """
    pace_raw = str(getattr(game, "wilderness_travel_mode", "normal") or "normal").strip().lower()
    pace = {"cautious": "careful", "forced": "fast"}.get(pace_raw, pace_raw)
    if pace not in ("careful", "normal", "fast"):
        pace = "normal"

    party_hex = tuple(getattr(game, "party_hex", (0, 0)) or (0, 0))
    q, r = int(party_hex[0]), int(party_hex[1])
    hx = (getattr(game, "world_hexes", {}) or {}).get(f"{q},{r}")
    terrain = "clear"
    if isinstance(hx, dict):
        terrain = str(hx.get("terrain", "clear") or "clear").strip().lower() or "clear"

    watch_name = "Unknown"
    if hasattr(game, "_watch_name"):
        try:
            watch_name = str(game._watch_name() or "Unknown")
        except Exception:
            watch_name = "Unknown"

    party_members = list(getattr(getattr(game, "party", None), "members", []) or [])
    living = [m for m in party_members if int(getattr(m, "hp", 0) or 0) > 0]
    injured = [m for m in living if int(getattr(m, "hp", 0) or 0) < int(getattr(m, "hp_max", 0) or 0)]
    party_ready = bool(living) and len(injured) <= max(1, len(living) // 2)

    party_size = max(1, len(party_members))
    rations = int(getattr(game, "rations", 0) or 0)
    low_supplies = rations < (party_size * 2)

    encounter_mod = 0
    discovery_mod = 0
    delay_mod = 0
    if pace == "careful":
        encounter_mod -= 1
        discovery_mod += 1
        delay_mod += 1
    elif pace == "fast":
        encounter_mod += 1
        discovery_mod -= 1
        delay_mod -= 1

    if watch_name == "Night":
        encounter_mod += 1
    if terrain in ("road",):
        encounter_mod -= 1
        discovery_mod -= 1
    elif terrain in ("forest", "hills", "swamp", "mountain", "ruins"):
        discovery_mod += 1

    if low_supplies:
        encounter_mod += 1

    return {
        "terrain": terrain,
        "pace": pace,
        "watch": watch_name,
        "on_road": terrain == "road",
        "party_ready": party_ready,
        "low_supplies": low_supplies,
        "encounter_mod": int(encounter_mod),
        "discovery_mod": int(discovery_mod),
        "delay_mod": int(delay_mod),
    }
