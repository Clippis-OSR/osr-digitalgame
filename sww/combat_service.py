from __future__ import annotations

from typing import TYPE_CHECKING

from .status_lifecycle import cleanup_actor_battle_status

if TYPE_CHECKING:
    from .game import Game
    from .models import Actor


class CombatService:
    """Combat orchestration helpers extracted from Game.

    First-pass scope: post-action combat cleanup orchestration for death and
    leave-combat paths.
    """

    def __init__(self, game: Game):
        self.game = game

    def cleanup_pool(self, *, ctx: dict | None = None) -> list[Actor]:
        """Best-effort actor pool for cleanup propagation hooks."""
        ctx = dict(ctx or {})
        pool: list[Actor] = []
        try:
            pool.extend(list(getattr(getattr(self.game, "party", None), "members", []) or []))
        except Exception:
            pass
        try:
            pool.extend(list(ctx.get("combat_foes") or []))
        except Exception:
            pass
        try:
            room = (getattr(self.game, "dungeon_rooms", {}) or {}).get(getattr(self.game, "current_room_id", 0), {})
            if isinstance(room, dict):
                pool.extend(list(room.get("foes") or []))
        except Exception:
            pass

        seen: set[int] = set()
        uniq: list[Actor] = []
        for actor in pool:
            if actor is None:
                continue
            oid = id(actor)
            if oid in seen:
                continue
            seen.add(oid)
            uniq.append(actor)
        return uniq

    def process_removal(
        self,
        actor: Actor,
        *,
        reason: str,
        marker: str = "",
        remove_effects: tuple[str, ...] = (),
        ctx: dict | None = None,
    ) -> None:
        """Single authoritative combat removal pipeline."""
        if actor is None:
            return

        key = "_combat_removal_reasons"
        seen = getattr(actor, key, None)
        if not isinstance(seen, set):
            seen = set()
            setattr(actor, key, seen)
        if reason in seen:
            return
        seen.add(reason)

        cleanup_actor_battle_status(actor)

        if marker:
            effects = list(getattr(actor, "effects", []) or [])
            for eff in tuple(remove_effects or ()):
                effects = [e for e in effects if str(e) != str(eff)]
            mk = str(marker or "").strip()
            if mk and mk not in effects:
                effects.append(mk)
            actor.effects = effects

        if reason == "death":
            try:
                self.game.emit("actor_died", name=getattr(actor, "name", "?"), monster_id=str(getattr(actor, "monster_id", "") or ""))
            except Exception:
                pass
            try:
                self.game.effects_mgr.cleanup_on_death(actor, pool=self.cleanup_pool(ctx=ctx))
            except Exception:
                pass

    def leave_combat_actor(self, actor: Actor, *, marker: str = "fled", remove_effects: tuple[str, ...] = ()) -> None:
        self.process_removal(
            actor,
            reason="leave_combat",
            marker=marker,
            remove_effects=remove_effects,
            ctx=None,
        )

    def notify_death(self, target: Actor, *, ctx: dict | None = None) -> None:
        try:
            if int(getattr(target, "hp", 0) or 0) > 0:
                return
        except Exception:
            return
        self.process_removal(target, reason="death", ctx=dict(ctx or {}))
