from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrapProfile:
    kind: str
    name: str
    warning_signs: tuple[str, ...]
    trigger_text: str
    damage: str
    alarm: bool = False
    noise: int = 1
    extra_turn_cost: int = 0
    light_loss: int = 0


TRAP_PROFILES: dict[str, TrapProfile] = {
    "pit": TrapProfile(
        kind="pit",
        name="Pit Trap",
        warning_signs=("Loose dust gathers around a seam in the floor.",),
        trigger_text="The floor drops away into a pit!",
        damage="1d6",
        alarm=False,
        noise=1,
    ),
    "dart": TrapProfile(
        kind="dart",
        name="Dart Trap",
        warning_signs=("Tiny holes pepper one wall at waist height.",),
        trigger_text="Darts hiss out from hidden slits!",
        damage="1d4",
        alarm=False,
        noise=1,
    ),
    "gas": TrapProfile(
        kind="gas",
        name="Gas Trap",
        warning_signs=("A faint acrid smell hangs in the stale air.",),
        trigger_text="A cloud of choking gas floods the chamber!",
        damage="1d4",
        alarm=False,
        noise=0,
        extra_turn_cost=1,
        light_loss=1,
    ),
}


LEGACY_DESC_TO_KIND = {
    "pit": "pit",
    "darts": "dart",
    "dart": "dart",
    "gas": "gas",
}


def trap_profile(kind: str | None) -> TrapProfile:
    k = str(kind or "").strip().lower()
    if not k:
        k = "pit"
    return TRAP_PROFILES.get(k, TrapProfile(kind=k, name=k.replace("_", " ").title(), warning_signs=(), trigger_text="A hidden mechanism triggers!", damage="1d6"))


def infer_trap_kind(room: dict[str, Any]) -> str:
    trap = room.get("trap") if isinstance(room.get("trap"), dict) else {}
    explicit = str(trap.get("kind") or room.get("trap_kind") or "").strip().lower()
    if explicit:
        return explicit
    desc = str(room.get("trap_desc") or "").lower()
    for token, kind in LEGACY_DESC_TO_KIND.items():
        if token in desc:
            return kind
    return "pit"


def ensure_trap_state(room: dict[str, Any]) -> dict[str, Any]:
    trap = dict(room.get("trap") or {}) if isinstance(room.get("trap"), dict) else {}
    kind = infer_trap_kind(room)
    profile = trap_profile(kind)
    trap.setdefault("kind", profile.kind)
    trap.setdefault("found", bool(room.get("trap_found", False)))
    trap.setdefault("triggered", bool(room.get("trap_triggered", False)))
    trap.setdefault("disarmed", bool(room.get("trap_disarmed", False)))
    trap.setdefault("disabled", bool(trap.get("disarmed", False) or trap.get("triggered", False)))
    trap.setdefault("damage", str(room.get("trap_damage") or profile.damage))
    trap.setdefault("alarm", bool(room.get("trap_alarm", profile.alarm)))
    trap.setdefault("noise", int(profile.noise))
    trap.setdefault("warning_signs", list(profile.warning_signs))
    room["trap"] = trap
    room["trap_kind"] = str(trap.get("kind") or profile.kind)
    room["trap_desc"] = str(room.get("trap_desc") or profile.trigger_text)
    room["trap_damage"] = str(trap.get("damage") or profile.damage)
    room["trap_alarm"] = bool(trap.get("alarm", profile.alarm))
    room["trap_found"] = bool(trap.get("found", False))
    room["trap_triggered"] = bool(trap.get("triggered", False))
    room["trap_disarmed"] = bool(trap.get("disarmed", False) or trap.get("disabled", False))
    return trap


def mark_trap_state(room: dict[str, Any], *, found: bool | None = None, triggered: bool | None = None, disarmed: bool | None = None, disabled: bool | None = None) -> dict[str, Any]:
    trap = ensure_trap_state(room)
    if found is not None:
        trap["found"] = bool(found)
    if triggered is not None:
        trap["triggered"] = bool(triggered)
    if disarmed is not None:
        trap["disarmed"] = bool(disarmed)
    if disabled is not None:
        trap["disabled"] = bool(disabled)
    trap["disabled"] = bool(trap.get("disabled", False) or trap.get("disarmed", False) or trap.get("triggered", False))
    room["trap_found"] = bool(trap.get("found", False))
    room["trap_triggered"] = bool(trap.get("triggered", False))
    room["trap_disarmed"] = bool(trap.get("disarmed", False) or trap.get("disabled", False))
    return trap
