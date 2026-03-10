
from __future__ import annotations

from dataclasses import dataclass, field

from .grid_map import GridMap
from .battle_events import BattleEvent, evt
from .status_lifecycle import apply_status, clear_status


@dataclass
class UnitState:
    unit_id: str
    actor: object
    side: str  # "pc" | "foe"
    x: int
    y: int
    move_remaining: int = 0
    actions_remaining: int = 1
    light_radius: int = 0  # tiles (manhattan) illuminated by this unit
    morale_state: str = "steady"  # steady | fallback | rout | surrender
    hidden: bool = False  # stealth state; hidden units are not targetable until detected

    @property
    def pos(self) -> tuple[int, int]:
        return (int(self.x), int(self.y))

    def is_alive(self) -> bool:
        return int(getattr(self.actor, "hp", 0) or 0) > 0


@dataclass
class GridBattleState:
    """Canonical tactical combat state (P4.5).

    P4.4 used per-unit turns. P4.5 restores OSR timing:
      - Side initiative each round
      - Declare actions (including spell targets) first
      - Resolve phases: Missiles -> Spells -> Melee

    The engine remains authoritative for attacks, saves, MR, immunities, and effects.
    """

    gm: GridMap
    units: dict[str, UnitState] = field(default_factory=dict)
    occupancy: dict[tuple[int, int], str] = field(default_factory=dict)

    round_no: int = 1
    base_move_points: int = 6

    # Initial living counts per side (for morale triggers)
    initial_counts: dict[str, int] = field(default_factory=dict)
    morale_first_casualty_checked: dict[str, bool] = field(default_factory=dict)
    morale_half_checked: dict[str, bool] = field(default_factory=dict)

    # P4.4 leftovers (still useful for UI panels)
    initiative: list[str] = field(default_factory=list)

    # ---- P4.5 phase system ----
    phase: str = "DECLARE"  # DECLARE | MISSILES | SPELLS | MELEE
    side_first: str = "pc"  # which side acts first this round (initiative winner)

    # Declaration order for the round (unit_ids), and per-unit declared commands.
    declare_order: list[str] = field(default_factory=list)
    declare_index: int = 0
    declared: dict[str, object] = field(default_factory=dict)  # unit_id -> Command | None

    # Opportunity attack usage tracking: attacker_id -> last round used.
    opp_used_round: dict[str, int] = field(default_factory=dict)

    # Typed event stream (replayable, UI-friendly).
    events: list[BattleEvent] = field(default_factory=list)
    dungeon_turn: int = 0
    dungeon_rounds_per_turn: int = 10
    surprise_rounds: dict[str, int] = field(default_factory=lambda: {'pc': 0, 'foe': 0})
    door_spike_strength: dict[tuple[int,int], int] = field(default_factory=dict)
    # Successful listens keyed by door tile; value is dungeon_turn when listened.
    listened_doors: dict[tuple[int,int], int] = field(default_factory=dict)

    # Reaction/encounter stance for foes: unseen|neutral|hostile
    foe_reaction: str = "unseen"
    reaction_checked_round: int = 0


    # ---------- core bookkeeping ----------
    def rebuild_occupancy(self) -> None:
        self.occupancy = {}
        for uid, u in self.units.items():
            if u.is_alive():
                self.occupancy[u.pos] = uid

    def add_unit(self, u: UnitState) -> None:
        self.units[u.unit_id] = u
        if u.is_alive():
            self.occupancy[u.pos] = u.unit_id

    def living_units(self) -> dict[str, UnitState]:
        return {uid: u for uid, u in self.units.items() if u.is_alive()}

    def refresh_budgets(self, uid: str) -> None:
        u = self.units.get(uid)
        if not u:
            return
        u.move_remaining = int(self.base_move_points)
        u.actions_remaining = 1

    def setup_default_initiative(self) -> None:
        pcs = sorted([uid for uid, u in self.units.items() if u.side == "pc" and u.is_alive()])
        foes = sorted([uid for uid, u in self.units.items() if u.side == "foe" and u.is_alive()])
        self.initiative = pcs + foes
        # Initialize morale baselines once (first combat frame).
        if not self.initial_counts:
            self.initial_counts = {"pc": len(pcs), "foe": len(foes)}
            self.morale_first_casualty_checked = {"pc": False, "foe": False}
            self.morale_half_checked = {"pc": False, "foe": False}
        # budgets apply each round
        for uid in self.initiative:
            self.refresh_budgets(uid)
        self.rebuild_occupancy()

    # ---------- phase/round helpers ----------
    def start_new_round(self, game) -> list[BattleEvent]:
        """Roll side initiative and start DECLARE."""
        ev: list[BattleEvent] = []
        self.remove_dead()
        for uid in list(self.living_units().keys()):
            self.refresh_budgets(uid)

        # Persistent simple status ticks used by grid items (P4.6.3).
        # burning: takes 1 damage at start of round and decrements.
        # entangled: decrements (handled as movement lock by controller/UI).
        for u in self.units.values():
            if not u.is_alive():
                continue
            st = getattr(u.actor, "status", {}) or {}
            if int(st.get("burning", 0) or 0) > 0:
                u.actor.hp = int(getattr(u.actor, "hp", 0)) - 1
                next_burning = int(st.get("burning", 0)) - 1
                apply_status(u.actor, "burning", next_burning)
                ev.append(evt("DAMAGE", src="burning", tgt=u.unit_id, amount=1, kind="fire"))
                if int(next_burning) <= 0:
                    clear_status(u.actor, "burning")
                    ev.append(evt("EFFECT_EXPIRED", tgt=u.unit_id, kind="burning"))
            if int(st.get("entangled", 0) or 0) > 0:
                next_entangled = int(st.get("entangled", 0)) - 1
                apply_status(u.actor, "entangled", next_entangled)
                if int(next_entangled) <= 0:
                    clear_status(u.actor, "entangled")
                    ev.append(evt("EFFECT_EXPIRED", tgt=u.unit_id, kind="entangled"))

        # Clear any previous "casting" markers.
        for u in self.units.values():
            if not u.is_alive():
                continue
            clear_status(u.actor, "casting")

        self.side_first = self._roll_side_initiative(game)
        self.phase = "DECLARE"
        self.declared = {}
        pcs = sorted([uid for uid, u in self.units.items() if u.side == "pc" and u.is_alive()])
        foes = sorted([uid for uid, u in self.units.items() if u.side == "foe" and u.is_alive()])
        self.declare_order = (pcs + foes) if self.side_first == "pc" else (foes + pcs)
        self.declare_index = 0

        ev.append(evt("ROUND_STARTED", round_no=int(self.round_no), side_first=str(self.side_first)))
        ev.append(evt("PHASE_STARTED", phase="DECLARE", round_no=int(self.round_no), side_first=str(self.side_first)))
        return ev

    def _roll_side_initiative(self, game) -> str:
        # Deterministic: uses game.dice_rng (LoggedRandom).
        # 1d12 side initiative, reroll ties.
        while True:
            pc = int(game.dice_rng.randint(1, 12))
            foe = int(game.dice_rng.randint(1, 12))
            if pc == foe:
                continue
            return "pc" if pc > foe else "foe"

    def declaring_unit_id(self) -> str | None:
        if self.declare_index < 0 or self.declare_index >= len(self.declare_order):
            return None
        return self.declare_order[self.declare_index]

    def declaring_unit(self) -> UnitState | None:
        uid = self.declaring_unit_id()
        return self.units.get(uid) if uid else None



    # ---------- compatibility helpers (P4.4 UI/model) ----------
    def active_unit_id(self) -> str | None:
        # During DECLARE, the declaring unit is the only "active" unit.
        if self.phase == "DECLARE":
            return self.declaring_unit_id()
        return None

    def active_unit(self) -> UnitState | None:
        uid = self.active_unit_id()
        return self.units.get(uid) if uid else None

    def remove_dead_from_initiative(self) -> None:
        self.remove_dead()

    def advance_turn(self) -> None:
        # Deprecated in P4.5 phase system.
        return

    def is_declare_complete(self) -> bool:
        return self.declare_index >= len(self.declare_order)

    def advance_declare(self) -> None:
        self.declare_index += 1

    def remove_dead(self) -> None:
        self.rebuild_occupancy()
        # Also prune initiative/order lists.
        alive = set(self.living_units().keys())
        self.initiative = [uid for uid in self.initiative if uid in alive]
        self.declare_order = [uid for uid in self.declare_order if uid in alive]
        self.rebuild_occupancy()


    def set_surprise(self, side: str, rounds: int) -> None:
        self.surprise_rounds[side] = max(0, int(rounds))

    def tick_surprise(self) -> None:
        for side in ("pc", "foe"):
            if int(self.surprise_rounds.get(side, 0) or 0) > 0:
                self.surprise_rounds[side] = int(self.surprise_rounds.get(side, 0)) - 1

    def is_surprised(self, side: str) -> bool:
        return int(self.surprise_rounds.get(side, 0) or 0) > 0
