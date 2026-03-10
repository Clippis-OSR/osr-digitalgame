"""Validate centralized time-spend pathways.

Run:
  python -m scripts.validate_time_coupling

This is a guardrail test to ensure dungeon time advancement always routes through
Game.spend_dungeon_time(), which applies the same turn-cost pipeline (torch burn,
status ticks, etc.) and performs wandering checks.

We keep it lightweight and deterministic.
"""

from __future__ import annotations


class _UI:
    def title(self, s: str) -> None:
        return

    def log(self, s: str) -> None:
        return

    def choose(self, prompt: str, options: list[str]) -> int:
        # Always pick first option deterministically.
        return 0

    def hr(self) -> None:
        return


def main() -> None:
    from sww.game import Game
    from sww.models import Party, Actor, Stats

    g = Game(_UI(), dice_seed=123, wilderness_seed=456)

    # Minimal party so status ticking code has something to iterate.
    a = Actor(name="Tester", hp=5, hp_max=5, ac_desc=9, hd=1, save=15)
    a.is_pc = True
    a.cls = "Fighter"
    a.level = 1
    a.stats = Stats(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    g.party = Party(members=[a])

    # Configure dungeon basics.
    g.current_room_id = 1
    g.dungeon_rooms = {}
    g.dungeon_turn = 0
    g.wandering_chance = 0  # ensure no wandering trigger
    g.noise_level = 0

    # Torch burn coupling.
    g.torch_turns_left = 3
    g.magical_light_turns = 0
    g.light_on = True

    g.spend_dungeon_time(2, reason="test")
    assert g.dungeon_turn == 2
    assert g.torch_turns_left == 1
    assert g.light_on is True

    # Spend one more turn: torch expires and light should go out.
    g.spend_dungeon_time(1, reason="test")
    assert g.dungeon_turn == 3
    assert g.torch_turns_left == 0
    assert g.light_on is False

    # Wilderness watch spend should advance watch/day consistently.
    g.campaign_day = 1
    g.day_watch = 5
    g.spend_wilderness_time(1, reason="test")
    assert g.campaign_day == 2
    assert g.day_watch == 0

    print("validate_time_coupling: OK")


if __name__ == "__main__":
    main()
