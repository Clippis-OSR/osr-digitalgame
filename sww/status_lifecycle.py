from __future__ import annotations

from typing import Any

from .status_service import StatusService, TRANSIENT_BATTLE_STATUS_KEYS as SERVICE_TRANSIENT_BATTLE_STATUS_KEYS


BLOCKED_STATUS_KEYS = {"asleep", "held", "paralyzed", "paralysis", "silence", "confused", "fear"}
# Back-compat export; canonical source now in status_service.
TRANSIENT_BATTLE_STATUS_KEYS = set(SERVICE_TRANSIENT_BATTLE_STATUS_KEYS)


def status_dict(actor: Any) -> dict:
    return StatusService.status_dict(actor)


def apply_status(actor: Any, key: str, value: Any, *, mode: str = "replace") -> bool:
    """Apply/refresh/stack a status key using a single contract.

    mode: replace|max|stack_int|block
    Returns True when the status changed.
    """
    return StatusService.apply_status(actor, key, value, mode=mode)


def clear_status(actor: Any, key: str) -> bool:
    """Remove a status key, returning True if it existed."""
    return StatusService.clear_status(actor, key)


def tick_round_statuses(actors: list[Any], *, phase: str = "start") -> None:
    """Round-based status ticking with one expiry path."""
    StatusService.expire_timed_statuses(actors, phase=phase)


def cleanup_actor_battle_status(actor: Any) -> None:
    """Cleanup path for death/flee/removal from battlefield."""
    StatusService.cleanup_on_death_or_exit(actor)
