from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """Engine-level command.

    Commands must be fully validated by the controller prior to resolution.
    """


@dataclass(frozen=True)
class CmdMove(Command):
    unit_id: str
    path: list[tuple[int, int]]  # includes destination; may include start+steps


@dataclass(frozen=True)
class CmdAttack(Command):
    unit_id: str
    target_id: str
    mode: str  # "melee" | "missile"


@dataclass(frozen=True)
class CmdMoveThenMelee(Command):
    unit_id: str
    path: list[tuple[int, int]]
    target_id: str


@dataclass(frozen=True)
class CmdTakeCover(Command):
    unit_id: str


@dataclass(frozen=True)
class CmdHide(Command):
    unit_id: str


@dataclass(frozen=True)
class CmdEndTurn(Command):
    unit_id: str


@dataclass(frozen=True)
class CmdCastSpell(Command):
    unit_id: str
    spell_name: str
    target_pos: tuple[int, int]
    # P4.5: store affected unit ids at declaration time for OSR declare/resolve timing.
    target_unit_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CmdDoorAction(Command):
    unit_id: str
    kind: str  # "open" | "force" | "spike"
    door_pos: tuple[int, int]


@dataclass(frozen=True)
class CmdThrowItem(Command):
    unit_id: str
    item_id: str  # "oil" | "holy_water" | "net" | "caltrops"
    target_pos: tuple[int, int]


@dataclass(frozen=True)
class CmdDungeonAction(Command):
    unit_id: str
    kind: str  # "listen" | "search"
    target_pos: tuple[int, int]


@dataclass(frozen=True)
class CmdParley(Command):
    """Pre-combat social action (P4.8.1).

    Resolved in the SOCIAL phase (before missiles) and may change foe_reaction.
    """

    unit_id: str
    kind: str  # "parley" | "bribe" | "threaten"


# ------------------------------------------------------------
# P4.10+: World / system commands (contracts, wilderness, dungeon)
# ------------------------------------------------------------

from dataclasses import asdict
from typing import Any, Tuple, Optional


@dataclass(frozen=True)
class CommandResult:
    """Result of executing a command.

    Compatibility helpers:
    - `.ok` mirrors older Result-style APIs
    - `.message` returns the first message (if any)
    """
    status: str = "ok"           # "ok" | "error"
    messages: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def message(self) -> str:
        return self.messages[0] if self.messages else ""


def serialize_command(cmd: Command) -> dict[str, Any]:
    """Stable serialization for replay logs."""
    try:
        payload = asdict(cmd)
    except Exception:
        payload = {}
    payload["type"] = cmd.__class__.__name__
    return payload


# Contracts
@dataclass(frozen=True)
class RefreshContracts(Command):
    pass


@dataclass(frozen=True)
class AcceptContract(Command):
    cid: str


@dataclass(frozen=True)
class AbandonContract(Command):
    cid: str


# Wilderness / time
@dataclass(frozen=True)
class AdvanceWatch(Command):
    watches: int = 1


@dataclass(frozen=True)
class CampToMorning(Command):
    pass


@dataclass(frozen=True)
class SetTravelMode(Command):
    mode: str  # 'cautious' | 'normal' | 'forced'


@dataclass(frozen=True)
class TravelDirection(Command):
    direction: str


@dataclass(frozen=True)
class TravelToHex(Command):
    q: int
    r: int


@dataclass(frozen=True)
class StepTowardTown(Command):
    pass


@dataclass(frozen=True)
class ReturnToTown(Command):
    pass


@dataclass(frozen=True)
class InvestigateCurrentLocation(Command):
    pass


# Dungeon
@dataclass(frozen=True)
class EnterDungeon(Command):
    pass


@dataclass(frozen=True)
class DungeonAdvanceTurn(Command):
    pass


@dataclass(frozen=True)
class DungeonListen(Command):
    pass


@dataclass(frozen=True)
class DungeonSearchSecret(Command):
    pass


@dataclass(frozen=True)
class DungeonSearchTraps(Command):
    pass


@dataclass(frozen=True)
class DungeonInteractTrap(Command):
    pass


@dataclass(frozen=True)
class DungeonSneak(Command):
    pass


@dataclass(frozen=True)
class DungeonPrepareSpells(Command):
    pass


@dataclass(frozen=True)
class DungeonCastSpell(Command):
    pass


@dataclass(frozen=True)
class DungeonCamp(Command):
    """Make camp in the dungeon.

    Refreshes spell preparation and provides modest healing.
    """
    pass


@dataclass(frozen=True)
class DungeonLightTorch(Command):
    pass


@dataclass(frozen=True)
class DungeonLeave(Command):
    pass


@dataclass(frozen=True)
class DungeonMove(Command):
    exit_key: str


@dataclass(frozen=True)
class DungeonDoorAction(Command):
    exit_key: str
    action: str  # "open" | "force" | "spike"


@dataclass(frozen=True)
class DungeonUseStairs(Command):
    direction: str  # "up" | "down"


# Fixtures / tests
@dataclass(frozen=True)
class TestCombat(Command):
    monster_id: str
    count: int = 1


@dataclass(frozen=True)
class TestSaveReload(Command):
    note: str = ""
