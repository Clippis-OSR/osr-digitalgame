"""Deterministic sanity checks for P4.10.13 Assassin poison rules.

Run from repo root (p44/):
  PYTHONPATH=. python scripts/validate_assassin_poison.py
"""


def poison_apply_max(level: int) -> int:
    return max(1, 1 + (int(level) // 3))


def dot_damage_dice(level: int) -> int:
    # Scaling: +1d3 per 3 levels.
    return 1 + (int(level) // 3)


def main() -> None:
    # Charges: 1 + floor(level/3)
    assert poison_apply_max(1) == 1
    assert poison_apply_max(2) == 1
    assert poison_apply_max(3) == 2
    assert poison_apply_max(5) == 2
    assert poison_apply_max(6) == 3
    assert poison_apply_max(9) == 4

    # DOT damage dice: (1 + floor(level/3))d3
    assert dot_damage_dice(1) == 1
    assert dot_damage_dice(2) == 1
    assert dot_damage_dice(3) == 2
    assert dot_damage_dice(4) == 2
    assert dot_damage_dice(6) == 3

    # Example outputs
    examples = [1, 3, 6, 9]
    for lvl in examples:
        print(f"Assassin L{lvl}: apply_charges={poison_apply_max(lvl)}, dot_damage={dot_damage_dice(lvl)}d3, duration=1d2 rounds")

    print("OK")


if __name__ == "__main__":
    main()
