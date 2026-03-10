from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .status_lifecycle import TRANSIENT_BATTLE_STATUS_KEYS


def validate_content_schemas(*, monsters: Any, spells: Any, monster_damage_profiles: Any, monster_specials: Any, monster_ai_profiles: Any, strict: bool = True) -> list[ValidationIssue]:
    """Lightweight schema validation for JSON-backed content.

    This is *not* a full JSON-schema implementation; it's a defensive set of
    checks that catch common corruption / hand-edit mistakes.
    """

    issues: list[ValidationIssue] = []

    # ---- monsters.json ----
    if not isinstance(monsters, list):
        issues.append(ValidationIssue("error", "monsters.json must be a list"))
    else:
        for i, m in enumerate(monsters[:20000]):
            if not isinstance(m, dict):
                issues.append(ValidationIssue("error", "monster entry must be an object", {"index": i}))
                continue
            mid = m.get("monster_id")
            nm = m.get("name")
            if not isinstance(mid, str) or not mid.strip():
                issues.append(ValidationIssue("error", "monster missing monster_id", {"index": i, "name": nm}))
            if not isinstance(nm, str) or not nm.strip():
                issues.append(ValidationIssue("error", "monster missing name", {"index": i, "monster_id": mid}))
            for k in ("ac", "hd"):
                v = m.get(k)
                if v is None:
                    continue
                if not isinstance(v, int):
                    issues.append(ValidationIssue("warn", f"monster {k} should be int", {"monster_id": mid, "value": v}))
            for k in ("aliases", "tags"):
                v = m.get(k)
                if v is not None and not isinstance(v, list):
                    issues.append(ValidationIssue("warn", f"monster {k} should be list", {"monster_id": mid, "value": v}))

    # ---- spells.json ----
    if not isinstance(spells, list):
        issues.append(ValidationIssue("error", "spells.json must be a list"))
    else:
        for i, s in enumerate(spells[:20000]):
            if not isinstance(s, dict):
                issues.append(ValidationIssue("error", "spell entry must be an object", {"index": i}))
                continue
            sid = s.get("spell_id")
            nm = s.get("name")
            if not isinstance(sid, str) or not sid.strip():
                issues.append(ValidationIssue("error", "spell missing spell_id", {"index": i, "name": nm}))
            if not isinstance(nm, str) or not nm.strip():
                issues.append(ValidationIssue("error", "spell missing name", {"index": i, "spell_id": sid}))
            tags = s.get("tags")
            if tags is not None and not isinstance(tags, list):
                issues.append(ValidationIssue("warn", "spell tags must be a list", {"spell_id": sid, "value": tags}))

    # ---- monster_specials.json ----
    if monster_specials is not None and not isinstance(monster_specials, dict):
        issues.append(ValidationIssue("error", "monster_specials.json must be an object keyed by monster_id"))

    # ---- monster_damage_profiles.json ----
    if monster_damage_profiles is not None and not isinstance(monster_damage_profiles, dict):
        issues.append(ValidationIssue("error", "monster_damage_profiles.json must be an object keyed by monster_id"))

    # ---- monster_ai_profiles.json ----
    if monster_ai_profiles is not None and not isinstance(monster_ai_profiles, dict):
        issues.append(ValidationIssue("error", "monster_ai_profiles.json must be an object keyed by monster_id"))

    if strict:
        errs = [x for x in issues if x.severity == "error"]
        if errs:
            # Keep behavior consistent with other strict-content checks.
            raise ValueError("Content schema validation failed:\n" + "\n".join(e.message for e in errs))

    # ---- Dungeon instance (P6.1) ----
    try:
        di = getattr(game, 'dungeon_instance', None)
        if di is not None:
            if isinstance(di, dict):
                did = str(di.get('dungeon_id') or '').strip()
                if not did:
                    issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                for k in ('seed','blueprint','stocking','delta'):
                    if k not in di:
                        issues.append(ValidationIssue('warn', f"dungeon_instance missing key '{k}'"))
            else:
                # if it's a dataclass-like object, try to_dict
                td = getattr(di, 'to_dict', None)
                if callable(td):
                    d = td()
                    if not str(d.get('dungeon_id') or '').strip():
                        issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                else:
                    issues.append(ValidationIssue('warn', 'dungeon_instance has unexpected type', {'type': type(di).__name__}))
    except Exception:
        pass

    return issues


@dataclass(slots=True)
class ValidationIssue:
    severity: str  # 'error' | 'warn'
    message: str
    data: dict[str, Any] | None = None


def validate_actor(a: Any, *, label: str = "actor") -> list[ValidationIssue]:
    """Validate an Actor/PC record.

    Conservative, duck-typed checks designed to catch corruption and
    incomplete initialization (especially during character creation and
    save/load).
    """

    issues: list[ValidationIssue] = []

    # Required-ish fields
    name = getattr(a, "name", None)
    if not isinstance(name, str) or not name.strip():
        issues.append(ValidationIssue("error", f"{label} has missing/invalid name", {"name": name}))

    for k in ("hp", "hp_max", "ac_desc", "hd", "save"):
        v = getattr(a, k, None)
        if not isinstance(v, int):
            issues.append(ValidationIssue("error", f"{label} has non-int {k}", {"field": k, "value": v}))

    hp = getattr(a, "hp", None)
    hp_max = getattr(a, "hp_max", None)
    if isinstance(hp, int) and isinstance(hp_max, int):
        if hp_max < 1:
            issues.append(ValidationIssue("warn", f"{label} hp_max < 1", {"hp_max": hp_max}))
        if hp > hp_max:
            issues.append(ValidationIssue("warn", f"{label} hp > hp_max", {"hp": hp, "hp_max": hp_max}))
        if hp < -20:
            issues.append(ValidationIssue("warn", f"{label} hp very low (possible corruption)", {"hp": hp}))

    # List fields (commonly initialized on Actor)
    for lst_field in ("effects", "tags", "spells_known", "spells_prepared"):
        v = getattr(a, lst_field, None)
        if v is not None and not isinstance(v, list):
            issues.append(ValidationIssue("warn", f"{label} {lst_field} is not a list", {"value": v}))

    st = getattr(a, "status", None)
    if st is not None and not isinstance(st, dict):
        issues.append(ValidationIssue("warn", f"{label} status is not a dict", {"value": st}))

    # PC-specific checks
    if bool(getattr(a, "is_pc", False)):
        cls = getattr(a, "cls", None)
        lvl = getattr(a, "level", None)
        xp = getattr(a, "xp", None)
        if not isinstance(cls, str) or not cls.strip():
            issues.append(ValidationIssue("error", f"{label} PC has missing/invalid cls", {"cls": cls}))
        if not isinstance(lvl, int) or lvl < 1:
            issues.append(ValidationIssue("error", f"{label} PC has invalid level", {"level": lvl}))
        if not isinstance(xp, int) or xp < 0:
            issues.append(ValidationIssue("warn", f"{label} PC has invalid xp", {"xp": xp}))

        st2 = getattr(a, "stats", None)
        if st2 is None:
            issues.append(ValidationIssue("error", f"{label} PC missing stats"))
        else:

            def _get_stat(obj: Any, key: str) -> Any:
                return getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key)

            for key in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
                val = _get_stat(st2, key)
                if not isinstance(val, int):
                    issues.append(
                        ValidationIssue("error", f"{label} PC stat {key} not int", {"key": key, "value": val})
                    )
                else:
                    if val < 3 or val > 18:
                        issues.append(
                            ValidationIssue(
                                "warn",
                                f"{label} PC stat {key} out of 3..18",
                                {"key": key, "value": val},
                            )
                        )

        # Equipment sanity
        weapon = getattr(a, "weapon", None)
        armor = getattr(a, "armor", None)
        shield = getattr(a, "shield", None)
        if weapon is not None and not isinstance(weapon, str):
            issues.append(ValidationIssue("warn", f"{label} PC weapon not str/None", {"weapon": weapon}))
        if armor is not None and not isinstance(armor, str):
            issues.append(ValidationIssue("warn", f"{label} PC armor not str/None", {"armor": armor}))
        if shield is not None and not isinstance(shield, bool):
            issues.append(ValidationIssue("warn", f"{label} PC shield not bool", {"shield": shield}))

    # Retainer-specific checks
    if bool(getattr(a, "is_retainer", False)):
        loy = getattr(a, "loyalty", None)
        wage = getattr(a, "wage_gp", None)
        if isinstance(loy, int) and (loy < 2 or loy > 12):
            issues.append(ValidationIssue("warn", f"{label} retainer loyalty out of 2..12", {"loyalty": loy}))
        if isinstance(wage, int) and wage < 0:
            issues.append(ValidationIssue("warn", f"{label} retainer wage negative", {"wage_gp": wage}))

    # ---- Dungeon instance (P6.1) ----
    try:
        di = getattr(game, 'dungeon_instance', None)
        if di is not None:
            if isinstance(di, dict):
                did = str(di.get('dungeon_id') or '').strip()
                if not did:
                    issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                for k in ('seed','blueprint','stocking','delta'):
                    if k not in di:
                        issues.append(ValidationIssue('warn', f"dungeon_instance missing key '{k}'"))
            else:
                # if it's a dataclass-like object, try to_dict
                td = getattr(di, 'to_dict', None)
                if callable(td):
                    d = td()
                    if not str(d.get('dungeon_id') or '').strip():
                        issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                else:
                    issues.append(ValidationIssue('warn', 'dungeon_instance has unexpected type', {'type': type(di).__name__}))
    except Exception:
        pass

    return issues


def validate_party(game: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    party = getattr(game, "party", None)
    members = getattr(party, "members", None) if party is not None else None
    if members is None:
        return issues
    if not isinstance(members, list):
        issues.append(ValidationIssue("error", "party.members is not a list"))
        return issues

    for i, m in enumerate(members):
        issues.extend(validate_actor(m, label=f"party[{i}]"))

    pcs = [m for m in members if bool(getattr(m, "is_pc", False)) and not bool(getattr(m, "is_retainer", False))]
    rets = [m for m in members if bool(getattr(m, "is_retainer", False))]
    if len(pcs) > int(getattr(game, "MAX_PCS", 9999)):
        issues.append(
            ValidationIssue(
                "warn",
                "Too many PCs in party",
                {"pcs": len(pcs), "max": getattr(game, "MAX_PCS", None)},
            )
        )
    if len(rets) > int(getattr(game, "MAX_RETAINERS", 9999)):
        issues.append(
            ValidationIssue(
                "warn",
                "Too many retainers in party",
                {"retainers": len(rets), "max": getattr(game, "MAX_RETAINERS", None)},
            )
        )
    # ---- Dungeon instance (P6.1) ----
    try:
        di = getattr(game, 'dungeon_instance', None)
        if di is not None:
            if isinstance(di, dict):
                did = str(di.get('dungeon_id') or '').strip()
                if not did:
                    issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                for k in ('seed','blueprint','stocking','delta'):
                    if k not in di:
                        issues.append(ValidationIssue('warn', f"dungeon_instance missing key '{k}'"))
            else:
                # if it's a dataclass-like object, try to_dict
                td = getattr(di, 'to_dict', None)
                if callable(td):
                    d = td()
                    if not str(d.get('dungeon_id') or '').strip():
                        issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                else:
                    issues.append(ValidationIssue('warn', 'dungeon_instance has unexpected type', {'type': type(di).__name__}))
    except Exception:
        pass

    return issues


def validate_state(game: Any) -> list[ValidationIssue]:
    """Validate key campaign invariants.

    This is intentionally lightweight and conservative: it focuses on catching
    corruption that would otherwise lead to crashes or silent bad state.
    """

    issues: list[ValidationIssue] = []

    # ---- Party / character creation ----
    issues.extend(validate_party(game))

    # ---- Combat participation invariants ----
    issues.extend(validate_combat_participation_state(game))

    # ---- Factions / clocks ----
    faction_ids = set((game.factions or {}).keys())
    for cid, clk in (game.conflict_clocks or {}).items():
        if not isinstance(clk, dict):
            issues.append(ValidationIssue("error", f"Clock {cid} is not a dict"))
            continue
        a = clk.get("a")
        b = clk.get("b")
        if a not in faction_ids or b not in faction_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    f"Clock {cid} references missing faction(s)",
                    {"a": a, "b": b},
                )
            )
        prog = int(clk.get("progress", 0) or 0)
        thr = int(clk.get("threshold", 0) or 0)
        if thr <= 0:
            issues.append(ValidationIssue("warn", f"Clock {cid} has non-positive threshold", {"threshold": thr}))
        if prog < 0:
            issues.append(ValidationIssue("warn", f"Clock {cid} has negative progress", {"progress": prog}))

    # ---- Contracts ----
    def _check_contract_list(lst: list[dict[str, Any]] | None, label: str) -> None:
        for c in (lst or []):
            if not isinstance(c, dict):
                issues.append(ValidationIssue("error", f"{label} contract entry is not a dict"))
                continue
            fid = c.get("faction_id")
            if fid and fid not in faction_ids:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"{label} contract references missing faction",
                        {"cid": c.get("cid"), "faction_id": fid},
                    )
                )
            th = c.get("target_hex")
            if th is not None:
                try:
                    int(th[0])
                    int(th[1])
                except Exception:
                    issues.append(
                        ValidationIssue(
                            "warn",
                            f"{label} contract has invalid target_hex",
                            {"cid": c.get("cid"), "target_hex": th},
                        )
                    )

    _check_contract_list(game.contract_offers, "Offer")
    _check_contract_list(game.active_contracts, "Active")

    # ---- Wilderness hexes ----
    if not isinstance(game.world_hexes, dict):
        issues.append(ValidationIssue("error", "world_hexes is not a dict"))
    else:
        # ensure keys parse as "q,r"
        for k, hx in list(game.world_hexes.items())[:5000]:
            if not isinstance(k, str) or "," not in k:
                issues.append(ValidationIssue("warn", "world_hexes has non 'q,r' key", {"key": k}))
                continue
            if not isinstance(hx, dict):
                issues.append(ValidationIssue("warn", "world_hexes entry is not a dict", {"key": k}))
                continue
            heat = int(hx.get("heat", 0) or 0)
            if heat < 0:
                issues.append(ValidationIssue("warn", "heat is negative", {"key": k, "heat": heat}))
            fid = hx.get("faction_id")
            if fid and fid not in faction_ids:
                issues.append(ValidationIssue("warn", "hex references missing faction", {"key": k, "faction_id": fid}))

    # ---- Dungeon rooms ----
    if not isinstance(game.dungeon_rooms, dict):
        issues.append(ValidationIssue("error", "dungeon_rooms is not a dict"))
    else:
        for rid, room in list(game.dungeon_rooms.items())[:5000]:
            if not isinstance(room, dict):
                issues.append(ValidationIssue("warn", "room is not a dict", {"room_id": rid}))
                continue
            exits = room.get("exits")
            if exits is not None and not isinstance(exits, dict):
                issues.append(ValidationIssue("warn", "room exits is not a dict", {"room_id": rid}))
            doors = room.get("doors")
            if doors is not None and not isinstance(doors, dict):
                issues.append(ValidationIssue("warn", "room doors is not a dict", {"room_id": rid}))

    # ---- Basic ranges ----
    if int(getattr(game, "campaign_day", 1) or 1) < 1:
        issues.append(ValidationIssue("warn", "campaign_day is < 1", {"campaign_day": getattr(game, "campaign_day", None)}))
    if int(getattr(game, "day_watch", 0) or 0) < 0:
        issues.append(ValidationIssue("warn", "day_watch is negative", {"day_watch": getattr(game, "day_watch", None)}))
    if int(getattr(game, "torches", 0) or 0) < 0:
        issues.append(ValidationIssue("warn", "torches is negative", {"torches": getattr(game, "torches", None)}))
    if int(getattr(game, "rations", 0) or 0) < 0:
        issues.append(ValidationIssue("warn", "rations is negative", {"rations": getattr(game, "rations", None)}))
    if int(getattr(game, "gold", 0) or 0) < 0:
        issues.append(ValidationIssue("warn", "gold is negative", {"gold": getattr(game, "gold", None)}))

    # ---- Monster identity invariants (P3.1.17) ----
    # Any persisted monster Actor should carry a stable monster_id so specials/profiles
    # remain decoupled from display names.
    try:
        rooms = getattr(game, "dungeon_rooms", {}) or {}
        if isinstance(rooms, dict):
            for rid, room in list(rooms.items())[:5000]:
                foes = (room or {}).get("foes")
                if not foes:
                    continue
                if not isinstance(foes, list):
                    continue
                for f in foes:
                    if f is None or bool(getattr(f, "is_pc", False)):
                        continue
                    mid = getattr(f, "monster_id", None)
                    if not isinstance(mid, str) or not mid.strip():
                        issues.append(
                            ValidationIssue(
                                "error",
                                "Persisted monster is missing monster_id",
                                {"room_id": rid, "name": getattr(f, "name", None)},
                            )
                        )
    except Exception:
        # Keep validation conservative.
        pass

    # ---- Dungeon instance (P6.1) ----
    try:
        di = getattr(game, 'dungeon_instance', None)
        if di is not None:
            if isinstance(di, dict):
                did = str(di.get('dungeon_id') or '').strip()
                if not did:
                    issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                for k in ('seed','blueprint','stocking','delta'):
                    if k not in di:
                        issues.append(ValidationIssue('warn', f"dungeon_instance missing key '{k}'"))
            else:
                # if it's a dataclass-like object, try to_dict
                td = getattr(di, 'to_dict', None)
                if callable(td):
                    d = td()
                    if not str(d.get('dungeon_id') or '').strip():
                        issues.append(ValidationIssue('warn', 'dungeon_instance missing dungeon_id'))
                else:
                    issues.append(ValidationIssue('warn', 'dungeon_instance has unexpected type', {'type': type(di).__name__}))
    except Exception:
        pass

    return issues


def validate_combat_participation_state(game: Any) -> list[ValidationIssue]:
    """Validate tactical combat participation invariants.

    Read-only checks only; this function reports potentially illegal combat
    state without mutating game objects.
    """

    issues: list[ValidationIssue] = []

    def _candidate_states() -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        for attr in ("grid_battle_state", "grid_state", "battle_state", "tactical_state", "_grid_state"):
            st = getattr(game, attr, None)
            if st is not None and hasattr(st, "units") and hasattr(st, "occupancy"):
                out.append((attr, st))
        tc = getattr(game, "turn_controller", None)
        tc_state = getattr(tc, "state", None)
        if tc_state is not None and hasattr(tc_state, "units") and hasattr(tc_state, "occupancy"):
            out.append(("turn_controller.state", tc_state))
        return out

    for label, state in _candidate_states():
        units = getattr(state, "units", None)
        occupancy = getattr(state, "occupancy", None)
        if not isinstance(units, dict) or not isinstance(occupancy, dict):
            continue

        occ_values = set(occupancy.values())
        declare_order = list(getattr(state, "declare_order", []) or [])
        initiative = list(getattr(state, "initiative", []) or [])

        for pos, uid in occupancy.items():
            u = units.get(uid)
            if u is None:
                issues.append(ValidationIssue("error", f"{label} occupancy references missing unit", {"pos": pos, "unit_id": uid}))
                continue
            if not bool(getattr(u, "is_alive")()):
                issues.append(ValidationIssue("error", f"{label} dead unit occupies active tile", {"unit_id": uid, "pos": pos}))
                continue
            fx = set(getattr(getattr(u, "actor", None), "effects", []) or [])
            if "fled" in fx or "surrendered" in fx or "removed" in fx or str(getattr(u, "morale_state", "")) == "surrender":
                issues.append(ValidationIssue("error", f"{label} non-active unit remains in occupancy", {"unit_id": uid, "pos": pos, "effects": sorted(list(fx))}))

        for uid, u in units.items():
            actor = getattr(u, "actor", None)
            fx = set(getattr(actor, "effects", []) or [])
            st = getattr(actor, "status", {}) or {}

            if "fled" in fx and "flee_pending" in fx:
                issues.append(ValidationIssue("warn", f"{label} unit has contradictory flee markers", {"unit_id": uid, "effects": ["fled", "flee_pending"]}))

            out_of_combat = ("fled" in fx) or ("surrendered" in fx) or ("removed" in fx) or str(getattr(u, "morale_state", "")) == "surrender"
            if out_of_combat:
                if uid in occ_values:
                    issues.append(ValidationIssue("error", f"{label} out-of-combat unit still occupies tile", {"unit_id": uid}))
                if uid in declare_order or uid in initiative:
                    issues.append(ValidationIssue("warn", f"{label} out-of-combat unit remains in active turn order", {"unit_id": uid}))
                stale_keys = sorted(k for k in TRANSIENT_BATTLE_STATUS_KEYS if k in st)
                if stale_keys:
                    issues.append(ValidationIssue("warn", f"{label} removed/out-of-combat unit retains transient combat statuses", {"unit_id": uid, "status_keys": stale_keys}))

    return issues


def validate_monster_library(
    monsters: list[dict],
    monster_specials: dict,
    monster_damage_profiles: dict,
    strict: bool = True,
) -> list[str]:
    """Validate that specials/profiles keys resolve to a monster entry.

    Returns a list of human-readable problems. If strict=True and any problems
    exist, raises ValueError.
    """

    keys = set()
    for m in monsters:
        if not isinstance(m, dict):
            continue
        n = m.get('name')
        if n:
            keys.add(n)
        mid = m.get('monster_id')
        if mid:
            keys.add(mid)
        for a in (m.get('aliases') or []):
            if a:
                keys.add(a)
    problems: list[str] = []

    for k in (monster_specials or {}).keys():
        if k not in keys:
            problems.append(f"monster_specials references missing monster id/name: {k!r}")

    for k in (monster_damage_profiles or {}).keys():
        if k not in keys:
            problems.append(f"monster_damage_profiles references missing monster id/name: {k!r}")

    if strict and problems:
        raise ValueError("Monster library validation failed:\n" + "\n".join(problems))
    return problems
