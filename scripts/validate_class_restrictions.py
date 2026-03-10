"""P4.10.8 validation: class equipment restrictions.

Run from p44/: python scripts/validate_class_restrictions.py
"""

from __future__ import annotations

from sww.content import Content
from sww.equipment import EquipmentDB
from sww.class_rules import load_class_restrictions, legal_weapons_for_class, is_armor_allowed


def main() -> None:
    content = Content()
    equipdb = EquipmentDB()
    restrs = load_class_restrictions(content.class_restrictions)

    required = [
        "Assassin",
        "Cleric",
        "Druid",
        "Fighter",
        "Magic-User",
        "Monk",
        "Paladin",
        "Ranger",
        "Thief",
    ]

    for cls in required:
        r = restrs.get(cls.casefold())
        assert r is not None, f"Missing restrictions for {cls}"

        weapons = legal_weapons_for_class(r, equipdb)
        assert weapons, f"{cls} has no legal weapons"

        # Armor sanity: 'None' always allowed; other armor must respect max.
        assert is_armor_allowed(r, None)
        for armor in ["Leather", "Ring", "Chain", "Plate"]:
            if armor in equipdb.armors:
                ok = is_armor_allowed(r, armor)
                if r.armor_max == "none":
                    assert not ok, f"{cls} should not allow armor {armor}"

    # Specific checks from your spec
    thief = restrs["thief"]
    w_thief = set(legal_weapons_for_class(thief, equipdb))
    assert "Crossbow, heavy" not in w_thief, "Thief should not allow heavy crossbow"
    assert "Sword, long" in w_thief and "Sword, short" in w_thief, "Thief should allow long/short sword"

    mu = restrs["magic-user"]
    w_mu = set(legal_weapons_for_class(mu, equipdb))
    assert "Dagger" in w_mu and "Dart" in w_mu and "Staff (two-handed)" in w_mu, "Magic-User weapon list mismatch"

    dr = restrs["druid"]
    w_dr = set(legal_weapons_for_class(dr, equipdb))
    for wn in ["Dagger", "Club", "Spear", "Sling"]:
        assert wn in w_dr, f"Druid should allow {wn}"

    cleric = restrs["cleric"]
    w_cl = set(legal_weapons_for_class(cleric, equipdb))
    assert "Sling" in w_cl, "Cleric should allow Sling"
    # Cleric should not allow common missile weapons other than sling
    for bad in ["Bow, long", "Bow, short", "Crossbow", "Crossbow, light", "Crossbow, heavy", "Dart"]:
        if bad in equipdb.missile:
            assert bad not in w_cl, f"Cleric should not allow {bad}"

    # Assassin/Thief: no two-handed melee weapons
    assassin = restrs["assassin"]
    w_as = set(legal_weapons_for_class(assassin, equipdb))
    for w in w_as:
        if w in equipdb.melee:
            assert "two-handed" not in w.casefold(), "Assassin should not allow two-handed melee weapons"

    for w in w_thief:
        if w in equipdb.melee:
            assert "two-handed" not in w.casefold(), "Thief should not allow two-handed melee weapons"

    print("OK: class restrictions validated")


if __name__ == "__main__":
    main()
