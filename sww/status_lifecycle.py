from __future__ import annotations

from typing import Any


STATUS_ALIASES = {
    "paralyzed": "held",
    "paralysis": "held",
    "confusion": "confused",
}

ACTION_BLOCKING_STATUSES = {
    "asleep",
    "held",
    "confused",
}
ACTION_BLOCKING_ORDER = ("asleep", "held", "confused")

CAST_BLOCKING_STATUSES = ACTION_BLOCKING_STATUSES | {
    "silence",
    "fear",
}
CAST_BLOCKING_ORDER = ("asleep", "held", "confused", "silence", "fear")


TRANSIENT_BATTLE_STATUS_KEYS = {
    "casting",
    "cover",
    "parry",
    "flee_pending",
    "surprised",
}


def normalize_status_key(key: str) -> str:
    k = str(key or "").strip().lower()
    return STATUS_ALIASES.get(k, k)


def status_dict(actor: Any) -> dict:
    st = getattr(actor, "status", None)
    if isinstance(st, dict):
        return st
    st = {}
    actor.status = st
    return st


def apply_status(actor: Any, key: str, value: Any, *, mode: str = "replace") -> bool:
    """Apply/refresh/stack a status key using a single contract.

    mode: replace|max|stack_int|block
    Returns True when the status changed.
    """
    st = status_dict(actor)
    k = normalize_status_key(key)
    if not k:
        return False
    if mode == "block" and k in st:
        return False
    if mode == "max":
        old = st.get(k, 0)
        try:
            st[k] = max(int(old or 0), int(value or 0))
        except Exception:
            st[k] = value
        return st.get(k) != old
    if mode == "stack_int":
        old = st.get(k, 0)
        try:
            st[k] = int(old or 0) + int(value or 0)
        except Exception:
            st[k] = value
        return st.get(k) != old
    old = st.get(k)
    st[k] = value
    return st.get(k) != old


def has_status(actor: Any, key: str) -> bool:
    st = status_dict(actor)
    k = normalize_status_key(key)
    v = st.get(k)
    if isinstance(v, int):
        return int(v) > 0
    if isinstance(v, dict) and "rounds" in v:
        try:
            return int(v.get("rounds", 0) or 0) > 0
        except Exception:
            return False
    return v is not None


def action_block_reason(actor: Any, *, for_casting: bool = False) -> str | None:
    keys = CAST_BLOCKING_ORDER if for_casting else ACTION_BLOCKING_ORDER
    for k in keys:
        if has_status(actor, k):
            return k
    return None


def tick_round_statuses(actors: list[Any], *, phase: str = "start") -> None:
    """Round-based status ticking with one expiry path.

    phase:
      - start: keep casting through round resolution
      - end: allow casting cleanup if any stale payload remains
    """
    for actor in actors:
        st = status_dict(actor)
        if not st:
            continue
        for k in list(st.keys()):
            nk = normalize_status_key(k)
            if nk != k:
                st[nk] = st.pop(k)
                k = nk
            if k == "casting" and phase == "start":
                continue
            v = st.get(k)
            if isinstance(v, int):
                if v > 0:
                    st[k] = v - 1
                if int(st.get(k, 0) or 0) <= 0:
                    st.pop(k, None)
            elif isinstance(v, dict) and "rounds" in v:
                try:
                    v["rounds"] = int(v.get("rounds", 0)) - 1
                except Exception:
                    v["rounds"] = 0
                if int(v.get("rounds", 0) or 0) <= 0:
                    st.pop(k, None)


def cleanup_actor_battle_status(actor: Any) -> None:
    """Cleanup path for death/flee/removal from battlefield."""
    st = status_dict(actor)
    for k in TRANSIENT_BATTLE_STATUS_KEYS:
        st.pop(k, None)
