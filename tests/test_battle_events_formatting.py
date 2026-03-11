from sww.battle_events import BattleEvent, evt, format_event


def test_round_header_and_attack_damage_formatting():
    assert format_event(evt("ROUND_STARTED", round_no=3, side_first="party")) == "Round 3 begins (initiative: party first)."
    assert (
        format_event(
            evt(
                "ATTACK",
                attacker_id="Aldric",
                target_id="Goblin",
                result="miss",
                roll=7,
                total=9,
                need=13,
                mode="melee",
            )
        )
        == "Aldric attacks Goblin (melee): miss. (roll 7 → 9 vs 13)"
    )
    assert (
        format_event(evt("DAMAGE", source_id="Goblin", target_id="Mira", amount=3, hp=4, hp_max=7))
        == "Goblin deals 3 damage to Mira. (HP 4/7)"
    )


def test_effect_and_save_and_outcome_formatting():
    assert format_event(BattleEvent("EFFECT_APPLIED", {"tgt": "Goblin", "kind": "poisoned"})) == "Goblin is affected by poisoned."
    assert format_event(BattleEvent("EFFECT_REFRESHED", {"tgt": "Bandit", "kind": "sleep"})) == "Sleep on Bandit is refreshed."
    assert format_event(BattleEvent("EFFECT_EXPIRED", {"tgt": "Bandit", "kind": "sleep"})) == "Sleep on Bandit expires."
    assert format_event(BattleEvent("EFFECT_REMOVED", {"tgt": "Aldric", "kind": "bless"})) == "Bless is removed from Aldric."
    assert format_event(BattleEvent("EFFECT_REMOVED", {"tgt": "Orc", "kind": "paralysis", "reason": "resisted"})) == "Orc resists paralysis."
    assert (
        format_event(evt("SAVE_THROW", unit_id="Orc", save="poison", spell="Poison", result="success", roll=14, need=13))
        == "Orc saves (poison) vs Poison: success. (roll 14 vs 13)"
    )
    assert format_event(evt("UNIT_DIED", unit_id="Goblin")) == "Goblin dies."
    assert format_event(evt("UNIT_ROUTED", unit_id="Bandit")) == "Bandit flees!"
