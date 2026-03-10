from __future__ import annotations

from typing import TYPE_CHECKING

from ..commands import (
    AdvanceWatch,
    CampToMorning,
    SetTravelMode,
    TravelDirection,
    TravelToHex,
    StepTowardTown,
    ReturnToTown,
    InvestigateCurrentLocation,
    CommandResult,
)

if TYPE_CHECKING:
    from ..game import Game


class WildernessSystem:
    def __init__(self, game: "Game"):
        self.game = game

    def handle(self, cmd):
        g = self.game

        # Encumbrance gating: if overloaded, you cannot travel until you drop items/gold.
        try:
            enc = g.party_encumbrance()
            overloaded = bool(getattr(enc, "state", "") == "overloaded")
        except Exception:
            overloaded = False

        if isinstance(cmd, AdvanceWatch):
            return g._cmd_advance_watch(int(cmd.watches))

        if isinstance(cmd, CampToMorning):
            return g._cmd_camp_to_morning()


        if isinstance(cmd, SetTravelMode):
            return g._cmd_set_travel_mode(str(cmd.mode))

        if isinstance(cmd, TravelDirection):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot travel. Drop items/gold.",))
            return g._cmd_travel_direction(str(cmd.direction))

        if isinstance(cmd, TravelToHex):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot travel. Drop items/gold.",))
            return g._cmd_travel_to_hex(int(cmd.q), int(cmd.r))

        if isinstance(cmd, StepTowardTown):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot travel. Drop items/gold.",))
            return g._cmd_step_toward_town()

        if isinstance(cmd, ReturnToTown):
            if overloaded:
                return CommandResult(status="error", messages=("You are overloaded and cannot travel. Drop items/gold.",))
            return g._cmd_return_to_town()

        if isinstance(cmd, InvestigateCurrentLocation):
            return g._cmd_investigate_current_location()

        return None
