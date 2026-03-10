from __future__ import annotations

from dataclasses import dataclass


# UI-level intents: represent raw player input (clicks, keypresses).
# The tactical controller validates and converts these into Commands.


@dataclass(frozen=True)
class Intent:
    pass


@dataclass(frozen=True)
class IntentHoverTile(Intent):
    x: int
    y: int


@dataclass(frozen=True)
class IntentSelectTile(Intent):
    x: int
    y: int


@dataclass(frozen=True)
class IntentSelectUnit(Intent):
    unit_id: str


@dataclass(frozen=True)
class IntentChooseAction(Intent):
    action_id: str


@dataclass(frozen=True)
class IntentConfirm(Intent):
    pass


@dataclass(frozen=True)
class IntentCancel(Intent):
    pass


@dataclass(frozen=True)
class IntentEndTurn(Intent):
    pass


@dataclass(frozen=True)
class IntentChooseSpell(Intent):
    spell_name: str


@dataclass(frozen=True)
class IntentChooseItem(Intent):
    item_id: str
