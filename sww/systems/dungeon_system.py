from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import (
    EnterDungeon,
    DungeonAdvanceTurn,
    DungeonListen,
    DungeonSearchSecret,
    DungeonSearchTraps,
    DungeonSneak,
    DungeonPrepareSpells,
    DungeonCastSpell,
    DungeonCamp,
    DungeonLightTorch,
    DungeonLeave,
    DungeonMove,
    DungeonDoorAction,
    DungeonUseStairs,
    CommandResult,
)

if TYPE_CHECKING:
    from ..game import Game


class DungeonSystem:
    def __init__(self, game: "Game"):
        self.game = game

    def handle(self, cmd):
        g = self.game

        # Encumbrance gating: overloaded parties cannot move between rooms/levels.
        try:
            enc = g.party_encumbrance()
            overloaded = bool(getattr(enc, "state", "") == "overloaded")
        except Exception:
            overloaded = False

        if isinstance(cmd, EnterDungeon):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot enter the dungeon. Drop items/gold.",))
            return g._cmd_enter_dungeon()

        if isinstance(cmd, DungeonAdvanceTurn):
            return g._cmd_dungeon_advance_turn()

        if isinstance(cmd, DungeonListen):
            return g._cmd_dungeon_listen()

        if isinstance(cmd, DungeonSearchSecret):
            return g._cmd_dungeon_search_secret()

        if isinstance(cmd, DungeonSearchTraps):
            return g._cmd_dungeon_search_traps()

        if isinstance(cmd, DungeonSneak):
            return g._cmd_dungeon_sneak()

        if isinstance(cmd, DungeonPrepareSpells):
            return g._cmd_dungeon_prepare_spells()

        if isinstance(cmd, DungeonCastSpell):
            return g._cmd_dungeon_cast_spell()

        if isinstance(cmd, DungeonCamp):
            return g._cmd_dungeon_camp()

        if isinstance(cmd, DungeonLightTorch):
            return g._cmd_dungeon_light_torch()

        if isinstance(cmd, DungeonLeave):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot travel. Drop items/gold.",))
            return g._cmd_dungeon_leave()

        if isinstance(cmd, DungeonMove):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot move. Drop items/gold.",))
            return g._cmd_dungeon_move(str(cmd.exit_key))

        if isinstance(cmd, DungeonDoorAction):
            return g._cmd_dungeon_door_action(str(cmd.exit_key), str(cmd.action))

        if isinstance(cmd, DungeonUseStairs):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot use the stairs. Drop items/gold.",))
            return g._cmd_dungeon_use_stairs(str(cmd.direction))

        return None
