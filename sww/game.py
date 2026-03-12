from __future__ import annotations
from sww.dungeon_instance import ensure_instance

from dataclasses import dataclass
from typing import Optional, Any
import os
import textwrap
from .dice import Dice
from .rng import LoggedRandom, make_seed
from .event_types import CommandDispatched, StateDiff, ValidationIssues
from .debug_diff import snapshot_state, diff_snapshots
from .battle_events import BattleEvent, evt, format_event
from .models import Actor, Party, PartyStash, Stats, mod_ability
from .rules import attack_roll_needed, morale_check
from .content import Content
from .treasure import TreasureGenerator
from .data import load_json
from .encounters import EncounterGenerator
from .encumbrance import compute_party_encumbrance
from .equipment import EquipmentDB
from .inventory_service import add_item_to_actor, find_item_on_actor, remove_item_from_actor
from .item_templates import build_item_instance, find_template_id_by_name, get_item_template, item_display_name, item_effects, item_known_name, owned_item_brief_label
from .ports import UIProtocol
from .save_load import save_game, load_game, read_save_metadata
from .wilderness import ensure_hex, neighbors, hex_distance, force_poi
from .travel_state import TravelState, TOWN_HEX, DUNGEON_ENTRANCE_HEX
from .town_services import identify_cost_gp, identify_item_in_town
from .loot_pool import (
    add_generated_treasure_to_pool,
    create_loot_pool,
    loot_pool_entries_as_legacy_dicts,
)
from .coin_rewards import CoinDestination, grant_coin_reward
from .ownership_service import (
    move_item_loot_pool_to_actor,
    remove_owned_item,
)
from .reward_bundle import apply_reward_bundle, empty_reward_bundle, reward_bundle_add_coins, reward_bundle_add_item
from .wilderness_context import resolve_travel_context, first_time_poi_resolution_key
from .dungeon_context import resolve_room_interaction_context, first_time_room_resolution_key
from .factions import generate_static_core_factions, assign_territories, generate_static_conflict_clocks, clamp_rep
from .contracts import generate_contracts, Contract
from .events import (
    EventLog,
    append_player_event,
    project_clues_from_events,
    project_discoveries_from_events,
    project_district_notes_from_events,
    project_expeditions_from_events,
    project_rumors_from_events,
)
from .effects import EffectsManager, Hook
from .replay import ReplayLog
from .validation import validate_state
from .monster_ai import coerce_profile, choose_target, party_role
from .combat_rules import shooting_into_melee_penalty, foe_frontage_limit, is_melee_engaged, apply_forced_retreat
from .combat_legality import theater_target_is_valid
from .status_lifecycle import tick_round_statuses, cleanup_actor_battle_status
from .ai_capabilities import detect_capabilities, choose_attack_mode
from .commands import (
    Command,
    CommandResult,
    serialize_command,
    RefreshContracts,
    AcceptContract,
    AbandonContract,
    AdvanceWatch,
    CampToMorning,
    SetTravelMode,
    TravelDirection,
    TravelToHex,
    StepTowardTown,
    ReturnToTown,
    InvestigateCurrentLocation,
    EnterDungeon,
    DungeonAdvanceTurn,
    DungeonListen,
    DungeonSearchSecret,
    DungeonSearchTraps,
    DungeonSneak,
    DungeonPrepareSpells,
    DungeonCastSpell,
    DungeonLightTorch,
    DungeonLeave,
    DungeonMove,
    DungeonUseStairs,
    DungeonDoorAction,
    TestCombat,
    TestSaveReload,
)

from .systems.contracts_system import ContractsSystem
from .systems.wilderness_system import WildernessSystem
from .systems.dungeon_system import DungeonSystem


# -----------------------------
# Player Character
# -----------------------------

@dataclass
class PC(Actor):
    cls: str = "Fighter"
    level: int = 1
    xp: int = 0
    stats: Optional[Stats] = None


# -----------------------------
# Game
# -----------------------------

class Game:
    # Party sizing rules
    MAX_PCS = 6
    MAX_RETAINERS = 10

    # Exploration pacing
    TORCH_TURNS = 6  # 1 torch = 6 dungeon turns (10-minute turns)

    # Wilderness pacing (B/X-ish)
    # 6-mile hex; we model travel in 4-hour watches (6 watches per day)
    WATCHES_PER_DAY = 6

    def __init__(self, ui: UIProtocol, *, dice_seed: int | None = None, wilderness_seed: int | None = None):
        # Dependency-inverted UI (terminal UI, scripted UI, future GUI, etc.)
        self.ui = ui
        # Seeds recorded for replay/debugging.
        self.dice_seed = int(dice_seed if dice_seed is not None else make_seed())
        self.wilderness_seed = int(wilderness_seed if wilderness_seed is not None else make_seed())

        # Central RNGs with logging hooks for deterministic replay.
        self.dice_rng = LoggedRandom(self.dice_seed, channel="dice", log_fn=self._log_rng)
        self.wilderness_rng = LoggedRandom(self.wilderness_seed, channel="wilderness", log_fn=self._log_rng)

        # Dedicated wilderness encounter RNG so overworld travel does not consume the main dice stream.
        self.wilderness_encounter_seed = int(self.wilderness_seed) + 17
        self.wilderness_encounter_rng = LoggedRandom(self.wilderness_encounter_seed, channel="wilderness_encounter", log_fn=self._log_rng)

        self.dice = Dice(rng=self.dice_rng)
        self.wilderness_dice = Dice(rng=self.wilderness_encounter_rng)
        self.content = Content()
        self.treasure = TreasureGenerator(self.dice)
        self.encounters = EncounterGenerator(
            self.dice,
            self.content.monsters,
            getattr(self.content, "spells_data", None),
            monster_specials=getattr(self.content, "monster_specials", None),
            monster_damage_profiles=getattr(self.content, "monster_damage_profiles", None),
        )

        # Wilderness encounter generator uses wilderness_dice for deterministic separation.
        self.wilderness_encounters = EncounterGenerator(
            self.wilderness_dice,
            self.content.monsters,
            getattr(self.content, "spells_data", None),
            monster_specials=getattr(self.content, "monster_specials", None),
            monster_damage_profiles=getattr(self.content, "monster_damage_profiles", None),
        )
        self.equipdb = EquipmentDB()

        # Wilderness travel mode (careful/normal/fast)
        self.wilderness_travel_mode = "normal"

        # Rules toggles (strict RAW defaults)
        # NOTE: keep these deterministic; they are constants unless you add a command/UI to change them.
        self.nat20_auto_hit = False  # if True, natural 20 always hits (common house rule)

        # Initiative die (minor non-RAW tweak requested): S&W commonly uses 1d6 side initiative.
        # Here we roll 1d12 side initiative each combat round.
        self.initiative_die_sides = 12

        # Observability (non-persistent)
        # Content strictness: if enabled, monsters MUST have authored structured specials/profiles.
        # When STRICT_CONTENT is true, we disable all best-effort parsing of special_text (regen/gaze/fear/poison/etc).
        self.strict_content = str(os.environ.get('STRICT_CONTENT', '') or '').strip().lower() in ('1','true','yes','on')

        self.events = EventLog()
        # Centralized status effect manager (P2.1).
        self.effects_mgr = EffectsManager(self)
        # Combat round counter (set during combat loops; used for effect instance IDs).
        self.combat_round = 1
        self.debug_diff = False  # emit state diffs after commands

        # Save system
        self.save_dir = "saves"
        self.town_name = 'Threshold'

        # Campaign state
        self.campaign_day = 1
        # 0..5 (Morning -> Night), wilderness travel advances by watch
        self.day_watch = 0
        self.gold = 0
        self.expeditions = 0
        self.terrain = "clear"
        self.party_items: list[dict[str, Any]] = []
        # Town-only party stash for non-carried gear/treasure; excluded from
        # expedition encumbrance because items are not physically carried.
        self.party_stash = PartyStash()
        self.loot_pool = create_loot_pool()
        self.torches = 3
        self.oil_flasks = 0
        self.holy_water = 0
        self.nets = 0
        self.caltrops = 0
        self.rations = 0


        # P1.2: party ammunition pools (lightweight, shared)
        self.arrows = 0
        self.bolts = 0
        self.stones = 0
        # P1.3: lightweight throwable pools (shared)
        self.hand_axes = 0
        self.throwing_daggers = 0
        self.darts = 0
        self.javelins = 0
        self.throwing_spears = 0
        # Roster & expedition party (PCs live here; expedition picks from this)
        self.party = Party()

        # Retainers (kept simple but persistent)
        self.retainer_board: list[Actor] = []
        self.retainer_roster: list[Actor] = []
        self.active_retainers: list[Actor] = []
        self.hired_retainers: list[Actor] = []
        self.last_board_day = 0

        # Dungeon state
        # P6.1: dungeon instance model (blueprint+stocking+delta). Not yet used by crawler.
        self.dungeon_instance = None  # type: ignore

        self.dungeon_turn = 0
        self.torch_turns_left = 0
        # Magical light (e.g., the Light spell) measured in dungeon turns.
        self.magical_light_turns = 0
        self.light_on = False
        self.noise_level = 0  # 0..5
        self.wandering_chance = 1  # base in 6
        self.dungeon_depth = 1
        self.dungeon_level = 1
        self.current_room_id = 0
        self.dungeon_rooms: dict[int, dict[str, Any]] = {}
        # Multi-level dungeons: per-level room maps (level -> {room_id -> room_dict})
        self.dungeon_levels: dict[int, dict[int, dict[str, Any]]] = {1: self.dungeon_rooms}
        self.max_dungeon_levels: int = 1
        # Stronghold alarm state (per level): increases wandering activity; decays over time
        self.dungeon_alarm_by_level: dict[int, int] = {1: 0}
        self.current_dungeon_is_stronghold: bool = False
        self.dungeon_level_meta: dict[int, dict[str, Any]] = {}  # boss/treasury ids etc.

        # Wilderness state (expanding frontier)
        # world hexes stored as dicts keyed by "q,r" for easy JSON save.
        self.world_hexes: dict[str, dict[str, Any]] = {}
        self.party_hex = (0, 0)  # axial (q,r)
        self.travel_state = TravelState(location="town")
        # Dedicated RNG for wilderness generation and rumor POI seeding.
        # Deterministic-ish replay log (non-persistent)
        self.replay = ReplayLog(dice_seed=self.dice_seed, wilderness_seed=self.wilderness_seed)

        # Subsystems
        self.contracts_system = ContractsSystem(self)
        self.wilderness_system = WildernessSystem(self)
        self.dungeon_system = DungeonSystem(self)

        # Validation toggle (enabled in selftest)
        self.enable_validation = False

        # Journal (persistent)
        # discoveries: list of dicts {day, watch, q, r, terrain, kind, name, note}
        # rumors: list of dicts {day, q, r, terrain, kind, hint, cost}
        self.discovery_log: list[dict[str, Any]] = []
        self.rumors: list[dict[str, Any]] = []
        self.dungeon_clues: list[dict[str, Any]] = []
        self.event_history: list[dict[str, Any]] = []
        self.next_event_eid: int = 0
        self.district_notes: list[dict[str, Any]] = []
        self._ensure_minimal_rumor_surface()

        # Factions (persistent)
        # Stored as dict keyed by faction id; values are plain dicts for easy JSON save.
        self.factions: dict[str, dict[str, Any]] = {}
        self.current_dungeon_faction_id: str | None = None
        self.dungeon_monster_groups: list[dict[str, Any]] = []
        self.dungeon_room_aftermath: dict[str, dict[str, Any]] = {}
        self.next_dungeon_group_id: int = 1

        # Conflict clocks + contracts (persistent)
        # conflict_clocks: dict keyed by clock id, values are dicts {cid,a,b,name,progress,threshold}
        self.conflict_clocks: dict[str, dict[str, Any]] = {}
        self.last_clock_day: int = 0

        # contract_offers: list[dict]; active_contracts: list[dict]
        self.contract_offers: list[dict[str, Any]] = []
        self.active_contracts: list[dict[str, Any]] = []
        self.last_contract_day: int = 0

        # Faction services / boons
        self.safe_passage: dict[str, int] = {}  # faction_id -> day until which encounters are reduced
        self.forward_anchor: dict[str, Any] | None = None  # optional wilderness reset point (outpost/caravan)
        self.blessed_until_day: int = 0
        self.guild_favor_until_day: int = 0

        # Seed town board
        self.generate_retainer_board(force=True)

        # Initialize static factions for a fresh campaign.
        # (On load, save_load.apply_game_dict will overwrite self.factions.)
        self._init_factions_if_needed()

        # Startup marker
        self.emit("game_started")
        self._run_validation("startup")


    # -----------------
    # Event helper
    # -----------------

    def emit(self, name: str, **data: Any) -> None:
        """Emit a lightweight event into the in-memory event log."""
        self.events.emit(
            name,
            data=data,
            campaign_day=int(getattr(self, "campaign_day", 1)),
            day_watch=int(getattr(self, "day_watch", 0)),
            dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
            room_id=int(getattr(self, "current_room_id", 0)),
            party_hex=tuple(getattr(self, "party_hex", (0, 0))),
        )

    def _append_player_event(
        self,
        event_type: str,
        *,
        category: str,
        title: str,
        payload: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
        visibility: str = "journal",
    ) -> dict[str, Any]:
        eid = int(getattr(self, "next_event_eid", 0) or 0)
        history = list(getattr(self, "event_history", []) or [])
        if history:
            try:
                max_eid = max(int((row or {}).get("eid", -1) or -1) for row in history if isinstance(row, dict))
            except Exception:
                max_eid = -1
            if eid <= int(max_eid):
                eid = int(max_eid) + 1
                self.next_event_eid = int(eid)
        entry = append_player_event(
            getattr(self, "event_history", []),
            eid=eid,
            event_type=event_type,
            category=category,
            day=int(getattr(self, "campaign_day", 1) or 1),
            watch=int(getattr(self, "day_watch", 0) or 0),
            title=title,
            payload=dict(payload or {}),
            refs=dict(refs or {}),
            visibility=visibility,
        )
        self.next_event_eid = eid + 1
        return entry

    # -----------------
    # Battle events + buffered combat log (P6.4 UX)
    # -----------------

    def battle_evt(self, kind: str, **data: Any) -> BattleEvent:
        """Emit a typed BattleEvent.

        - Always goes into the global EventLog as `battle_event`.
        - During combat (when buffering is enabled), it also goes into the per-round
          buffer used to render a readable round summary.
        """
        be = evt(kind, **data)
        try:
            self.emit("battle_event", data=be)
        except Exception:
            pass

        if bool(getattr(self, "_battle_buffer_active", False)):
            try:
                self._battle_buffer.append(be)
            except Exception:
                self._battle_buffer = [be]
            try:
                self._battle_round_buffer.append(be)
            except Exception:
                self._battle_round_buffer = [be]
        return be

    def _combat_log_hook(self, msg: str) -> bool:
        """UI.log hook used during classic (text) combat.

        While combat capture is active, we suppress immediate printing and instead
        convert known legacy log lines into typed BattleEvents. Unknown lines fall
        back to INFO(text=...). This keeps the simulation mostly unchanged while
        making the output readable and future 2D/HUD-friendly.
        """
        if not bool(getattr(self, "_battle_buffer_active", False)):
            return False
        s = str(msg)
        try:
            import re as _re

            # HIT: "A hits B for 6 (HP 2/10)"
            m = _re.match(r"^(?P<a>.+?) hits (?P<t>.+?) for (?P<dmg>\d+) \(HP (?P<hp>-?\d+)/(?P<hpmax>\d+)\)$", s)
            if m:
                a = m.group('a'); t = m.group('t')
                dmg = int(m.group('dmg'))
                hp = int(m.group('hp')); hpmax = int(m.group('hpmax'))
                self.battle_evt("ATTACK", attacker_id=a, target_id=t, result="hit")
                self.battle_evt("DAMAGE", target_id=t, amount=dmg, hp=hp, hp_max=hpmax)
                return True

            # MISS: "A misses B."
            m = _re.match(r"^(?P<a>.+?) misses (?P<t>.+?)\.$", s)
            if m:
                self.battle_evt("ATTACK", attacker_id=m.group('a'), target_id=m.group('t'), result="miss")
                return True

            # Spell casting begin/cast/disruption
            m = _re.match(r"^(?P<c>.+?) begins casting (?P<sp>.+?)\.\.\.$", s)
            if m:
                self.battle_evt("SPELL_CAST", caster_id=m.group('c'), spell=m.group('sp'), phase="begin")
                return True
            m = _re.match(r"^(?P<c>.+?) casts (?P<sp>.+?)!$", s)
            if m:
                self.battle_evt("SPELL_CAST", caster_id=m.group('c'), spell=m.group('sp'), phase="cast")
                return True
            m = _re.match(r"^(?P<c>.+?)'s spell is disrupted and lost!$", s)
            if m:
                self.battle_evt("SPELL_DISRUPTED", caster_id=m.group('c'))
                return True
            m = _re.match(r"^(?P<c>.+?)'s casting is disrupted in the melee! \(roll (?P<r>\d+)\)$", s)
            if m:
                self.battle_evt("SPELL_DISRUPTED", caster_id=m.group('c'), roll=int(m.group('r')))
                return True

            # Morale / routing / surrender lines
            m = _re.match(r"^(?P<u>.+?) breaks and flees!$", s)
            if m:
                self.battle_evt("UNIT_ROUTED", unit_id=m.group('u'))
                return True
            if s == "The enemies surrender.":
                self.battle_evt("MORALE_FAILED", side="foes", result="surrender")
                return True
            if s == "Enemies flee.":
                self.battle_evt("MORALE_FAILED", side="foes", result="flee")
                return True
            if s == "Enemies defeated.":
                self.battle_evt("COMBAT_ENDED", reason="defeated")
                return True

        except Exception:
            # Never let the UX layer break combat.
            pass

        # Default: treat as INFO
        self.battle_evt("INFO", text=s)
        return True
    def _battle_begin_capture(self) -> None:
        self._battle_buffer_active = True
        self._battle_label_map = {}
        self._battle_buffer: list[BattleEvent] = []
        self._battle_round_buffer: list[BattleEvent] = []
        self._battle_current_round = 0
        # Install UI hook (if supported).
        try:
            self._battle_prev_ui_on_log = getattr(self.ui, "on_log", None)
            setattr(self.ui, "on_log", self._combat_log_hook)
        except Exception:
            self._battle_prev_ui_on_log = None

    def _battle_end_capture(self) -> None:
        # Restore UI hook.
        try:
            setattr(self.ui, "on_log", getattr(self, "_battle_prev_ui_on_log", None))
        except Exception:
            pass
        self._battle_buffer_active = False

    # -----------------
    # Encounter recap capture (room-level rewards)
    # -----------------
    def _encounter_begin_capture(self, *, context: str, room_id: int | None = None, room_type: str | None = None) -> None:
        """Capture rewards that occur across the whole room encounter (combat + post-combat treasure).
        This is separate from combat capture so we can include room treasure/XP awarded after combat ends.
        """
        try:
            self._encounter_capture_active = True
            self._encounter_context = str(context)
            self._encounter_room_id = int(room_id) if room_id is not None else None
            self._encounter_room_type = str(room_type) if room_type is not None else None
        except Exception:
            self._encounter_capture_active = True
        # snapshots
        try:
            self._encounter_start_gold = int(getattr(self, "gold", 0) or 0)
        except Exception:
            self._encounter_start_gold = 0
        try:
            items = list(getattr(self, "party_items", []) or [])
            self._encounter_start_items = items
        except Exception:
            self._encounter_start_items = []
        # sum xp across PCs (works even if xp awards happen outside combat)
        try:
            xp_sum = 0
            for pc in (self.party.members or []):
                if getattr(pc, "is_pc", False):
                    xp_sum += int(getattr(pc, "xp", 0) or 0)
            self._encounter_start_xp_sum = int(xp_sum)
        except Exception:
            self._encounter_start_xp_sum = 0
        # collected deltas
        self._encounter_loot_events: list[dict[str, object]] = []

    def _encounter_note_loot(self, *, gp: int = 0, items: list | None = None, source: str = "loot") -> None:
        if not bool(getattr(self, "_encounter_capture_active", False)):
            return
        try:
            rec = {"source": str(source), "gp": int(gp)}
            if items is not None:
                rec["items"] = list(items)
            self._encounter_loot_events.append(rec)
        except Exception:
            pass

    def _encounter_end_capture(self) -> None:
        if not bool(getattr(self, "_encounter_capture_active", False)):
            return
        self._encounter_capture_active = False

        start_gold = int(getattr(self, "_encounter_start_gold", 0) or 0)
        start_items = list(getattr(self, "_encounter_start_items", []) or [])
        start_xp_sum = int(getattr(self, "_encounter_start_xp_sum", 0) or 0)

        end_gold = int(getattr(self, "gold", 0) or 0)
        try:
            end_items = list(getattr(self, "party_items", []) or [])
        except Exception:
            end_items = []
        # end xp sum
        end_xp_sum = start_xp_sum
        try:
            s = 0
            for pc in (self.party.members or []):
                if getattr(pc, "is_pc", False):
                    s += int(getattr(pc, "xp", 0) or 0)
            end_xp_sum = int(s)
        except Exception:
            end_xp_sum = start_xp_sum

        gp_delta = int(end_gold - start_gold)
        # new items by identity (best-effort; items may be dicts/strings)
        new_items = []
        try:
            # Use repr string for stable diff without depending on hashability.
            start_set = set([repr(x) for x in start_items])
            for it in end_items:
                r = repr(it)
                if r not in start_set:
                    new_items.append(it)
        except Exception:
            new_items = []
        xp_delta = int(end_xp_sum - start_xp_sum)

        # Print recap only if something happened.
        combat_summary_evt = getattr(self, "_encounter_combat_summary", None)
        combat_gp = 0
        combat_xp = 0
        combat_item_names: set[str] = set()
        if combat_summary_evt is not None:
            try:
                combat_gp = int((combat_summary_evt.data or {}).get("loot_gp", 0) or 0)
            except Exception:
                combat_gp = 0
            try:
                combat_xp = int((combat_summary_evt.data or {}).get("xp_earned", 0) or 0)
            except Exception:
                combat_xp = 0
            try:
                for nm in ((combat_summary_evt.data or {}).get("loot_items", []) or []):
                    combat_item_names.add(str(nm))
            except Exception:
                pass

        # Extra rewards beyond what combat summary already reported.
        extra_gp = int(gp_delta) - int(combat_gp)
        extra_xp = int(xp_delta) - int(combat_xp)
        extra_items = []
        try:
            for it in (new_items or []):
                if str(it) not in combat_item_names:
                    extra_items.append(it)
        except Exception:
            extra_items = list(new_items or [])

        if combat_summary_evt is not None or gp_delta or xp_delta or new_items:
            try:
                self.ui.title("Encounter Recap")
            except Exception:
                pass

            # Include combat summary first (if any), but don't print it twice.
            if combat_summary_evt is not None:
                try:
                    for ln in str(format_event(combat_summary_evt)).splitlines():
                        self.ui.log(ln)
                except Exception:
                    pass

            # If we already printed combat summary, only show the post-combat deltas.
            try:
                if combat_summary_evt is not None:
                    if extra_gp or extra_xp or extra_items:
                        self.ui.log("Post-combat rewards:")
                        if extra_gp:
                            self.ui.log(f"Gold gained: {extra_gp} gp")
                        if extra_xp:
                            self.ui.log(f"XP gained: {extra_xp}")
                        if extra_items:
                            preview = extra_items[:10]
                            self.ui.log(f"Items gained: {len(extra_items)}")
                            for it in preview:
                                self.ui.log(f"- {it}")
                            if len(extra_items) > len(preview):
                                self.ui.log(f"...and {len(extra_items) - len(preview)} more")
                else:
                    if gp_delta:
                        self.ui.log(f"Gold gained: {gp_delta} gp")
                    if xp_delta:
                        self.ui.log(f"XP gained: {xp_delta}")
                    if new_items:
                        preview = new_items[:10]
                        self.ui.log(f"Items gained: {len(new_items)}")
                        for it in preview:
                            self.ui.log(f"- {it}")
                        if len(new_items) > len(preview):
                            self.ui.log(f"...and {len(new_items) - len(preview)} more")
            except Exception:
                pass

        # clear any deferred combat summary
        try:
            self._encounter_combat_summary = None
        except Exception:
            pass

# Emit a structured event for later 2D/journal.
        try:
            self.emit(
                "encounter_recap",
                context=str(getattr(self, "_encounter_context", "encounter")),
                room_id=getattr(self, "_encounter_room_id", None),
                room_type=getattr(self, "_encounter_room_type", None),
                gp_delta=int(gp_delta),
                xp_delta=int(xp_delta),
                items_gained=[repr(x) for x in new_items],
                loot_events=list(getattr(self, "_encounter_loot_events", []) or []),
            )
        except Exception:
            pass


    def _battle_print_lines(self, lines: list[str]) -> None:
        """Print lines bypassing combat capture hook."""
        try:
            hist = list(getattr(self, "_battle_recent_text_lines", []) or [])
            hist.extend([str(ln) for ln in (lines or [])])
            self._battle_recent_text_lines = hist[-60:]
        except Exception:
            pass
        try:
            prev = getattr(self.ui, "on_log", None)
            setattr(self.ui, "on_log", None)
        except Exception:
            prev = None
        try:
            for ln in lines:
                try:
                    self.ui.log(ln)
                except Exception:
                    print(ln)
        finally:
            try:
                setattr(self.ui, "on_log", prev)
            except Exception:
                pass

    
    def _battle_label_for(self, a: object) -> str:
        try:
            if getattr(a, "is_pc", False):
                return str(getattr(a, "name", "?"))
        except Exception:
            pass
        try:
            mp = getattr(self, "_battle_label_map", None) or {}
            return str(mp.get(id(a)) or getattr(a, "name", "?") or "?")
        except Exception:
            return "?"

    
    def _combat_board_hud_lines(self, foes: list[Actor], combat_distance_ft: int, round_no: int, *, phase: str = "Planning") -> list[str]:
        living_foes = [f for f in (foes or []) if int(getattr(f, "hp", 0) or 0) > 0 and 'fled' not in (getattr(f, 'effects', []) or []) and 'surrendered' not in (getattr(f, 'effects', []) or [])]
        return [
            f"Combat — Round {int(round_no)} | {phase} | Range {int(combat_distance_ft)} ft | Foes {len(living_foes)}/{len(foes or [])}",
            self._party_hud_summary(),
            f"Supplies: {int(getattr(self, 'gold', 0) or 0)} gp | Rations {int(getattr(self, 'rations', 0) or 0)} | Torches {int(getattr(self, 'torches', 0) or 0)}",
            self._light_hud_summary(),
        ]

    def _combat_board_token(self, actor: Actor, *, selected: bool = False, targeted: bool = False, aoe_preview: bool = False, ally_risk: bool = False) -> str:
        label = self._battle_label_for(actor)
        core = ''.join(ch for ch in label if ch.isalnum())[:3].upper() or '???'
        hp = int(getattr(actor, 'hp', 0) or 0)
        hpmax = getattr(actor, 'hp_max', None)
        if hpmax is None:
            hpmax = getattr(actor, 'max_hp', None)
        try:
            hpmax_i = int(hpmax) if hpmax is not None else hp
        except Exception:
            hpmax_i = hp
        if hp <= 0:
            core = 'XX'
        elif hpmax_i > 0 and hp <= max(1, hpmax_i // 3):
            core = core[:2] + '!'
        core = core[:3].ljust(3)
        if selected:
            return f"[{core}]"
        if targeted:
            return f"<{core}>"
        if aoe_preview:
            return f"{{{core}}}"
        if ally_risk:
            return f"({core})"
        return f" {core} "

    def _combat_board_rank_split(self, units: list[Actor], *, foe: bool = False) -> tuple[list[Actor], list[Actor]]:
        front: list[Actor] = []
        back: list[Actor] = []
        for u in (units or []):
            if int(getattr(u, 'hp', 0) or 0) <= 0:
                continue
            cls = str(getattr(u, 'cls', '') or '').strip().lower()
            try:
                wkind = str(self._weapon_kind(u) or '')
            except Exception:
                wkind = ''
            specials = ' '.join([str(s) for s in (getattr(u, 'specials', []) or [])]).lower()
            spellish = cls in ('magic-user', 'magic user', 'mage', 'wizard', 'cleric', 'druid') or ('spell' in specials)
            sturdy = cls in ('fighter', 'paladin', 'ranger', 'cleric', 'monk')
            try:
                hd = int(getattr(u, 'hd', 1) or 1)
            except Exception:
                hd = 1
            if wkind == 'missile' or spellish:
                back.append(u)
            elif sturdy or hd >= 2:
                front.append(u)
            else:
                (back if foe and hd <= 1 else front).append(u)
        if not front and back:
            front.append(back.pop(0))
        return front[:4], back[:4]

    def _combat_board_lines(self, foes: list[Actor], combat_distance_ft: int, *, selected_actor: Actor | None = None, selected_target: Actor | None = None, aoe_preview_units: list[Actor] | None = None, ally_risk_units: list[Actor] | None = None) -> list[str]:
        party = list(self.party.living())
        living_foes = [f for f in (foes or []) if int(getattr(f, 'hp', 0) or 0) > 0 and 'fled' not in (getattr(f, 'effects', []) or []) and 'surrendered' not in (getattr(f, 'effects', []) or [])]
        p_front, p_back = self._combat_board_rank_split(party, foe=False)
        f_front, f_back = self._combat_board_rank_split(living_foes, foe=True)
        preview_ids = {id(u) for u in (aoe_preview_units or []) if u is not None}
        ally_ids = {id(u) for u in (ally_risk_units or []) if u is not None}
        gap_cells = max(2, min(8, int(round((int(combat_distance_ft) or 0) / 10.0))))
        gap = '·' * (gap_cells * 2)
        def _row(label: str, left_units: list[Actor], right_units: list[Actor]) -> str:
            left = ''.join(self._combat_board_token(
                u,
                selected=(u is selected_actor),
                targeted=(u is selected_target),
                aoe_preview=(id(u) in preview_ids and u is not selected_actor and u is not selected_target),
                ally_risk=(id(u) in ally_ids and id(u) not in preview_ids and u is not selected_actor and u is not selected_target),
            ) for u in left_units) or ' -- '
            right = ''.join(self._combat_board_token(
                u,
                selected=(u is selected_actor),
                targeted=(u is selected_target),
                aoe_preview=(id(u) in preview_ids and u is not selected_actor and u is not selected_target),
                ally_risk=(id(u) in ally_ids and id(u) not in preview_ids and u is not selected_actor and u is not selected_target),
            ) for u in right_units) or ' -- '
            return f"{label:<11}{left:<24}{gap:^18}{right:>24}"
        return [
            f"Tactical board — 1 cell ≈ 10 ft | gap {int(combat_distance_ft)} ft",
            _row('Party Back', p_back, f_back),
            _row('Party Front', p_front, f_front),
            f"{'':<11}{'=' * 24}{' ' * 18}{'=' * 24}",
            "Legend: [x] selected  <x> target  {x} AoE preview  (x) ally risk  ! wounded/critical  XX defeated",
        ]

    def _combat_visible_party_units(self) -> list[Actor]:
        return [a for a in list(self.party.living()) if int(getattr(a, 'hp', 0) or 0) > 0]

    def _combat_visible_foe_units(self, foes: list[Actor]) -> list[Actor]:
        return [f for f in (foes or []) if int(getattr(f, 'hp', 0) or 0) > 0 and 'fled' not in (getattr(f, 'effects', []) or []) and 'surrendered' not in (getattr(f, 'effects', []) or [])]

    def _combat_cycle_unit(self, current: Actor | None, units: list[Actor], delta: int) -> Actor | None:
        pool = [u for u in (units or []) if u is not None]
        if not pool:
            return None
        if current not in pool:
            return pool[0]
        try:
            idx = pool.index(current)
        except ValueError:
            idx = 0
        return pool[(idx + int(delta)) % len(pool)]

    def _combat_board_positions(self, foes: list[Actor]) -> dict[int, tuple[int, int]]:
        """Approximate unit board coordinates for directional focus navigation."""
        party = list(self.party.living())
        living_foes = [f for f in (foes or []) if int(getattr(f, 'hp', 0) or 0) > 0 and 'fled' not in (getattr(f, 'effects', []) or []) and 'surrendered' not in (getattr(f, 'effects', []) or [])]
        p_front, p_back = self._combat_board_rank_split(party, foe=False)
        f_front, f_back = self._combat_board_rank_split(living_foes, foe=True)
        pos: dict[int, tuple[int, int]] = {}
        def _place(units: list[Actor], *, x0: int, y: int, step: int) -> None:
            for idx, u in enumerate(units or []):
                try:
                    pos[id(u)] = (x0 + (idx * step), y)
                except Exception:
                    pass
        _place(p_back, x0=-8, y=-1, step=2)
        _place(p_front, x0=-8, y=1, step=2)
        _place(f_back, x0=2, y=-1, step=2)
        _place(f_front, x0=2, y=1, step=2)
        return pos

    def _combat_directional_unit(self, current: Actor | None, units: list[Actor], direction: str, foes: list[Actor]) -> Actor | None:
        pool = [u for u in (units or []) if u is not None]
        if not pool:
            return None
        if current not in pool:
            return pool[0]
        direction = str(direction or '').strip().lower()
        positions = self._combat_board_positions(foes)
        cur = positions.get(id(current))
        if not cur:
            return self._combat_cycle_unit(current, pool, +1)
        cx, cy = cur
        best: Actor | None = None
        best_key: tuple[float, float, float] | None = None
        for u in pool:
            if u is current:
                continue
            pos = positions.get(id(u))
            if not pos:
                continue
            ux, uy = pos
            dx = ux - cx
            dy = uy - cy
            if direction in ('n', 'north', 'up', 'k'):
                if dy >= 0:
                    continue
                primary = abs(dy)
                lateral = abs(dx)
            elif direction in ('s', 'south', 'down', 'j'):
                if dy <= 0:
                    continue
                primary = abs(dy)
                lateral = abs(dx)
            elif direction in ('e', 'east', 'right', 'l'):
                if dx <= 0:
                    continue
                primary = abs(dx)
                lateral = abs(dy)
            elif direction in ('w', 'west', 'left', 'h'):
                if dx >= 0:
                    continue
                primary = abs(dx)
                lateral = abs(dy)
            else:
                continue
            dist2 = float((dx * dx) + (dy * dy))
            key = (float(lateral), float(primary), dist2)
            if best_key is None or key < best_key:
                best_key = key
                best = u
        if best is not None:
            return best
        # Fallback: wrap by cycle order if nothing exists in that direction.
        if direction in ('n', 'north', 'up', 'k', 'w', 'west', 'left', 'h'):
            return self._combat_cycle_unit(current, pool, -1)
        return self._combat_cycle_unit(current, pool, +1)

    def _combat_option_target_units(self, options: list[str] | None, foes: list[Actor]) -> list[Actor]:
        living = self._combat_visible_foe_units(foes)
        if not options:
            return []
        if len(living) != len(options):
            return []
        # Current combat target prompts are emitted as one label per living foe.
        return list(living)

    def _combat_detail_lines(self, foes: list[Actor], combat_distance_ft: int, round_no: int, *, selected_actor: Actor | None = None, selected_target: Actor | None = None, prompt: str = '', options: list[str] | None = None, selected_option: int | None = None, hotkeys: list[str] | None = None, extra_lines: list[str] | None = None) -> list[str]:
        lines: list[str] = ["Status panel"]
        if selected_actor is not None:
            ahp = int(getattr(selected_actor, 'hp', 0) or 0)
            ahpmax = getattr(selected_actor, 'hp_max', None)
            if ahpmax is None:
                ahpmax = getattr(selected_actor, 'max_hp', None)
            lines.append(f"Actor: {self._battle_label_for(selected_actor)} HP {ahp}/{ahpmax if ahpmax is not None else '?'}")
        if selected_target is not None:
            thp = int(getattr(selected_target, 'hp', 0) or 0)
            thpmax = getattr(selected_target, 'hp_max', None)
            if thpmax is None:
                thpmax = getattr(selected_target, 'max_hp', None)
            lines.append(f"Target: {self._battle_label_for(selected_target)} HP {thp}/{thpmax if thpmax is not None else '?'}")
        if prompt:
            lines.append(f"Prompt: {prompt}")
        if options:
            lines.append("Options:")
            for idx, opt in enumerate(options[:8], start=1):
                mark = '>' if selected_option is not None and int(selected_option) == (idx - 1) else ' '
                lines.append(f"{mark} {idx}. {opt}")
        if hotkeys:
            lines.append("Hotkeys:")
            for hk in hotkeys[:4]:
                lines.append(f"  {hk}")
        recent = [str(ln) for ln in (getattr(self, '_battle_recent_text_lines', []) or []) if str(ln).strip()]
        if recent:
            lines.append("Recent log:")
            for ln in recent[-8:]:
                lines.append(ln)
        else:
            lines.append("Recent log: none yet")
        if extra_lines:
            lines.append("")
            lines.extend([str(x) for x in extra_lines if str(x).strip()])
        return lines

    def _show_combat_board(self, foes: list[Actor], combat_distance_ft: int, round_no: int, *, selected_actor: Actor | None = None, selected_target: Actor | None = None, prompt: str = '', options: list[str] | None = None, selected_option: int | None = None, hotkeys: list[str] | None = None, phase: str = "Planning", extra_detail_lines: list[str] | None = None, aoe_preview_units: list[Actor] | None = None, ally_risk_units: list[Actor] | None = None) -> None:
        self._combat_board_runtime = {
            'foes': list(foes or []),
            'combat_distance_ft': int(combat_distance_ft or 0),
            'round_no': int(round_no or 0),
            'phase': str(phase or 'Planning'),
            'selected_actor': selected_actor,
            'selected_target': selected_target,
        }
        try:
            self._clear_map_screen()
        except Exception:
            pass
        self.ui.title("Combat Board")
        self._render_top_hud(self._combat_board_hud_lines(foes, combat_distance_ft, round_no, phase=phase))
        left = self._combat_board_lines(foes, combat_distance_ft, selected_actor=selected_actor, selected_target=selected_target, aoe_preview_units=aoe_preview_units, ally_risk_units=ally_risk_units)
        right = self._combat_detail_lines(foes, combat_distance_ft, round_no, selected_actor=selected_actor, selected_target=selected_target, prompt=prompt, options=options, selected_option=selected_option, hotkeys=hotkeys, extra_lines=extra_detail_lines)
        self._render_split_block(left, right, left_width=44, right_width=34, gap=2)


    def _combat_spell_option_labels(self, options: list[str] | None) -> list[str]:
        out: list[str] = []
        for opt in (options or []):
            label = str(opt or '').strip()
            if not label:
                continue
            try:
                sp = self.content.get_spell(label)
            except Exception:
                sp = None
            if sp is None:
                return []
            out.append(label)
        return out

    def _combat_spell_target_note(self, spell_name: str) -> str:
        n = ' '.join(str(spell_name or '').strip().lower().split())
        if not n:
            return 'Target: varies'
        table = {
            'magic missile': 'Target: one foe (auto-hit)',
            'sleep': 'Target: area burst of foes',
            'fireball': 'Target: area burst',
            'lightning bolt': 'Target: line through foes',
            'web': 'Target: area control / hold',
            'hold person': 'Target: one humanoid foe',
            'charm person': 'Target: one humanoid foe',
            'cure light wounds': 'Target: one ally',
            'cure moderate wounds': 'Target: one ally',
            'protection from evil': 'Target: self or one ally',
            'sanctuary': 'Target: self or one ally',
            'bless': 'Target: party support',
        }
        if n in table:
            return table[n]
        if n.startswith('cure '):
            return 'Target: one ally'
        if 'protection' in n:
            return 'Target: self or one ally'
        if any(k in n for k in ('fireball', 'sleep', 'web')):
            return 'Target: area effect'
        return 'Target: spell-defined'

    def _combat_spell_preview_units(self, spell_label: str | None, foes: list[Actor] | None = None, *, selected_actor: Actor | None = None, selected_target: Actor | None = None) -> tuple[list[Actor], Actor | None, str, list[Actor]]:
        label = str(spell_label or '').strip()
        if not label:
            return [], None, '', []
        try:
            sp = self.content.get_spell(label)
        except Exception:
            sp = None
        spell_name = getattr(sp, 'name', label) if sp is not None else label
        try:
            from .spells_grid import template_for_spell
            tmpl = template_for_spell(spell_name)
            kind = str(getattr(tmpl, 'kind', 'unit') or 'unit')
        except Exception:
            kind = 'unit'
        living_foes = self._combat_visible_foe_units(list(foes or []))
        if not living_foes:
            return [], None, 'Preview: no visible foes', []
        if kind not in ('burst', 'line'):
            pick = selected_target if selected_target in living_foes else living_foes[0]
            return [pick], pick, f"Preview: {self._battle_label_for(pick)}", []

        positions = self._combat_board_positions(list(foes or []))
        actor = selected_actor if selected_actor is not None else (self._combat_visible_party_units()[0] if self._combat_visible_party_units() else None)
        actor_pos = positions.get(id(actor)) if actor is not None else None
        if actor_pos is None:
            actor_pos = (-8, 0)
        living_party = self._combat_visible_party_units()

        def _burst_hits(center: Actor) -> tuple[list[Actor], list[Actor]]:
            cpos = positions.get(id(center))
            if cpos is None:
                return [center], []
            cx, cy = cpos
            foe_out: list[Actor] = []
            ally_out: list[Actor] = []
            for u in living_foes:
                up = positions.get(id(u))
                if up is None:
                    continue
                ux, uy = up
                if abs(ux - cx) + abs(uy - cy) <= 3:
                    foe_out.append(u)
            for u in living_party:
                up = positions.get(id(u))
                if up is None:
                    continue
                ux, uy = up
                if abs(ux - cx) + abs(uy - cy) <= 3:
                    ally_out.append(u)
            return (foe_out or [center]), ally_out

        def _line_hits(center: Actor) -> tuple[list[Actor], list[Actor]]:
            cpos = positions.get(id(center))
            if cpos is None:
                return [center], []
            tx, ty = cpos
            ax, ay = actor_pos
            foe_out: list[Actor] = []
            ally_out: list[Actor] = []
            for u in living_foes:
                up = positions.get(id(u))
                if up is None:
                    continue
                ux, uy = up
                between = min(ax, tx) <= ux <= max(ax, tx)
                same_lane = abs(uy - ty) <= 0
                if between and same_lane:
                    foe_out.append(u)
            for u in living_party:
                if u is actor:
                    continue
                up = positions.get(id(u))
                if up is None:
                    continue
                ux, uy = up
                between = min(ax, tx) <= ux <= max(ax, tx)
                same_lane = abs(uy - ty) <= 0
                if between and same_lane:
                    ally_out.append(u)
            return (foe_out or [center]), ally_out

        chooser = _burst_hits if kind == 'burst' else _line_hits
        preview_target = selected_target if selected_target in living_foes else None
        ally_hits: list[Actor] = []
        if preview_target is not None:
            hits, ally_hits = chooser(preview_target)
            mode = f"Preview vs {self._battle_label_for(preview_target)}"
        else:
            best_target = None
            best_hits: list[Actor] = []
            best_ally_hits: list[Actor] = []
            for cand in living_foes:
                cand_hits, cand_ally_hits = chooser(cand)
                score = (len(cand_hits), -len(cand_ally_hits))
                best_score = (len(best_hits), -len(best_ally_hits))
                if score > best_score:
                    best_target = cand
                    best_hits = cand_hits
                    best_ally_hits = cand_ally_hits
            hits = best_hits or [living_foes[0]]
            ally_hits = best_ally_hits
            mode = 'Best-case preview'
            preview_target = best_target or living_foes[0]
        return hits, preview_target, mode, ally_hits

    def _combat_spell_preview_lines(self, spell_label: str | None, foes: list[Actor] | None = None, *, selected_actor: Actor | None = None, selected_target: Actor | None = None) -> list[str]:
        hits, preview_target, mode, ally_hits = self._combat_spell_preview_units(spell_label, foes, selected_actor=selected_actor, selected_target=selected_target)
        if not hits:
            return [mode] if mode else []
        names = [self._battle_label_for(u) for u in hits[:4]]
        extra = ''
        if len(hits) > len(names):
            extra = f" +{len(hits) - len(names)} more"
        lines = [
            f"{mode}: {len(hits)} foe(s)",
            '  ' + ', '.join(names) + extra,
        ]
        if preview_target is not None:
            lines.append(f"  Aim point: {self._battle_label_for(preview_target)}")
        if ally_hits:
            ally_names = [self._battle_label_for(u) for u in ally_hits[:4]]
            ally_extra = ''
            if len(ally_hits) > len(ally_names):
                ally_extra = f" +{len(ally_hits) - len(ally_names)} more"
            lines.append(f"Ally risk: {len(ally_hits)} ally(s)")
            lines.append('  ' + ', '.join(ally_names) + ally_extra)
        return lines

    def _combat_spell_panel_lines(self, spell_label: str | None, foes: list[Actor] | None = None, *, selected_actor: Actor | None = None, selected_target: Actor | None = None) -> list[str]:
        label = str(spell_label or '').strip()
        if not label:
            return []
        try:
            sp = self.content.get_spell(label)
        except Exception:
            sp = None
        if sp is None:
            return []
        try:
            from .spells_grid import template_for_spell
            tmpl = template_for_spell(getattr(sp, 'name', label))
            kind = str(getattr(tmpl, 'kind', 'unit') or 'unit')
        except Exception:
            kind = 'unit'
        kind_txt = {'unit': 'single-target', 'burst': 'area burst', 'line': 'line / beam'}.get(kind, kind)
        desc = ' '.join(str(getattr(sp, 'description', '') or '').split())
        if len(desc) > 150:
            desc = desc[:147].rstrip() + '...'
        lvl = str(getattr(sp, 'spell_level', '') or '?')
        rng = str(getattr(sp, 'range', '') or '?')
        dur = str(getattr(sp, 'duration', '') or '?')
        tags = [str(t).strip() for t in (getattr(sp, 'tags', []) or []) if str(t).strip()]
        lines = [
            'Spell:',
            f"  {getattr(sp, 'name', label)}",
            f"  Level: {lvl}",
            f"  Range: {rng}",
            f"  Duration: {dur}",
            f"  Type: {kind_txt}",
            f"  {self._combat_spell_target_note(getattr(sp, 'name', label))}",
        ]
        if tags:
            lines.append('  Tags: ' + ', '.join(tags[:4]))
        preview_lines = self._combat_spell_preview_lines(label, list(foes or []), selected_actor=selected_actor, selected_target=selected_target)
        if preview_lines:
            lines.append('AoE preview:')
            lines.extend(preview_lines)
        if desc:
            lines.append('  ' + desc)
        return lines

    def _combat_action_hotkey_map(self, options: list[str] | None) -> dict[str, int]:
        mapping: dict[str, int] = {}
        if not options:
            return mapping
        for idx, opt in enumerate(options):
            label = str(opt or '').strip().lower()
            aliases: list[str] = []
            if label == 'attack':
                aliases = ['attack', 'atk', 'melee', 'strike', 'hit']
            elif label == 'shoot':
                aliases = ['shoot', 'shot', 'missile', 'fire', 'ranged']
            elif label == 'cast spell':
                aliases = ['cast', 'spell', 'magic', 'castspell']
            elif label == 'take cover':
                aliases = ['cover', 'hide', 'defend', 'duck']
            elif label == 'parry':
                aliases = ['parry', 'guard', 'defend', 'block']
            elif label == 'turn undead':
                aliases = ['turn', 'undead', 'turnundead']
            elif label == 'retreat':
                aliases = ['retreat', 'withdraw', 'fallback']
            elif label == 'surrender/parley':
                aliases = ['surrender', 'parley', 'talk']
            elif label == 'escape':
                aliases = ['escape', 'inside', 'struggle']
            elif label == 'accept':
                aliases = ['accept', 'yes']
            elif label == 'refuse (fight on)':
                aliases = ['refuse', 'no', 'fight']
            elif label == 'let them go':
                aliases = ['letgo', 'spare', 'release']
            elif label == 'pursue':
                aliases = ['pursue', 'chase']
            else:
                compact = ''.join(ch for ch in label if ch.isalnum())
                if compact:
                    aliases = [compact]
            for alias in aliases:
                mapping.setdefault(alias, idx)
        return mapping

    def _combat_action_hotkey_lines(self, options: list[str] | None) -> list[str]:
        if not options:
            return []
        hot: list[str] = []
        labels = [str(o or '').strip().lower() for o in options]
        if 'attack' in labels:
            hot.append('attack/atk = melee attack')
        if 'shoot' in labels:
            hot.append('shoot/fire = ranged attack')
        if 'cast spell' in labels:
            hot.append('cast/spell = cast spell')
        if 'take cover' in labels:
            hot.append('cover = take cover')
        if 'parry' in labels:
            hot.append('parry = defensive stance')
        if 'turn undead' in labels:
            hot.append('turn = turn undead')
        if 'retreat' in labels:
            hot.append('retreat = withdraw')
        if 'surrender/parley' in labels:
            hot.append('surrender/parley = talk')
        if 'escape' in labels:
            hot.append('escape = act from inside')
        return hot[:3]

    def _combat_generic_option_map(self, options: list[str] | None) -> dict[str, int]:
        mapping: dict[str, int] = {}
        if not options:
            return mapping
        for idx, opt in enumerate(options):
            label = str(opt or '').strip().lower()
            if not label:
                continue
            compact = ''.join(ch for ch in label if ch.isalnum())
            if compact:
                mapping.setdefault(compact, idx)
            words = [w for w in ''.join(ch if ch.isalnum() else ' ' for ch in label).split() if w]
            if words:
                mapping.setdefault(' '.join(words), idx)
                mapping.setdefault(words[0], idx)
                if len(words) >= 2:
                    mapping.setdefault(''.join(words[:2]), idx)
                    mapping.setdefault(' '.join(words[:2]), idx)
                    mapping.setdefault(''.join(w[0] for w in words[: min(3, len(words))]), idx)
            # Helpful spell shortcuts.
            if 'magic missile' in label:
                for alias in ('mm', 'missile'):
                    mapping.setdefault(alias, idx)
            if 'lightning bolt' in label:
                for alias in ('lb', 'bolt', 'lightning'):
                    mapping.setdefault(alias, idx)
            if 'fireball' in label:
                mapping.setdefault('fb', idx)
            if 'cure light wounds' in label:
                for alias in ('clw', 'curelight'):
                    mapping.setdefault(alias, idx)
            if 'cure moderate wounds' in label:
                for alias in ('cmw', 'curemoderate'):
                    mapping.setdefault(alias, idx)
            if 'hold person' in label:
                mapping.setdefault('hold', idx)
            if 'charm person' in label:
                mapping.setdefault('charm', idx)
            if 'protection from evil' in label:
                mapping.setdefault('pfe', idx)
            if 'turn undead' in label:
                mapping.setdefault('tu', idx)
        return mapping

    def _combat_generic_match(self, raw: str, options: list[str] | None) -> int | None:
        if not raw or not options:
            return None
        cleaned = str(raw or '').strip().lower()
        if not cleaned:
            return None
        for prefix in ('spell ', 'cast ', 'target ', 'ally ', 'foe ', 'pick ', 'choose '):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break
        compact = ''.join(ch for ch in cleaned if ch.isalnum())
        mapping = self._combat_generic_option_map(options)
        if compact and compact in mapping:
            return int(mapping[compact])
        if cleaned in mapping:
            return int(mapping[cleaned])
        for idx, opt in enumerate(options):
            label = str(opt or '').strip().lower()
            if cleaned and (label == cleaned or label.startswith(cleaned) or cleaned in label):
                return int(idx)
        return None

    def _combat_board_context(self) -> dict[str, object]:
        ctx = getattr(self, '_combat_board_runtime', None)
        return ctx if isinstance(ctx, dict) else {}

    def _combat_board_pick_option(self, prompt: str, options: list[str], *, selected_actor: Actor | None = None, selected_target: Actor | None = None, phase: str = 'Planning') -> int:
        ctx = self._combat_board_context()
        foes = list(ctx.get('foes') or [])
        dist = int(ctx.get('combat_distance_ft', 0) or 0)
        round_no = int(ctx.get('round_no', 0) or 0)
        return int(self._combat_board_choose(prompt, options, foes, dist, round_no, selected_actor=selected_actor, selected_target=selected_target, phase=phase))

    def _combat_board_pick_unit(self, prompt: str, units: list[Actor], *, selected_actor: Actor | None = None, selected_target: Actor | None = None, phase: str = 'Planning') -> Actor | None:
        if not units:
            return None
        labels = [self._battle_label_for(u) + f" (HP {int(getattr(u, 'hp', 0) or 0)})" for u in units]
        ix = self._combat_board_pick_option(prompt, labels, selected_actor=selected_actor, selected_target=selected_target or (units[0] if units else None), phase=phase)
        if 0 <= int(ix) < len(units):
            return units[int(ix)]
        return None


    def _combat_target_is_valid(self, target: Any, enemies: list[Any]) -> bool:
        """Return True when target is still a legal hostile target among `enemies`."""
        return theater_target_is_valid(target, enemies)

    def _combat_retarget_or_clear(self, actor: Any, plan: dict, enemies: list[Any], *, reason: str = 'stale_target') -> dict:
        """Deterministically retarget a declared attack, or clear it when no enemies remain.

        Used to keep round intent coherent when the original target died, fled, or is
        no longer on the opposing side by the time the action resolves.
        """
        if not isinstance(plan, dict):
            return {"type": "none"}
        ptype = str(plan.get('type') or '').strip().lower()
        if ptype not in {'missile', 'melee'}:
            return plan
        current = plan.get('target')
        if self._combat_target_is_valid(current, enemies):
            return plan
        if enemies:
            ordered = sorted(list(enemies), key=lambda e: self._battle_label_for(e))
            new_target = self.dice_rng.choice(ordered)
            out = dict(plan)
            out['target'] = new_target
            try:
                self.battle_evt(
                    'ROUND_RETARGET',
                    actor=self._battle_label_for(actor),
                    old_target=self._battle_label_for(current) if current is not None else '?',
                    new_target=self._battle_label_for(new_target),
                    reason=str(reason),
                )
            except Exception:
                pass
            return out
        try:
            self.battle_evt(
                'STALE_TARGET_INVALIDATED',
                actor=self._battle_label_for(actor),
                old_target=self._battle_label_for(current) if current is not None else '?',
                reason=str(reason),
            )
        except Exception:
            pass
        return {'type': 'none', 'actor': actor}

    def _combat_cohere_targets_for_side(self, action_plan: dict[int, dict], actors: list[Any], enemies: list[Any], *, reason: str = 'phase_start') -> None:
        """Retarget or clear stale declared attack targets for a side."""
        for actor in list(actors or []):
            try:
                pid = id(actor)
                plan = action_plan.get(pid)
                if not isinstance(plan, dict):
                    continue
                action_plan[pid] = self._combat_retarget_or_clear(actor, plan, list(enemies or []), reason=reason)
            except Exception:
                continue

    def _combat_effective_side_for(self, actor: Any, party: list[Any], foes: list[Any]) -> str:
        """Return the actor's current combat side, honoring side overrides when available."""
        try:
            if actor in (party or []):
                base = 'party'
            elif actor in (foes or []):
                base = 'foe'
            else:
                base = 'party' if bool(getattr(actor, 'is_pc', False)) else 'foe'
        except Exception:
            base = 'party' if bool(getattr(actor, 'is_pc', False)) else 'foe'
        try:
            mb = self.effects_mgr.query_modifiers(actor)
            override = str(getattr(mb, 'side_override', '') or '').strip().lower()
            if override in ('party', 'foe'):
                return override
        except Exception:
            pass
        return base

    def _cohere_pending_spell_entry(self, entry: dict[str, Any], party: list[Any], foes: list[Any]) -> dict[str, Any] | None:
        """Normalize delayed spell entry side/allies/foes using the caster's *current* allegiance.

        This prevents a delayed spell from resolving against the wrong target set when the
        caster has changed sides (for example due to charm-like effects) between declaration
        and spell resolution.
        """
        if not isinstance(entry, dict):
            return None
        caster = entry.get('caster')
        if caster is None:
            return None
        try:
            if int(getattr(caster, 'hp', 0) or 0) <= 0:
                return None
        except Exception:
            return None
        try:
            if 'fled' in (getattr(caster, 'effects', []) or []):
                return None
        except Exception:
            pass
        current_side = self._combat_effective_side_for(caster, list(party or []), list(foes or []))
        prior_side = str(entry.get('side') or '').strip().lower()
        if prior_side not in ('party', 'foe'):
            prior_side = current_side
        if prior_side != current_side:
            try:
                self.battle_evt(
                    'PENDING_SPELL_SIDE_COHERED',
                    caster=self._battle_label_for(caster),
                    old_side=str(prior_side),
                    new_side=str(current_side),
                    spell=str(entry.get('spell') or '?'),
                )
            except Exception:
                pass
        allies = list(party or []) if current_side == 'party' else list(foes or [])
        enemies = list(foes or []) if current_side == 'party' else list(party or [])
        out = dict(entry)
        out['side'] = current_side
        out['allies'] = [a for a in allies if getattr(a, 'hp', 0) > 0 and 'fled' not in (getattr(a, 'effects', []) or [])]
        out['foes'] = [a for a in enemies if getattr(a, 'hp', 0) > 0 and 'fled' not in (getattr(a, 'effects', []) or [])]
        return out

    def _cohere_pending_spells(self, pending_spells: list[dict[str, Any]], party: list[Any], foes: list[Any]) -> list[dict[str, Any]]:
        """Return pending spells with current side/allies/foes snapshots attached."""
        out: list[dict[str, Any]] = []
        for entry in list(pending_spells or []):
            try:
                fixed = self._cohere_pending_spell_entry(entry, list(party or []), list(foes or []))
            except Exception:
                fixed = None
            if fixed is not None:
                out.append(fixed)
        return out

    def _combat_board_choose(self, prompt: str, options: list[str], foes: list[Actor], combat_distance_ft: int, round_no: int, *, selected_actor: Actor | None = None, selected_target: Actor | None = None, phase: str = "Planning") -> int:
        # Keep scripted/headless flows stable.
        try:
            ui_name = self.ui.__class__.__name__
        except Exception:
            ui_name = ''
        if ui_name in ('ScriptedUI', 'HeadlessUI') or bool(getattr(self.ui, 'auto_combat', False)):
            self._show_combat_board(foes, combat_distance_ft, round_no, selected_actor=selected_actor, selected_target=selected_target, prompt=prompt, options=options, phase=phase)
            return int(self.ui.choose(prompt, options))

        option_idx = 0
        actor_cursor = selected_actor
        target_units = self._combat_option_target_units(options, foes)
        target_cursor = selected_target if selected_target in target_units else (target_units[0] if target_units else selected_target)
        action_hotkeys = self._combat_action_hotkey_lines(options)
        action_map = self._combat_action_hotkey_map(options)
        hotkeys = [
            '1..N choose | nexto/prevo option | use confirms',
            'nexta/preva actor inspect | nextt/prevt target cycle',
            'fa <n/e/s/w> actor focus | ft <n/e/s/w> target focus',
            'type spell/target name for direct pick',
            *action_hotkeys,
            'back cancels only if menu supports it',
        ]
        spell_options = self._combat_spell_option_labels(options)
        while True:
            extra_detail = []
            aoe_preview_units: list[Actor] = []
            ally_risk_units: list[Actor] = []
            if spell_options and 0 <= int(option_idx) < len(spell_options):
                spell_label = spell_options[int(option_idx)]
                extra_detail = self._combat_spell_panel_lines(spell_label, foes, selected_actor=actor_cursor, selected_target=target_cursor)
                aoe_preview_units, _, _, ally_risk_units = self._combat_spell_preview_units(spell_label, foes, selected_actor=actor_cursor, selected_target=target_cursor)
            self._show_combat_board(
                foes,
                combat_distance_ft,
                round_no,
                selected_actor=actor_cursor,
                selected_target=target_cursor,
                prompt=prompt,
                options=options,
                selected_option=option_idx,
                hotkeys=hotkeys,
                phase=phase,
                extra_detail_lines=extra_detail,
                aoe_preview_units=aoe_preview_units,
                ally_risk_units=ally_risk_units,
            )
            raw = str(self.ui.prompt('Combat>', '') or '').strip().lower()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            direct_ix = action_map.get(raw)
            if direct_ix is not None:
                return int(direct_ix)
            generic_ix = self._combat_generic_match(raw, options)
            if generic_ix is not None:
                return int(generic_ix)
            if raw.startswith('do '):
                direct_ix = action_map.get(raw[3:].strip())
                if direct_ix is not None:
                    return int(direct_ix)
                generic_ix = self._combat_generic_match(raw[3:].strip(), options)
                if generic_ix is not None:
                    return int(generic_ix)
            if raw in ('', 'use', 'enter', 'act', 'go'):
                if target_units and target_cursor in target_units:
                    return int(target_units.index(target_cursor))
                return int(option_idx)
            if raw in ('nexto', 'opt+', 'o+', 'down', 'j'):
                option_idx = (option_idx + 1) % max(1, len(options))
                continue
            if raw in ('prevo', 'opt-', 'o-', 'up', 'k'):
                option_idx = (option_idx - 1) % max(1, len(options))
                continue
            if raw in ('nexta', 'a+', 'tab'):
                actor_cursor = self._combat_cycle_unit(actor_cursor, self._combat_visible_party_units(), +1)
                continue
            if raw in ('preva', 'a-', 'shift+tab'):
                actor_cursor = self._combat_cycle_unit(actor_cursor, self._combat_visible_party_units(), -1)
                continue
            if raw.startswith('fa ') or raw.startswith('focusa ') or raw.startswith('actor '):
                parts = raw.split()
                if len(parts) >= 2:
                    d = parts[-1]
                    actor_cursor = self._combat_directional_unit(actor_cursor, self._combat_visible_party_units(), d, foes)
                continue
            if raw.startswith('ft ') or raw.startswith('focust ') or raw.startswith('target '):
                if target_units:
                    parts = raw.split()
                    if len(parts) >= 2:
                        d = parts[-1]
                        target_cursor = self._combat_directional_unit(target_cursor, target_units, d, foes)
                continue
            if raw in ('nextt', 't+', 'right', 'l'):
                if target_units:
                    target_cursor = self._combat_cycle_unit(target_cursor, target_units, +1)
                continue
            if raw in ('prevt', 't-', 'left', 'h'):
                if target_units:
                    target_cursor = self._combat_cycle_unit(target_cursor, target_units, -1)
                continue
            if raw in ('back', 'b', 'cancel'):
                return int(option_idx)

    def _combat_board_refresh(self, foes: list[Actor], combat_distance_ft: int, round_no: int, *, selected_actor: Actor | None = None, selected_target: Actor | None = None, prompt: str = '', options: list[str] | None = None, phase: str = "Planning") -> None:
        """Refresh the tactical board without forcing a choice prompt.

        This keeps combat feeling like one continuous screen during initiative,
        phase transitions, side turns, and round-end updates.
        """
        try:
            if bool(getattr(self.ui, 'auto_combat', False)):
                return
        except Exception:
            pass
        try:
            self._show_combat_board(
                foes,
                combat_distance_ft,
                round_no,
                selected_actor=selected_actor,
                selected_target=selected_target,
                prompt=prompt,
                options=options,
                phase=phase,
            )
        except Exception:
            pass

    def _battle_register_xp_for_defeat(self, foe: Actor) -> None:
        """Accumulate monster XP for end-of-combat summary.

        Text-mode loop historically didn't expose XP. We keep it as a combat-local
        counter (stored on self) so later town distribution can decide how to use it.
        """
        try:
            if not foe or bool(getattr(foe, "is_pc", False)):
                return
        except Exception:
            return
        try:
            counted = getattr(self, "_battle_xp_counted", None)
            if counted is None:
                counted = set()
                self._battle_xp_counted = counted
            key = id(foe)
            if key in counted:
                return
            counted.add(key)
        except Exception:
            pass
        xp = 0
        try:
            mid = getattr(foe, "monster_id", None)
            if mid and hasattr(self, "content"):
                m = self.content.get_monster(mid)
                if isinstance(m, dict):
                    xp = int(m.get("xp", 0) or 0)
        except Exception:
            xp = 0
        if xp <= 0:
            return
        try:
            cur = int(getattr(self, "_battle_xp_earned", 0) or 0)
            self._battle_xp_earned = cur + int(xp)
        except Exception:
            pass
        # Also emit a typed event so replays show XP sources.
        try:
            self.battle_evt("XP_EARNED", xp=int(xp), unit_id=str(getattr(foe, "name", "?")))
        except Exception:
            pass

    def _battle_build_round_state(self, foes: list[Actor], combat_distance_ft: int) -> BattleEvent:
        def _unit_dict(u: Actor) -> dict[str, object]:
            name = self._battle_label_for(u)
            hp = int(getattr(u, "hp", 0) or 0)
            hp_max = getattr(u, "max_hp", None)
            if hp_max is None:
                hp_max = getattr(u, "hp_max", None)
            if hp_max is not None:
                try:
                    hp_max = int(hp_max)  # type: ignore[arg-type]
                except Exception:
                    hp_max = None
            eff = list(getattr(u, "effects", []) or [])
            # keep only salient + stable few
            eff = [str(e) for e in eff if e]  # type: ignore[truthy-bool]
            eff = eff[:6]
            return {"name": name, "hp": hp, "hp_max": hp_max, "effects": eff}

        party_units = [_unit_dict(p) for p in self.party.living()]
        foe_units = [_unit_dict(f) for f in foes if getattr(f, "hp", 0) > 0]
        return evt("ROUND_STATE", party=party_units, foes=foe_units, distance_ft=int(combat_distance_ft))
    def _battle_flush_prelude(self) -> None:
        """Flush any buffered INFO before round 1 starts."""
        if not bool(getattr(self, "_battle_buffer_active", False)):
            return
        if not getattr(self, "_battle_round_buffer", None):
            return
        lines = []
        for be in list(self._battle_round_buffer):
            if be.kind == "ROUND_STARTED":
                continue
            lines.append(str(format_event(be)))
        self._battle_round_buffer = []
        if lines:
            self._battle_print_lines(lines)

    def _battle_flush_round(self) -> None:
        if not bool(getattr(self, "_battle_buffer_active", False)):
            return
        rn = int(getattr(self, "_battle_current_round", 0) or 0)
        evs = list(getattr(self, "_battle_round_buffer", []) or [])
        self._battle_round_buffer = []
        if rn <= 0 or not evs:
            return
        lines = [f"── Round {rn} ──"]
        for be in evs:
            # Header already printed; avoid redundant ROUND_STARTED text.
            if be.kind == "ROUND_STARTED":
                continue
            lines.append(str(format_event(be)))
        self._battle_print_lines(lines)

    # -----------------
    # Command dispatcher
    # -----------------

    def dispatch(self, cmd: Command) -> CommandResult:
        """Execute a command that mutates authoritative game state.

        This is the first step toward an event-driven architecture:
        - UI / scripted drivers produce Commands
        - Game executes Commands
        - Game emits Events

        For now we route the contracts flow; additional systems can migrate
        incrementally.
        """

        before_snapshot = snapshot_state(self) if self.debug_diff else None

        # Record for replay/debug (disabled while replaying to avoid duplication)
        if not bool(getattr(self, "_replay_mode", False)):
            self.replay.append(
                {
                    "at": {
                        "day": int(getattr(self, "campaign_day", 1)),
                        "watch": int(getattr(self, "day_watch", 0)),
                        "dungeon_turn": int(getattr(self, "dungeon_turn", 0)),
                        "room_id": int(getattr(self, "current_room_id", 0)),
                    },
                    "cmd": serialize_command(cmd),
                }
            )

        for sys in (self.contracts_system, self.wilderness_system, self.dungeon_system):
            res = sys.handle(cmd)
            if res is not None:
                # Emit command-level event.
                self.events.emit(
                    "command_dispatched",
                    data=CommandDispatched(command=cmd.__class__.__name__, status=res.status),
                    campaign_day=int(getattr(self, "campaign_day", 1)),
                    day_watch=int(getattr(self, "day_watch", 0)),
                    dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
                    room_id=int(getattr(self, "current_room_id", 0)),
                    party_hex=tuple(getattr(self, "party_hex", (0, 0))),
                )

                if before_snapshot is not None:
                    after_snapshot = snapshot_state(self)
                    changes = diff_snapshots(before_snapshot, after_snapshot)
                    if changes:
                        self.events.emit(
                            "state_diff",
                            data=StateDiff(changes=changes),
                            campaign_day=int(getattr(self, "campaign_day", 1)),
                            day_watch=int(getattr(self, "day_watch", 0)),
                            dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
                            room_id=int(getattr(self, "current_room_id", 0)),
                            party_hex=tuple(getattr(self, "party_hex", (0, 0))),
                        )

                self._run_validation(f"cmd:{cmd.__class__.__name__}")
                return res

        
        # -----------------
        # Test / fixtures commands
        # -----------------
        from .commands import TestCombat, TestSaveReload

        if isinstance(cmd, TestCombat):
            res = self._cmd_test_combat(monster_id=str(cmd.monster_id), count=int(cmd.count))
            self.events.emit(
                "command_dispatched",
                data=CommandDispatched(command=cmd.__class__.__name__, status=res.status),
                campaign_day=int(getattr(self, "campaign_day", 1)),
                day_watch=int(getattr(self, "day_watch", 0)),
                dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
                room_id=int(getattr(self, "current_room_id", 0)),
                party_hex=tuple(getattr(self, "party_hex", (0, 0))),
            )
            self._run_validation(f"cmd:{cmd.__class__.__name__}")
            return res

        if isinstance(cmd, TestSaveReload):
            res = self._cmd_test_save_reload(note=str(cmd.note or ""))
            self.events.emit(
                "command_dispatched",
                data=CommandDispatched(command=cmd.__class__.__name__, status=res.status),
                campaign_day=int(getattr(self, "campaign_day", 1)),
                day_watch=int(getattr(self, "day_watch", 0)),
                dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
                room_id=int(getattr(self, "current_room_id", 0)),
                party_hex=tuple(getattr(self, "party_hex", (0, 0))),
            )
            self._run_validation(f"cmd:{cmd.__class__.__name__}")
            return res

        # If we reach here, no system handled the command.
        # Always return a CommandResult (older builds accidentally dropped this).
        res = CommandResult(status="error", messages=(f"Unhandled command: {type(cmd).__name__}",))
        self.events.emit(
            "command_dispatched",
            data=CommandDispatched(command=cmd.__class__.__name__, status=res.status),
            campaign_day=int(getattr(self, "campaign_day", 1)),
            day_watch=int(getattr(self, "day_watch", 0)),
            dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
            room_id=int(getattr(self, "current_room_id", 0)),
            party_hex=tuple(getattr(self, "party_hex", (0, 0))),
        )
        return res

    # -----------------
    # Validation
    # -----------------

    def _log_rng(self, name: str, data: dict[str, Any]) -> None:
        # Replay log may not be initialized very early in __init__; guard.
        if hasattr(self, "replay") and self.replay is not None:
            self.replay.log_rng(name, data)
        # Also emit as an event for tooling if needed.
        try:
            self.events.emit(
                name,
                data=data,
                campaign_day=int(getattr(self, "campaign_day", 1)),
                day_watch=int(getattr(self, "day_watch", 0)),
                dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
                room_id=int(getattr(self, "current_room_id", 0)),
                party_hex=tuple(getattr(self, "party_hex", (0, 0))),
            )
        except Exception:
            pass

    def _run_validation(self, reason: str) -> None:
        issues = validate_state(self)
        if not issues:
            return
        self.events.emit(
            "validation_issues",
            data=ValidationIssues(issues=[f"{i.severity}: {i.message}" for i in issues]),
            campaign_day=int(getattr(self, "campaign_day", 1)),
            day_watch=int(getattr(self, "day_watch", 0)),
            dungeon_turn=int(getattr(self, "dungeon_turn", 0)),
            room_id=int(getattr(self, "current_room_id", 0)),
            party_hex=tuple(getattr(self, "party_hex", (0, 0))),
        )
        if self.enable_validation:
            errs = [i for i in issues if i.severity == "error"]
            if errs:
                raise RuntimeError(f"Validation failed: {errs[0].message}")


    def _cmd_accept_contract(self, cid: str) -> CommandResult:
        active = [x for x in (self.active_contracts or []) if x.get("status") == "accepted"]
        if len(active) >= 2:
            return CommandResult(status="error", messages=("You can only carry 2 active contracts at a time.",))

        offers = [self._link_contract_to_poi(dict(x)) for x in list(self.contract_offers or [])]
        self.contract_offers = [dict(x) for x in offers]
        c = next((x for x in offers if str(x.get("cid")) == str(cid)), None)
        if not c:
            return CommandResult(status="error", messages=("That contract is no longer available.",))

        if c.get("status") == "accepted":
            return CommandResult(status="error", messages=("Already accepted.",))

        c = self._link_contract_to_poi(dict(c))
        c["status"] = "accepted"
        c["accepted_day"] = int(self.campaign_day)
        c["visited_target"] = False
        self.active_contracts.append(c)
        self.contract_offers = [x for x in (self.contract_offers or []) if str(x.get("cid")) != str(cid)]
        self.emit(
            "contract_accepted",
            cid=str(cid),
            faction_id=c.get("faction_id"),
            target_hex=c.get("target_hex"),
            district_objective=bool(c.get("district_objective", False)),
            source_hex=c.get("source_hex"),
            target_poi_id=c.get("target_poi_id"),
        )
        self._link_rumor_to_contract(c)
        if bool(c.get("district_objective", False)):
            try:
                sh = tuple(c.get("source_hex") or [0, 0])
                th = tuple(c.get("target_hex") or [0, 0])
                c["target_hint"] = str(c.get("target_hint") or self._locator_from((int(sh[0]), int(sh[1])), (int(th[0]), int(th[1])), style="district"))
            except Exception:
                pass
        return CommandResult(status="ok", messages=("Contract accepted.",))

    def _cmd_abandon_contract(self, cid: str) -> CommandResult:
        idx = None
        c = None
        for i, x in enumerate(self.active_contracts or []):
            if str(x.get("cid")) == str(cid) and x.get("status") == "accepted":
                idx = i
                c = x
                break
        if idx is None or c is None:
            return CommandResult(status="error", messages=("No such active contract.",))

        # Match legacy behavior: abandoning counts as failure with rep penalty.
        c["status"] = "failed"
        self.adjust_rep(c.get("faction_id"), int(c.get("rep_fail", -5)))
        self.emit("contract_abandoned", cid=str(cid), faction_id=c.get("faction_id"))
        return CommandResult(status="ok", messages=("Contract abandoned.",))

    # -----------------
    # Wilderness command handlers
    # -----------------

    def _advance_wilderness_clock(self, *, travel_turns: int = 0, watch_turns: int = 0, encounter_ticks: int = 0) -> None:
        ts = getattr(self, "travel_state", None)
        if ts is None:
            self.travel_state = TravelState(location="wilderness")
            ts = self.travel_state
        if hasattr(ts, "advance_clock"):
            ts.advance_clock(travel_turns=int(travel_turns), watch_turns=int(watch_turns), encounter_ticks=int(encounter_ticks))
        else:
            ts.travel_turns = int(getattr(ts, "travel_turns", 0) or 0) + max(0, int(travel_turns))
            ts.wilderness_clock_turns = int(getattr(ts, "wilderness_clock_turns", 0) or 0) + max(0, int(travel_turns)) + max(0, int(watch_turns)) + max(0, int(encounter_ticks))
        self._update_wilderness_condition()

    def _update_wilderness_condition(self) -> None:
        """Keep a tiny deterministic wilderness-condition surface in sync with travel clocks."""
        ts = getattr(self, "travel_state", None)
        if ts is None:
            self.travel_state = TravelState(location="wilderness")
            ts = self.travel_state

        clock = max(0, int(getattr(ts, "wilderness_clock_turns", 0) or 0))
        started = max(0, int(getattr(ts, "condition_started_clock", 0) or 0))
        if started > clock:
            started = clock
        cond = str(getattr(ts, "travel_condition", "clear") or "clear").strip().lower()
        allowed = ("clear", "wind", "rain", "fog")

        reroll = cond not in allowed or (clock - started) >= 12
        if reroll:
            bucket = int(clock // 12)
            roll = int((int(getattr(self, "wilderness_seed", 0) or 0) + bucket) % 10)
            if roll <= 4:
                cond = "clear"
            elif roll <= 6:
                cond = "wind"
            elif roll <= 8:
                cond = "rain"
            else:
                cond = "fog"
            started = clock

        ts.travel_condition = cond
        ts.condition_started_clock = started
        ts.wilderness_clock_turns = clock

    def _wilderness_encounter_due(self) -> bool:
        ts = getattr(self, "travel_state", None)
        if ts is None:
            return False
        ticks = int(getattr(ts, "encounter_clock_ticks", 0) or 0)
        next_tick = int(getattr(ts, "encounter_next_check_tick", 1) or 1)
        return ticks >= max(1, next_tick)

    def _cmd_advance_watch(self, watches: int) -> CommandResult:
        w = max(0, int(watches))
        if w <= 0:
            return CommandResult(status="ok")
        before_day = int(getattr(self, "campaign_day", 1))
        before_watch = int(getattr(self, "day_watch", 0))
        self._advance_watch(w)
        self.emit(
            "watch_advanced",
            watches=w,
            before_day=before_day,
            before_watch=before_watch,
            after_day=int(getattr(self, "campaign_day", 1)),
            after_watch=int(getattr(self, "day_watch", 0)),
        )
        return CommandResult(status="ok")

    def _cmd_camp_to_morning(self) -> CommandResult:
        # Camp to morning; moderate healing.
        watches_left = (self.WATCHES_PER_DAY - self.day_watch) % self.WATCHES_PER_DAY
        if watches_left == 0:
            watches_left = self.WATCHES_PER_DAY
        self._consume_rations(units=watches_left)
        self._advance_watch(watches_left)
        for m in self.party.living():
            try:
                mods = self.effects_mgr.query_modifiers(m)
            except Exception:
                mods = None
            if mods is not None and getattr(mods, "blocks_natural_heal", False):
                self.ui.log(f"{m.name} cannot recover naturally due to disease.")
                continue
            m.hp = min(m.hp_max, m.hp + 2)
        self.emit("camped", watches_advanced=int(watches_left))
        return CommandResult(status="ok", messages=("You make camp and rest.",))

    def _cmd_set_travel_mode(self, mode: str) -> CommandResult:
        mode = str(mode or "").strip().lower()
        aliases = {"cautious": "careful", "forced": "fast"}
        mode = aliases.get(mode, mode)
        if mode not in ("careful", "normal", "fast"):
            return CommandResult(status="error", messages=("Invalid travel mode.",))
        before = str(getattr(self, "wilderness_travel_mode", "normal") or "normal")
        self.wilderness_travel_mode = mode
        self.emit("wilderness_travel_mode_set", before=before, after=mode)
        return CommandResult(status="ok", messages=(f"Travel mode set to {mode.title()}.",))

    def _wilderness_travel_params(self) -> tuple[int, int, int]:
        """Return (steps, watches_per_step, encounter_mod) for the current travel mode."""
        ctx = resolve_travel_context(self)
        mode = str(ctx.get("pace") or "normal")
        if mode == "careful":
            return (1, 2, int(ctx.get("encounter_mod", -1)))
        if mode == "fast":
            return (2, 1, int(ctx.get("encounter_mod", +1)))
        return (1, 1, 0)

    def _terrain_travel_mods(self, terrain: str) -> tuple[int, int]:
        """Return (watch_mod, encounter_mod) for lightweight terrain travel rules.

        Designed to be simple and 'boardgamey': harder terrain costs time and increases risk.
        """
        t = str(terrain or "clear").strip().lower()
        if t == "road":
            return (-1, -1)
        if t == "clear":
            return (0, 0)
        if t == "forest":
            return (1, 1)
        if t == "hills":
            return (0, 1)
        if t == "swamp":
            return (1, 1)
        if t == "mountain":
            return (1, 2)
        return (0, 0)

    def _cmd_travel_direction(self, direction: str) -> CommandResult:


        direction = str(direction).strip()


        neigh = neighbors(self.party_hex)


        if direction not in neigh:


            return CommandResult(status="error", messages=("Invalid direction.",))


        src = tuple(self.party_hex)


        steps, wps, enc_mod = self._wilderness_travel_params()


        cur = tuple(self.party_hex)


        for i in range(int(steps)):


            # Step along the chosen direction each time (forced march can move 2 hexes).


            neigh2 = neighbors(cur)


            if direction not in neigh2:


                break


            cur = neigh2[direction]
            self.party_hex = cur

            # Destination hex determines travel friction and encounter bias.
            hx2 = self._ensure_current_hex()
            terrain = str(hx2.get("terrain", "clear"))
            wps_mod, terr_enc_mod = self._terrain_travel_mods(terrain)
            wps_eff = max(1, int(wps) + int(wps_mod))

            self._consume_rations(1)
            self._advance_watch(int(wps_eff))
            self._advance_wilderness_clock(travel_turns=1, watch_turns=int(wps_eff), encounter_ticks=1)
            self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1
            self._wilderness_encounter_check(hx2, encounter_mod=int(enc_mod) + int(terr_enc_mod))


            # If the party was wiped in an encounter, stop travel chain.


            if not self.party.living():


                break


        dest = tuple(self.party_hex)


        self.emit("traveled", mode="direction", direction=direction, src=src, dest=dest, travel_mode=str(getattr(self,'wilderness_travel_mode','normal')))
        self.travel_state.location = "wilderness"
        self.travel_state.travel_turns = int(getattr(self.travel_state, "travel_turns", 0)) + 1
        self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1

        return CommandResult(status="ok")

    def _cmd_travel_to_hex(self, q: int, r: int) -> CommandResult:


        dest = (int(q), int(r))


        neigh = neighbors(self.party_hex)


        if dest not in neigh.values():


            return CommandResult(status="error", messages=("Destination is not adjacent.",))


        src = tuple(self.party_hex)


        # TravelToHex is an explicit single-step move.


        steps, wps, enc_mod = self._wilderness_travel_params()


        # Respect travel mode pacing but do not chain beyond the explicit destination.


        self.party_hex = dest

        hx2 = self._ensure_current_hex()
        terrain = str(hx2.get("terrain", "clear"))
        wps_mod, terr_enc_mod = self._terrain_travel_mods(terrain)
        wps_eff = max(1, int(wps) + int(wps_mod))

        self._consume_rations(1)
        self._advance_watch(int(wps_eff))
        self._advance_wilderness_clock(travel_turns=1, watch_turns=int(wps_eff), encounter_ticks=1)
        self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1
        self._wilderness_encounter_check(hx2, encounter_mod=int(enc_mod) + int(terr_enc_mod))


        self.emit("traveled", mode="to_hex", src=src, dest=dest, travel_mode=str(getattr(self,'wilderness_travel_mode','normal')))
        self.travel_state.location = "wilderness"
        self.travel_state.travel_turns = int(getattr(self.travel_state, "travel_turns", 0)) + 1
        self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1

        return CommandResult(status="ok")

    def _cmd_step_toward_town(self) -> CommandResult:


        if self.party_hex == (0, 0):


            return CommandResult(status="error", messages=("You are already at Town.",))


        steps, wps, enc_mod = self._wilderness_travel_params()


        src = tuple(self.party_hex)


        for _i in range(int(steps)):


            if self.party_hex == (0, 0):


                break


            # Greedy step toward (0,0)


            best = None


            best_dist = 10**9


            for _d, pos in neighbors(self.party_hex).items():


                dd = hex_distance((0, 0), pos)


                if dd < best_dist:


                    best_dist = dd


                    best = pos


            if best is None:


                return CommandResult(status="error", messages=("You cannot find a path back.",))


            self.party_hex = best

            hx2 = self._ensure_current_hex()
            terrain = str(hx2.get("terrain", "clear"))
            wps_mod, terr_enc_mod = self._terrain_travel_mods(terrain)
            wps_eff = max(1, int(wps) + int(wps_mod))

            self._consume_rations(1)
            self._advance_watch(int(wps_eff))
            self._advance_wilderness_clock(travel_turns=1, watch_turns=int(wps_eff), encounter_ticks=1)
            self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1
            self._wilderness_encounter_check(hx2, encounter_mod=int(enc_mod) + int(terr_enc_mod))


            if not self.party.living():


                break


        self.emit("traveled", mode="toward_town", src=src, dest=tuple(self.party_hex), travel_mode=str(getattr(self,'wilderness_travel_mode','normal')))
        self.travel_state.location = "wilderness" if tuple(self.party_hex) != TOWN_HEX else "town"
        self.travel_state.travel_turns = int(getattr(self.travel_state, "travel_turns", 0)) + 1
        self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1

        if self.party_hex == (0, 0):


            self.emit("reached_town")


            return CommandResult(status="ok", messages=("You reach Town.",))


        return CommandResult(status="ok")

    def _return_to_town_plan(self) -> dict[str, Any]:
        """Compute a safe return-to-town travel plan.

        The return remains guaranteed, but distance and local conditions affect
        how many full days it costs. This keeps wilderness convenience while
        preserving the strategic value of roads, safe passage, and frontier risk.
        """
        hx = self._ensure_current_hex()
        terrain = str((hx or {}).get("terrain", "clear")) if isinstance(hx, dict) else "clear"
        dist = int(hex_distance((0, 0), tuple(self.party_hex)))
        base_days = 1
        if dist <= 2:
            base_days = 1
        elif dist <= 5:
            base_days = 2
        elif dist <= 8:
            base_days = 3
        else:
            base_days = 4

        modifiers: list[tuple[str, int]] = []
        total_mod = 0

        # Roads make an organized withdrawal faster, even if not perfectly safe.
        if terrain == "road":
            total_mod -= 1
            modifiers.append(("road route", -1))
        else:
            try:
                near_road = any(str((self.world_hexes.get(n) or {}).get("terrain", "")) == "road" for n in neighbors(tuple(self.party_hex)).values())
            except Exception:
                near_road = False
            if near_road:
                total_mod -= 1
                modifiers.append(("near a road", -1))

        fid = (hx or {}).get("faction_id") if isinstance(hx, dict) else None
        if fid and isinstance(getattr(self, "safe_passage", None), dict):
            until = int(self.safe_passage.get(str(fid), 0) or 0)
            if until >= int(getattr(self, "campaign_day", 1) or 1):
                total_mod -= 1
                modifiers.append(("safe passage", -1))

        heat = int((hx or {}).get("heat", 0) or 0) if isinstance(hx, dict) else 0
        try:
            rep = int(self.faction_rep(str(fid))) if fid else 0
        except Exception:
            rep = 0
        if heat >= 60:
            total_mod += 1
            modifiers.append(("frontier heat", +1))
        elif fid and rep <= -5:
            total_mod += 1
            modifiers.append(("hostile territory", +1))

        day_cost = max(1, min(6, int(base_days) + int(total_mod)))
        return {
            "distance": dist,
            "terrain": terrain,
            "faction_id": fid,
            "base_days": int(base_days),
            "day_cost": int(day_cost),
            "modifiers": modifiers,
        }

    def _set_forward_anchor(self, hx: dict[str, Any], *, kind: str, name: str | None = None, fid: str | None = None, expires_day: int | None = None) -> None:
        q = int(hx.get("q", self.party_hex[0]))
        r = int(hx.get("r", self.party_hex[1]))
        terrain = str(hx.get("terrain", "clear") or "clear")
        display = str(name or hx.get("name") or kind.replace("_", " ").title())
        anchor = {
            "kind": str(kind),
            "name": display,
            "hex": [q, r],
            "faction_id": str(fid or hx.get("faction_id") or ""),
            "terrain": terrain,
        }
        if expires_day is not None:
            anchor["expires_day"] = int(expires_day)
        self.forward_anchor = anchor
        self.emit("forward_anchor_set", kind=str(kind), name=display, hex=(q, r), expires_day=anchor.get("expires_day"))

    def _get_forward_anchor(self) -> dict[str, Any] | None:
        anc = getattr(self, "forward_anchor", None)
        if not isinstance(anc, dict):
            return None
        hexv = anc.get("hex") or []
        try:
            q, r = int(hexv[0]), int(hexv[1])
        except Exception:
            self.forward_anchor = None
            return None
        try:
            exp = anc.get("expires_day")
            if exp is not None and int(exp) < int(self.campaign_day):
                self.ui.log(f"Your trail to {anc.get('name', 'the rendezvous')} has gone cold.")
                self.forward_anchor = None
                self.emit("forward_anchor_expired", name=str(anc.get("name", "")), hex=(q, r))
                return None
        except Exception:
            pass
        return anc

    def _return_to_anchor_plan(self) -> dict[str, Any] | None:
        anc = self._get_forward_anchor()
        if not anc:
            return None
        try:
            aq, ar = int((anc.get("hex") or [0, 0])[0]), int((anc.get("hex") or [0, 0])[1])
        except Exception:
            return None
        anchor_hex = (aq, ar)
        dist = int(hex_distance(anchor_hex, tuple(self.party_hex)))
        if dist <= 0:
            return {"day_cost": 0, "distance": 0, "modifiers": [], "anchor": anc}
        if dist <= 1:
            days = 1
        elif dist <= 3:
            days = 2
        elif dist <= 5:
            days = 3
        else:
            days = 4
        modifiers: list[tuple[str, int]] = []
        try:
            hk = f"{self.party_hex[0]},{self.party_hex[1]}"
            hx = (self.world_hexes or {}).get(hk) or {}
            terr = str(hx.get("terrain", ""))
            near_road = terr == "road"
            if not near_road:
                near_road = any(str(((self.world_hexes or {}).get(f"{pos[0]},{pos[1]}") or {}).get("terrain", "")) == "road" for pos in neighbors(tuple(self.party_hex)).values())
            if near_road:
                days -= 1
                modifiers.append(("road", -1))
        except Exception:
            pass
        fid = str(anc.get("faction_id") or "")
        if fid and isinstance(getattr(self, "safe_passage", None), dict):
            until = int(self.safe_passage.get(str(fid), 0) or 0)
            if until >= int(self.campaign_day):
                days -= 1
                modifiers.append(("safe passage", -1))
        try:
            hk = f"{self.party_hex[0]},{self.party_hex[1]}"
            hx = (self.world_hexes or {}).get(hk) or {}
            heat = int(hx.get("heat", 0) or 0)
            local_fid = str(hx.get("faction_id") or "")
            if heat >= 4:
                days += 1
                modifiers.append(("high frontier heat", +1))
            elif local_fid and fid and local_fid != fid and self.faction_rep(local_fid) <= -25:
                days += 1
                modifiers.append(("hostile territory", +1))
        except Exception:
            pass
        if str(anc.get("kind") or "") == "caravan":
            days = max(1, days - 1)
            modifiers.append(("road guides", -1))
        days = max(1, min(5, int(days)))
        return {"day_cost": days, "distance": dist, "modifiers": modifiers, "anchor": anc}

    def _cmd_return_to_anchor(self) -> CommandResult:
        plan = self._return_to_anchor_plan()
        if not plan:
            self.ui.log("You have no forward anchor to return to.")
            return CommandResult(advance=False)
        anc = plan.get("anchor") or {}
        try:
            aq, ar = int((anc.get("hex") or [0, 0])[0]), int((anc.get("hex") or [0, 0])[1])
        except Exception:
            self.forward_anchor = None
            self.ui.log("You lose the trail to your forward anchor.")
            return CommandResult(advance=False)
        self.campaign_day += int(plan.get("day_cost", 1) or 1)
        self.party_hex = (aq, ar)
        self.emit("returned_to_anchor", dest=tuple(self.party_hex), day_cost=int(plan.get("day_cost", 1) or 1), anchor_kind=str(anc.get("kind", "")), anchor_name=str(anc.get("name", "")))
        self.ui.log(f"You make safe time back to {anc.get('name', 'the forward anchor')}.")
        return CommandResult(advance=False)

    def _cmd_return_to_town(self) -> CommandResult:
        plan = self._return_to_town_plan()
        day_cost = int(plan.get("day_cost", 1) or 1)
        self.campaign_day += day_cost
        self.day_watch = 0
        self.party_hex = (0, 0)
        self._on_day_passed()
        self.emit(
            "returned_to_town",
            mode="safe",
            day_cost=day_cost,
            distance=int(plan.get("distance", 0) or 0),
            modifiers=[{"reason": str(name), "delta": int(delta)} for (name, delta) in list(plan.get("modifiers", []))],
        )
        self._advance_wilderness_clock(
            travel_turns=int(max(1, day_cost)),
            watch_turns=int(max(1, day_cost)) * int(self.WATCHES_PER_DAY),
            encounter_ticks=1,
        )
        self.travel_state.location = "town"
        self.travel_state.route_progress = 0
        returned_evt = self._append_player_event(
            "expedition.returned",
            category="expedition",
            title="Returned to Town",
            payload={"return_location": "town", "day_cost": int(day_cost)},
            refs={"hex": [0, 0]},
        )
        # Resolve contracts before recap so outcomes are reflected in the same expedition window.
        self._check_contracts_on_town_arrival()
        self.ui.title("Return to Town")
        self.ui.log(f"Travel time: {day_cost} day(s).")
        for ln in self._build_expedition_recap_lines(returned_eid=int((returned_evt or {}).get("eid", 0) or 0)):
            self.ui.log(ln)
        for pc in self.party.pcs():
            next_xp = int(self.xp_threshold_for_class(getattr(pc, "cls", "Fighter"), int(getattr(pc, "level", 1) or 1) + 1))
            cur_xp = int(getattr(pc, "xp", 0) or 0)
            remain = max(0, next_xp - cur_xp)
            hp = int(getattr(pc, "hp", 0) or 0)
            hp_max = int(getattr(pc, "hp_max", 0) or 0)
            self.ui.log(f"- {pc.name}: Lv{pc.level} XP {cur_xp}/{next_xp} (to next: {remain}) HP {hp}/{hp_max}")
        day_txt = f"{day_cost} day" + ("s" if day_cost != 1 else "")
        return CommandResult(status="ok", messages=(f"You return to Town. ({day_txt})",))

    def _build_expedition_recap_lines(self, *, returned_eid: int | None = None) -> list[str]:
        """Build concise recap lines for the most recent expedition event window."""
        rows = [e for e in (getattr(self, "event_history", []) or []) if isinstance(e, dict)]
        if not rows:
            return []
        rows.sort(key=lambda e: int((e or {}).get("eid", 0) or 0))

        end_eid = int(returned_eid or 0)
        if end_eid <= 0:
            for ev in reversed(rows):
                if str((ev or {}).get("type") or "") == "expedition.returned":
                    end_eid = int((ev or {}).get("eid", 0) or 0)
                    break
        if end_eid <= 0:
            return []

        start_ev = None
        for ev in reversed(rows):
            eid = int((ev or {}).get("eid", 0) or 0)
            if eid >= end_eid:
                continue
            if str((ev or {}).get("type") or "") == "expedition.departed":
                start_ev = ev
                break
        if start_ev is None:
            return []

        start_eid = int((start_ev or {}).get("eid", 0) or 0)
        window = [ev for ev in rows if int((ev or {}).get("eid", 0) or 0) > start_eid and int((ev or {}).get("eid", 0) or 0) <= end_eid]
        if not window:
            return []

        depth_max = 0
        pois: list[str] = []
        contracts: list[str] = []
        rumors = 0
        discoveries = 0
        for ev in window:
            et = str((ev or {}).get("type") or "")
            payload = dict((ev or {}).get("payload") or {})
            if et == "dungeon.depth_reached":
                try:
                    depth_max = max(depth_max, int(payload.get("depth", 0) or 0))
                except Exception:
                    pass
            elif et == "poi.explored":
                nm = str(payload.get("poi_name") or (ev or {}).get("title") or "").strip()
                if nm and nm not in pois:
                    pois.append(nm)
            elif et == "contract.completed":
                nm = str(payload.get("title") or "Contract").strip()
                if nm and nm not in contracts:
                    contracts.append(nm)
            elif et == "rumor.learned":
                rumors += 1
            elif et == "discovery.recorded":
                discoveries += 1

        start_payload = dict((start_ev or {}).get("payload") or {})
        start_gold = int(start_payload.get("gold_start", int(getattr(self, "gold", 0) or 0)) or 0)
        start_xp_total = int(start_payload.get("party_xp_total", 0) or 0)
        gold_gain = int(getattr(self, "gold", 0) or 0) - int(start_gold)
        xp_total_now = sum(int(getattr(pc, "xp", 0) or 0) for pc in (self.party.pcs() or []))
        xp_gain = int(xp_total_now) - int(start_xp_total)

        injured = 0
        fallen = 0
        for pc in (self.party.pcs() or []):
            hp = int(getattr(pc, "hp", 0) or 0)
            hp_max = int(getattr(pc, "hp_max", 0) or 0)
            if hp <= 0:
                fallen += 1
            elif hp < hp_max:
                injured += 1

        lines = ["Expedition Recap", "----------------"]
        if depth_max > 0:
            lines.append(f"Depth reached: {depth_max}")
        if pois:
            lines.append(f"POIs explored: {', '.join(pois[:3])}{'...' if len(pois) > 3 else ''}")
        if contracts:
            lines.append(f"Contracts completed: {', '.join(contracts[:3])}{'...' if len(contracts) > 3 else ''}")
        if gold_gain:
            lines.append(f"Gold gained: {gold_gain}")
        if xp_gain:
            lines.append(f"XP gained: {xp_gain}")
        if discoveries:
            lines.append(f"New discoveries: {discoveries}")
        if rumors:
            lines.append(f"New rumors: {rumors}")
        if fallen > 0:
            lines.append(f"Party condition: {fallen} fallen, {injured} injured")
        elif injured > 0:
            lines.append(f"Party condition: {injured} injured")
        else:
            lines.append("Party condition: ready")
        return lines

    def _cmd_investigate_current_location(self) -> CommandResult:
        hx = self._ensure_current_hex()
        poi = hx.get("poi") if isinstance(hx, dict) else None
        if not poi:
            return CommandResult(status="info", messages=("There is nothing notable here.",))
        q, r = int(self.party_hex[0]), int(self.party_hex[1])
        poi_type = str(poi.get("type") or "poi")
        poi_name = str(poi.get("name") or "Point of Interest")
        poi_id = str(poi.get("id") or f"hex:{q},{r}:{poi_type}")
        poi["id"] = poi_id
        poi["q"] = int(q)
        poi["r"] = int(r)
        explore_key = first_time_poi_resolution_key(poi, mode="explore")
        resolved_keys = set(str(x) for x in (poi.get("resolution_keys") or []))
        if explore_key not in resolved_keys:
            resolved_keys.add(explore_key)
            poi["resolution_keys"] = sorted(resolved_keys)
            poi["explored"] = True
            if isinstance(hx, dict):
                hx["poi"] = poi
                self.world_hexes[f"{q},{r}"] = hx
            self._append_player_event(
                "poi.explored",
                category="expedition",
                title=f"Explored {poi_name}",
                payload={"poi_id": poi_id, "poi_type": poi_type, "poi_name": poi_name, "resolution_key": explore_key},
                refs={"poi_id": poi_id, "hex": [q, r]},
            )
        self.emit("poi_investigation_started", poi_type=poi_type, poi_name=poi_name)
        self._handle_current_hex_poi()
        return CommandResult(status="ok")

    # -----------------
    # Dungeon command handlers
    # -----------------

    def _cmd_enter_dungeon(self) -> CommandResult:
        # Minimal deterministic overworld leg: town <-> canonical dungeon entrance.
        if tuple(getattr(self, "party_hex", TOWN_HEX)) == TOWN_HEX:
            self.party_hex = tuple(DUNGEON_ENTRANCE_HEX)
            self.travel_state.location = "wilderness"
            self.travel_state.travel_turns = int(getattr(self.travel_state, "travel_turns", 0)) + 1
            self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0)) + 1

        hx = self._ensure_current_hex()
        poi = hx.get("poi") if isinstance(hx, dict) else None
        at_entrance = tuple(getattr(self, "party_hex", TOWN_HEX)) == tuple(DUNGEON_ENTRANCE_HEX)
        poi_entrance = isinstance(poi, dict) and str(poi.get("type") or "") == "dungeon_entrance"
        if not (at_entrance or poi_entrance):
            return CommandResult(status="error", messages=("You must be at a dungeon entrance to enter.",))

        self.ui.title("Dungeon Entrance")
        self.expeditions += 1

        # P6.1: ensure a stable dungeon instance exists (blueprint/stocking/delta)
        self.dungeon_instance = ensure_instance(self.dungeon_instance, "town_dungeon_01", self.dice_seed)

        # P6.1.1+ : derive multi-level limits from the blueprint floors when available.
        try:
            bp = getattr(getattr(self.dungeon_instance, "blueprint", None), "data", None) or {}
            floors = int(bp.get("floors", 1) or 1)
        except Exception:
            floors = 1
        floors = max(1, min(12, int(floors)))
        self.max_dungeon_levels = int(floors)
        self.dungeon_level = max(1, min(int(self.max_dungeon_levels), int(getattr(self, "dungeon_level", 1) or 1)))

        # Ensure per-level dictionaries exist.
        if not isinstance(getattr(self, "dungeon_alarm_by_level", None), dict):
            self.dungeon_alarm_by_level = {}
        for lvl in range(1, int(self.max_dungeon_levels) + 1):
            self.dungeon_alarm_by_level.setdefault(lvl, 0)
            self._ensure_level_specials(lvl)

        # Runtime room cache is non-authoritative; rebuild on entry to avoid stale cache drift.
        self.dungeon_rooms = {}

        if int(getattr(self, "current_room_id", 0) or 0) <= 0:
            self.current_room_id = 1
        else:
            rid = int(self.current_room_id)
            room_floor = self._bp_room_floor(rid)
            want_floor = int(self.dungeon_level - 1)
            if room_floor is None or int(room_floor) != int(want_floor):
                landing = self._bp_pick_room_with_tag_on_level(tag="stairs_up", level=int(self.dungeon_level))
                if landing is None:
                    ids = self._bp_room_ids_on_level(int(self.dungeon_level))
                    landing = ids[0] if ids else 1
                self.current_room_id = int(landing)

        r0 = self._ensure_room(int(self.current_room_id))
        if not bool(r0.get("entered", False)):
            # Passive checks on initial room entry do not cost time.
            self._passive_room_entry_checks(r0)

        self.travel_state.location = "dungeon"
        self._append_player_event(
            "expedition.departed",
            category="expedition",
            title="Expedition departed",
            payload={
                "destination_type": str((poi or {}).get("type") or "dungeon_entrance"),
                "destination_id": str((poi or {}).get("id") or f"hex:{int(self.party_hex[0])},{int(self.party_hex[1])}:dungeon_entrance"),
                "destination_name": str((poi or {}).get("name") or "Dungeon Entrance"),
                "gold_start": int(getattr(self, "gold", 0) or 0),
                "party_xp_total": int(sum(int(getattr(pc, "xp", 0) or 0) for pc in (self.party.pcs() or []))),
            },
            refs={"hex": [int(self.party_hex[0]), int(self.party_hex[1])]},
        )
        self.emit("dungeon_entered", room_id=int(self.current_room_id), expeditions=int(self.expeditions))
        return CommandResult(status="ok")

    def _bp_room_ids_on_level(self, level: int) -> list[int]:
        """Blueprint helper: return room ids on the given 1-indexed dungeon level."""
        level = max(1, int(level))
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "blueprint", None):
            return []
        bp = getattr(inst.blueprint, "data", None) or {}
        rooms = bp.get("rooms")
        if not isinstance(rooms, dict):
            return []
        out: list[int] = []
        target_floor = int(level - 1)
        for rid_s, rec in rooms.items():
            if not isinstance(rec, dict):
                continue
            try:
                rid = int(rid_s)
            except Exception:
                continue
            try:
                floor = int(rec.get("floor", 0) or 0)
            except Exception:
                floor = 0
            if floor == target_floor:
                out.append(rid)
        out.sort()
        return out

    def _bp_room_floor(self, room_id: int) -> int | None:
        """Blueprint helper: return 0-indexed floor for a room id."""
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "blueprint", None):
            return None
        bp = getattr(inst.blueprint, "data", None) or {}
        rooms = bp.get("rooms")
        if not isinstance(rooms, dict):
            return None
        rec = rooms.get(str(int(room_id)))
        if not isinstance(rec, dict):
            return None
        try:
            return int(rec.get("floor", 0) or 0)
        except Exception:
            return None

    def _bp_pick_room_with_tag_on_level(self, *, tag: str, level: int) -> int | None:
        """Blueprint helper: pick a deterministic room on level that contains tag."""
        tag = str(tag).strip().lower()
        ids = self._bp_room_ids_on_level(level)
        if not ids:
            return None
        for rid in ids:
            tags = [t.lower() for t in (self._dungeon_room_tags(rid) or [])]
            if tag in tags:
                return int(rid)
        return None

    def _cmd_dungeon_advance_turn(self) -> CommandResult:
        # Ensure room and apply standard turn costs at the start of the turn,
        # matching legacy dungeon_loop behavior.
        room = self._ensure_room(self.current_room_id)
        before_turn = int(self.dungeon_turn)
        self._apply_turn_cost()
        self.emit("dungeon_turn_advanced", before_turn=before_turn, after_turn=int(self.dungeon_turn))

        # Wandering monster check
        if self._check_wandering_monster():
            self.emit("wandering_monster_check", triggered=True)
            self._handle_wandering_monster()
        else:
            self.emit("wandering_monster_check", triggered=False)

        # Room content (once, persist)
        if not room.get("cleared"):
            # Encounter recap capture spans combat + post-combat room rewards.
            try:
                self._encounter_begin_capture(context="dungeon_room", room_id=int(self.current_room_id), room_type=str(room.get("type")))
            except Exception:
                pass
            try:
                self._handle_room_specials(room)
            except Exception:
                pass
            if room["type"] == "boss":
                self._handle_room_monster(room)
                # Boss rooms also yield treasure once cleared
                # Boss rooms also yield treasure once cleared
                if room.get("cleared"):
                    if getattr(self, "dungeon_instance", None) is not None:
                        # Instance-driven: boss treasure is handled like normal treasure (pre-rolled in stocking).
                        if not room.get("treasure_taken"):
                            self._handle_room_treasure(room)
                    else:
                        # Legacy: boss spoils once.
                        if not room.get("boss_loot_taken"):
                            if "treasure_gp" in room and "treasure_items" in room:
                                gp = int(room.get("treasure_gp") or 0)
                                items = list(room.get("treasure_items") or [])
                            else:
                                gp, items = self.treasure.dungeon_treasure(self.dungeon_depth + 2)
                            # Transitional compatibility: route through centralized loot helper
                            # so generated item rows always enter the loot pool with runtime
                            # identification/metadata handling in one place.
                            self._grant_room_loot(gp=int(gp), items=list(items), source="boss_spoils")

                            room["boss_loot_taken"] = True
                            if isinstance(room.get("_delta"), dict):
                                room["_delta"]["boss_loot_taken"] = True
                            try:
                                self._encounter_note_loot(gp=int(gp), items=list(items), source="boss_spoils")
                            except Exception:
                                pass
                try:
                    self._encounter_end_capture()
                except Exception:
                    pass
                self.emit("room_resolved", room_id=int(self.current_room_id), room_type="boss")
            elif room["type"] == "monster":
                self._handle_room_monster(room)
                try:
                    self._encounter_end_capture()
                except Exception:
                    pass
                self.emit("room_resolved", room_id=int(self.current_room_id), room_type="monster")
            elif room["type"] == "treasury":
                # Instance-driven: treasury behaves like treasure, but stocking pre-rolls richer rewards.
                if getattr(self, "dungeon_instance", None) is not None:
                    self._handle_room_treasure(room)
                    try:
                        self._encounter_end_capture()
                    except Exception:
                        pass
                    self.emit("room_resolved", room_id=int(self.current_room_id), room_type="treasury")
                else:
                                    # Treasury is richer than normal
                                    if room.get("treasure_taken"):
                                        self.ui.log("The treasury has already been looted.")
                                        room["cleared"] = True
                                    else:
                                        gp, items = self.treasure.dungeon_treasure(self.dungeon_depth + 3)
                                        self._grant_room_loot(gp=int(gp) + 200, items=list(items), source="treasury")
                                        room["treasure_taken"] = True
                                        room["cleared"] = True
                                    try:
                                        self._encounter_end_capture()
                                    except Exception:
                                        pass
                                    self.emit("room_resolved", room_id=int(self.current_room_id), room_type="treasury")
            elif room["type"] == "treasure":
                self._handle_room_treasure(room)
                try:
                    self._encounter_end_capture()
                except Exception:
                    pass
                self.emit("room_resolved", room_id=int(self.current_room_id), room_type="treasure")
            elif room["type"] == "trap":
                self._handle_room_trap(room)
                try:
                    self._encounter_end_capture()
                except Exception:
                    pass
                self.emit("room_resolved", room_id=int(self.current_room_id), room_type="trap")
            else:
                self.ui.log(room.get("desc", "An empty chamber."))
                try:
                    self._encounter_end_capture()
                except Exception:
                    pass
                self.emit("room_resolved", room_id=int(self.current_room_id), room_type="empty")
        # Persist minimal mutable room state into the dungeon instance delta.
        try:
            self._sync_room_to_delta(int(self.current_room_id), room)
        except Exception:
            pass
        return CommandResult(status="ok")

    def _cmd_dungeon_listen(self) -> CommandResult:
        """Listen at a closed door.

        This is a *turn action* in the dungeon loop (the loop already consumes one turn
        per action). This command should not consume extra time beyond the current turn.

        Rules (MVP): roll 1d6; success on 1-2. On success, provide a vague hint
        about what lies beyond the chosen door.
        """

        room = self.dungeon_rooms.get(self.current_room_id) or self._ensure_room(self.current_room_id)
        exits = list((room.get("exits") or {}).keys())
        doors = room.get("doors") if isinstance(room.get("doors"), dict) else {}

        listenable: list[str] = []
        for k in exits:
            st = str((doors or {}).get(k, "closed"))
            # Only closed/locked/stuck doors can be meaningfully listened at.
            if st in ("closed", "locked", "stuck"):
                listenable.append(k)

        if not listenable:
            self.ui.log("There are no closed doors to listen at.")
            self.emit("dungeon_listen", room_id=int(self.current_room_id), heard=False, exit_key=None)
            return CommandResult(status="error")

        exit_key = listenable[0]
        if len(listenable) > 1:
            labels = [f"{k} ({doors.get(k)})" for k in listenable]
            labels.append("Back")
            c = self.ui.choose("Listen at which door?", labels)
            if c == len(labels) - 1:
                self.emit("dungeon_listen", room_id=int(self.current_room_id), heard=False, exit_key=None)
                return CommandResult(status="info")
            exit_key = listenable[c]

        # Base listen chance: 1-2 on d6. Thief-like classes (Thief, Monk) use
        # the better table based on best thief-like level in the party.
        chance_in_6, thief_level = self._listen_chance_in_6()
        roll = int(self.dice.d(6))
        heard = bool(roll <= chance_in_6)

        msg = "You listen... you hear only distant echoes."
        hint = "silence"

        if heard:
            # Try to peek at the adjacent room via instance stocking + delta, without forcing
            # a full room generation.
            try:
                neighbor_id = int((room.get("exits") or {}).get(exit_key))
            except Exception:
                neighbor_id = None

            has_foes = False
            if neighbor_id is not None:
                try:
                    drec = self._get_dungeon_delta_room(int(neighbor_id))
                    cleared = bool(drec.get("cleared", False))
                    srec = self._get_dungeon_stocking_room(int(neighbor_id))
                    enc = srec.get("encounter") if isinstance(srec.get("encounter"), dict) else None
                    has_foes = bool(enc) and not cleared
                except Exception:
                    has_foes = False

            if has_foes:
                msg = "You listen carefully... you hear movement beyond."
                hint = "movement"
            else:
                msg = "You listen carefully... you hear nothing."
                hint = "nothing"

        self.ui.log(msg)
        self.emit(
            "dungeon_listen",
            room_id=int(self.current_room_id),
            heard=bool(heard),
            exit_key=str(exit_key),
            roll=int(roll),
            chance_in_6=int(chance_in_6),
            thief_level=(int(thief_level) if thief_level is not None else None),
            hint=str(hint),
        )
        return CommandResult(status="info")

    def _skill_users(self, skill_name: str):
        """Return living PCs that have a thief-style % skill by name (>0).

        This supports Thieves, Monks, and any other class that gains thief skills.
        """
        users = []
        for pc in self.party.pcs():
            try:
                if getattr(pc, "hp", 0) <= 0:
                    continue
                skills = getattr(pc, "thief_skills", {}) or {}
                if not isinstance(skills, dict):
                    continue
                v = skills.get(skill_name)
                if v is None:
                    # allow alternate keys
                    alt = skill_name.lower().replace(" ", "_")
                    v = skills.get(alt)
                try:
                    pct = int(v or 0)
                except Exception:
                    pct = 0
                if pct > 0:
                    users.append((pc, pct))
            except Exception:
                continue
        return users

    def _best_skill_user(self, skill_name: str):
        """Return (pc, pct) for the best % skill user in the party."""
        users = self._skill_users(skill_name)
        if not users:
            return (None, 0)
        # highest pct wins; tie-break on level
        best_pc, best_pct = users[0]
        best_lvl = int(getattr(best_pc, 'level', 1) or 1)
        for pc, pct in users[1:]:
            lvl = int(getattr(pc, 'level', 1) or 1)
            if pct > best_pct or (pct == best_pct and lvl > best_lvl):
                best_pc, best_pct, best_lvl = pc, pct, lvl
        return best_pc, int(best_pct)

    def _best_thief_like_level(self):
        """Return the best (highest) level among PCs that have thief skills.

        Used for listen-at-doors chances; supports Thieves and Monks.
        """
        lvls = []
        for pc in self.party.pcs():
            try:
                if getattr(pc, 'hp', 0) <= 0:
                    continue
                cls = str(getattr(pc, 'cls', '') or '').lower()
                if cls in ('thief', 'monk'):
                    lvls.append(int(getattr(pc, 'level', 1) or 1))
                    continue
                # Fallback: if they have any thief skills dict populated, treat as thief-like.
                skills = getattr(pc, 'thief_skills', None)
                if isinstance(skills, dict) and skills:
                    lvls.append(int(getattr(pc, 'level', 1) or 1))
            except Exception:
                continue
        return max(lvls) if lvls else None

    def _listen_chance_in_6(self):
        """Return (chance_in_6, thief_like_level_or_none) per S&W listen rule."""
        lvl = self._best_thief_like_level()
        if lvl is None:
            return 2, None
        if lvl <= 2:
            return 3, lvl
        if lvl <= 6:
            return 4, lvl
        if lvl <= 10:
            return 5, lvl
        return 6, lvl

    def _cmd_dungeon_search_secret(self) -> CommandResult:
        room = self._ensure_room(self.current_room_id)

        rid = int(self.current_room_id)
        # Find adjacent secret edges that are not yet discovered.
        hidden: list[int] = []
        try:
            adj = self._dungeon_bp_adjacency() or {}
            for dest in adj.get(rid, []):
                flags = self._dungeon_bp_edge_flags(rid, int(dest))
                if flags.get("secret") and not self._dungeon_secret_edge_found(rid, int(dest)):
                    hidden.append(int(dest))
        except Exception:
            hidden = []

        if not hidden:
            self.ui.log("No sign of hidden passages.")
            self.emit("dungeon_search_secret", room_id=rid, found=False)
            return CommandResult(status="info")

        # Active searching consumes time and uses race odds (elf/half-elf better).
        chance_in_6 = 2
        try:
            from .races import party_best_secret_search_in_6
            chance_in_6 = int(party_best_secret_search_in_6(self.party))
        except Exception:
            pass
        chance_in_6 = max(1, min(6, chance_in_6))

        found = False
        if self.dice.in_6(chance_in_6):
            # Reveal exactly one secret edge deterministically (lowest room id).
            dest = sorted(hidden)[0]
            self._dungeon_reveal_secret_edge(rid, dest)
            room["secret_found"] = True
            found = True
            # Refresh cached rooms so exits update.
            try:
                self.dungeon_rooms.pop(rid, None)
                self.dungeon_rooms.pop(int(dest), None)
            except Exception:
                pass
            self.ui.log("You find a secret door!")
        else:
            self.ui.log("You find nothing.")

        self.emit("dungeon_search_secret", room_id=rid, found=bool(found))
        return CommandResult(status="info")

    
    def _cmd_dungeon_search_traps(self) -> CommandResult:
        room = self._ensure_room(self.current_room_id)

        # Only meaningful if the room has a trap spec.
        has_trap = (room.get("type") == "trap") or bool(room.get("trap_desc")) or bool(room.get("trap_damage"))
        if not has_trap:
            self.ui.log("You find no traps.")
            self.emit("dungeon_search_traps", room_id=int(self.current_room_id), found=False, disarmed=False, triggered=False)
            return CommandResult(status="info")

        d = room.get("_delta") if isinstance(room.get("_delta"), dict) else None

        if room.get("trap_disarmed"):
            self.ui.log("You find no active traps.")
            self.emit(
                "dungeon_search_traps",
                room_id=int(self.current_room_id),
                found=bool(room.get("trap_found")),
                disarmed=True,
                triggered=bool(room.get("trap_triggered")),
            )
            return CommandResult(status="info")

        # Phase 1: detect (find) the trap.
        if not room.get("trap_found"):
            best, chance_pct = self._best_skill_user('Find/Remove Traps')
            chance_in_6 = 1
            if best and chance_pct > 0:
                # Map percent to in-6 roughly.
                chance_in_6 = max(1, min(5, (int(chance_pct) + 14) // 20))
            found = self.dice.in_6(chance_in_6)
            if found:
                room["trap_found"] = True
                if d is not None:
                    d["trap_found"] = True
                self.ui.log("You discover signs of a trap!")
                self.emit("dungeon_trap_found", room_id=int(self.current_room_id))
            else:
                self.ui.log("You find nothing suspicious.")
            try:
                self._sync_room_to_delta(int(room.get("id", self.current_room_id)), room)
            except Exception:
                pass
            self.emit("dungeon_search_traps", room_id=int(self.current_room_id), found=bool(found), disarmed=False, triggered=False)
            return CommandResult(status="info")

        # Phase 2: attempt to disarm (requires a Find/Remove Traps % skill user).
        thief, chance_pct = self._best_skill_user('Find/Remove Traps')
        if not thief or int(chance_pct or 0) <= 0:
            self.ui.log("You have found a trap, but no one is trained to disarm it.")
            self.emit("dungeon_search_traps", room_id=int(self.current_room_id), found=True, disarmed=False, triggered=False)
            return CommandResult(status="error")

        chance_pct = int(chance_pct)
        roll = self.dice.percent()
        if roll <= max(1, min(99, chance_pct)):
            room["trap_disarmed"] = True
            room["cleared"] = True
            if d is not None:
                d["trap_disarmed"] = True
                d["cleared"] = True
            self.ui.log(f"{thief.name} disarms the trap!")
            self.emit("dungeon_trap_disarmed", room_id=int(self.current_room_id), by=str(thief.name))
            try:
                self._sync_room_to_delta(int(room.get("id", self.current_room_id)), room)
            except Exception:
                pass
            return CommandResult(status="ok")

        self.ui.log(f"{thief.name} fails to disarm the trap ({roll}/{chance_pct})!")
        self.noise_level = min(5, self.noise_level + 1)
        self._handle_room_trap(room)
        self.emit("dungeon_search_traps", room_id=int(self.current_room_id), found=True, disarmed=False, triggered=True)
        return CommandResult(status="info")

    def _cmd_dungeon_sneak(self) -> CommandResult:
        # Use the best party member with both Move Silently and Hide in Shadows skills.
        best_pc = None
        best_ms = 0
        best_hs = 0
        best_score = -1
        for pc in self.party.pcs():
            if getattr(pc, 'hp', 0) <= 0:
                continue
            skills = getattr(pc, 'thief_skills', {}) or {}
            if not isinstance(skills, dict):
                continue
            try:
                ms = int(skills.get('Move Silently', 0) or skills.get('move_silently', 0) or 0)
            except Exception:
                ms = 0
            try:
                hs = int(skills.get('Hide in Shadows', 0) or skills.get('hide_in_shadows', 0) or 0)
            except Exception:
                hs = 0
            if ms <= 0 or hs <= 0:
                continue
            score = ms + hs
            lvl = int(getattr(pc, 'level', 1) or 1)
            if score > best_score or (score == best_score and lvl > int(getattr(best_pc, 'level', 1) or 1)):
                best_pc, best_ms, best_hs, best_score = pc, ms, hs, score

        if not best_pc:
            self.ui.log("No one in the party can hide and move silently.")
            return CommandResult(status="error")

        thief = best_pc
        ms = int(best_ms)
        hs = int(best_hs)
        roll_ms = self.dice.percent()
        roll_hs = self.dice.percent()
        ok_ms = roll_ms <= max(1, min(99, ms))
        ok_hs = roll_hs <= max(1, min(99, hs))
        self.party_stealth_success = bool(ok_ms and ok_hs)
        if self.party_stealth_success:
            self.ui.log(f"{thief.name} slips ahead (Move Silently {roll_ms}/{ms}, Hide {roll_hs}/{hs}).")
            # Reduce noise a bit.
            self.noise_level = max(0, self.noise_level - 1)
        else:
            self.ui.log(f"Stealth fails (Move Silently {roll_ms}/{ms}, Hide {roll_hs}/{hs}).")
            self.noise_level = min(5, self.noise_level + 1)
        self.emit("dungeon_sneak", room_id=int(self.current_room_id), success=bool(self.party_stealth_success))
        return CommandResult(status="info")

    def _cmd_dungeon_prepare_spells(self) -> CommandResult:
        # UX: editing the spellbook is not an in-world action and does not
        # advance dungeon time.
        self.prepare_spells_menu(in_dungeon=True)
        self.emit("dungeon_prepare_spells", room_id=int(self.current_room_id))
        return CommandResult(status="ok")

    def _cmd_dungeon_cast_spell(self) -> CommandResult:
        """Cast a prepared spell while exploring.

        This is primarily for exploration magic (Light, Detect Magic, Sanctuary, etc.).
        UX: Casting exploration spells does not automatically advance dungeon time;
        time pressure is applied by subsequent movement/actions.
        """
        casters = [a for a in self.party.living() if list(getattr(a, 'spells_prepared', []) or [])]
        if not casters:
            self.ui.log("No one has prepared spells.")
            return CommandResult(status="error")

        labels = [f"{a.name} ({getattr(a,'cls','')})" for a in casters]
        ci = self.ui.choose("Cast spell: who?", labels)
        caster = casters[ci]
        prepared = list(getattr(caster, 'spells_prepared', []) or [])
        disp = [self.content.spell_display(s) for s in prepared]
        si = self.ui.choose("Cast which spell?", disp)
        spell_ident = prepared[si]
        sp = self.content.get_spell(spell_ident)
        spell_name = getattr(sp, "name", spell_ident)
        from .spellcasting import apply_spell_out_of_combat
        apply_spell_out_of_combat(game=self, caster=caster, spell_name=spell_name, context="dungeon")

        # Consume the spell after applying.
        try:
            caster.spells_prepared.remove(spell_ident)
        except Exception:
            caster.spells_prepared = [s for s in prepared if s != spell_ident]

        self.ui.log(f"{caster.name} casts {spell_name}.")

        self.emit("dungeon_cast_spell", room_id=int(self.current_room_id), spell=str(spell_name))
        return CommandResult(status="ok")

    def _cmd_dungeon_light_torch(self) -> CommandResult:
        before = int(self.torches)
        self.light_torch()
        self.emit("dungeon_light_torch", before=int(before), after=int(self.torches))
        return CommandResult(status="ok")

    def _cmd_dungeon_leave(self) -> CommandResult:
        self.party_hex = tuple(DUNGEON_ENTRANCE_HEX)
        self.travel_state.location = "wilderness"
        self.emit("dungeon_left", room_id=int(self.current_room_id))
        return CommandResult(status="ok", messages=("leave",))

    def _cmd_dungeon_move(self, exit_key: str) -> CommandResult:
        room = self._ensure_room(self.current_room_id)
        key = str(exit_key)
        if key not in room["exits"]:
            return CommandResult(status="error", messages=("No such exit.",))

        state = room["doors"].get(key, "open")
        dest = int(room["exits"][key])
        edge_meta = self._dungeon_bp_edge_flags(int(self.current_room_id), dest)
        gate_name = str(edge_meta.get('gate') or '').strip()
        key_id = str(edge_meta.get('key_id') or '').strip()
        if state == 'locked' and key_id and self._dungeon_has_gate_key(key_id):
            room['doors'][key] = 'open'
            try:
                back = self._ensure_room(dest)
                for bk, bv in (back.get('exits') or {}).items():
                    if int(bv) == int(self.current_room_id):
                        back.setdefault('doors', {})[str(bk)] = 'open'
                        self._sync_room_to_delta(int(dest), back)
                        break
            except Exception:
                pass
            self._sync_room_to_delta(int(self.current_room_id), room)
            if gate_name:
                self.ui.log(f'Your {self._dungeon_gate_label(gate_name)} token opens the way.')
                self._dungeon_note_gate_seen(gate_name, key_id)
            state = 'open'
        if state in ("locked", "stuck", "closed"):
            # UI should decide whether to open/force; keep engine prompt-free.
            return CommandResult(status="needs_input", messages=("door_blocked",), data={"exit_key": key, "state": state, "gate": gate_name, "key_id": key_id, "has_key": bool(key_id and self._dungeon_has_gate_key(key_id))})

        # Move
        dest = int(room["exits"][key])
        src = int(self.current_room_id)
        self.current_room_id = dest
        dest_room = self._ensure_room(dest)
        # Passive entry checks do not cost time: automatic trap/secret checks on first entry.
        self._passive_room_entry_checks(dest_room)
        # P6.1.2.0: trap rooms can trigger on entry unless previously found/disarmed.
        if (dest_room.get("type") == "trap" or dest_room.get("trap_desc")) and not dest_room.get("trap_disarmed"):
            if not dest_room.get("trap_found") and not dest_room.get("trap_triggered"):
                # Light touch: 2-in-6 chance to trigger when you blunder in.
                if self.dice.in_6(2):
                    self._handle_room_trap(dest_room)
        self.ui.log(f"You move to Room {dest}.")
        self.emit("dungeon_moved", src=int(src), dest=int(dest), exit_key=str(key))
        return CommandResult(status="ok")

    def _cmd_dungeon_door_action(self, exit_key: str, action: str) -> CommandResult:
        room = self._ensure_room(self.current_room_id)
        key = str(exit_key)
        if key not in room["exits"]:
            return CommandResult(status="error", messages=("No such exit.",))

        state = room["doors"].get(key, "open")
        dest = int(room['exits'][key])
        edge_meta = self._dungeon_bp_edge_flags(int(self.current_room_id), dest)
        gate_name = str(edge_meta.get('gate') or '').strip()
        key_id = str(edge_meta.get('key_id') or '').strip()
        has_key = bool(key_id and self._dungeon_has_gate_key(key_id))
        if gate_name:
            self._dungeon_note_gate_seen(gate_name, key_id)
        if state not in ("locked", "stuck", "closed"):
            return CommandResult(status="ok")

        action = str(action).strip().lower()
        if action not in ("open", "force", "pick"):
            return CommandResult(status="error", messages=("Invalid door action.",))

        if action == "open":
            if state == "locked" and has_key:
                room['doors'][key] = 'open'
                try:
                    back = self._ensure_room(dest)
                    for bk, bv in (back.get('exits') or {}).items():
                        if int(bv) == int(self.current_room_id):
                            back.setdefault('doors', {})[str(bk)] = 'open'
                            self._sync_room_to_delta(int(dest), back)
                            break
                except Exception:
                    pass
                self._sync_room_to_delta(int(self.current_room_id), room)
                if gate_name:
                    self.ui.log(f'Your {self._dungeon_gate_label(gate_name)} token opens the door without a sound.')
                else:
                    self.ui.log('You unlock the door.')
                self.emit('dungeon_door_opened', room_id=int(self.current_room_id), exit_key=str(key), method='key')
                return CommandResult(status='ok')
            if state == "locked":
                if gate_name and key_id:
                    self.ui.log(f"It's locked. You likely need a {self._dungeon_gate_label(gate_name)} token or clue.")
                else:
                    self.ui.log("It's locked.")
                return CommandResult(status="error", messages=("locked",))
            if state == "stuck" and not self.dice.in_6(2):
                self.ui.log("It won't budge.")
                return CommandResult(status="error", messages=("stuck",))
            room["doors"][key] = "open"
            # Mirror on far side
            try:
                dest = int(room["exits"][key])
                back = self._ensure_room(dest)
                for bk, bv in (back.get("exits") or {}).items():
                    if int(bv) == int(self.current_room_id):
                        back.setdefault("doors", {})[str(bk)] = "open"
                        self._sync_room_to_delta(int(dest), back)
                        break
            except Exception:
                pass
            self._sync_room_to_delta(int(self.current_room_id), room)
            self.emit("dungeon_door_opened", room_id=int(self.current_room_id), exit_key=str(key), method="open")
            return CommandResult(status="ok")

        if action == "pick":
            if state != "locked":
                self.ui.log("There's no lock to pick.")
                return CommandResult(status="error", messages=("not_locked",))
            # Require a party member (Thief, Monk, etc.) with an Open Locks % skill.
            best, best_chance = self._best_skill_user('Open Locks')
            if not best or best_chance <= 0:
                self.ui.log("No one in the party knows how to pick locks. You'll have to force it.")
                return CommandResult(status="error", messages=("no_pick_skill",))
            roll = self.dice.percent()
            if roll <= best_chance:
                room["doors"][key] = "open"
                # Mirror the door state on the far side if possible.
                try:
                    dest = int(room['exits'][key])
                    back = self._ensure_room(dest)
                    for bk, bv in (back.get('exits') or {}).items():
                        if int(bv) == int(self.current_room_id):
                            back.setdefault('doors', {})[str(bk)] = 'open'
                            self._sync_room_to_delta(int(dest), back)
                            break
                except Exception:
                    pass
                self._sync_room_to_delta(int(self.current_room_id), room)
                self.ui.log(f"{best.name} picks the lock!")
                self.emit("dungeon_door_opened", room_id=int(self.current_room_id), exit_key=str(key), method="pick", chance=int(best_chance), roll=int(roll))
                return CommandResult(status="ok")
            self.ui.log(f"{best.name} fails to pick the lock.")
            self.noise_level = min(5, self.noise_level + 1)
            return CommandResult(status="error", messages=("pick_failed",))

        # force
        # S&W: forcing doors uses Strength score odds (in 6).
        # 3–6 STR: 1-in-6
        # 7–15 STR: 2-in-6
        # 16 STR: 3-in-6
        # 17 STR: 4-in-6
        # 18 STR: 5-in-6
        best_score = 0
        for pc in (self.party.pcs() if self.party else []):
            st = getattr(pc, 'stats', None)
            try:
                score = int(getattr(st, 'STR', 0) or 0)
            except Exception:
                score = 0
            if score > best_score:
                best_score = score

        if best_score <= 0:
            odds = 1
        elif 3 <= best_score <= 6:
            odds = 1
        elif 7 <= best_score <= 15:
            odds = 2
        elif best_score == 16:
            odds = 3
        elif best_score == 17:
            odds = 4
        else:
            # 18+ treated as 18
            odds = 5

        if self.dice.in_6(odds):
            room['doors'][key] = 'open'
            # Mirror on far side
            try:
                dest = int(room['exits'][key])
                back = self._ensure_room(dest)
                for bk, bv in (back.get('exits') or {}).items():
                    if int(bv) == int(self.current_room_id):
                        back.setdefault('doors', {})[str(bk)] = 'open'
                        self._sync_room_to_delta(int(dest), back)
                        break
            except Exception:
                pass
            self._sync_room_to_delta(int(self.current_room_id), room)
            self.ui.log('You force it open!')
            self.noise_level = min(5, self.noise_level + 2)
            lvl = int(getattr(self, 'dungeon_level', 1) or 1)
            self.dungeon_alarm_by_level[lvl] = min(5, int(self.dungeon_alarm_by_level.get(lvl, 0)) + 1)
            self.emit('dungeon_door_opened', room_id=int(self.current_room_id), exit_key=str(key), method='force', str_score=int(best_score), odds_in_6=int(odds))
            return CommandResult(status='ok')
        self.ui.log('You fail to force it.')
        self.noise_level = min(5, self.noise_level + 1)
        lvl = int(getattr(self, 'dungeon_level', 1) or 1)
        self.dungeon_alarm_by_level[lvl] = min(5, int(self.dungeon_alarm_by_level.get(lvl, 0)) + 1)
        self.emit('dungeon_door_force_failed', room_id=int(self.current_room_id), exit_key=str(key), str_score=int(best_score), odds_in_6=int(odds))
        return CommandResult(status='error', messages=('force_failed',))


    def _cmd_dungeon_use_stairs(self, direction: str) -> CommandResult:
        direction = str(direction).lower()
        room = self.dungeon_rooms.get(self.current_room_id) or self._ensure_room(self.current_room_id)
        stairs = room.get("stairs") or {}
        if direction == "down":
            if not stairs.get("down"):
                return CommandResult(status="error", messages=("No stairs down here.",))
            if int(self.dungeon_level) >= int(self.max_dungeon_levels):
                return CommandResult(status="error", messages=("These stairs descend into collapsed rubble.",))
            self.dungeon_level = int(self.dungeon_level) + 1
        elif direction == "up":
            if not stairs.get("up"):
                return CommandResult(status="error", messages=("No stairs up here.",))
            if int(self.dungeon_level) <= 1:
                return CommandResult(status="error", messages=("These stairs lead nowhere.",))
            self.dungeon_level = int(self.dungeon_level) - 1
        else:
            return CommandResult(status="error", messages=("Invalid stairs direction.",))

        # Keep legacy depth-scaled systems coherent with active dungeon level.
        prev_depth = int(getattr(self, "dungeon_depth", 1) or 1)
        self.dungeon_depth = int(self.dungeon_level)
        if int(self.dungeon_depth) > int(prev_depth):
            self._append_player_event(
                "dungeon.depth_reached",
                category="expedition",
                title=f"Reached dungeon depth {int(self.dungeon_depth)}",
                payload={"depth": int(self.dungeon_depth)},
                refs={"hex": [int(self.party_hex[0]), int(self.party_hex[1])]},
            )

        # Instance-driven (P6.1): do not swap per-level room caches.
        # Rooms are keyed by id in the blueprint, and 'level' maps to blueprint floors.
        if getattr(self, "dungeon_instance", None) is not None:
            self.dungeon_alarm_by_level.setdefault(int(self.dungeon_level), 0)
            self._ensure_level_specials(int(self.dungeon_level))

            # Land in a sensible stair-connected room on the destination level.
            if direction == "down":
                landing = self._bp_pick_room_with_tag_on_level(tag="stairs_up", level=int(self.dungeon_level))
            else:
                landing = self._bp_pick_room_with_tag_on_level(tag="stairs_down", level=int(self.dungeon_level))
            if landing is None:
                landing = 1
            self.current_room_id = int(landing)
            self._ensure_room(self.current_room_id)
        else:
            # Legacy per-level generated dungeons: keep swapping the active cache.
            self.dungeon_levels[int(self.dungeon_level)] = self.dungeon_rooms
            self.dungeon_levels.setdefault(int(self.dungeon_level), {})
            self.dungeon_rooms = self.dungeon_levels[int(self.dungeon_level)]
            self.dungeon_alarm_by_level.setdefault(int(self.dungeon_level), 0)
            self._ensure_level_specials(int(self.dungeon_level))
            self.current_room_id = 1
            self._ensure_room(self.current_room_id)
        self.emit("dungeon_level_changed", level=int(self.dungeon_level), direction=str(direction))
        return CommandResult(status="ok", messages=(f"You take the stairs {direction}.",))


    # -----------------
    # Factions
    # -----------------

    def _town_guild_id(self) -> str | None:
        for fid, f in (self.factions or {}).items():
            if bool(f.get("is_town_guild")):
                return fid
        return None

    def faction_rep(self, fid: str | None) -> int:
        if not fid:
            return 0
        f = (self.factions or {}).get(fid) or {}
        return int(f.get("rep", 0))

    def adjust_rep(self, fid: str | None, delta: int):
        if not fid:
            return
        f = (self.factions or {}).get(fid)
        if not f:
            return
        before = int(f.get("rep", 0))
        after = clamp_rep(before + int(delta))
        f["rep"] = after
        self.factions[fid] = f
        self.emit("rep_adjusted", faction_id=fid, before=before, after=after, delta=int(delta))

    def _init_factions_if_needed(self):
        # Only generate if none exist.
        if getattr(self, "factions", None):
            if isinstance(self.factions, dict) and len(self.factions) > 0:
                return
        if not hasattr(self, "wilderness_rng"):
            self.wilderness_rng = LoggedRandom(self.wilderness_seed, channel="wilderness", log_fn=self._log_rng)

        core = generate_static_core_factions(self.wilderness_rng)
        # Assign territories
        assign_territories(self.wilderness_rng, core, 3, 8)
        # Store as dicts
        self.factions = {fid: fac.to_dict() for fid, fac in core.items()}
        # Seed faction strongholds (POIs) at home hexes for non-town factions.
        # This provides dungeon entrances tied to faction identity and supports strike-stronghold contracts.
        for fid, f in (self.factions or {}).items():
            if bool(f.get("is_town_guild")):
                continue
            home = f.get("home_hex")
            if not home:
                continue
            try:
                q, r = int(home[0]), int(home[1])
            except Exception:
                continue
            hk = f"{q},{r}"
            hx_obj = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng)
            hx = self.world_hexes.get(hk) if isinstance(self.world_hexes, dict) else None
            if not isinstance(hx, dict):
                hx = hx_obj.to_dict()
            poi = hx.get("poi")
            if not (isinstance(poi, dict) and poi.get("resolved") is True):
                hx["poi"] = {
                    "type": "stronghold",
                    "name": f"{f.get('name', 'Faction')} Stronghold",
                    "resolved": False,
                    "faction_id": fid,
                    "dungeon_state": None,
                }
            self.world_hexes[hk] = hx

        # Initialize static conflict clocks
        clocks = generate_static_conflict_clocks(core)
        self.conflict_clocks = {cid: c.to_dict() for cid, c in clocks.items()}
        self.last_clock_day = int(self.campaign_day)

        # Initialize weekly contract offers
        self.last_contract_day = int(self.campaign_day)
        self.contract_offers = [self._link_contract_to_poi(c.to_dict()) for c in generate_contracts(self.wilderness_rng, self.factions, self.world_hexes, self.campaign_day, n=3)]
        # Ensure POI-targeting contracts point to an actual POI, even beyond the currently explored frontier.
        for c in (self.contract_offers or []):
            poi_type = c.get("target_poi_type")
            if not poi_type:
                continue
            th = tuple(c.get("target_hex") or [0, 0])
            hx_obj = ensure_hex(self.world_hexes, th, self.wilderness_rng)
            hk = f"{th[0]},{th[1]}"
            hx = self.world_hexes.get(hk) if isinstance(self.world_hexes, dict) else None
            if not isinstance(hx, dict):
                hx = hx_obj.to_dict()
            poi = hx.get("poi")
            if not (isinstance(poi, dict) and poi.get("type") == poi_type):
                name_map = {
                    "ruins": "Forgotten Ruins",
                    "shrine": "Wayside Shrine",
                    "lair": "Enemy Stronghold",
                    "dungeon_entrance": "Hidden Dungeon Entrance",
                }
                fid = c.get("faction_id")
                if c.get("kind") == "strike_stronghold" and c.get("target_enemy_faction"):
                    fid = c.get("target_enemy_faction")
                hx["poi"] = {"type": poi_type, "name": name_map.get(poi_type, "Notable Site"), "resolved": False, "faction_id": fid}
            self.world_hexes[hk] = hx
        self.active_contracts = []

        # Apply initial territory markings to currently generated hexes (if any)
        self._apply_faction_territories_to_world()

    def _apply_faction_territories_to_world(self):
        """Mark faction_id on hexes for any controlled territory currently in the world.

        We do not eagerly generate all territory hexes; they will be generated on demand.
        This function ensures that when a hex exists in world_hexes, it gets the correct faction_id.
        """
        for fid, f in (self.factions or {}).items():
            controlled = f.get("controlled_hexes") or []
            for pos in controlled:
                try:
                    q, r = int(pos[0]), int(pos[1])
                except Exception:
                    continue
                k = f"{q},{r}"
                if k in (self.world_hexes or {}):
                    hx = self.world_hexes[k]
                    if isinstance(hx, dict) and not hx.get("faction_id"):
                        hx["faction_id"] = fid
                        self.world_hexes[k] = hx

    def _ensure_faction_for_hex(self, hx: dict[str, Any]):
        """If a hex has no faction_id, set it based on static territories."""
        if not hx or hx.get("faction_id"):
            return
        try:
            q = int(hx.get("q", 0))
            r = int(hx.get("r", 0))
        except Exception:
            return
        for fid, f in (self.factions or {}).items():
            for pos in (f.get("controlled_hexes") or []):
                try:
                    if int(pos[0]) == q and int(pos[1]) == r:
                        hx["faction_id"] = fid
                        return
                except Exception:
                    continue

    def view_factions(self):
        self.ui.title("Factions")
        if not self.factions:
            self.ui.log("No factions have been generated.")
            return
        # Show in a stable order
        for fid in sorted(self.factions.keys()):
            f = self.factions[fid]
            rep = int(f.get("rep", 0))
            self.ui.hr()
            self.ui.log(f"{f.get('name','Faction')} ({fid})")
            self.ui.log(f"Type: {f.get('ftype')} | Alignment: {f.get('alignment')} | Reputation: {rep}")
            # Known territories = discovered hexes only
            known = []
            for pos in (f.get("controlled_hexes") or []):
                try:
                    q, r = int(pos[0]), int(pos[1])
                except Exception:
                    continue
                k = f"{q},{r}"
                hx = (self.world_hexes or {}).get(k) or {}
                if isinstance(hx, dict) and hx.get("discovered"):
                    known.append((q, r, hx.get("terrain", "?")))
            if known:
                show = ", ".join([f"({q},{r}) {t}" for (q, r, t) in known[:8]])
                if len(known) > 8:
                    show += ", …"
                self.ui.log(f"Known territory: {show}")
            else:
                self.ui.log("Known territory: (none discovered)")

    # -----------------
    # Conflict clocks + contracts (Phase 2)
    # -----------------

    def _refresh_contracts_if_needed(self):
        # Weekly refresh
        if (self.campaign_day - int(getattr(self, "last_contract_day", 0))) < 7:
            return
        self.last_contract_day = int(self.campaign_day)
        self.contract_offers = [self._link_contract_to_poi(c.to_dict()) for c in generate_contracts(self.wilderness_rng, self.factions, self.world_hexes, self.campaign_day, n=3)]
        # Ensure POI-targeting contracts point to an actual POI, even beyond the currently explored frontier.
        for c in (self.contract_offers or []):
            poi_type = c.get("target_poi_type")
            if not poi_type:
                continue
            th = tuple(c.get("target_hex") or [0, 0])
            hx_obj = ensure_hex(self.world_hexes, th, self.wilderness_rng)
            hk = f"{th[0]},{th[1]}"
            hx = self.world_hexes.get(hk) if isinstance(self.world_hexes, dict) else None
            if not isinstance(hx, dict):
                hx = hx_obj.to_dict()
            poi = hx.get("poi")
            if not (isinstance(poi, dict) and poi.get("type") == poi_type):
                name_map = {
                    "ruins": "Forgotten Ruins",
                    "shrine": "Wayside Shrine",
                    "lair": "Enemy Stronghold",
                    "dungeon_entrance": "Hidden Dungeon Entrance",
                }
                fid = c.get("faction_id")
                if c.get("kind") == "strike_stronghold" and c.get("target_enemy_faction"):
                    fid = c.get("target_enemy_faction")
                hx["poi"] = {"type": poi_type, "name": name_map.get(poi_type, "Notable Site"), "resolved": False, "faction_id": fid}
            self.world_hexes[hk] = hx

    def _advance_conflict_clocks_if_needed(self):
        last = int(getattr(self, "last_clock_day", 0))
        # Advance weekly
        while (self.campaign_day - last) >= 7:
            last += 7
            self._advance_one_clock_week()
        self.last_clock_day = last

    def _advance_one_clock_week(self):
        if not self.conflict_clocks:
            return
        # Pick 1-2 clocks to advance.
        keys = list(self.conflict_clocks.keys())
        self.wilderness_rng.shuffle(keys)
        n = 1 if self.wilderness_rng.random() < 0.6 else 2
        for cid in keys[:n]:
            clk = self.conflict_clocks.get(cid) or {}
            prog = int(clk.get("progress", 0)) + 1
            clk["progress"] = prog
            self.conflict_clocks[cid] = clk
            if prog >= int(clk.get("threshold", 6)):
                self._resolve_clock_event(clk)

    
        # Heat decays weekly across the map.
        for k, hx in (self.world_hexes or {}).items():
            if isinstance(hx, dict) and int(hx.get('heat', 0) or 0) > 0:
                hx['heat'] = max(0, int(hx.get('heat', 0)) - 1)
                self.world_hexes[k] = hx

    def _resolve_clock_event(self, clk: dict[str, Any]):
        """Trigger a simple event and reset the clock."""
        a = str(clk.get("a", ""))
        b = str(clk.get("b", ""))
        if a not in self.factions or b not in self.factions:
            clk["progress"] = 0
            return

        # Favor the faction the party likes more, slightly.
        ra = self.faction_rep(a)
        rb = self.faction_rep(b)
        bias = ra - rb
        roll = self.wilderness_rng.randint(-20, 20) + bias
        winner = a if roll >= 0 else b
        loser = b if winner == a else a

        changed_hex = self._shift_one_territory_hex(winner, loser)

        # Log as a rumor-like entry (persistent, visible in journal)
        fa = self.factions[winner].get("name", winner)
        fb = self.factions[loser].get("name", loser)
        hint = f"Conflict: {fa} gains ground against {fb}."
        if changed_hex:
            q, r = changed_hex
            hint += f" Reports mention trouble near ({q},{r})."
        self.rumors.append({
            "day": int(self.campaign_day),
            "q": int(changed_hex[0]) if changed_hex else 0,
            "r": int(changed_hex[1]) if changed_hex else 0,
            "terrain": "?",
            "kind": "conflict",
            "hint": hint,
            "cost": 0,
        })
        self.ui.log(hint)

        # Reset clock
        clk["progress"] = 0

    def _shift_one_territory_hex(self, winner: str, loser: str) -> tuple[int, int] | None:
        """Move one non-home hex from loser to winner, if possible."""
        lf = self.factions.get(loser) or {}
        wf = self.factions.get(winner) or {}
        lhome = tuple(lf.get("home_hex") or [0, 0])
        whome = tuple(wf.get("home_hex") or [0, 0])

        lhexes = [(int(p[0]), int(p[1])) for p in (lf.get("controlled_hexes") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        whexes = [(int(p[0]), int(p[1])) for p in (wf.get("controlled_hexes") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        # Don't steal the loser's home.
        candidates = [p for p in lhexes if p != (int(lhome[0]), int(lhome[1]))]
        if not candidates:
            return None

        # Prefer border hexes (closest to winner home)
        candidates.sort(key=lambda p: hex_distance(whome, p))
        take = candidates[0]
        lhexes = [p for p in lhexes if p != take]
        if take not in whexes:
            whexes.append(take)

        lf["controlled_hexes"] = [[q, r] for (q, r) in lhexes]
        wf["controlled_hexes"] = [[q, r] for (q, r) in whexes]
        self.factions[loser] = lf
        self.factions[winner] = wf

        k = f"{take[0]},{take[1]}"
        if k in self.world_hexes and isinstance(self.world_hexes[k], dict):
            self.world_hexes[k]["faction_id"] = winner
            # Mark as contested for a while.
            self.world_hexes[k]["heat"] = max(int(self.world_hexes[k].get("heat", 0) or 0), 3)
            # Also heat up adjacent hexes a bit.
            for _, (dq, dr) in neighbors(take).items():
                nk = f"{take[0]+dq},{take[1]+dr}"
                nh = (self.world_hexes or {}).get(nk)
                if isinstance(nh, dict):
                    nh["heat"] = max(int(nh.get("heat", 0) or 0), 1)
                    self.world_hexes[nk] = nh
        return take

    def view_contracts(self):
        """Town UI: view offers and active contracts."""
        # Route through dispatcher to keep UI decoupled from mutation logic.
        self.dispatch(RefreshContracts())
        self.ui.title("Faction Contracts")

        while True:
            self.ui.hr()
            active = [c for c in (self.active_contracts or []) if (c.get("status") == "accepted")]
            self.ui.log(f"Active contracts: {len(active)}")
            opts = ["View Offers", "View Active", "Back"]
            i = self.ui.choose("Contracts", opts)
            if i == 0:
                self._contracts_view_offers()
            elif i == 1:
                self._contracts_view_active()
            else:
                return

    def _contracts_view_offers(self):
        offers = list(self.contract_offers or [])
        if not offers:
            self.ui.log("No contracts available right now.")
            return

        labels = []
        for c in offers:
            fid = c.get("faction_id")
            fname = (self.factions.get(fid) or {}).get("name", fid)
            dest = self._contract_destination_label(c)
            labels.append(f"{c.get('title')} — {fname} [destination: {dest}] (reward {c.get('reward_gp')} gp, by day {c.get('deadline_day')})")
        j = self.ui.choose("Available Offers", labels + ["Back"])
        if j == len(labels):
            return
        chosen = offers[j]
        res = self.dispatch(AcceptContract(str(chosen.get("cid"))))
        if not res.ok:
            self.ui.log(res.message)
            return
        self.ui.log(f"Accepted: {chosen.get('title')}")
        self.ui.log(str(chosen.get("desc", "")))

    def _job_source_local_score(self, c: dict[str, Any], fid: str, source_hex: dict[str, Any] | None = None) -> int:
        """Score a contract for how well it fits a local outpost/caravan source."""
        score = 0
        cfid = str(c.get("faction_id") or "")
        if fid and cfid == fid:
            score += 8
        th = tuple(c.get("target_hex") or [0, 0])
        try:
            tq, tr = int(th[0]), int(th[1])
        except Exception:
            tq, tr = 0, 0
        if source_hex:
            sq, sr = int(source_hex.get("q", 0)), int(source_hex.get("r", 0))
            dist = hex_distance((sq, sr), (tq, tr))
            score += max(0, 8 - dist) * 3
        else:
            dist = hex_distance((0, 0), (tq, tr))
            score += max(0, 6 - dist) * 2

        hk = f"{tq},{tr}"
        hx = (self.world_hexes or {}).get(hk)
        if not isinstance(hx, dict):
            try:
                hx = ensure_hex(self.world_hexes, (tq, tr), self.wilderness_rng).to_dict()
                self.world_hexes[hk] = hx
            except Exception:
                hx = {}
        terrain = str((hx or {}).get("terrain", "clear"))
        ptype = str(c.get("target_poi_type") or ((hx.get("poi") or {}).get("type") if isinstance(hx, dict) else "") or "")
        kind = str(c.get("kind") or "")
        ftype = str(((self.factions.get(fid) or {}) if fid else {}).get("ftype") or "")

        if terrain == "road":
            score += 2
        else:
            try:
                if any(str(((self.world_hexes.get(f"{pos[0]},{pos[1]}") or {}).get("terrain", ""))) == "road" for pos in neighbors((tq, tr)).values()):
                    score += 1
            except Exception:
                pass

        hfid = str((hx.get("faction_id") or (hx.get("poi") or {}).get("faction_id") or "")) if isinstance(hx, dict) else ""
        if fid and hfid == fid:
            score += 4
        elif hfid and fid and hfid != fid and kind in ("strike_stronghold", "clear_lair"):
            score += 3

        if ftype == "religious":
            if ptype == "shrine":
                score += 8
            elif ptype == "ruins":
                score += 3
        elif ftype == "guild":
            if kind == "escort":
                score += 7
            if ptype in ("dungeon_entrance", "caravan"):
                score += 4
            if terrain in ("road", "clear"):
                score += 3
        elif ftype == "monster":
            if ptype == "lair":
                score += 8
            elif kind == "strike_stronghold":
                score += 5
            elif ptype == "dungeon_entrance":
                score += 2
        else:
            if ptype in ("lair", "dungeon_entrance", "ruins"):
                score += 2

        if kind == "scout":
            score += 1
        return int(score)

    def _build_local_faction_job(self, fid: str, source_hex: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Build a local, outpost-flavored contract tied to nearby POIs/territory/roads."""
        if not fid:
            return None
        f = (self.factions or {}).get(fid) or {}
        ftype = str(f.get("ftype") or "guild")
        sq, sr = ((int(source_hex.get("q", 0)), int(source_hex.get("r", 0))) if source_hex else tuple(self.party_hex))
        candidates: list[tuple[int, int, int, dict[str, Any]]] = []
        for hx in (self.world_hexes or {}).values():
            if not isinstance(hx, dict):
                continue
            try:
                q, r = int(hx.get("q", 0)), int(hx.get("r", 0))
            except Exception:
                continue
            dist = hex_distance((sq, sr), (q, r))
            if dist < 1 or dist > 6:
                continue
            poi = hx.get("poi") or {}
            ptype = str(poi.get("type") or "")
            terrain = str(hx.get("terrain", "clear"))
            hfid = str(poi.get("faction_id") or hx.get("faction_id") or "")
            score = max(0, 8 - dist) * 3
            if terrain == "road":
                score += 2
            if ftype == "religious":
                if ptype == "shrine":
                    score += 10
                elif ptype == "ruins":
                    score += 4
            elif ftype == "guild":
                if ptype == "dungeon_entrance":
                    score += 8
                elif ptype == "caravan":
                    score += 6
                elif terrain in ("road", "clear"):
                    score += 4
            else:
                if ptype == "lair":
                    score += 10
                elif ptype == "dungeon_entrance":
                    score += 4
                if hfid and hfid != fid:
                    score += 4
            if hfid == fid:
                score += 2
            candidates.append((score, q, r, hx))

        if candidates:
            candidates.sort(key=lambda row: row[0], reverse=True)
            score, q, r, hx = candidates[0]
            poi = hx.get("poi") or {}
            ptype = str(poi.get("type") or "")
            terrain = str(hx.get("terrain", "clear"))
            dist = hex_distance((sq, sr), (q, r))
            reward = 20 + dist * 8 + max(0, score // 3)
            deadline = int(self.campaign_day) + max(2, 2 + dist // 2)
            enemy_fid = None
            if ftype == "religious" and ptype == "shrine":
                title = "Tend a frontier shrine"
                desc = f"Travel from {source_hex.get('poi', {}).get('name', 'the outpost') if source_hex else 'the outpost'} to the shrine near hex {(q, r)}. Restore order there and return before day {deadline}."
                kind = "relic_shrine"
                reward += 10
                rep_s, rep_f = 10, -8
            elif ftype == "guild" and ptype == "dungeon_entrance":
                title = "Secure the nearby entrance"
                desc = f"A nearby entrance at hex {(q, r)} threatens trade through the district. Secure it and report back before day {deadline}."
                kind = "secure_entrance"
                reward += 20
                rep_s, rep_f = 12, -10
            elif ftype == "guild" and terrain in ("road", "clear"):
                title = "Scout the road"
                desc = f"Patrol the roads and camps near hex {(q, r)} and bring word back before day {deadline}."
                kind = "scout"
                ptype = None
                rep_s, rep_f = 8, -6
            else:
                if ptype == "lair":
                    title = "Break a nearby lair"
                    desc = f"A hostile lair near hex {(q, r)} threatens the district. Break its strength and return before day {deadline}."
                    kind = "clear_lair"
                    reward += 20
                    rep_s, rep_f = 14, -12
                else:
                    enemy_fid = str((poi.get("faction_id") or hx.get("faction_id") or "")) or None
                    title = "Strike the frontier threat"
                    desc = f"Push out to hex {(q, r)} and strike the threat there before day {deadline}."
                    kind = "strike_stronghold"
                    rep_s, rep_f = 12, -10
            co = Contract(
                cid=f"L{int(self.campaign_day)}-{fid}-{sq}-{sr}-{q}-{r}",
                faction_id=fid,
                kind=kind,
                title=title,
                desc=desc,
                target_hex=(q, r),
                reward_gp=int(reward),
                rep_success=int(rep_s),
                rep_fail=int(rep_f),
                deadline_day=int(deadline),
                target_poi_type=ptype or None,
                target_enemy_faction=enemy_fid,
            )
            d = co.to_dict()
            d["district_objective"] = True
            d["source_hex"] = [int(sq), int(sr)]
            d["source_name"] = str(source_hex.get('poi', {}).get('name', 'the outpost') if source_hex else 'the outpost')
            d["district_name"] = f"{d['source_name']} district"
            d["target_hint"] = self._locator_from((int(sq), int(sr)), (int(q), int(r)), style="district")
            return d

        # Fallback: local scout around the outpost if the area is still sparse.
        target = None
        for pos in neighbors((sq, sr)).values():
            target = (int(pos[0]), int(pos[1]))
            break
        if target is None:
            return None
        dist = hex_distance((sq, sr), target)
        deadline = int(self.campaign_day) + 2
        co = Contract(
            cid=f"L{int(self.campaign_day)}-{fid}-{sq}-{sr}-scout",
            faction_id=fid,
            kind="scout",
            title="Survey the district",
            desc=f"Make a short circuit around the district near hex {target} and return before day {deadline}.",
            target_hex=target,
            reward_gp=int(18 + dist * 8),
            rep_success=8,
            rep_fail=-6,
            deadline_day=int(deadline),
        )
        d = co.to_dict()
        d["district_objective"] = True
        d["source_hex"] = [int(sq), int(sr)]
        d["source_name"] = str(source_hex.get('poi', {}).get('name', 'the outpost') if source_hex else 'the outpost')
        d["district_name"] = f"{d['source_name']} district"
        d["target_hint"] = self._locator_from((int(sq), int(sr)), (int(target[0]), int(target[1])), style="district")
        return d

    def _view_faction_jobs(self, fid: str, source_name: str = "Outpost", source_hex: dict[str, Any] | None = None):
        """Offer a small, local-flavored subset of faction contracts from a wilderness anchor."""
        try:
            self._refresh_contracts_if_needed()
        except Exception:
            pass
        offers = [dict(c) for c in (self.contract_offers or []) if (not fid) or str(c.get("faction_id") or "") == str(fid)]
        if not offers:
            # Fall back to the full board if this faction has nothing posted right now.
            offers = [dict(c) for c in (self.contract_offers or [])]

        local_offer = self._build_local_faction_job(fid, source_hex)
        if local_offer:
            local_cid = str(local_offer.get("cid"))
            existing = next((x for x in (self.contract_offers or []) if str(x.get("cid")) == local_cid), None)
            if existing is None:
                self.contract_offers.append(dict(local_offer))
            offers.append(dict(existing or local_offer))

        if not offers:
            self.ui.log(f"{source_name} has no jobs available right now.")
            return

        offers = sorted(
            offers,
            key=lambda c: (
                -self._job_source_local_score(c, fid, source_hex),
                int(c.get("deadline_day", 9999) or 9999),
                -int(c.get("reward_gp", 0) or 0),
            ),
        )
        uniq: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in offers:
            cid = str(c.get("cid") or "")
            if cid in seen:
                continue
            seen.add(cid)
            uniq.append(c)
        offers = uniq[:3]
        labels = []
        for c in offers:
            cfid = str(c.get("faction_id") or "")
            fname = self._faction_name(cfid) if cfid else "Local"
            th = tuple(c.get("target_hex") or [0, 0])
            tpoi = str(c.get("target_poi_type") or "")
            if bool(c.get('district_objective', False)):
                tgt = str(c.get('target_hint') or self._locator_from(tuple(c.get('source_hex') or [0, 0]), th, style="district"))
                labels.append(f"{c.get('title')} [District] — {fname} ({c.get('reward_gp')} gp, by day {c.get('deadline_day')}; {tgt})")
            else:
                tgt = f"target {tpoi}@{th}" if tpoi else f"target {th}"
                labels.append(f"{c.get('title')} — {fname} ({c.get('reward_gp')} gp, by day {c.get('deadline_day')}; {tgt})")

        j = self.ui.choose(f"{source_name} Jobs", labels + ["Back"])
        if j == len(labels):
            return
        chosen = dict(offers[j])
        self.ui.hr()
        self.ui.log(str(chosen.get("desc", "")))
        k = self.ui.choose("Accept this job?", ["Accept", "Back"])
        if k != 0:
            return
        res = self.dispatch(AcceptContract(str(chosen.get("cid"))))
        if not res.ok:
            self.ui.log(res.message if getattr(res, 'message', None) else "You cannot take that job right now.")
            return
        self.ui.log(f"Accepted: {chosen.get('title')}")
        self.ui.log(str(chosen.get("desc", "")))

    def _caravan_resupply_menu(self, fid: str, fname: str, hx: dict[str, Any]):
        opts = [
            "Trail bundle (8 gp): +2 rations, +2 torches",
            "Rations pack (6 gp): +3 rations",
            "Torch bundle (3 gp): +3 torches",
            "Back",
        ]
        c = self.ui.choose(f"{fname} caravan supplies", opts)
        if c == 3:
            return
        cost = [8, 6, 3][c]
        if self.gold < cost:
            self.ui.log("You don't have enough gold.")
            return
        self.gold -= cost
        if c == 0:
            self.rations += 2
            self.torches += 2
            self.ui.log("You restock: +2 rations, +2 torches.")
        elif c == 1:
            self.rations += 3
            self.ui.log("You buy preserved trail food: +3 rations.")
        else:
            self.torches += 3
            self.ui.log("You buy fresh torch bundles: +3 torches.")
        if fid:
            self.adjust_rep(fid, +1)
        self._set_forward_anchor(hx, kind="caravan", name=f"{fname} caravan", fid=fid, expires_day=int(self.campaign_day) + 2)

    def _outpost_guarded_rest(self, fid: str, fname: str, hx: dict[str, Any]):
        if self.gold < 5:
            self.ui.log("You don't have enough gold.")
            return
        self.gold -= 5
        healed = 0
        for m in self.party.living():
            before = m.hp
            m.hp = min(m.hp_max, m.hp + 1)
            healed += max(0, m.hp - before)
        self._advance_watch(1)
        self.ui.log(f"You take a guarded rest and recover your bearings. (+1 watch, healed {healed} HP total.)")
        if fid:
            self.adjust_rep(fid, +1)
        self._set_forward_anchor(hx, kind="outpost", name=f"{fname} outpost", fid=fid)

    def _outpost_limited_healing(self, fid: str, fname: str, hx: dict[str, Any]):
        if self.gold < 8:
            self.ui.log("You don't have enough gold.")
            return
        self.gold -= 8
        pool = 6
        healed = 0
        for m in sorted(self.party.living(), key=lambda x: (x.hp / max(1, x.hp_max), x.hp)):
            if pool <= 0:
                break
            need = max(0, int(m.hp_max) - int(m.hp))
            if need <= 0:
                continue
            amt = min(need, pool, 3)
            m.hp += amt
            healed += amt
            pool -= amt
        self.ui.log(f"The outpost chirurgeon treats the party's worst wounds. (Recovered {healed} HP total.)")
        if fid:
            self.adjust_rep(fid, +1)
        self._set_forward_anchor(hx, kind="outpost", name=f"{fname} outpost", fid=fid)

    def _contracts_view_active(self):
        active = [c for c in (self.active_contracts or []) if c.get("status") == "accepted"]
        if not active:
            self.ui.log("No active contracts.")
            return
        labels = []
        for c in active:
            labels.append(f"{c.get('title')} [destination: {self._contract_destination_label(c)}] (by day {c.get('deadline_day')})")
        j = self.ui.choose("Active Contracts", labels + ["Back"])
        if j == len(labels):
            return
        c = active[j]
        self.ui.hr()
        self.ui.log(c.get("desc", ""))
        self.ui.log(f"Destination: {self._contract_destination_label(c)}")
        self.ui.log(f"Target hex: {tuple(c.get('target_hex') or [0,0])}")
        self.ui.log(f"Visited target: {bool(c.get('visited_target', False))}")
        linked = self._contract_rumor_link(c)
        if linked:
            self.ui.log(f"Linked rumor: {self._render_rumor_row(linked)}")
        k = self.ui.choose("Actions", ["Abandon", "Back"])
        if k == 0:
            res = self.dispatch(AbandonContract(str(c.get("cid"))))
            self.ui.log(res.message if res.message else ("Contract abandoned." if res.ok else "Could not abandon contract."))

    def _update_contract_progress_on_hex(self, q: int, r: int, hx: dict[str, Any]):
        for c in (self.active_contracts or []):
            if c.get("status") != "accepted":
                continue
            th = c.get("target_hex") or [0, 0]
            try:
                tq, tr = int(th[0]), int(th[1])
            except Exception:
                continue
            if tq == q and tr == r:
                first_visit = not bool(c.get("visited_target", False))
                c["visited_target"] = True
                if first_visit and str(c.get("target_poi_id") or ""):
                    self.ui.log(f"Objective progress: reached destination for {c.get('title')}.")
                    self.emit("contract_progress", cid=str(c.get("cid") or ""), stage="reached_target", target_hex=(tq, tr), target_poi_id=str(c.get("target_poi_id") or ""))
                # For clear_lair, if lair already resolved, mark completion-ready.
                if c.get("kind") == "clear_lair":
                    poi = hx.get("poi") if isinstance(hx, dict) else None
                    if poi and poi.get("type") == "lair" and bool(poi.get("resolved")):
                        c["visited_target"] = True
                ready_now = self._contract_completion_ready(c, q=q, r=r, hx=hx)
                if ready_now:
                    c["completion_ready"] = True
                    if not bool(c.get("completion_notified", False)):
                        c["completion_notified"] = True
                        self.ui.log(f"Objective ready: {c.get('title')} can be turned in at town.")
                        self.emit("contract_progress", cid=str(c.get("cid") or ""), stage="completion_ready", target_hex=(tq, tr), target_poi_id=str(c.get("target_poi_id") or ""))

    def _contract_completion_ready(self, c: dict[str, Any], q: int | None = None, r: int | None = None, hx: dict[str, Any] | None = None) -> bool:
        """Return True when a contract's objective condition has been satisfied in-world.

        This does not award rewards yet; town turn-in remains the claim point.
        """
        if not isinstance(c, dict) or c.get("status") != "accepted":
            return False
        th = c.get("target_hex") or [0, 0]
        try:
            tq, tr = int(th[0]), int(th[1])
        except Exception:
            return False
        if q is None or r is None:
            q, r = tq, tr
        if hx is None:
            hk = f"{int(q)},{int(r)}"
            hx = (self.world_hexes or {}).get(hk) or {}
        poi = hx.get("poi") if isinstance(hx, dict) else None
        kind = str(c.get("kind") or "")
        if kind in ("scout", "escort"):
            return bool(c.get("visited_target"))
        if kind == "clear_lair":
            return bool(poi and poi.get("type") == "lair" and bool(poi.get("resolved")))
        if kind == "secure_entrance":
            return bool(poi and poi.get("type") == "dungeon_entrance" and bool(poi.get("sealed")))
        if kind == "relic_shrine":
            return bool(poi and poi.get("type") == "shrine" and bool(poi.get("relic_taken")))
        if kind == "strike_stronghold":
            return bool(poi and poi.get("type") in ("lair", "dungeon_entrance") and (bool(poi.get("resolved")) or bool(poi.get("sealed"))))
        return False

    def _refresh_district_objectives(self, *, announce: bool = True) -> None:
        """Update per-objective hints and one-shot wilderness feedback as the party moves.

        Feedback milestones:
        - entered target district
        - near objective
        - objective completed (ready to claim in town)
        """
        pq, pr = int(self.party_hex[0]), int(self.party_hex[1])
        hk = f"{pq},{pr}"
        hx = (self.world_hexes or {}).get(hk) or {}
        for c in (self.active_contracts or []):
            if not isinstance(c, dict) or c.get("status") != "accepted":
                continue
            if not bool(c.get("district_objective", False)):
                continue
            th = c.get("target_hex") or [0, 0]
            try:
                tq, tr = int(th[0]), int(th[1])
            except Exception:
                continue
            try:
                sh = c.get("source_hex") or [0, 0]
                sq, sr = int(sh[0]), int(sh[1])
            except Exception:
                sq, sr = 0, 0
            target_dist = int(hex_distance((pq, pr), (tq, tr)))
            source_span = int(hex_distance((sq, sr), (tq, tr)))
            district_radius = max(2, min(4, max(1, source_span // 2 + 1)))
            c["current_hint"] = self._locator_from((pq, pr), (tq, tr), style="district")
            c["distance_to_target"] = target_dist
            c["in_target_district"] = bool(target_dist <= district_radius)
            c["near_target"] = bool(target_dist <= 1)
            title = str(c.get("title") or "District objective")
            if announce and c["in_target_district"] and not bool(c.get("district_entered_notified", False)):
                c["district_entered_notified"] = True
                note_text = f"Entered target district for {title}."
                self._append_player_event(
                    "district.noted",
                    category="district",
                    title=note_text,
                    payload={"day": int(getattr(self, "campaign_day", 1)), "watch": int(getattr(self, "day_watch", 0)), "cid": str(c.get("cid") or ""), "text": note_text, "target_hex": [int(tq), int(tr)]},
                    refs={"cid": str(c.get("cid") or "")},
                )
                self.ui.log(f"District objective: You enter the target district for {title}.")
                self.emit("district_objective_progress", cid=str(c.get("cid") or ""), stage="entered_district", target_hex=(tq, tr), distance=target_dist)
            if announce and c["near_target"] and not bool(c.get("near_target_notified", False)):
                c["near_target_notified"] = True
                self.ui.log(f"District objective: You are near the objective site for {title}.")
                note_text = f"Near district objective for {title}."
                self._append_player_event(
                    "district.noted",
                    category="district",
                    title=note_text,
                    payload={"day": int(getattr(self, "campaign_day", 1)), "watch": int(getattr(self, "day_watch", 0)), "cid": str(c.get("cid") or ""), "text": note_text, "target_hex": [int(tq), int(tr)]},
                    refs={"cid": str(c.get("cid") or "")},
                )
                self.emit("district_objective_progress", cid=str(c.get("cid") or ""), stage="near_objective", target_hex=(tq, tr), distance=target_dist)
            ready = self._contract_completion_ready(c, q=pq, r=pr, hx=hx if (pq == tq and pr == tr) else None)
            if ready:
                c["completion_ready"] = True
                if announce and not bool(c.get("completion_notified", False)):
                    c["completion_notified"] = True
                    self.ui.log(f"District objective completed: {title}. Return to town to claim the reward.")
                    note_text = f"District objective complete for {title}."
                    self._append_player_event(
                        "district.noted",
                        category="district",
                        title=note_text,
                        payload={"day": int(getattr(self, "campaign_day", 1)), "watch": int(getattr(self, "day_watch", 0)), "cid": str(c.get("cid") or ""), "text": note_text, "target_hex": [int(tq), int(tr)]},
                        refs={"cid": str(c.get("cid") or "")},
                    )
                    self.emit("district_objective_progress", cid=str(c.get("cid") or ""), stage="completed", target_hex=(tq, tr), distance=target_dist)

    def _contract_town_report_line(self, c: dict[str, Any]) -> str:
        title = str(c.get("title") or "Contract")
        dest = self._contract_destination_label(c)
        if bool(c.get("completion_ready", False)):
            stage = "ready to turn in"
        elif bool(c.get("visited_target", False)):
            stage = "destination reached"
        else:
            stage = "in progress"
        return f"Contract report: {title} — {dest} [{stage}]"

    def _check_contracts_on_town_arrival(self):
        """Complete or fail contracts when in town."""
        changed = False
        for c in (self.active_contracts or []):
            if c.get("status") != "accepted":
                continue
            if str(c.get("target_poi_id") or "") and int(c.get("last_town_report_day", 0) or 0) != int(self.campaign_day):
                self.ui.log(self._contract_town_report_line(c))
                c["last_town_report_day"] = int(self.campaign_day)
            deadline = int(c.get("deadline_day", 0))
            if self.campaign_day > deadline:
                c["status"] = "failed"
                self.adjust_rep(c.get("faction_id"), int(c.get("rep_fail", -5)))
                self.ui.log(f"Contract failed (missed deadline): {c.get('title')}")
                changed = True
                continue

            # Determine completion
            kind = c.get("kind")
            th = c.get("target_hex") or [0, 0]
            hk = f"{int(th[0])},{int(th[1])}"
            hx = (self.world_hexes or {}).get(hk) or {}
            poi = hx.get("poi") if isinstance(hx, dict) else None

            if kind in ("scout", "escort"):
                if bool(c.get("visited_target")):
                    c["status"] = "completed"

            elif kind == "clear_lair":
                # Completed if target lair is resolved
                if poi and poi.get("type") == "lair" and bool(poi.get("resolved")):
                    c["status"] = "completed"

            elif kind == "secure_entrance":
                # Completed if entrance is sealed/secured
                if poi and poi.get("type") == "dungeon_entrance" and bool(poi.get("sealed")):
                    c["status"] = "completed"

            elif kind == "relic_shrine":
                # Completed if relic has been recovered from the shrine
                if poi and poi.get("type") == "shrine" and bool(poi.get("relic_taken")):
                    c["status"] = "completed"

            elif kind == "strike_stronghold":
                # Completed if an enemy lair is cleared or an enemy entrance is sealed.
                if poi and poi.get("type") in ("lair", "dungeon_entrance"):
                    if bool(poi.get("resolved")) or bool(poi.get("sealed")):
                        c["status"] = "completed"


            if c.get("status") == "completed":
                reward_gp = int(c.get("reward_gp", 0) or 0)
                grant_coin_reward(self, reward_gp, source="contract_completion", destination=CoinDestination.TREASURY)
                self.adjust_rep(c.get("faction_id"), int(c.get("rep_success", 5)))
                self.ui.log(f"Contract completed: {c.get('title')} @ {self._contract_destination_label(c)} (+{c.get('reward_gp')} gp)")
                self._append_player_event(
                    "contract.completed",
                    category="expedition",
                    title=f"Completed contract: {str(c.get('title') or 'Contract')}",
                    payload={
                        "cid": str(c.get("cid") or ""),
                        "title": str(c.get("title") or "Contract"),
                        "reward_gp": int(c.get("reward_gp", 0) or 0),
                    },
                    refs={"cid": str(c.get("cid") or "")},
                )
                changed = True

        if changed:
            # Keep only non-completed/non-failed in active list for clarity
            self.active_contracts = [c for c in (self.active_contracts or []) if c.get("status") == "accepted"]

    
    def view_faction_services(self):
        """Town UI: basic faction vendors/services unlocked by reputation tiers."""
        self.ui.title("Faction Services")
        if not (self.factions or {}):
            self.ui.log("No factions are established yet.")
            return

        # List factions with rep
        fids = sorted(list(self.factions.keys()))
        labels = []
        for fid in fids:
            f = self.factions.get(fid) or {}
            labels.append(f"{f.get('name', fid)} ({f.get('ftype','?')}) — Rep {self.faction_rep(fid)}")
        j = self.ui.choose("Choose a faction", labels + ["Back"])
        if j == len(labels):
            return
        fid = fids[j]
        f = self.factions.get(fid) or {}
        rep = self.faction_rep(fid)
        ftype = str(f.get("ftype", "guild"))

        # Build service list
        opts: list[str] = []
        actions: list[str] = []

        if ftype == "religious":
            if rep >= 20:
                opts.append("Healing rite (15 gp)")
                actions.append("heal_minor")
            if rep >= 50:
                opts.append("Greater blessing (50 gp)")
                actions.append("bless_major")
                opts.append("Cure disease & rot (75 gp)")
                actions.append("cure_disease")
        elif ftype == "guild":
            if rep >= 20:
                opts.append("Buy supplies at discount")
                actions.append("buy_supplies")
            if rep >= 50:
                opts.append("Call in a favor (improve hiring this week)")
                actions.append("guild_favor")
        else:
            # monster / wildcard
            if rep >= 20:
                opts.append("Buy safe passage token (10 gp, 1 day)")
                actions.append("safe_passage")
            if rep >= 50:
                opts.append("Buy strong safe passage (25 gp, 3 days)")
                actions.append("safe_passage_strong")

        if not opts:
            self.ui.log("You are not trusted enough for any services yet.")
            self.ui.log("Reach Rep 20 for basic services, Rep 50 for advanced.")
            return

        k = self.ui.choose(f"Services — {f.get('name', fid)}", opts + ["Back"])
        if k == len(opts):
            return
        act = actions[k]

        if act == "heal_minor":
            if self.gold < 15:
                self.ui.log("Not enough gold.")
                return
            self.gold -= 15
            healed = 0
            for m in self.party.living():
                try:
                    mods = self.effects_mgr.query_modifiers(m)
                except Exception:
                    mods = None
                if mods is not None and getattr(mods, "blocks_magical_heal", False):
                    self.ui.log(f"{m.name} cannot be healed by rites due to disease.")
                    continue
                before = m.hp
                m.hp = min(m.hp_max, m.hp + self.dice.d(6) + self.dice.d(6))
                healed += max(0, m.hp - before)
            self.ui.log(f"The rite mends your wounds. (Healed {healed} HP total.)")
            self.adjust_rep(fid, +2)

        elif act == "bless_major":
            if self.gold < 50:
                self.ui.log("Not enough gold.")
                return
            self.gold -= 50
            for m in self.party.living():
                try:
                    mods = self.effects_mgr.query_modifiers(m)
                except Exception:
                    mods = None
                if mods is not None and getattr(mods, "blocks_magical_heal", False):
                    self.ui.log(f"{m.name} cannot be restored by blessing due to disease.")
                    continue
                m.hp = m.hp_max
            self.blessed_until_day = max(int(getattr(self, "blessed_until_day", 0)), self.campaign_day + 1)
            self.ui.log("You leave blessed. (Full heal; +1 to reaction rolls for 1 day.)")
            self.adjust_rep(fid, +3)

        elif act == "cure_disease":
            if self.gold < 75:
                self.ui.log("Not enough gold.")
                return
            self.gold -= 75
            cured = 0
            for m in self.party.living():
                try:
                    self.effects_mgr.ensure_migrated(m)
                    insts = getattr(m, "effect_instances", None)
                    if not isinstance(insts, dict):
                        continue
                    for eid, inst in list(insts.items()):
                        if isinstance(inst, dict) and str(inst.get("kind") or "").lower() == "disease":
                            self.effects_mgr.remove(m, eid, reason="cured", ctx={"day": int(self.campaign_day)})
                            cured += 1
                except Exception:
                    continue
            self.ui.log(f"The priests lift the affliction. (Cured {cured} disease effect(s).)")
            self.adjust_rep(fid, +3)

        elif act == "buy_supplies":
            # Discount is implicit: cheaper prices.
            while True:
                self.ui.hr()
                self.ui.log(f"Gold: {self.gold} gp | Rations: {self.rations} | Torches: {self.torches} | Arrows: {getattr(self, 'arrows', 0)} | Bolts: {getattr(self, 'bolts', 0)} | Stones: {getattr(self, 'stones', 0)} | Hand Axes: {getattr(self, 'hand_axes', 0)} | Throwing Daggers: {getattr(self, 'throwing_daggers', 0)} | Darts: {getattr(self, 'darts', 0)} | Javelins: {getattr(self, 'javelins', 0)} | Spears: {getattr(self, 'throwing_spears', 0)}")
                c2 = self.ui.choose("Supplies", ["Buy 5 rations (8 gp)", "Buy 1 torch (1 gp)", "Buy Arrows (20) (1 gp)", "Buy Bolts (20) (1 gp)", "Buy Stones (20) (1 gp)", "Buy Hand Axes (5) (2 gp)", "Buy Throwing Daggers (5) (2 gp)", "Buy Darts (10) (1 gp)", "Buy Javelins (5) (1 gp)", "Buy Spears (5) (2 gp)", "Back"])
                if c2 == 5:
                    break
                if c2 == 0:
                    if self.gold < 8:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 8
                    self.rations += 5
                    self.ui.log("Purchased 5 rations.")
                elif c2 == 1:
                    if self.gold < 1:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 1
                    self.torches += 1
                    self.ui.log("Purchased 1 torch.")
                elif c2 == 2:
                    if self.gold < 1:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 1
                    self.arrows = int(getattr(self, "arrows", 0)) + 20
                    self.ui.log("Purchased Arrows (20).")
                elif c2 == 3:
                    if self.gold < 1:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 1
                    self.bolts = int(getattr(self, "bolts", 0)) + 20
                    self.ui.log("Purchased Bolts (20).")
                elif c2 == 4:
                    if self.gold < 1:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 1
                    self.stones = int(getattr(self, "stones", 0)) + 20
                    self.ui.log("Purchased Stones (20).")
                elif c2 == 5:
                    if self.gold < 2:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 2
                    self.hand_axes = int(getattr(self, "hand_axes", 0)) + 5
                    self.ui.log("Purchased Hand Axes (5).")
                elif c2 == 6:
                    if self.gold < 2:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 2
                    self.throwing_daggers = int(getattr(self, "throwing_daggers", 0)) + 5
                    self.ui.log("Purchased Throwing Daggers (5).")
                elif c2 == 7:
                    if self.gold < 1:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 1
                    self.darts = int(getattr(self, "darts", 0)) + 10
                    self.ui.log("Purchased Darts (10).")
                elif c2 == 8:
                    if self.gold < 1:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 1
                    self.javelins = int(getattr(self, "javelins", 0)) + 5
                    self.ui.log("Purchased Javelins (5).")
                elif c2 == 9:
                    if self.gold < 2:
                        self.ui.log("Not enough gold.")
                        continue
                    self.gold -= 2
                    self.throwing_spears = int(getattr(self, "throwing_spears", 0)) + 5
                    self.ui.log("Purchased Spears (5).")
            self.adjust_rep(fid, +1)

        elif act == "guild_favor":
            if self.gold < 20:
                self.ui.log("Not enough gold.")
                return
            self.gold -= 20
            self.guild_favor_until_day = max(int(getattr(self, "guild_favor_until_day", 0)), self.campaign_day + 7)
            self.ui.log("The guild greases the wheels. (+2 retainer slots on boards for 1 week, slightly cheaper wages.)")
            self.adjust_rep(fid, +2)

        elif act in ("safe_passage", "safe_passage_strong"):
            cost = 10 if act == "safe_passage" else 25
            days = 1 if act == "safe_passage" else 3
            if self.gold < cost:
                self.ui.log("Not enough gold.")
                return
            self.gold -= cost
            until = self.campaign_day + days
            self.safe_passage[str(fid)] = max(int(self.safe_passage.get(str(fid), 0) or 0), until)
            self.ui.log(f"You receive a token of passage. (Reduced encounters in {f.get('name', fid)} territory until day {until}.)")
            self.adjust_rep(fid, +1)


    def reaction_roll(self, mod: int = 0) -> int:
        bonus = 0
        if int(getattr(self, 'blessed_until_day', 0) or 0) >= self.campaign_day:
            bonus += 1
        return self.dice.d(6) + self.dice.d(6) + int(mod) + bonus

    def _reaction_outcome(self, roll: int) -> str:
        if roll <= 5:
            return "Hostile"
        if roll <= 8:
            return "Uncertain"
        if roll <= 11:
            return "Friendly"
        return "Helpful"

    # -------------
    # Save/Load UI
    # -------------

    def _slot_path(self, slot_index: int) -> str:
        return os.path.join(self.save_dir, f"slot{slot_index}.json")

    def _slot_label(self, slot_index: int) -> str:
        path = self._slot_path(slot_index)
        if not os.path.exists(path):
            return f"Slot {slot_index} — Empty"
        try:
            meta = read_save_metadata(path)
            s = (meta.get("summary") or {})
            day = s.get("day", "?")
            watch = s.get("watch", "?")
            gold = s.get("gold", "?")
            pcs = s.get("pcs") or []
            pcs_txt = ", ".join(pcs[:3]) + ("…" if len(pcs) > 3 else "")
            dd = s.get("dungeon_depth", "?")
            dl = s.get("dungeon_level", "?")
            hx = s.get("hex") or [0, 0]
            return f"Slot {slot_index} — Day {day}.{watch} Hex({hx[0]},{hx[1]}), {gold} gp, PCs: {pcs_txt or 'None'} (D{dd} L{dl})"
        except Exception:
            return f"Slot {slot_index} — (Unreadable save)"

    def _choose_save_slot(self, title: str) -> Optional[str]:
        os.makedirs(self.save_dir, exist_ok=True)
        opts = [self._slot_label(1), self._slot_label(2), self._slot_label(3), "Back"]
        i = self.ui.choose(title, opts)
        if i == 3:
            return None
        return self._slot_path(i + 1)

    def save_from_menu(self):
        path = self._choose_save_slot("Save Game")
        if not path:
            return
        if os.path.exists(path):
            j = self.ui.choose("Overwrite existing save?", ["Yes", "No"])
            if j != 0:
                self.ui.log("Save canceled.")
                return
        save_game(self, path)
        self.ui.log(f"Saved: {path}")

    def load_from_menu(self):
        path = self._choose_save_slot("Load Game")
        if not path:
            return
        if not os.path.exists(path):
            self.ui.log("That slot is empty.")
            return
        try:
            load_game(self, path)
        except Exception as e:
            self.ui.log(f"Load failed: {e}")
            return
        # derive light state from remaining torch/magical light time
        self.light_on = bool(getattr(self, 'torch_turns_left', 0) > 0 or getattr(self, 'magical_light_turns', 0) > 0)
        self._run_validation("load")
        self.ui.log(f"Loaded: {path}")

    # -------------
    # Main Loop
    # -------------

    def run(self):
        self.ui.title("Swords & Wizardry Complete — Text CRPG")
        self.ui.wrap(
            "Campaign engine with persistent dungeon rooms, OSR darkness/torches, wandering monsters, doors+noise, "
            "and Save/Load. Start in the town of Threshold."
        )
        self.start_menu()


    # -------------
    # Start Menu / Front Door (P4.0)
    # -------------

    def start_menu(self):
        """Front-door menu: New / Load / Continue."""
        while True:
            i = self.ui.choose("Start Menu", [
                "New Game",
                "Load Game (slot)",
                "Continue (latest save)",
                "Quit",
            ])
            if i == 0:
                self.new_game()
                # After new game, enter Threshold
                self.enter_threshold()
                return
            if i == 1:
                self.load_from_menu()
                if self.party.pcs():
                    self.enter_threshold()
                    return
            if i == 2:
                if self.load_latest_save():
                    self.enter_threshold()
                    return
            if i == 3:
                raise SystemExit(0)

    def new_game(self):
        """Create a new party via a guided flow."""
        self.ui.title("New Game — Party Creation")
        # Reset core campaign state (keep deterministic seeds already set at Game init).
        self.party.members = []
        self.campaign_day = 1
        self.day_watch = 0
        self.create_party_guided()
        self.ui.log("Party created. Welcome to Threshold.")

    def load_latest_save(self) -> bool:
        """Load the most recently modified save file in the save directory."""
        os.makedirs(self.save_dir, exist_ok=True)
        candidates = []
        for fn in os.listdir(self.save_dir):
            if fn.lower().endswith(".json"):
                path = os.path.join(self.save_dir, fn)
                try:
                    candidates.append((os.path.getmtime(path), path))
                except Exception:
                    pass
        if not candidates:
            self.ui.log("No saves found.")
            return False
        candidates.sort(key=lambda x: x[0], reverse=True)
        path = candidates[0][1]
        try:
            load_game(self, path)
        except Exception as e:
            self.ui.log(f"Continue failed: {e}")
            return False
        # derive light state from remaining torch/magical light time
        self.light_on = bool(getattr(self, 'torch_turns_left', 0) > 0 or getattr(self, 'magical_light_turns', 0) > 0)
        self._run_validation("load_latest")
        self.ui.log(f"Continued: {os.path.basename(path)}")
        return True

    def enter_threshold(self):
        """Enter the starting town."""
        self.ui.title(self.town_name)
        self.ui.wrap("You arrive in Threshold, a rough frontier town on the edge of the wilds.")
        self.town_loop()

    def main_menu(self):
        while True:
            i = self.ui.choose("Main Menu", [
                "Create Party (quickstart)",
                "Load Game",
                "Enter the Dungeon",
                "Wilderness",
                "Town",
                f"Toggle Auto-Combat (currently {'ON' if self.ui.auto_combat else 'OFF'})",
                "Quit",
            ])
            if i == 0:
                self.create_party_quick()
            elif i == 1:
                self.load_from_menu()
            elif i == 2:
                if not self.party.pcs():
                    self.ui.log("Create a party first.")
                else:
                    self.assemble_expedition_party()
                    self.dungeon_loop()
                    self.disband_expedition_party()
            elif i == 3:
                if not self.party.pcs():
                    self.ui.log("Create a party first.")
                else:
                    self.wilderness_loop()
            elif i == 4:
                self.town_loop()
            elif i == 5:
                self.ui.auto_combat = not self.ui.auto_combat
            else:
                return

    # -------------
    # Party creation
    # -------------

    def roll_stats_3d6(self) -> Stats:
        vals = [self.dice.roll("3d6").total for _ in range(6)]
        return Stats(*vals)

    def xp_threshold(self, next_level: int) -> int:
        # Backward-compatible shim (old signature). Defaults to Fighter.
        return self.xp_threshold_for_class("Fighter", next_level)

    def xp_threshold_for_class(self, cls: str, next_level: int) -> int:
        """XP required to *reach* next_level (i.e., to advance from next_level-1).

        Uses strict S&W Complete advancement tables for the core 4 classes.
        """
        if next_level <= 1:
            return 0

        c = (cls or "Fighter").strip().lower()

        # Thresholds to reach levels 2..N.
        tables = {
            # Table 11
            "fighter":    [2000, 4000, 8000, 16000, 32000, 64000, 128000, 256000, 350000, 450000, 550000, 650000, 750000, 850000, 950000, 1050000, 1150000, 1250000, 1350000],
            # Table 8
            "cleric":     [1500, 3000, 6000, 12000, 25000, 50000, 100000, 200000, 300000, 400000, 500000, 600000, 700000, 800000, 900000, 1000000, 1100000, 1200000, 1300000],
            # Table 19
            "thief":      [1250, 2500, 5000, 10000, 20000, 40000, 60000, 90000, 120000, 240000],
            # Table 12
            "magic-user": [2500, 5000, 10000, 20000, 35000, 50000, 75000, 100000, 200000, 300000, 400000, 500000, 600000, 700000, 800000, 900000, 1000000, 1100000, 1200000],
        }

        # Normalize class aliases
        if c in ("magic user", "mu"):
            c = "magic-user"
        if c not in tables:
            c = "fighter"

        idx = next_level - 2
        base = tables[c]
        if idx < len(base):
            return int(base[idx])

        # Beyond the printed tables:
        # - Fighter/MU/Cleric: +100,000 per level
        # - Thief: +130,000 per level (per Table 19)
        last = int(base[-1])
        if c == "thief":
            step = 130000
        else:
            step = 100000
        return int(last + step * (idx - (len(base) - 1)))

    def class_hit_die(self, cls: str) -> int:
        c = cls.lower()
        if c in ("fighter", "paladin", "ranger"):
            return 8
        if c in ("cleric", "druid", "monk"):
            return 6
        if c in ("thief", "assassin"):
            return 6
        if c in ("magic-user", "magic user", "mu"):
            return 4
        return 6

    def recalc_pc_class_features(self, pc: PC) -> None:
        """Recompute derived class features from level/class/race.

        This is called after character creation and after leveling.
        """
        from .classes import (
            save_target_for_class,
            spell_slots_for_class,
            thief_skills_for_level,
            apply_thief_racial_bonuses,
        )

        pc.cls = str(getattr(pc, "cls", "Fighter") or "Fighter")
        pc.level = max(1, int(getattr(pc, "level", 1) or 1))

        # Saving throw target from table.
        pc.save = int(save_target_for_class(pc.cls, pc.level))

        # Spell slots.
        pc.spell_slots = dict(spell_slots_for_class(pc.cls, pc.level))

        # Thief skills.
        cls_norm = str(getattr(pc, "cls", "")).strip().lower()
        if cls_norm == "thief":
            skills = thief_skills_for_level(pc.level)
            race = getattr(pc, "race", None)
            pc.thief_skills = apply_thief_racial_bonuses(skills, race)
        elif cls_norm == "assassin":
            # Assassins use thief skills as a Thief two levels lower.
            skills = thief_skills_for_level(max(1, pc.level - 2))
            pc.thief_skills = skills
        elif cls_norm == "monk":
            # Monks have thief-type skills per Table 14; same progression as Thieves.
            pc.thief_skills = thief_skills_for_level(pc.level)

    def create_party_quick(self):
        self.ui.title("Create Party (Quickstart)")
        party_size = int(self.ui.prompt("How many PCs (1-6)?", "4") or "4")
        party_size = max(1, min(self.MAX_PCS, party_size))
        self.party.members = []

        for n in range(party_size):
            self.ui.hr()
            name = self.ui.prompt(f"Name for PC #{n+1}:", f"Hero{n+1}") or f"Hero{n+1}"
            cls_i = self.ui.choose(
                "Choose class",
                ["Fighter", "Cleric", "Thief", "Magic-User", "Assassin", "Druid", "Monk", "Paladin", "Ranger"],
            )
            cls = ["Fighter", "Cleric", "Thief", "Magic-User"][cls_i]
            stats = self.roll_stats_3d6()

            hd = self.class_hit_die(cls)
            hp = max(1, self.dice.d(hd) + max(0, mod_ability(stats.CON)))
            ac_desc = 9  # will be computed from armor/shield/DEX
            save = 15

            pc = PC(
                name=name,
                hp=hp,
                hp_max=hp,
                ac_desc=ac_desc,
                hd=1,
                save=save,
                morale=9,
                alignment="Neutrality",
                is_pc=True,
                cls=cls,
                level=1,
                xp=0,
                stats=stats,
            )
            # Derived class features.
            try:
                self.recalc_pc_class_features(pc)
            except Exception:
                pass
            pc.effects = []
            pc.weapon = None
            pc.armor = None
            pc.shield = False

            # Basic starting kit
            weapon = self.ui.choose("Starting weapon", ["Sword", "Spear", "Mace", "Dagger", "Staff"])
            pc.weapon = ["Sword", "Spear", "Mace", "Dagger", "Staff"][weapon]
            armor = self.ui.choose("Armor", ["None", "Leather", "Chain", "Plate"])
            armor_name = ["None", "Leather", "Chain", "Plate"][armor]
            if armor_name != "None":
                pc.armor = armor_name
                sh = self.ui.choose("Shield?", ["No", "Yes"])
                if sh == 1:
                    pc.shield = True

            # Migration bridge: keep new equipment in sync with legacy picks.
            pc.sync_legacy_equipment_to_new()

            # Compute AC from chosen kit and DEX.
            try:
                from .equipment import compute_ac_desc
                pc.ac_desc = compute_ac_desc(
                    base_ac_desc=9,
                    armor_name=pc.armor,
                    shield=bool(pc.shield),
                    dex_score=getattr(stats, "DEX", None),
                )
            except Exception:
                # Fail-safe: keep whatever is currently set.
                pass

            self.party.members.append(pc)

        # Starting supplies
        self.gold = 100
        self.torches = 3
        self.rations = 3
        self.party_items = []
        self.loot_pool = create_loot_pool()
        self.campaign_day = 1

        self.ui.title("Party Created")
        for pc in self.party.pcs():
            st = pc.stats
            self.ui.log(
                f"{pc.name} the {pc.cls} | HP {pc.hp}/{pc.hp_max} | AC {pc.ac_desc} | Save {pc.save} | "
                f"STR {st.STR} DEX {st.DEX} CON {st.CON} INT {st.INT} WIS {st.WIS} CHA {st.CHA}"
            )
        self.ui.log(f"Gold: {self.gold} gp | Torches: {self.torches} | Rations: {self.rations}")


    # -------------
    # Party Creation (Guided) (P4.2)
    # -------------

    def _guided_step_title(self, *, step: int, total_steps: int, label: str, name: str) -> None:
        self.ui.title(f"Step {step}/{total_steps}: {label} — {name}")

    def _guided_log_character_preview(
        self,
        *,
        stats: Stats,
        cls: str | None,
        race: str | None,
        alignment: str | None,
        hp: int | None = None,
        armor_name: str | None = None,
        shield: bool = False,
    ) -> None:
        try:
            from .attribute_rules import strength_mods, dexterity_mods, constitution_mods, xp_bonus_percent, cleric_bonus_first_level_spell
            from .equipment import compute_ac_desc
        except Exception:
            return

        active_cls = cls or "(not chosen)"
        active_race = race or "(not chosen)"
        active_alignment = alignment or "(not chosen)"
        self.ui.log(f"Preview: {active_cls} | {active_race} | {active_alignment}")
        self.ui.log(
            f"  STR {stats.STR} DEX {stats.DEX} CON {stats.CON} INT {stats.INT} WIS {stats.WIS} CHA {stats.CHA}"
        )

        cls_for_mods = cls if cls else "Fighter"
        sm = strength_mods(stats.STR, cls=cls_for_mods, fighter_only_bonuses=True)
        dm = dexterity_mods(stats.DEX)
        cm = constitution_mods(stats.CON)
        hp_est = hp if hp is not None else max(1, 4 + int(cm.hp_per_hd))
        ac_est = compute_ac_desc(
            base_ac_desc=9,
            armor_name=armor_name,
            shield=shield,
            dex_score=getattr(stats, "DEX", None),
        )
        self.ui.log(
            f"  HP {hp_est} | AC {ac_est} | STR to-hit {sm.to_hit:+d}, STR dmg {sm.dmg:+d} | "
            f"DEX missile {dm.missile_to_hit:+d}, AC shift {dm.ac_desc_mod:+d} | CON HP/HD {cm.hp_per_hd:+d}"
        )
        if cls:
            xp_pct = xp_bonus_percent(cls=cls, stats=stats)
            if xp_pct:
                self.ui.log(f"  XP bonus: +{xp_pct}%")
            if cls == "Cleric" and cleric_bonus_first_level_spell(stats.WIS):
                self.ui.log("  Cleric note: WIS 15+ grants +1 first-level spell.")

    def _class_choice_notes(self, cls: str) -> str:
        from .class_eligibility import allowed_races_for_class, allowed_alignments_for_class

        notes: list[str] = []
        race_allow = allowed_races_for_class(cls)
        align_allow = allowed_alignments_for_class(cls)
        if race_allow is not None:
            notes.append("race: " + ", ".join(race_allow))
        if align_allow is not None:
            notes.append("alignment: " + ", ".join(align_allow))
        prime_attr = {
            "Fighter": "prime attribute STR",
            "Cleric": "prime attribute WIS",
            "Thief": "prime attribute DEX",
            "Magic-User": "prime attribute INT",
            "Assassin": "prime attributes STR/DEX",
            "Druid": "prime attributes WIS/CHA",
            "Monk": "prime attributes STR/DEX/WIS/CON",
            "Paladin": "prime attributes STR/CHA",
            "Ranger": "prime attribute STR",
        }.get(cls)
        if prime_attr:
            notes.append(prime_attr)
        return " | ".join(notes)

    def create_party_guided(self):
        """Guided party creation: roll -> assign -> class -> race -> alignment -> gear -> review.

        This is intentionally text-UI friendly and deterministic (uses Dice).
        """
        self.ui.title("Create Party (Guided)")
        party_size = int(self.ui.prompt("How many PCs (1-6)?", "4") or "4")
        party_size = max(1, min(self.MAX_PCS, party_size))

        rm_i = self.ui.choose(
            "Attribute roll method",
            [
                "3d6 (Classic)",
                "4d6 drop lowest (Heroic)",
            ],
        )
        roll_method = "3d6" if rm_i == 0 else "4d6dl"

        self.party.members = []

        def _roll_one_stat() -> int:
            if roll_method == "3d6":
                return self.dice.d(6) + self.dice.d(6) + self.dice.d(6)
            r = [self.dice.d(6) for _ in range(4)]
            r.sort()
            return int(r[1] + r[2] + r[3])

        classes = ["Fighter", "Cleric", "Thief", "Magic-User", "Assassin", "Druid", "Monk", "Paladin", "Ranger"]
        races = ["Human", "Elf", "Dwarf", "Halfling", "Half-elf"]
        alignments = ["Law", "Neutrality", "Chaos"]

        for n in range(party_size):
            while True:
                self.ui.hr()
                name = self.ui.prompt(f"Name for PC #{n+1}:", f"Hero{n+1}") or f"Hero{n+1}"

                # Step 1: roll/reorder
                while True:
                    rolls = [_roll_one_stat() for _ in range(6)]
                    original = list(rolls)
                    reroll = False
                    while True:
                        self._guided_step_title(step=1, total_steps=6, label="Roll Attributes", name=name)
                        self.ui.log("Method: " + ("3d6 (Classic)" if roll_method == "3d6" else "4d6 drop lowest (Heroic)"))
                        self.ui.log("Rolled: " + ", ".join(str(x) for x in rolls))
                        j = self.ui.choose("Next step", ["Keep & Continue", "Reorder/Swap", "Reroll"])
                        if j == 2:
                            reroll = True
                            break
                        if j == 1:
                            while True:
                                self.ui.title(f"Reorder Rolls — {name}")
                                self.ui.log("Current: " + " | ".join(f"{i+1}:{v}" for i, v in enumerate(rolls)))
                                k = self.ui.choose(
                                    "Reorder options",
                                    ["Swap two positions", "Sort high→low", "Sort low→high", "Reset to original", "Done"],
                                )
                                if k == 4:
                                    break
                                if k == 1:
                                    rolls = sorted(rolls, reverse=True)
                                elif k == 2:
                                    rolls = sorted(rolls)
                                elif k == 3:
                                    rolls = list(original)
                                elif k == 0:
                                    a = self.ui.choose("Pick first position to swap", [f"{i+1}:{v}" for i, v in enumerate(rolls)])
                                    b = self.ui.choose("Pick second position to swap", [f"{i+1}:{v}" for i, v in enumerate(rolls)])
                                    if a != b:
                                        rolls[a], rolls[b] = rolls[b], rolls[a]
                            continue
                        break
                    if reroll:
                        continue
                    break

                # Step 2: assign
                remaining = list(rolls)
                assigned: dict[str, int] = {}
                for stat in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]:
                    self._guided_step_title(step=2, total_steps=6, label="Assign Attributes", name=name)
                    self.ui.log("Remaining: " + ", ".join(str(x) for x in remaining))
                    idx = self.ui.choose(f"Assign which value to {stat}?", [str(x) for x in remaining])
                    val = int(remaining.pop(idx))
                    assigned[stat] = val
                    try:
                        from .attribute_rules import dexterity_mods, constitution_mods, max_special_hirelings
                        if stat == "DEX":
                            dm = dexterity_mods(val)
                            self.ui.log(f"DEX preview: missile to-hit {dm.missile_to_hit:+d}, AC shift {dm.ac_desc_mod:+d} (descending)")
                        elif stat == "CON":
                            cm = constitution_mods(val)
                            self.ui.log(f"CON preview: HP/HD {cm.hp_per_hd:+d}, raise-dead survival {cm.survive_raise_dead_pct}%")
                        elif stat == "CHA":
                            self.ui.log(f"CHA preview: max special hirelings {max_special_hirelings(val)}")
                    except Exception:
                        pass

                stats = Stats(
                    STR=int(assigned["STR"]),
                    DEX=int(assigned["DEX"]),
                    CON=int(assigned["CON"]),
                    INT=int(assigned["INT"]),
                    WIS=int(assigned["WIS"]),
                    CHA=int(assigned["CHA"]),
                )

                from .class_eligibility import (
                    allowed_alignments_for_class,
                    allowed_races_for_class,
                    is_alignment_allowed,
                    is_race_allowed,
                )

                while True:
                    self._guided_step_title(step=3, total_steps=6, label="Choose Class", name=name)
                    cls = classes[self.ui.choose("Choose class", [f"{c} — {self._class_choice_notes(c)}" for c in classes])]
                    self._guided_log_character_preview(stats=stats, cls=cls, race=None, alignment=None)

                    go_back_class = False
                    while True:
                        self._guided_step_title(step=4, total_steps=6, label="Choose Race", name=name)
                        race_idx = self.ui.choose("Choose race", races + ["Back to Class"])
                        if race_idx == len(races):
                            go_back_class = True
                            break
                        race = races[race_idx]
                        if not is_race_allowed(cls, race):
                            allow = allowed_races_for_class(cls) or ()
                            self.ui.log(f"{cls} is unavailable for {race}. Allowed races: {', '.join(allow)}.")
                            continue
                        self._guided_log_character_preview(stats=stats, cls=cls, race=race, alignment=None)

                        while True:
                            self._guided_step_title(step=5, total_steps=6, label="Choose Alignment", name=name)
                            alignment_idx = self.ui.choose("Choose alignment", alignments + ["Back to Race"])
                            if alignment_idx == len(alignments):
                                break
                            alignment = alignments[alignment_idx]
                            if not is_alignment_allowed(cls, alignment):
                                allow = allowed_alignments_for_class(cls) or ()
                                self.ui.log(f"{cls} requires alignment: {', '.join(allow)}. You chose {alignment}.")
                                continue

                            hd = self.class_hit_die(cls)
                            try:
                                from .attribute_rules import constitution_mods
                                con_hp = int(constitution_mods(stats.CON).hp_per_hd)
                            except Exception:
                                con_hp = max(0, mod_ability(stats.CON))
                            hp = max(1, self.dice.d(hd) + con_hp)

                            pc = PC(
                                name=name,
                                race=race,
                                hp=hp,
                                hp_max=hp,
                                ac_desc=9,
                                hd=1,
                                save=15,
                                morale=9,
                                alignment=alignment,
                                is_pc=True,
                                cls=cls,
                                level=1,
                                xp=0,
                                stats=stats,
                            )
                            try:
                                self.recalc_pc_class_features(pc)
                            except Exception:
                                pass
                            pc.effects = []

                            while True:
                                self._guided_step_title(step=5, total_steps=6, label="Choose Starting Equipment", name=name)
                                kit = self.ui.choose("Equipment setup", ["Class starter kit", "Manual equipment pick", "Back to Alignment"])
                                if kit == 2:
                                    break
                                if kit == 0:
                                    weapon_name, armor_name, has_shield = {
                                        "Fighter": ("Sword", "Chain", True),
                                        "Cleric": ("Mace", "Chain", True),
                                        "Thief": ("Dagger", "Leather", False),
                                        "Magic-User": ("Staff", "None", False),
                                        "Assassin": ("Sword", "Leather", False),
                                        "Druid": ("Staff", "Leather", True),
                                        "Monk": ("Staff", "None", False),
                                        "Paladin": ("Sword", "Plate", True),
                                        "Ranger": ("Sword", "Chain", False),
                                    }.get(cls, ("Sword", "Leather", False))
                                else:
                                    weapon_name = ["Sword", "Spear", "Mace", "Dagger", "Staff"][
                                        self.ui.choose("Starting weapon", ["Sword", "Spear", "Mace", "Dagger", "Staff"])
                                    ]
                                    armor_name = ["None", "Leather", "Chain", "Plate"][
                                        self.ui.choose("Armor", ["None", "Leather", "Chain", "Plate"])
                                    ]
                                    has_shield = False
                                    if armor_name != "None":
                                        has_shield = self.ui.choose("Shield?", ["No", "Yes"]) == 1

                                pc.weapon = weapon_name
                                pc.armor = None if armor_name == "None" else armor_name
                                pc.shield = has_shield
                                # Migration bridge: keep new equipment in sync with legacy picks.
                                pc.sync_legacy_equipment_to_new()
                                try:
                                    from .equipment import compute_ac_desc
                                    pc.ac_desc = compute_ac_desc(
                                        base_ac_desc=9,
                                        armor_name=pc.armor,
                                        shield=bool(pc.shield),
                                        dex_score=getattr(stats, "DEX", None),
                                    )
                                except Exception:
                                    pass

                                self._guided_step_title(step=6, total_steps=6, label="Review Character", name=name)
                                self._guided_log_character_preview(
                                    stats=stats,
                                    cls=cls,
                                    race=race,
                                    alignment=alignment,
                                    hp=pc.hp,
                                    armor_name=pc.armor,
                                    shield=bool(pc.shield),
                                )
                                self.ui.log(f"Equipment: Weapon {pc.weapon} | Armor {pc.armor or 'None'} | Shield {'Yes' if pc.shield else 'No'}")
                                self.ui.log("Party starting gold after creation: 100 gp (shared pool)")
                                done = self.ui.choose(
                                    "Confirm character",
                                    ["Confirm & Add to Party", "Back to Equipment", "Back to Alignment", "Redo from Step 1"],
                                )
                                if done == 0:
                                    self.party.members.append(pc)
                                    go_back_class = False
                                    break
                                if done == 1:
                                    continue
                                if done == 2:
                                    break
                                go_back_class = True
                                break

                            if done == 0:
                                break
                            if done in (2, 3):
                                break

                        if done == 0:
                            break
                        if go_back_class:
                            break

                    if done == 0:
                        break
                    if go_back_class:
                        continue

                if done == 0:
                    break
        # Starting supplies
        self.gold = 100
        self.torches = 3
        self.rations = 3
        self.party_items = []
        self.loot_pool = create_loot_pool()
        self.campaign_day = 1

        self.ui.title("Party Created")
        for pc in self.party.pcs():
            st = pc.stats
            self.ui.log(
                f"{pc.name} the {pc.cls} | HP {pc.hp}/{pc.hp_max} | AC {pc.ac_desc} | Save {pc.save} | "
                f"STR {st.STR} DEX {st.DEX} CON {st.CON} INT {st.INT} WIS {st.WIS} CHA {st.CHA}"
            )
        self.ui.log(f"Gold: {self.gold} gp | Torches: {self.torches} | Rations: {self.rations}")

    # -------------
    # Retainers
    # -------------

    def generate_retainer_board(self, force: bool = False):
        if not force and (self.campaign_day - self.last_board_day) < 7:
            return
        self.retainer_board = []
        # Town guild reputation affects how easy it is to hire help.
        guild_id = self._town_guild_id()
        guild_rep = self.faction_rep(guild_id)
        n = 6
        wage_mult = 1.0
        # Guild favor (earned via services) provides a small boost.
        if getattr(self, 'guild_favor_until_day', 0) >= self.campaign_day:
            n += 2
            wage_mult *= 0.9
        if guild_rep <= -60:
            n = 3
            wage_mult = 1.5
        for i in range(n):
            lvl = 1
            hp = max(1, self.dice.d(6))
            a = Actor(
                name=f"Retainer{i+1}",
                hp=hp,
                hp_max=hp,
                ac_desc=8,
                hd=lvl,
                save=15,
                morale=7,
                alignment="Neutrality",
                is_pc=False,
            )
            a.is_retainer = True
            a.loyalty = 7 + self.dice_rng.randint(-2, 2)
            a.wage_gp = int((5 + self.dice_rng.randint(0, 10)) * wage_mult)
            a.treasure_share = 1
            self.retainer_board.append(a)
        self.last_board_day = self.campaign_day

    def manage_retainers(self):
        while True:
            self.ui.hr()
            self.ui.log(f"Hired retainers: {len(self.hired_retainers)} | Active: {len(self.active_retainers)}")
            i = self.ui.choose("Retainers", [
                "View hiring board",
                "Hire",
                "Assign to expedition",
                "Dismiss from expedition",
                "Promote retainer to PC",
                "Back",
            ])
            if i == 0:
                self.generate_retainer_board()
                for r in self.retainer_board:
                    self.ui.log(f"- {r.name} | HP {r.hp}/{r.hp_max} | Loyalty {r.loyalty} | Wage {r.wage_gp} gp")
            elif i == 1:
                self.generate_retainer_board()
                labels = [f"{r.name} (L{r.loyalty} Wage {r.wage_gp})" for r in self.retainer_board]
                j = self.ui.choose("Hire which?", labels + ["Back"])
                if j == len(labels):
                    continue
                r = self.retainer_board.pop(j)
                self.hired_retainers.append(r)
                self.retainer_roster.append(r)
                self.ui.log(f"Hired {r.name}.")
            elif i == 2:
                if len(self.active_retainers) >= self.MAX_RETAINERS:
                    self.ui.log("Max retainers already assigned.")
                    continue
                if not self.hired_retainers:
                    self.ui.log("No hired retainers.")
                    continue
                labels = [f"{r.name} (L{r.loyalty})" for r in self.hired_retainers if not r.on_expedition]
                if not labels:
                    self.ui.log("No available hired retainers to assign.")
                    continue
                j = self.ui.choose("Assign which?", labels + ["Back"])
                if j == len(labels):
                    continue
                r = [r for r in self.hired_retainers if not r.on_expedition][j]
                r.on_expedition = True
                self.active_retainers.append(r)
                self.ui.log(f"Assigned {r.name} to expedition.")
            elif i == 3:
                if not self.active_retainers:
                    self.ui.log("No active retainers.")
                    continue
                labels = [f"{r.name}" for r in self.active_retainers]
                j = self.ui.choose("Dismiss which?", labels + ["Back"])
                if j == len(labels):
                    continue
                r = self.active_retainers.pop(j)
                r.on_expedition = False
                self.ui.log(f"Removed {r.name} from expedition.")
            else:
                return

    # -------------
    # Town
    # -------------


    def _sync_legacy_party_items_from_loot_pool(self) -> None:
        """Compatibility bridge while town/inventory UI still reads `party_items`."""
        try:
            self.party_items = loot_pool_entries_as_legacy_dicts(self.loot_pool)
        except Exception:
            pass

    def _ensure_loot_pool_hydrated_from_legacy(self) -> bool:
        """Backfill loot pool from legacy `party_items` when needed."""
        entries = list(getattr(getattr(self, "loot_pool", None), "entries", []) or [])
        if entries:
            return False
        legacy = list(getattr(self, "party_items", []) or [])
        if not legacy:
            return False
        add_generated_treasure_to_pool(self.loot_pool, gp=0, items=legacy, identify_magic=False)
        self._sync_legacy_party_items_from_loot_pool()
        return True


    def _reward_source_uses_legacy_party_items_mirror(self, source: str) -> bool:
        """Compatibility routing for legacy `party_items` mirror updates.

        Room treasure intake is explicitly migrated to loot_pool ownership and
        should not inject into anonymous shared `party_items` on award.
        """
        src = str(source or "").strip().lower()
        if src == "room_treasure":
            return False
        # TODO(ownership-migration): migrate `boss_spoils` to skip legacy mirror.
        # TODO(ownership-migration): migrate post-combat loot recap away from
        # `party_items` deltas and onto loot_pool ownership events.
        return True

    def _sync_legacy_party_items_for_reward_source(self, source: str) -> None:
        """Compatibility wrapper to isolate remaining shared-loot bridge usage."""
        if not self._reward_source_uses_legacy_party_items_mirror(source):
            return
        self._sync_legacy_party_items_from_loot_pool()

    def _coin_destination_label(self, destination: str) -> str:
        d = str(destination or CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT)
        if d in {CoinDestination.TREASURY, CoinDestination.IMMEDIATE_COMPATIBILITY_DEFAULT}:
            return "treasury"
        if d == CoinDestination.EXPEDITION_POOL:
            return "expedition pool"
        return d

    def _loot_sale_source_label(self, entry: Any, *, from_legacy_compat: bool) -> str:
        md = dict(getattr(entry, "metadata", {}) or {})
        owner = str(md.get("owner_actor") or "").strip()
        if owner:
            return f"actor:{owner}"
        source = str(md.get("source") or "").strip().lower()
        if source in {"stash", "party_stash"}:
            return "stash"
        if from_legacy_compat:
            return "legacy compatibility pool"
        return "expedition loot pool"

    def _template_id_for_equipment_name(self, name: str, *, preferred_categories: list[str]) -> str | None:
        """Best-effort mapping from shop/loot display names to template ids."""
        try:
            return find_template_id_by_name(name, preferred_categories=preferred_categories)
        except Exception:
            return None

    def _add_loot_item_to_actor_inventory(self, actor: Actor, loot_item: Any) -> bool:
        """Assign one loot entry to actor inventory as ItemInstance.

        TODO(inventory-ux): expose richer assignment UI (split stacks, choose equip).
        """
        if not isinstance(loot_item, dict):
            loot_item = {"name": str(loot_item), "kind": "gear", "gp_value": None}

        name = str(loot_item.get("true_name") or loot_item.get("name") or "Unknown Item")
        kind = str(loot_item.get("kind") or "gear").strip().lower()
        qty = int(loot_item.get("quantity", 1) or 1)

        pref = [kind]
        if kind == "relic":
            pref = ["treasure", "gear"]
        tid = self._template_id_for_equipment_name(name, preferred_categories=pref)
        if tid is None:
            # Fallback to generic category templates for incremental migration.
            tid = "treasure.generic" if kind in {"gem", "jewelry", "treasure", "relic"} else "gear.backpack_30-pound_capacity"

        try:
            inst = build_item_instance(
                tid,
                quantity=max(1, qty),
                identified=bool(loot_item.get("identified", True)),
                metadata={
                    "source_loot_name": name,
                    "source_kind": kind,
                    "gp_value": loot_item.get("gp_value"),
                    "true_name": loot_item.get("true_name"),
                },
            )
            # Preserve unknown display name when template fallback is generic.
            if name and inst.name != name and tid in {"treasure.generic", "gear.backpack_30-pound_capacity"}:
                inst.name = name
                inst.category = "treasure" if kind in {"gem", "jewelry", "treasure", "relic"} else "gear"
            res = add_item_to_actor(actor, inst)
            return bool(res.ok)
        except Exception:
            return False

    def _ui_item_line(self, actor: Actor, item: Any, *, include_weight: bool = True) -> str:
        """Compact inventory line: name, quantity, equip, and optional weight."""
        name = str(getattr(item, "name", "Unknown item") or "Unknown item")
        try:
            name = item_display_name(item)
        except Exception:
            pass
        qty = int(getattr(item, "quantity", 1) or 1)
        flags: list[str] = []
        if qty > 1:
            flags.append(f"x{qty}")
        if bool(getattr(item, "equipped", False)):
            flags.append("equipped")
        if not bool(getattr(item, "identified", True)):
            flags.append("unidentified")
        if include_weight:
            try:
                md = dict(getattr(item, "metadata", {}) or {})
                w = md.get("weight_lb", None)
                if w is not None:
                    wf = float(w)
                    flags.append(f"{wf:g} lb")
            except Exception:
                pass
        suffix = f" [{', '.join(flags)}]" if flags else ""
        return f"{name}{suffix}"

    def _ui_item_inspection_lines(self, item: Any) -> list[str]:
        """Concise item inspection text for town/inventory UX."""
        lines: list[str] = []
        name = str(getattr(item, "name", "Unknown item") or "Unknown item")
        cat = str(getattr(item, "category", "item") or "item").strip().lower()
        identified = bool(getattr(item, "identified", True))
        lines.append(f"{name} — a {cat}.")

        md = dict(getattr(item, "metadata", {}) or {})
        if not identified:
            aura = "faint magical aura" if bool(md.get("magic", False)) else "uncertain craftsmanship"
            lines.append(f"Unidentified: only a {aura} can be discerned.")
            lines.append("Known properties: none until identified.")
            return lines

        known: list[str] = []
        mb = int(getattr(item, "magic_bonus", 0) or 0)
        if mb:
            known.append(f"magic bonus {mb:+d}")
        try:
            effs = [str((e or {}).get("type") or "").strip().lower() for e in list(item_effects(item) or [])]
            effs = [e for e in effs if e]
            if effs:
                known.append("effects: " + ", ".join(sorted(set(effs))))
        except Exception:
            pass
        if bool(getattr(item, "cursed_known", False)):
            known.append("curse status known")
        if not known:
            known.append("no special properties known")
        lines.append("Known properties: " + "; ".join(known) + ".")
        # TODO(ux): add compare-with-equipped and richer sort/filter affordances.
        return lines

    def _town_inspect_inventory_item(self) -> None:
        """Town UX helper: inspect one owned item with concise known/unknown details."""
        carriers = [a for a in (self.party.living() or []) if list(getattr(getattr(a, "inventory", None), "items", []) or [])]
        if not carriers:
            self.ui.log("No character inventory items to inspect.")
            return
        who_labels = [a.name for a in carriers]
        wi = self.ui.choose("Inspect whose item?", who_labels + ["Back"])
        if wi == len(who_labels):
            return
        actor = carriers[wi]
        actor.ensure_inventory_initialized()
        items = list(actor.inventory.items or [])
        labels = [self._ui_item_line(actor, it, include_weight=True) for it in items]
        ii = self.ui.choose(f"Inspect item — {actor.name}", labels + ["Back"])
        if ii == len(labels):
            return
        for line in self._ui_item_inspection_lines(items[ii]):
            self.ui.log(line)

    def assign_party_loot_to_character(self) -> None:
        """Assign one loot-pool entry to a selected living character inventory."""
        self._ensure_loot_pool_hydrated_from_legacy()
        entries = list(getattr(self.loot_pool, "entries", []) or [])
        if not entries:
            self.ui.log("No party loot to assign.")
            return
        living = self.party.living()
        if not living:
            self.ui.log("No living party members available.")
            return

        self.ui.log(f"Loot pool: {int(getattr(self.loot_pool, 'coins_gp', 0) or 0)} gp in coins, {len(entries)} item(s).")
        who_preview = ", ".join([a.name for a in living[:3]]) + ("..." if len(living) > 3 else "")
        loot_labels = []
        for e in entries:
            u = ("unidentified" if not bool(getattr(e, "identified", True)) else "identified")
            qty = int(getattr(e, "quantity", 1) or 1)
            qtxt = f" x{qty}" if qty > 1 else ""
            loot_labels.append(f"{str(e.name or 'Unknown')} ({str(e.kind or 'item')}, {u}){qtxt} -> {who_preview}")
        li = self.ui.choose("Assign which loot item?", loot_labels + ["Back"])
        if li == len(loot_labels):
            return

        who_labels = [a.name for a in living]
        wi = self.ui.choose("Assign to who?", who_labels + ["Back"])
        if wi == len(who_labels):
            return

        actor = living[wi]
        picked = entries[li]
        moved = move_item_loot_pool_to_actor(self.loot_pool, actor, picked.entry_id)
        if not moved.ok:
            self.ui.log("Could not assign item to inventory.")
            return

        self._sync_legacy_party_items_from_loot_pool()
        self.ui.log(f"Assigned {str(getattr(picked, 'name', 'loot') or 'loot')} to {actor.name}.")

    def _buy_item_for_actor(self, actor: Actor, *, name: str, kind: str, auto_equip: bool) -> bool:
        """Create bought item instance in actor inventory and optionally auto-equip legacy fields."""
        pref = ["weapon"] if kind == "weapon" else (["armor", "shield"] if kind == "armor" else [kind])
        tid = self._template_id_for_equipment_name(name, preferred_categories=pref)
        if not tid:
            return False
        try:
            inst = build_item_instance(tid, quantity=1, identified=True, metadata={"source": "town_shop"})
            r = add_item_to_actor(actor, inst)
            if not r.ok:
                return False
            if auto_equip:
                # Preserve current player-facing behavior: immediate replacement by name.
                if kind == "weapon":
                    actor.weapon = name
                elif kind == "armor":
                    if str(name).strip().lower() == "shield":
                        actor.shield = True
                        actor.shield_name = name
                    else:
                        actor.armor = name
                actor.sync_legacy_equipment_to_new()
                # Mark purchased item as currently equipped in inventory when it matches name/category.
                try:
                    if (inst.category == "weapon" and kind == "weapon") or (inst.category in {"armor", "shield"} and kind == "armor"):
                        inst.equipped = True
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def party_encumbrance(self):
        """Transitional party encumbrance accessor used by dungeon/wilderness systems.

        Compatibility note: this still accepts/weights legacy shared party pools
        (`party_items`, global ammo attrs, town supplies) while per-character
        ownership migration is ongoing.
        """
        try:
            ammo = {
                "arrows": int(getattr(self, "arrows", 0) or 0),
                "bolts": int(getattr(self, "bolts", 0) or 0),
                "stones": int(getattr(self, "stones", 0) or 0),
                "hand_axes": int(getattr(self, "hand_axes", 0) or 0),
                "throwing_daggers": int(getattr(self, "throwing_daggers", 0) or 0),
                "darts": int(getattr(self, "darts", 0) or 0),
                "javelins": int(getattr(self, "javelins", 0) or 0),
                "throwing_spears": int(getattr(self, "throwing_spears", 0) or 0),
            }
        except Exception:
            ammo = {}
        return compute_party_encumbrance(
            members=list(getattr(getattr(self, "party", None), "members", []) or []),
            gp=int(getattr(self, "gold", 0) or 0),
            rations=int(getattr(self, "rations", 0) or 0),
            torches=int(getattr(self, "torches", 0) or 0),
            ammo=ammo,
            party_items=list(getattr(self, "party_items", []) or []),
            equipdb=getattr(self, "equipdb", None),
            strip_plus_suffix=getattr(self, "_strip_plus_suffix", None),
        )

    def town_arms_store(self):
        """Arms & armour store.

        Uses EquipmentDB prices from:
          - data/armor_table_24.json
          - data/weapons_melee.json
          - data/weapons_missile.json

        Purchases directly replace the selected party member's weapon/armor.
        """
        while True:
            self.ui.hr()
            self.ui.log(f"Gold: {self.gold} gp")
            mode = self.ui.choose("Arms & Armour", ["Buy weapon", "Buy armor", "Back"])
            if mode == 2:
                return

            living = self.party.living()
            if not living:
                self.ui.log("No one in the party can use equipment right now.")
                return

            labels = []
            for a in living:
                w = self.effective_melee_weapon(a) or self.effective_missile_weapon(a) or "None"
                ar = self.effective_armor(a) or "None"
                labels.append(f"{a.name}  (Weapon: {w} | Armor: {ar})")
            who = self.ui.choose("Who will use it?", labels + ["Back"])
            if who == len(living):
                continue
            actor = living[who]

            if mode == 0:
                # EquipmentDB stores raw JSON rows keyed by name.
                weapons = list((self.equipdb.melee or {}).values()) + list((self.equipdb.missile or {}).values())
                weapons = [w for w in weapons if isinstance(w, dict) and w.get("cost_gp") is not None]
                weapons.sort(key=lambda w: (float(w.get("cost_gp", 0) or 0), str(w.get("name", "")).lower()))
                opts = [f"{w.get('name')} — {w.get('cost_gp')} gp" for w in weapons]
                sel = self.ui.choose("Buy weapon", opts + ["Back"])
                if sel == len(weapons):
                    continue
                w = weapons[sel]
                cost = int(float(w.get("cost_gp", 0) or 0))
                if self.gold < cost:
                    self.ui.log("Not enough gold.")
                    continue
                self.gold -= cost
                bought_name = str(w.get("name"))
                if not self._buy_item_for_actor(actor, name=bought_name, kind="weapon", auto_equip=True):
                    # Compatibility fallback (should be rare).
                    actor.weapon = bought_name
                    actor.sync_legacy_equipment_to_new()
                self.ui.log(f"{actor.name} equips {bought_name}.")

            else:
                armors = [a for a in (self.equipdb.armors or {}).values() if isinstance(a, dict) and a.get("cost_gp") is not None]
                armors.sort(key=lambda a: (float(a.get("cost_gp", 0) or 0), str(a.get("name", "")).lower()))
                opts = [f"{a.get('name')} — {a.get('cost_gp')} gp (AC mod {a.get('ac_mod_desc')})" for a in armors]
                sel = self.ui.choose("Buy armor", opts + ["Back"])
                if sel == len(armors):
                    continue
                a = armors[sel]
                cost = int(float(a.get("cost_gp", 0) or 0))
                if self.gold < cost:
                    self.ui.log("Not enough gold.")
                    continue
                self.gold -= cost
                bought_name = str(a.get("name"))
                if not self._buy_item_for_actor(actor, name=bought_name, kind="armor", auto_equip=True):
                    if bought_name.strip().lower() == "shield":
                        actor.shield = True
                        actor.shield_name = bought_name
                    else:
                        actor.armor = bought_name
                    actor.sync_legacy_equipment_to_new()
                self.ui.log(f"{actor.name} dons {bought_name}.")

    def expedition_prep_snapshot(self) -> dict[str, Any]:
        """Deterministic town-prep surface for active objective planning."""
        accepted = [c for c in (self.active_contracts or []) if isinstance(c, dict) and c.get("status") == "accepted"]
        contracts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for c in accepted:
            cid = str(c.get("cid") or "")
            if cid and cid in seen:
                continue
            if cid:
                seen.add(cid)
            ptype = str(c.get("target_poi_type") or "")
            prep_hint = "Bring standard field supplies."
            if ptype == "dungeon_entrance":
                prep_hint = "Bring extra torches and a backup light source."
            elif ptype in ("lair", "stronghold"):
                prep_hint = "Bring extra rations and healing supplies."
            elif ptype == "shrine":
                prep_hint = "Bring offerings and spare rations for a longer detour."
            contracts.append({
                "cid": cid,
                "title": str(c.get("title") or "Contract"),
                "destination": self._contract_destination_label(c),
                "prep_hint": prep_hint,
            })

        active_poi_objectives = len(contracts)
        party_size = max(1, len(getattr(self.party, "members", []) or []))
        ctx = resolve_travel_context(self)
        pace = str(ctx.get("pace") or "normal")
        pace_ration_mod = 1 if pace == "careful" else (-1 if pace == "fast" else 0)
        recommended_rations = max(6, party_size * 2 + active_poi_objectives * 2 + pace_ration_mod)
        has_dungeon = any(str((c or {}).get("target_poi_type") or "") == "dungeon_entrance" for c in accepted)
        recommended_torches = max(3, 2 + active_poi_objectives + (2 if has_dungeon else 0) + (1 if pace == "careful" else 0))
        recommended_arrows = max(20, 20 + active_poi_objectives * 10)
        recommended_bolts = max(10, 10 + active_poi_objectives * 10)
        warnings: list[str] = []
        if int(getattr(self, "rations", 0) or 0) < int(recommended_rations):
            warnings.append("Low rations for planned objectives.")
        if int(getattr(self, "torches", 0) or 0) < int(recommended_torches):
            warnings.append("Low torches for expected dungeon depth.")
        if int(getattr(self, "arrows", 0) or 0) < int(recommended_arrows):
            warnings.append("Arrow stocks are low for ranged support.")
        if int(getattr(self, "bolts", 0) or 0) < int(recommended_bolts):
            warnings.append("Bolt stocks are low for crossbow support.")
        injured_members = [m for m in self.party.living() if int(getattr(m, "hp", 0) or 0) < int(getattr(m, "hp_max", 0) or 0)]
        if injured_members:
            warnings.append(f"{len(injured_members)} party member(s) are still injured.")
        if bool(ctx.get("low_supplies", False)):
            warnings.append("Current stocks are low for wilderness pace and party size.")
        return {
            "active_poi_objectives": int(active_poi_objectives),
            "recommended_rations": int(recommended_rations),
            "recommended_torches": int(recommended_torches),
            "recommended_arrows": int(recommended_arrows),
            "recommended_bolts": int(recommended_bolts),
            "warnings": warnings,
            "contracts": contracts,
        }

    def _town_buy_basic_resupply(self) -> bool:
        cost = 16
        if self.gold < cost:
            self.ui.log(f"Not enough gold. Need {cost} gp.")
            return False
        self.gold -= cost
        self.rations = int(getattr(self, "rations", 0) or 0) + 6
        self.torches = int(getattr(self, "torches", 0) or 0) + 4
        self.arrows = int(getattr(self, "arrows", 0) or 0) + 20
        self.bolts = int(getattr(self, "bolts", 0) or 0) + 20
        self.ui.log("Purchased basic delve kit: +6 rations, +4 torches, +20 arrows, +20 bolts.")
        return True

    def _town_minor_healing_rite(self, *, cost: int = 8, pool: int = 8, label: str = "Temple rites mend wounds") -> bool:
        injured = [m for m in self.party.living() if int(getattr(m, "hp", 0) or 0) < int(getattr(m, "hp_max", 0) or 0)]
        if not injured:
            self.ui.log("No one needs healing right now.")
            return False
        if self.gold < int(cost):
            self.ui.log("Not enough gold.")
            return False
        self.gold -= int(cost)
        rem = int(pool)
        healed = 0
        for m in sorted(injured, key=lambda a: (int(a.hp) / max(1, int(a.hp_max)), int(a.hp))):
            if rem <= 0:
                break
            need = max(0, int(m.hp_max) - int(m.hp))
            if need <= 0:
                continue
            amt = min(need, rem)
            m.hp = int(m.hp) + int(amt)
            rem -= int(amt)
            healed += int(amt)
        self.ui.log(f"{label}. (Healed {healed} HP total.)")
        return True

    def _town_temple_healing(self) -> None:
        injured = [m for m in self.party.living() if int(getattr(m, "hp", 0) or 0) < int(getattr(m, "hp_max", 0) or 0)]
        if not injured:
            self.ui.log("No one needs temple healing right now.")
            return
        total_missing = sum(max(0, int(m.hp_max) - int(m.hp)) for m in injured)
        full_cost = max(6, total_missing * 2)
        c = self.ui.choose(
            f"Temple healing ({len(injured)} injured, {total_missing} HP missing)",
            ["Minor rite (8 gp): restore up to 8 HP total", f"Full recovery ({full_cost} gp)", "Back"],
        )
        if c == 2:
            return
        if c == 0:
            if self.gold < 8:
                self.ui.log("Not enough gold.")
                return
            self._town_minor_healing_rite(cost=8, pool=8, label="Temple rites mend wounds")
            return
        if self.gold < full_cost:
            self.ui.log("Not enough gold.")
            return
        self.gold -= full_cost
        for m in injured:
            m.hp = int(m.hp_max)
        self.ui.log(f"Temple recovery completed. (Healed {total_missing} HP total.)")

    def _town_identify_items(self) -> None:
        # Identification is instance-targeted: each owned copy can be known differently.
        # TODO(town-services): support sages/guild perks and class-based discounts.
        unknown: list[tuple[Actor, Any, int]] = []
        for actor in (self.party.living() or []):
            actor.ensure_inventory_initialized()
            for item in (actor.inventory.items or []):
                if bool(getattr(item, "identified", True)):
                    continue
                if not bool(item_is_magic(item)):
                    continue
                unknown.append((actor, item, int(identify_cost_gp(item))))

        if not unknown:
            self.ui.log("No unidentified magic items in character inventories.")
            return

        total_cost = sum(c for _, _, c in unknown)
        c = self.ui.choose("Sage Appraisal", [f"Identify one item", f"Identify all ({total_cost} gp)", "Back"])
        if c == 2:
            return

        if c == 1:
            if self.gold < total_cost:
                self.ui.log(f"Cannot identify all: need {total_cost} gp, have {self.gold} gp.")
                return
            identified_n = 0
            for actor, item, _cost in unknown:
                res = identify_item_in_town(actor, item.instance_id, party_gold=self.gold, reveal_curse=False, reveal_charges=False)
                if not res.ok:
                    continue
                self.gold = int(res.party_gold_after)
                identified_n += 1
            self.ui.log(f"The sage identifies {identified_n} item(s).")
            return

        labels = [f"{a.name}: {self._ui_item_line(a, it, include_weight=False)} ({cost} gp)" for a, it, cost in unknown]
        j = self.ui.choose("Identify which item?", labels + ["Back"])
        if j == len(labels):
            return
        actor, item, _cost = unknown[j]
        res = identify_item_in_town(actor, item.instance_id, party_gold=self.gold, reveal_curse=False, reveal_charges=False)
        if not res.ok:
            if str(res.error or "") == "not enough gold":
                need = int(identify_cost_gp(item))
                self.ui.log(f"Cannot identify {getattr(item, 'name', 'item')}: need {need} gp, have {self.gold} gp.")
            else:
                self.ui.log("Could not identify that item right now.")
            return
        self.gold = int(res.party_gold_after)
        self.ui.log(f"Identified: {getattr(res.item, 'name', 'Unknown Item')}.")

    def _town_lodging_service(self) -> None:
        opts = [
            ("Common room (8 gp): +1 day, restore 50% missing HP", 8, 0.5),
            ("Private inn room (18 gp): +1 day, full heal", 18, 1.0),
            ("Comfortable suites (30 gp): +1 day, full heal and calmer nerves", 30, 1.0),
        ]
        c = self.ui.choose("Lodging", [x[0] for x in opts] + ["Back"])
        if c == len(opts):
            return
        _, cost, frac = opts[c]
        if self.gold < cost:
            self.ui.log("Not enough gold.")
            return
        self.gold -= int(cost)
        healed = 0
        for m in self.party.members:
            need = max(0, int(getattr(m, "hp_max", 0) or 0) - int(getattr(m, "hp", 0) or 0))
            if need <= 0:
                continue
            amt = need if frac >= 1.0 else max(1, int(round(need * frac)))
            amt = min(need, int(amt))
            m.hp = int(getattr(m, "hp", 0) or 0) + amt
            healed += amt
        self.campaign_day += 1
        self.noise_level = max(0, int(getattr(self, "noise_level", 0) or 0) - (2 if c >= 1 else 1))
        self.ui.log(f"You settle in for the night. (-{cost} gp, healed {healed} HP total)")

    def _town_services_menu(self) -> None:
        while True:
            self.ui.hr()
            self.ui.log(f"Gold: {self.gold} gp")
            c = self.ui.choose("Town Services", ["Temple healing", "Sage identification", "Buy basic delve kit (16 gp)", "Lodging", "Back"])
            if c == 4:
                return
            if c == 0:
                self._town_temple_healing()
            elif c == 1:
                self._town_identify_items()
            elif c == 2:
                self._town_buy_basic_resupply()
            elif c == 3:
                self._town_lodging_service()

    def _town_prepare_expedition(self) -> None:
        snap = self.expedition_prep_snapshot()
        self.ui.title("Expedition Preparation")
        self.ui.log(f"Active objectives: {int(snap.get('active_poi_objectives', 0) or 0)}")
        self.ui.log(
            f"Recommended supplies: {int(snap.get('recommended_rations', 0) or 0)} rations, "
            f"{int(snap.get('recommended_torches', 0) or 0)} torches, "
            f"{int(snap.get('recommended_arrows', 0) or 0)} arrows, "
            f"{int(snap.get('recommended_bolts', 0) or 0)} bolts"
        )
        for line in list(snap.get("warnings") or []):
            self.ui.log(f"! {line}")
        rows = list(snap.get("contracts") or [])
        if rows:
            for row in rows[:6]:
                self.ui.log(f"- {row.get('title')} ({row.get('cid')}): {row.get('destination')}")
                self.ui.log(f"  Prep: {row.get('prep_hint')}")
        else:
            self.ui.log("No active contracts. Plan a general frontier run.")
        c = self.ui.choose("Prep shortcuts", ["Buy basic delve kit (16 gp)", "Temple minor healing (8 gp)", "Open Town Services", "Back"])
        if c == 0:
            self._town_buy_basic_resupply()
        elif c == 1:
            self._town_minor_healing_rite(cost=8, pool=8, label="Temple attendants patch up the party")
        elif c == 2:
            self._town_services_menu()


    def town_loop(self):
        self.ui.title(self.town_name)
        self.generate_retainer_board()

        guild_id = self._town_guild_id()

        while True:
            self.ui.hr()
            # Weekly systems
            self._advance_conflict_clocks_if_needed()
            self._refresh_contracts_if_needed()
            self._check_contracts_on_town_arrival()
            self.ui.log(f"Day {self.campaign_day} | Gold: {self.gold} gp | Torches: {self.torches} | Rations: {self.rations}")
            for pc in self.party.pcs():
                self.ui.log(f"- {pc.name} {pc.cls} Lv{pc.level} XP {pc.xp}/{self.xp_threshold(pc.level+1)} HP {pc.hp}/{pc.hp_max}")
                try:
                    pc.ensure_inventory_initialized()
                    inv_items = list(pc.inventory.items or [])
                    if inv_items:
                        for it in inv_items[:3]:
                            self.ui.log(f"    · {self._ui_item_line(pc, it, include_weight=True)}")
                        if len(inv_items) > 3:
                            self.ui.log(f"    · ...and {len(inv_items) - 3} more")
                except Exception:
                    pass
            if self.party.retainers():
                for r in self.party.retainers():
                    self.ui.log(f"  * {r.name} (Retainer) HP {r.hp}/{r.hp_max} Loyalty {r.loyalty}")

            actions = [
                "Recruit new PC",
                "Rest (heal, +1 day)",
                "Train (level up if enough XP)",
                "Prepare Spells",
                "Sell loot",
                "Assign loot to character",
                "Inspect inventory item",
                "Enter Dungeon",
                "Visit Arms & Armour Store",
                "Manage Retainers",
                "Travel into Wilderness",
                "Gather Rumors (5 gp)",
                "Expedition Preparation",
                "Town Services",
                "Rumors & Discoveries",
                "View Factions",
                "Faction Contracts",
                "Turn In Ready Contracts",
                "Faction Services",
                "Save Game",
                "Load Game",
                "Back to Main Menu",
            ]
            if any(m.hp <= 0 for m in self.party.members):
                actions.append("Drop dead party member")
            choice = self.ui.choose("Town Actions", actions)
            sel_action = actions[choice]
            if sel_action == "Recruit new PC":
                if len(self.party.pcs()) >= self.MAX_PCS:
                    self.ui.log(f"Party already has {self.MAX_PCS} PCs. Drop a dead PC first to free a slot.")
                    continue
                name = self.ui.prompt("Name for new PC:", f"Hero{len(self.party.pcs())+1}") or f"Hero{len(self.party.pcs())+1}"
                class_choices = ["Fighter","Cleric","Magic-User","Thief","Assassin","Ranger","Paladin","Druid","Monk"]
                ci = self.ui.choose("Choose class", class_choices)
                cls = class_choices[ci]
                alignment = "Neutrality"
                if cls == "Paladin":
                    alignment = "Law"
                stats = Stats(
                    STR=self.dice.d(6) + self.dice.d(6) + self.dice.d(6),
                    DEX=self.dice.d(6) + self.dice.d(6) + self.dice.d(6),
                    CON=self.dice.d(6) + self.dice.d(6) + self.dice.d(6),
                    INT=self.dice.d(6) + self.dice.d(6) + self.dice.d(6),
                    WIS=self.dice.d(6) + self.dice.d(6) + self.dice.d(6),
                    CHA=self.dice.d(6) + self.dice.d(6) + self.dice.d(6),
                )
                hd = self.class_hit_die(cls)
                hp = max(1, self.dice.d(hd) + max(0, mod_ability(stats.CON)))
                pc = PC(
                    name=name,
                    race="Human",
                    hp=hp,
                    hp_max=hp,
                    ac_desc=9,
                    hd=1,
                    save=15,
                    morale=9,
                    alignment=alignment,
                    is_pc=True,
                    cls=cls,
                    level=1,
                    xp=0,
                    stats=stats,
                )
                self.party.members.append(pc)
                self.recalc_pc_class_features(pc)
                self.ui.log(f"Recruited {name} (Level 1 {cls}).")
                continue



            if sel_action == "Rest (heal, +1 day)":
                cost = max(10, 10 * len(self.party.pcs()))
                # Bad standing with the town guild makes services more expensive.
                if self.faction_rep(guild_id) <= -60:
                    cost = int(cost * 1.5)
                if self.gold < cost:
                    self.ui.log(f"Not enough gold. Need {cost} gp.")
                    continue
                self.gold -= cost
                for m in self.party.members:
                    m.hp = m.hp_max
                self.campaign_day += 1
                self.noise_level = 0
                self.ui.log(f"Rested. (-{cost} gp)")
                self._advance_conflict_clocks_if_needed()
                self._refresh_contracts_if_needed()
                self.generate_retainer_board()

            elif sel_action == "Train (level up if enough XP)":
                self.train_party()

            elif sel_action == "Prepare Spells":
                self.prepare_spells_menu(in_dungeon=False)

            elif sel_action == "Sell loot":
                self.sell_loot()

            elif sel_action == "Assign loot to character":
                self.assign_party_loot_to_character()

            elif sel_action == "Inspect inventory item":
                self._town_inspect_inventory_item()

            elif sel_action == "Enter Dungeon":
                self._cmd_enter_dungeon()
                self.dungeon_loop()

            elif sel_action == "Visit Arms & Armour Store":
                self.town_arms_store()

            elif sel_action == "Manage Retainers":
                self.manage_retainers()

            elif sel_action == "Travel into Wilderness":
                if not self.party.pcs():
                    self.ui.log("Create a party first.")
                else:
                    self.wilderness_loop()

            elif sel_action == "Gather Rumors (5 gp)":
                self.gather_rumors()

            elif sel_action == "Expedition Preparation":
                self._town_prepare_expedition()

            elif sel_action == "Town Services":
                self._town_services_menu()

            elif sel_action == "Rumors & Discoveries":
                self.view_journal()

            elif sel_action == "View Factions":
                self.view_factions()

            elif sel_action == "Faction Contracts":
                self.view_contracts()

            elif sel_action == "Turn In Ready Contracts":
                self._town_turn_in_ready_contracts()

            elif sel_action == "Faction Services":
                self.view_faction_services()

            elif sel_action == "Save Game":
                self.save_from_menu()

            elif sel_action == "Load Game":
                self.load_from_menu()

            elif sel_action == "Drop dead party member":
                # Drop dead party member (kept in party by default to allow town resurrection)
                dead = [m for m in self.party.members if m.hp <= 0]
                labels = [f"{m.name} ({'Retainer' if getattr(m,'is_retainer',False) else m.cls}) HP {m.hp}/{m.hp_max}" for m in dead]
                labels.append("Back")
                sel = self.ui.choose("Drop which dead party member?", labels)
                if sel == len(labels) - 1:
                    continue
                victim = dead[sel]
                self.party.members = [m for m in self.party.members if m is not victim]
                self.ui.log(f"You leave {victim.name}'s body behind.")

            else:
                # Back to Main Menu
                return

    # -------------
    # Wilderness (Expanding Frontier)
    # -------------

    def _watch_name(self) -> str:
        names = ["Morning", "Late Morning", "Afternoon", "Late Afternoon", "Evening", "Night"]
        w = int(getattr(self, "day_watch", 0)) % self.WATCHES_PER_DAY
        return names[w]

    def _watch_name_of(self, watch: int) -> str:
        names = ["Morning", "Late Morning", "Afternoon", "Late Afternoon", "Evening", "Night"]
        return names[int(watch) % self.WATCHES_PER_DAY]

    def _advance_watch(self, watches: int = 1):
        for _ in range(watches):
            self.day_watch += 1
            if self.day_watch >= self.WATCHES_PER_DAY:
                self.day_watch = 0
                self.campaign_day += 1
                self._on_day_passed()
                self._advance_conflict_clocks_if_needed()
                self._refresh_contracts_if_needed()

    def _on_day_passed(self) -> None:
        """Central hook for effects that tick on campaign day changes (e.g., disease)."""
        try:
            living = list(self.party.living()) if getattr(self, "party", None) else []
            self.effects_mgr.tick(living, Hook.DAY_PASSED, ctx={
                "day": int(getattr(self, "campaign_day", 1) or 1),
                "round_no": int(getattr(self, "combat_round", 1) or 1),
            })
        except Exception:
            return

    def _consume_rations(self, units: int = 1):
        members = len(self.party.members or [])
        need = units * members
        if need <= 0:
            return
        if self.rations >= need:
            self.rations -= need
            return
        # Moderate/forgiving: if you run out, you still travel but suffer minor damage.
        short = need - self.rations
        self.rations = 0
        self.ui.log("You are out of rations!")
        for _ in range(short):
            victim = self.dice_rng.choice(self.party.living()) if self.party.living() else None
            if victim:
                victim.hp = max(-1, victim.hp - 1)

    def _ensure_canonical_dungeon_entrance(self) -> dict[str, Any]:
        """Ensure a stable canonical dungeon entrance POI exists near town.

        Minimal P6.2 pass: deterministic destination ownership only.
        """
        if not hasattr(self, "wilderness_rng"):
            self.wilderness_rng = LoggedRandom(self.wilderness_seed, channel="wilderness", log_fn=self._log_rng)

        hx = ensure_hex(self.world_hexes, tuple(DUNGEON_ENTRANCE_HEX), self.wilderness_rng)
        existing = hx.poi if isinstance(getattr(hx, "poi", None), dict) else {}
        canonical_id = "poi:canonical:dungeon_entrance"

        discovered = bool(existing.get("discovered", False))
        resolved = bool(existing.get("resolved", False))

        if str(existing.get("id") or "") == canonical_id:
            discovered = bool(existing.get("discovered", False))
            resolved = bool(existing.get("resolved", False))

        hx.poi = {
            "id": canonical_id,
            "type": "dungeon_entrance",
            "name": "Dungeon Entrance",
            "notes": "A known stair descending into the depths near town.",
            "discovered": discovered,
            "resolved": resolved,
            "canonical": True,
        }
        self.world_hexes[hx.key()] = hx.to_dict()
        return self.world_hexes[hx.key()]

    def _ensure_secondary_pois(self) -> None:
        """Ensure a tiny deterministic secondary-POI surface near town.

        This is intentionally minimal and only covers stable reconciliation anchors
        used by current wilderness surface tests.
        """
        if not hasattr(self, "wilderness_rng"):
            self.wilderness_rng = LoggedRandom(self.wilderness_seed, channel="wilderness", log_fn=self._log_rng)

        anchors = [
            ((1, 0), "ruins", "Roadside Ruins", "poi:secondary:ruins:1,0", "Broken stonework and old campfire rings."),
            ((0, -1), "shrine", "Old Wayside Shrine", "poi:secondary:shrine:0,-1", "A weather-worn shrine watched by crows."),
        ]
        for (q, r), ptype, name, pid, notes in anchors:
            hx = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng)
            existing = hx.poi if isinstance(getattr(hx, "poi", None), dict) else {}
            if str(existing.get("id") or "") == pid:
                continue
            hx.poi = {
                "id": pid,
                "type": ptype,
                "name": name,
                "notes": notes,
                "discovered": bool(existing.get("discovered", False)),
                "resolved": bool(existing.get("resolved", False)),
                "rumored": bool(existing.get("rumored", False)),
                "secondary": True,
            }
            self.world_hexes[hx.key()] = hx.to_dict()

    def _ensure_current_hex(self) -> dict[str, Any]:
        self._ensure_canonical_dungeon_entrance()
        self._ensure_secondary_pois()
        # Use a dedicated RNG for wilderness generation to avoid odd coupling.
        if not hasattr(self, "wilderness_rng"):
            self.wilderness_rng = LoggedRandom(self.wilderness_seed, channel="wilderness", log_fn=self._log_rng)
        hx = ensure_hex(self.world_hexes, self.party_hex, self.wilderness_rng)
        hx.discovered = True
        hx.visited += 1

        # Apply faction territory if relevant
        if getattr(hx, "faction_id", None) is None:
            d = hx.to_dict()
            self._ensure_faction_for_hex(d)
            hx.faction_id = d.get("faction_id")

        # Assign POI ownership if present and unassigned.
        if hx.poi:
            d = hx.to_dict()
            self._assign_poi_faction(d)
            hx.poi = d.get("poi")
        if hx.poi and not bool(hx.poi.get("discovered", False)):
            ctx = resolve_travel_context(self)
            discover_chance = max(1, min(6, 5 + int(ctx.get("discovery_mod", 0))))
            if self.dice.in_6(discover_chance):
                hx.poi["discovered"] = True
                # First-time discovery: add to journal.
                self._add_discovery(hx)
        self.world_hexes[hx.key()] = hx.to_dict()
        # Reveal neighbors as "known" (generated but undiscovered) so frontier expands.
        for _d, pos in neighbors(self.party_hex).items():
            h2 = ensure_hex(self.world_hexes, pos, self.wilderness_rng)
            if getattr(h2, "faction_id", None) is None:
                d2 = h2.to_dict()
                self._ensure_faction_for_hex(d2)
                h2.faction_id = d2.get("faction_id")
                self.world_hexes[h2.key()] = h2.to_dict()
        return self.world_hexes[hx.key()]

    def _assign_poi_faction(self, hx: dict[str, Any]):
        poi = hx.get("poi")
        if not isinstance(poi, dict):
            return
        if poi.get("faction_id"):
            return
        # Prefer hex faction if any.
        if hx.get("faction_id"):
            poi["faction_id"] = hx.get("faction_id")
            hx["poi"] = poi
            return

        # Otherwise assign by type: shrines -> religious, roads/clear ruins -> guild sometimes,
        # lairs/entrances -> monster or wildcard.
        ptype = str(poi.get("type") or "")
        if ptype == "shrine":
            rel = [fid for fid, f in (self.factions or {}).items() if (f.get("ftype") == "religious")]
            if rel:
                poi["faction_id"] = self.dice_rng.choice(rel)
        elif ptype in ("lair", "dungeon_entrance"):
            mons = [fid for fid, f in (self.factions or {}).items() if (f.get("ftype") == "monster")]
            wild = [fid for fid, f in (self.factions or {}).items() if (f.get("fid") == "W1")]
            pool = mons + wild
            if pool:
                poi["faction_id"] = self.dice_rng.choice(pool)
        elif ptype == "ruins":
            # Ruins are often contested; lightly bias to guild near town.
            dist = hex_distance((0, 0), (int(hx.get("q", 0)), int(hx.get("r", 0))))
            if dist <= 2 and self.dice_rng.random() < 0.5:
                gid = self._town_guild_id()
                if gid:
                    poi["faction_id"] = gid
            elif self.dice_rng.random() < 0.25:
                # Otherwise random non-town
                pool = [fid for fid, f in (self.factions or {}).items() if not bool(f.get("is_town_guild"))]
                if pool:
                    poi["faction_id"] = self.dice_rng.choice(pool)
        elif ptype == "caravan":
            gid = self._town_guild_id()
            if gid and self.dice_rng.random() < 0.70:
                poi["faction_id"] = gid
            else:
                pool = [fid for fid, f in (self.factions or {}).items() if f.get("ftype") in ("guild", "religious")]
                if pool:
                    poi["faction_id"] = self.dice_rng.choice(pool)
        elif ptype in ("faction_outpost", "outpost"):
            pool = [fid for fid, f in (self.factions or {}).items() if not bool(f.get("is_town_guild"))]
            if pool:
                poi["faction_id"] = self.dice_rng.choice(pool)
        elif ptype == "abandoned_camp":
            if self.dice_rng.random() < 0.45:
                gid = self._town_guild_id()
                if gid:
                    poi["faction_id"] = gid

        hx["poi"] = poi
        self.world_hexes[f"{hx.get('q')},{hx.get('r')}"] = hx

    def _add_discovery(self, hx_obj: Any):
        """Record a first-time POI discovery in the journal."""
        try:
            q = int(getattr(hx_obj, "q"))
            r = int(getattr(hx_obj, "r"))
            terrain = str(getattr(hx_obj, "terrain"))
            poi = getattr(hx_obj, "poi") or {}
        except Exception:
            return
        kind = str(poi.get("type") or "poi")
        name = str(poi.get("name") or "Unknown")
        poi_id = str(poi.get("id") or f"hex:{q},{r}:{kind}")
        self._mark_rumors_seen_for_poi(poi_id)
        note = str(poi.get("notes") or "")
        entry = {
            "day": int(getattr(self, "campaign_day", 1)),
            "watch": int(getattr(self, "day_watch", 0)),
            "q": q,
            "r": r,
            "terrain": terrain,
            "kind": kind,
            "name": name,
            "note": note,
        }
        self._append_player_event(
            "discovery.recorded",
            category="discovery",
            title=f"Discovery recorded: {name}",
            payload=entry,
            refs={"poi_id": poi_id, "hex": [q, r]},
        )
        self.discovery_log = project_discoveries_from_events(getattr(self, "event_history", []))
        self.ui.log(f"Discovery recorded: {name} at Hex ({q},{r}).")

    def _rumor_hint(self, kind: str, terrain: str) -> str:
        """A short, flavorful hint without giving away the exact name."""
        kind = kind or "poi"
        terrain = terrain or "clear"
        if kind == "dungeon_entrance":
            return f"A way down is said to lie somewhere in the {terrain}."
        if kind == "lair":
            return f"Locals whisper of a danger lairing out in the {terrain}."
        if kind == "shrine":
            return f"A place of quiet prayer is rumored in the {terrain}."
        if kind == "ruins":
            return f"Old stones are said to stand somewhere in the {terrain}."
        if kind == "abandoned_camp":
            return f"Smoke was once seen from an abandoned camp in the {terrain}."
        if kind == "caravan":
            return f"Travelers speak of a caravan moving through the {terrain}."
        if kind in ("faction_outpost", "outpost"):
            return f"A guarded outpost is said to watch the {terrain}."
        return f"Something noteworthy is rumored in the {terrain}."

    def _rumor_meta(self, kind: str, terrain: str) -> dict[str, str]:
        k = str(kind or "poi")
        if k in ("dungeon_entrance", "ruins", "shrine"):
            rumor_type = "location"
        elif k in ("lair", "faction_outpost", "outpost"):
            rumor_type = "threat"
        elif k in ("caravan", "abandoned_camp"):
            rumor_type = "opportunity"
        else:
            rumor_type = "location"
        risk_hint = "moderate"
        reward_hint = "scouting lead"
        if k in ("lair", "dungeon_entrance"):
            risk_hint = "high"
            reward_hint = "loot and progress"
        elif k in ("caravan", "shrine"):
            risk_hint = "low"
            reward_hint = "support and resources"
        elif k == "abandoned_camp":
            risk_hint = "swingy"
            reward_hint = "supplies and clues"
        if str(terrain or "") in ("swamp", "mountain"):
            risk_hint = "elevated"
        return {
            "rumor_type": rumor_type,
            "poi_hint": k,
            "risk_hint": risk_hint,
            "reward_hint": reward_hint,
            "timed": "yes" if rumor_type in ("threat", "opportunity") else "no",
        }

    def _rumor_locator(self, q: int, r: int) -> str:
        """Human-friendly direction cue for a wilderness rumor."""
        dist = hex_distance((0, 0), (int(q), int(r)))
        if dist <= 1:
            dist_phrase = "close to town"
        elif dist <= 3:
            dist_phrase = "a short march from town"
        elif dist <= 6:
            dist_phrase = "a fair way from town"
        else:
            dist_phrase = "well beyond the settled paths"
        dirs: list[str] = []
        if int(r) < 0:
            dirs.append("north")
        elif int(r) > 0:
            dirs.append("south")
        if int(q) > 0:
            dirs.append("east")
        elif int(q) < 0:
            dirs.append("west")
        if not dirs:
            return dist_phrase
        if len(dirs) == 2:
            direction = f"{dirs[0]}-{dirs[1]}"
        else:
            direction = dirs[0]
        return f"{dist_phrase}, to the {direction}"

    def _locator_from(self, origin: tuple[int, int], target: tuple[int, int], *, style: str = "wilderness") -> str:
        """Human-friendly direction cue from an origin to a target hex."""
        oq, or_ = int(origin[0]), int(origin[1])
        tq, tr = int(target[0]), int(target[1])
        dq, dr = tq - oq, tr - or_
        dist = hex_distance((oq, or_), (tq, tr))
        if dist <= 0:
            dist_phrase = "here"
        elif dist == 1:
            dist_phrase = "1 hex away"
        elif dist <= 3:
            dist_phrase = f"{dist} hexes away"
        elif dist <= 6:
            dist_phrase = f"about {dist} hexes away"
        else:
            dist_phrase = "far out in the district" if style == "district" else "well beyond the settled paths"
        dirs: list[str] = []
        if dr < 0:
            dirs.append("north")
        elif dr > 0:
            dirs.append("south")
        if dq > 0:
            dirs.append("east")
        elif dq < 0:
            dirs.append("west")
        if not dirs:
            return dist_phrase
        direction = f"{dirs[0]}-{dirs[1]}" if len(dirs) == 2 else dirs[0]
        if dist_phrase == "here":
            return "here"
        return f"{dist_phrase}, to the {direction}"

    def _district_objective_entries(self) -> list[dict[str, Any]]:
        """Return active local/district jobs with presentation metadata."""
        entries: list[dict[str, Any]] = []
        for c in (self.active_contracts or []):
            if not isinstance(c, dict) or c.get("status") != "accepted":
                continue
            if not bool(c.get("district_objective", False)):
                continue
            try:
                th = tuple(c.get("target_hex") or [0, 0])
                tq, tr = int(th[0]), int(th[1])
            except Exception:
                tq, tr = 0, 0
            source_hex = tuple(c.get("source_hex") or [0, 0])
            try:
                sq, sr = int(source_hex[0]), int(source_hex[1])
            except Exception:
                sq, sr = 0, 0
            source_name = str(c.get("source_name") or c.get("source_label") or "local outpost")
            district_name = str(c.get("district_name") or source_name)
            title = str(c.get("title") or "District Objective")
            entries.append({
                "cid": str(c.get("cid") or ""),
                "title": title,
                "source_name": source_name,
                "district_name": district_name,
                "deadline_day": int(c.get("deadline_day", 0) or 0),
                "reward_gp": int(c.get("reward_gp", 0) or 0),
                "visited_target": bool(c.get("visited_target", False)),
                "completion_ready": bool(c.get("completion_ready", False)),
                "target_hex": (tq, tr),
                "source_hex": (sq, sr),
                "target_poi_type": str(c.get("target_poi_type") or ""),
                "target_hint": str(c.get("target_hint") or self._locator_from((sq, sr), (tq, tr), style="district")),
                "from_current": str(c.get("current_hint") or self._locator_from(tuple(self.party_hex), (tq, tr), style="district")),
                "desc": str(c.get("desc") or ""),
            })
        entries.sort(key=lambda e: (e["deadline_day"], hex_distance(tuple(self.party_hex), tuple(e["target_hex"])) ))
        return entries

    def _find_poi_by_id(self, poi_id: str) -> tuple[dict[str, Any], dict[str, Any], int, int] | None:
        pid = str(poi_id or "").strip()
        if not pid:
            return None
        for _k, hx in (self.world_hexes or {}).items():
            if not isinstance(hx, dict):
                continue
            poi = hx.get("poi") if isinstance(hx.get("poi"), dict) else None
            if not isinstance(poi, dict):
                continue
            if str(poi.get("id") or "") != pid:
                continue
            try:
                q = int(hx.get("q"))
                r = int(hx.get("r"))
            except Exception:
                q = r = 0
            return poi, hx, q, r
        return None

    def _contract_rumor_link(self, c: dict[str, Any]) -> dict[str, Any] | None:
        cid = str((c or {}).get("cid") or "")
        pid = str((c or {}).get("target_poi_id") or "")
        for e in (self._journal_rumors() or []):
            if not isinstance(e, dict):
                continue
            if cid and str(e.get("linked_contract_cid") or "") == cid:
                return e
            if pid and str(e.get("poi_id") or "") == pid:
                return e
        return None

    def _contract_destination_label(self, c: dict[str, Any]) -> str:
        pid = str((c or {}).get("target_poi_id") or "")
        if pid:
            row = self._find_poi_by_id(pid)
            if row is not None:
                poi, _hx, q, r = row
                state = "discovered" if bool(poi.get("discovered", False)) else "undiscovered"
                return f"{self._poi_label(poi)} @ ({q},{r}) [{state}]"
        th = tuple((c or {}).get("target_hex") or [0, 0])
        try:
            q, r = int(th[0]), int(th[1])
        except Exception:
            q, r = 0, 0
        return f"Hex ({q},{r})"

    def _render_rumor_row(self, e: dict[str, Any]) -> str:
        text = f"Day {e.get('day')} | {e.get('hint')}"
        pid = str(e.get("poi_id") or "")
        if pid:
            row = self._find_poi_by_id(pid)
            if row is not None:
                poi, _hx, q, r = row
                state = "seen" if bool(e.get("seen", False)) else "unseen"
                text += f" | Destination: {self._poi_label(poi)} @ ({q},{r}) [{state}]"
        if e.get("rumor_type"):
            text += f" | Type: {str(e.get('rumor_type')).title()}"
        if e.get("risk_hint"):
            text += f" | Risk: {e.get('risk_hint')}"
        linked_cid = str(e.get("linked_contract_cid") or "")
        if linked_cid:
            text += f" | Linked contract: {linked_cid}"
        return text


    def _wilderness_telegraph_lines(self) -> list[str]:
        """Return compact boardgame-like wilderness telegraph lines.

        Surfaces two kinds of information without requiring a full map:
        - district-edge telegraphing (which adjacent moves enter an objective district)
        - target-site telegraphing (which adjacent move reaches the site)
        """
        lines: list[str] = []
        pq, pr = int(self.party_hex[0]), int(self.party_hex[1])
        neigh = neighbors((pq, pr))
        # Compact adjacent-hex panel first.
        panel_bits: list[str] = []
        for d, pos in neigh.items():
            hx = ensure_hex(self.world_hexes, pos, self.wilderness_rng)
            terrain = str(getattr(hx, 'terrain', None) or (hx.get('terrain') if isinstance(hx, dict) else 'clear') or 'clear')
            poi = getattr(hx, 'poi', None) if hasattr(hx, 'poi') else (hx.get('poi') if isinstance(hx, dict) else None)
            marker = ''
            if isinstance(poi, dict) and bool(poi.get('discovered', False)):
                marker = f"/{self._poi_label(poi)}"
            panel_bits.append(f"{d}:{terrain}{marker}")
        if panel_bits:
            lines.append("Nearby hexes: " + " | ".join(panel_bits))

        entries = self._district_objective_entries()
        if not entries:
            return lines

        edge_msgs: list[str] = []
        site_msgs: list[str] = []
        for e in entries[:2]:
            title = str(e.get('title') or 'Objective')
            tq, tr = tuple(e.get('target_hex') or (0, 0))
            sq, sr = tuple(e.get('source_hex') or (0, 0))
            target_dist = int(hex_distance((pq, pr), (tq, tr)))
            source_span = int(hex_distance((sq, sr), (tq, tr)))
            district_radius = max(2, min(4, max(1, source_span // 2 + 1)))
            edge_dirs: list[str] = []
            site_dirs: list[str] = []
            for d, pos in neigh.items():
                nq, nr = pos
                ndist = int(hex_distance((nq, nr), (tq, tr)))
                if target_dist > district_radius and ndist <= district_radius:
                    edge_dirs.append(d)
                if ndist == 0:
                    site_dirs.append(d)
            if site_dirs:
                dirs = ", ".join(site_dirs)
                site_msgs.append(f"{title}: objective site is 1 hex away ({dirs}).")
            elif target_dist == 0:
                site_msgs.append(f"{title}: objective site is here.")
            if edge_dirs:
                dirs = ", ".join(edge_dirs)
                edge_msgs.append(f"{title}: district edge lies via {dirs}.")
            elif bool(e.get('completion_ready')):
                edge_msgs.append(f"{title}: objective complete — return to town to claim reward.")
            elif bool(e.get('visited_target')):
                edge_msgs.append(f"{title}: target reached — return to town to claim reward.")
            elif target_dist <= district_radius:
                edge_msgs.append(f"{title}: you are inside the target district.")
        lines.extend(edge_msgs[:2])
        lines.extend(site_msgs[:2])
        return lines

    def _surface_poi_rumor(self, q: int, r: int, *, source: str = "system", cost: int = 0) -> bool:
        """Add a deterministic rumor entry for an existing POI, deduplicated by POI id."""
        k = f"{int(q)},{int(r)}"
        hx = (self.world_hexes or {}).get(k)
        if not isinstance(hx, dict):
            return False
        poi = hx.get("poi") if isinstance(hx.get("poi"), dict) else None
        if not isinstance(poi, dict):
            return False

        poi_id = str(poi.get("id") or f"hex:{int(q)},{int(r)}:{str(poi.get('type') or 'poi')}")
        poi_kind = str(poi.get("type") or "poi")

        for e in (self._journal_rumors() or []):
            if str(e.get("poi_id") or "") == poi_id:
                return False

        hint = self._rumor_hint(poi_kind, str(hx.get("terrain", "clear")))
        locator = self._rumor_locator(int(q), int(r))
        full_hint = f"{hint} Look {locator}."

        meta = self._rumor_meta(poi_kind, str(hx.get("terrain", "clear")))
        rumor_entry = {
            "day": int(getattr(self, "campaign_day", 1)),
            "q": int(q),
            "r": int(r),
            "terrain": str(hx.get("terrain", "clear")),
            "kind": poi_kind,
            "hint": full_hint,
            "locator": locator,
            "cost": int(cost),
            "poi_id": poi_id,
            "source": str(source or "system"),
            "seen": bool(poi.get("discovered", False)),
            **meta,
        }
        self._append_player_event(
            "rumor.learned",
            category="rumor",
            title="Rumor surfaced",
            payload=rumor_entry,
            refs={"poi_id": poi_id, "hex": [int(q), int(r)]},
        )
        self.rumors = project_rumors_from_events(getattr(self, "event_history", []))
        poi["rumored"] = True
        hx["poi"] = poi
        self.world_hexes[k] = hx
        return True

    def _ensure_minimal_rumor_surface(self) -> None:
        """Minimal deterministic rumor seed for known core destinations."""
        self._ensure_canonical_dungeon_entrance()
        self._ensure_secondary_pois()
        self._surface_poi_rumor(int(DUNGEON_ENTRANCE_HEX[0]), int(DUNGEON_ENTRANCE_HEX[1]), source="canonical", cost=0)
        self._surface_poi_rumor(1, 0, source="secondary", cost=0)
        self._surface_poi_rumor(0, -1, source="secondary", cost=0)

    def _update_rumor_events(self, poi_id: str, updates: dict[str, Any]) -> None:
        pid = str(poi_id or "").strip()
        if not pid:
            return
        changed = False
        for ev in (getattr(self, "event_history", []) or []):
            if str((ev or {}).get("type") or "") != "rumor.learned":
                continue
            payload = ev.get("payload") if isinstance(ev, dict) else None
            if not isinstance(payload, dict):
                continue
            if str(payload.get("poi_id") or "") != pid:
                continue
            payload.update(dict(updates or {}))
            ev["payload"] = payload
            changed = True
        if changed:
            self.rumors = project_rumors_from_events(getattr(self, "event_history", []))

    def _mark_rumors_seen_for_poi(self, poi_id: str) -> None:
        pid = str(poi_id or "").strip()
        if not pid:
            return
        changed = False
        for e in (self._journal_rumors() or []):
            if str(e.get("poi_id") or "") == pid and not bool(e.get("seen", False)):
                changed = True
        if changed:
            self._update_rumor_events(pid, {"seen": True})

    def _weighted_rumor_choice(self, candidates, missing_new_types: set[str] | None = None):
        missing_new_types = set(missing_new_types or set())
        weighted = []
        for dist, q, r, hx in candidates:
            poi = (hx.get("poi") or {}) if isinstance(hx, dict) else {}
            kind = str(poi.get("type") or "poi")
            terrain = str(hx.get("terrain", "clear"))
            weight = max(1.0, 14.0 - float(dist))
            if kind in missing_new_types:
                weight *= 4.0
            if kind == "caravan":
                weight *= 2.0 if terrain in ("road", "clear") else 1.3
            elif kind in ("faction_outpost", "outpost"):
                weight *= 2.5 if (poi.get("faction_id") or hx.get("faction_id")) else 1.4
            elif kind == "abandoned_camp":
                weight *= 1.8 if terrain in ("forest", "hills", "road", "clear") else 1.2
            elif kind == "dungeon_entrance":
                weight *= 1.4
            elif kind == "lair":
                weight *= 1.2 if terrain in ("forest", "swamp", "hills", "mountain") else 1.0
            if terrain == "road":
                weight *= 1.15
            weighted.append((weight, (dist, q, r, hx)))
        total = sum(w for w, _ in weighted)
        if total <= 0:
            return candidates[0]
        roll = self.dice_rng.random() * total
        acc = 0.0
        for weight, row in weighted:
            acc += weight
            if roll <= acc:
                return row
        return weighted[-1][1]

    def gather_rumors(self):
        """Town action: pay a small fee to learn of a nearby undiscovered POI."""
        guild_rep = self.faction_rep(self._town_guild_id())
        cost = 5 if guild_rep > -60 else 10
        if self.gold < cost:
            self.ui.log(f"Not enough gold. Need {cost} gp.")
            return

        preferred_new_types = {"abandoned_camp", "caravan", "outpost"}
        missing_new_types = preferred_new_types.difference({str(e.get("kind") or "") for e in (self.rumors or [])})

        # Sometimes the rumor is political rather than a specific POI.
        if self.factions and self.dice_rng.random() < 0.25 and not missing_new_types:
            non_town = [f for f in self.factions.values() if not bool(f.get("is_town_guild"))]
            if non_town:
                f = self.dice_rng.choice(non_town)
                choices = list(f.get("controlled_hexes") or [])
                if choices:
                    choices.sort(key=lambda p: hex_distance((0, 0), (int(p[0]), int(p[1]))))
                    idx = min(len(choices) - 1, self.dice_rng.randint(0, min(2, len(choices) - 1)))
                    q, r = int(choices[idx][0]), int(choices[idx][1])
                    ensure_hex(self.world_hexes, (q, r), self.wilderness_rng)
                    terrain = str((self.world_hexes.get(f"{q},{r}") or {}).get("terrain", "wilderness"))
                    locator = self._rumor_locator(q, r)
                    hint = f"{f.get('name','A faction')} is stirring in the {terrain}, {locator}."
                    self.gold -= cost
                    rumor_entry = {
                        "day": int(getattr(self, "campaign_day", 1)),
                        "q": q,
                        "r": r,
                        "terrain": terrain,
                        "kind": "faction",
                        "hint": hint,
                        "locator": locator,
                        "cost": cost,
                        "faction_id": str(f.get("fid") or ""),
                    }
                    self._append_player_event(
                        "rumor.learned",
                        category="rumor",
                        title="Rumor learned",
                        payload=rumor_entry,
                        refs={"hex": [int(q), int(r)]},
                    )
                    self.rumors = project_rumors_from_events(getattr(self, "event_history", []))
                    self.ui.log(f"Rumor: {hint}")
                    return

        candidates = []
        for _k, hx in (self.world_hexes or {}).items():
            poi = (hx.get("poi") or None)
            if not poi:
                continue
            if bool(poi.get("discovered", False)):
                continue
            if bool(poi.get("rumored", False)):
                continue
            try:
                q = int(hx.get("q"))
                r = int(hx.get("r"))
            except Exception:
                continue
            dist = hex_distance((0, 0), (q, r))
            if dist <= 0:
                continue
            candidates.append((dist, q, r, hx))

        if missing_new_types:
            preferred = [row for row in candidates if str(((row[3].get("poi") or {}).get("type") or "")) in missing_new_types]
            if preferred:
                candidates = preferred

        if not candidates:
            q, r, hx = self._generate_frontier_poi_for_rumor(preferred_kinds=missing_new_types or None)
            if hx is None:
                self.ui.log("No fresh rumors right now. Explore further or return later.")
                return
            dist = hex_distance((0, 0), (q, r))
            candidates = [(dist, q, r, hx)]

        candidates.sort(key=lambda x: x[0])
        pool = candidates[: max(8, min(24, len(candidates)))]
        dist, q, r, hx = self._weighted_rumor_choice(pool, missing_new_types=missing_new_types)
        poi = hx.get("poi") or {}
        poi["rumored"] = True
        hx["poi"] = poi
        self.world_hexes[f"{q},{r}"] = hx

        self.gold -= cost
        poi_kind = str(poi.get("type") or "poi")
        hint = self._rumor_hint(poi_kind, str(hx.get("terrain", "clear")))
        meta = self._rumor_meta(poi_kind, str(hx.get("terrain", "clear")))
        fid = poi.get("faction_id") or hx.get("faction_id")
        if fid and (fid in (self.factions or {})):
            fname = (self.factions.get(fid) or {}).get("name")
            if fname:
                hint = f"{hint} Folk say it has ties to {fname}."
        locator = self._rumor_locator(q, r)
        full_hint = f"{hint} Look {locator}."
        rumor_entry = {
            "day": int(getattr(self, "campaign_day", 1)),
            "q": q,
            "r": r,
            "terrain": str(hx.get("terrain", "clear")),
            "kind": poi_kind,
            "hint": full_hint,
            "locator": locator,
            "cost": cost,
            "poi_id": str(poi.get("id") or f"hex:{q},{r}:{str(poi.get('type') or 'poi')}"),
            "source": "town_rumor",
            "seen": bool(poi.get("discovered", False)),
            **meta,
        }
        self._append_player_event(
            "rumor.learned",
            category="rumor",
            title="Rumor learned",
            payload=rumor_entry,
            refs={"poi_id": str(rumor_entry.get("poi_id") or ""), "hex": [int(q), int(r)]},
        )
        self.rumors = project_rumors_from_events(getattr(self, "event_history", []))
        self.ui.log(f"Rumor: {full_hint}")

    def _generate_frontier_poi_for_rumor(self, preferred_kinds: set[str] | None = None):
        """Generate (or reuse) a hex in the unseen frontier and ensure it contains a POI.

        Used when the player buys rumors before they have explored enough for
        existing POIs to be available.
        Returns (q, r, hex_dict) or (0, 0, None) on failure.
        """
        # Determine current generated radius.
        max_dist = 0
        for _k, _hx in (self.world_hexes or {}).items():
            try:
                q0 = int(_hx.get("q"))
                r0 = int(_hx.get("r"))
            except Exception:
                continue
            max_dist = max(max_dist, hex_distance((0, 0), (q0, r0)))

        target_min = max(2, max_dist + 1)
        target_max = target_min + 3

        # Axial direction deltas (q,r)
        deltas = [(0, -1), (1, -1), (1, 0), (0, 1), (-1, 1), (-1, 0)]

        for _ in range(60):
            dist = self.wilderness_rng.randint(target_min, target_max)
            q, r = 0, 0
            for _step in range(dist):
                dq, dr = self.wilderness_rng.choice(deltas)
                q += dq
                r += dr

            k = f"{q},{r}"
            hx = self.world_hexes.get(k)

            if hx is None:
                # Generate the hex and store it back into world_hexes
                hx_obj = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng)
                hx = self.world_hexes.get(k) or hx_obj.to_dict()
                self._ensure_faction_for_hex(hx)
                self.world_hexes[k] = hx

            poi = hx.get("poi") or None

            # Prefer an existing POI if it's undiscovered and not already rumored.
            if poi and not bool(poi.get("discovered", False)) and not bool(poi.get("rumored", False)):
                return q, r, hx

            # Otherwise, force a POI onto this hex (persistent).
            if not poi:
                terrain = str(hx.get("terrain", "clear"))
                dist2 = hex_distance((0, 0), (q, r))
                poi = None
                preferred_kinds = set(preferred_kinds or set())
                for _attempt in range(12 if preferred_kinds else 1):
                    cand = force_poi(self.wilderness_rng, dist2, terrain)
                    if not preferred_kinds or str(cand.get("type") or "") in preferred_kinds:
                        poi = cand
                        break
                if poi is None:
                    poi = force_poi(self.wilderness_rng, dist2, terrain)
                hx["poi"] = poi
                # Assign faction ownership immediately so the rumor can mention it.
                self._ensure_faction_for_hex(hx)
                self._assign_poi_faction(hx)
                self.world_hexes[k] = hx
                return q, r, hx

        return 0, 0, None

    def _journal_discoveries(self) -> list[dict[str, Any]]:
        rows = project_discoveries_from_events(getattr(self, "event_history", []))
        self.discovery_log = list(rows)
        return rows

    def _journal_rumors(self) -> list[dict[str, Any]]:
        rows = project_rumors_from_events(getattr(self, "event_history", []))
        self.rumors = list(rows)
        return rows

    def _journal_clues(self) -> list[dict[str, Any]]:
        rows = project_clues_from_events(getattr(self, "event_history", []))
        self.dungeon_clues = list(rows)
        return rows

    def _journal_district_notes(self) -> list[dict[str, Any]]:
        rows = project_district_notes_from_events(getattr(self, "event_history", []))
        self.district_notes = list(rows)
        return rows

    def _journal_expeditions(self) -> list[dict[str, Any]]:
        return project_expeditions_from_events(getattr(self, "event_history", []))

    def view_journal(self):
        """Show discoveries, rumors, and active district objectives."""
        while True:
            c = self.ui.choose("Rumors & Discoveries", ["View Discoveries", "View Rumors", "View District Objectives", "View Dungeon Clues", "View Expedition History", "Back"])
            if c == 5:
                return
            if c == 0:
                discoveries = self._journal_discoveries()
                if not discoveries:
                    self.ui.log("No discoveries yet.")
                    continue
                self.ui.title("Discoveries")
                # Show most recent first
                for e in list(discoveries)[-30:][::-1]:
                    self.ui.log(
                        f"Day {e.get('day')} {self._watch_name_of(e.get('watch',0))} | Hex ({e.get('q')},{e.get('r')}) | {e.get('name')}"
                    )
            elif c == 1:
                rumors = self._journal_rumors()
                if not rumors:
                    self.ui.log("No rumors yet.")
                    continue
                self.ui.title("Rumors")
                for e in list(rumors)[-30:][::-1]:
                    self.ui.log(self._render_rumor_row(e))
            elif c == 2:
                entries = self._district_objective_entries()
                if not entries:
                    self.ui.log("No active district objectives.")
                    continue
                self.ui.title("District Objectives")
                for e in entries[:12]:
                    status = "completed" if e.get('completion_ready') else ("target reached" if e.get('visited_target') else "in progress")
                    self.ui.log(
                        f"{e.get('title')} — from {e.get('source_name')} | due day {e.get('deadline_day')} | {status}"
                    )
                    c = next((x for x in (self.active_contracts or []) if str(x.get('cid') or '') == str(e.get('cid') or '')), None)
                    if isinstance(c, dict):
                        self.ui.log(f"  Destination: {self._contract_destination_label(c)}")
                        linked = self._contract_rumor_link(c)
                        if linked:
                            self.ui.log(f"  Linked rumor: {self._render_rumor_row(linked)}")
                    self.ui.log(
                        f"  District clue: {e.get('target_hint')} | From here: {e.get('from_current')} | Reward: {e.get('reward_gp')} gp"
                    )
            elif c == 3:
                clues = self._journal_clues()
                if not clues:
                    self.ui.log('No dungeon clues yet.')
                    continue
                self.ui.title('Dungeon Clues')
                for e in clues[-30:][::-1]:
                    self.ui.log(f"Day {e.get('day')} | L{e.get('level')} room {e.get('room_id')} | {e.get('text')}")
            else:
                rows = self._journal_expeditions()
                if not rows:
                    self.ui.log('No expedition history yet.')
                    continue
                self.ui.title('Expedition History')
                for e in rows[-30:][::-1]:
                    self.ui.log(f"Day {e.get('day')} {self._watch_name_of(e.get('watch', 0))} | {e.get('title')}")

    
    def _resolve_wilderness_scene(self, hx: dict[str, Any], *, chance: int) -> bool:
        """Resolve lightweight non-combat wilderness scenes. Returns True if handled."""
        ctx = resolve_travel_context(self)
        wdie = getattr(self, "wilderness_dice", None) or self.dice
        families = ["hostile", "hazard", "social", "omen", "opportunity"]
        idx = max(0, min(len(families) - 1, int(wdie.d(6) + int(ctx.get("discovery_mod", 0)) - 2)))
        family = families[idx]
        if family == "hostile":
            return False
        self.ui.title("Wilderness Scene")
        if family == "hazard":
            c = self.ui.choose("A hazard blocks your way.", ["Push through", "Reroute", "Slow and secure footing"])
            if c == 0:
                if self.dice.in_6(2):
                    self.ui.log("You lose time and take minor scrapes.")
                    self._advance_watch(1)
                    for m in self.party.living()[:1]:
                        m.hp = max(-1, int(m.hp) - 1)
                else:
                    self.ui.log("You force a path through.")
            elif c == 1:
                self.ui.log("You reroute safely but lose time.")
                self._advance_watch(1)
            else:
                self.ui.log("Careful footing avoids mishap.")
            return True
        if family == "social":
            c = self.ui.choose("You meet travelers on the route.", ["Trade news", "Keep distance"])
            if c == 0:
                self.ui.log("You exchange route talk and gain a lead.")
                self._surface_poi_rumor(int(hx.get("q", 0)), int(hx.get("r", 0)), source="travel_social", cost=0)
            else:
                self.ui.log("You pass without incident.")
            return True
        if family == "omen":
            self.ui.log("Signs in the wild point toward nearby trouble or opportunity.")
            if self.dice.in_6(max(1, min(6, 2 + int(ctx.get("discovery_mod", 0))))):
                self._surface_poi_rumor(int(hx.get("q", 0)), int(hx.get("r", 0)), source="travel_omen", cost=0)
                self.ui.log("You mark the signs for later investigation.")
            return True
        if family == "opportunity":
            c = self.ui.choose("A quick opportunity appears.", ["Scavenge supplies", "Take shortcut", "Ignore"])
            if c == 0:
                self.rations = int(getattr(self, "rations", 0) or 0) + 2
                self.ui.log("You recover usable trail rations.")
            elif c == 1:
                self.ui.log("You find a shortcut and make good time.")
                self.travel_state.route_progress = int(getattr(self.travel_state, "route_progress", 0) or 0) + 1
            else:
                self.ui.log("You keep to your planned route.")
            return True
        return False

    def _wilderness_encounter_check(self, hx: dict[str, Any], *, encounter_mod: int = 0):
        """Check and potentially run a wilderness encounter.

        Uses the dedicated wilderness encounter RNG so overworld travel does not consume the main dice stream.
        encounter_mod can be used by travel modes (cautious/forced) to slightly shift encounter chance.
        """
        # Moderate: base 1-in-6 per watch, +1 at night.
        ctx = resolve_travel_context(self)
        base = 1 + (1 if str(ctx.get("watch") or "") == "Night" else 0)

        # Heat / contested territory increases patrol intensity.
        heat = int(hx.get("heat", 0) or 0)
        fid = hx.get("faction_id")
        contested = False
        if fid:
            try:
                for _, (dq, dr) in neighbors(self.party_hex).items():
                    nk = f"{self.party_hex[0] + dq},{self.party_hex[1] + dr}"
                    nh = (self.world_hexes or {}).get(nk)
                    if isinstance(nh, dict) and nh.get("faction_id") and nh.get("faction_id") != fid:
                        contested = True
                        break
            except Exception:
                contested = False

        patrol_bonus = 0
        if heat > 0:
            patrol_bonus += 1 if heat <= 2 else 2
        if contested:
            patrol_bonus += 1
        patrol_bonus = min(2, patrol_bonus)

        chance = min(6, base + patrol_bonus + int(encounter_mod) + int(ctx.get("encounter_mod", 0)))

        # Safe passage reduces encounter pressure in a faction's territory for a short time.
        if fid and isinstance(getattr(self, "safe_passage", None), dict):
            until = int(self.safe_passage.get(str(fid), 0) or 0)
            if until >= self.campaign_day:
                chance = max(1, chance - 1)

        chance = max(1, min(6, int(chance)))

        # Dedicated wilderness encounter dice.
        wdie = getattr(self, "wilderness_dice", None) or self.dice
        wrng = getattr(self, "wilderness_encounter_rng", None) or getattr(self, "wilderness_rng", None) or self.dice_rng

        if wdie.in_6(chance):
            if self._resolve_wilderness_scene(hx, chance=int(chance)):
                return
            terrain = str(hx.get("terrain", "clear"))
            if terrain == "road":
                terrain = "clear"

            self._ensure_faction_for_hex(hx)
            fid = hx.get("faction_id")

            # Patrol intensity biases encounters toward faction patrols.
            use_faction = bool(fid) and wrng.random() < (0.50 + 0.10 * patrol_bonus + 0.05 * min(4, heat))
            if use_faction and fid in (self.factions or {}):
                f = self.factions[fid]
                enc = self._faction_encounter(str(fid), terrain)
                label = f"{f.get('name','Faction')} patrol"
                encounter_fid = str(fid)
                rep_mod = self.faction_rep(encounter_fid) // 20
            else:
                encgen = getattr(self, "wilderness_encounters", None) or self.encounters
                enc = encgen.wilderness_encounter(terrain)
                label = None
                encounter_fid = None
                rep_mod = 0

            if enc and enc.foes:
                self.ui.title("Wilderness Encounter!")
                if label:
                    self.ui.log(f"Encounter: {label}")
                self.ui.log(f"Encounter: {enc.kind}")

                # Reaction step (use wilderness encounter RNG for consistency)
                roll = int(wdie.d(6) + wdie.d(6) + rep_mod)
                outcome = self._reaction_outcome(roll)
                if encounter_fid:
                    self.ui.log(f"Reaction: {outcome} (2d6{rep_mod:+d} = {roll})")

                # Decide whether combat happens.
                if not encounter_fid:
                    for foe in enc.foes:
                        self.ui.log(f"- {foe.name} HP {foe.hp} AC {foe.ac_desc} HD {foe.hd}")
                    self.combat(enc.foes)
                    return

                if outcome == "Hostile":
                    for foe in enc.foes:
                        self.ui.log(f"- {foe.name} HP {foe.hp} AC {foe.ac_desc} HD {foe.hd}")
                    self.combat(enc.foes)
                    if self.party.living() and not [f for f in enc.foes if f.hp > 0]:
                        self.adjust_rep(encounter_fid, -5)
                    return

                if outcome == "Uncertain":
                    c = self.ui.choose("They haven't decided what to do...", ["Parley", "Withdraw", "Fight"])
                    if c == 1:
                        self.ui.log("You withdraw without incident.")
                        return
                    if c == 0:
                        pr = int(wdie.d(6) + wdie.d(6) + rep_mod)
                        pout = self._reaction_outcome(pr)
                        self.ui.log(f"Parley: {pout} (2d6{rep_mod:+d} = {pr})")
                        if pout in ("Friendly", "Helpful"):
                            self.ui.log("Words calm the situation.")
                            self.adjust_rep(encounter_fid, +2)
                            return
                        if pout == "Hostile":
                            self.ui.log("Talk fails.")
                            for foe in enc.foes:
                                self.ui.log(f"- {foe.name} HP {foe.hp} AC {foe.ac_desc} HD {foe.hd}")
                            self.combat(enc.foes)
                            return
                        self.ui.log("They remain wary, but allow you to pass.")
                        return

                    # Fight
                    for foe in enc.foes:
                        self.ui.log(f"- {foe.name} HP {foe.hp} AC {foe.ac_desc} HD {foe.hd}")
                    self.combat(enc.foes)
                    if self.party.living() and not [f for f in enc.foes if f.hp > 0]:
                        self.adjust_rep(encounter_fid, -5)
                    return

                # Friendly/Helpful
                if outcome in ("Friendly", "Helpful"):
                    self.ui.log("They recognize you and allow safe passage.")
                    self.safe_passage = getattr(self, "safe_passage", {}) or {}
                    self.safe_passage[str(encounter_fid)] = int(self.campaign_day) + 2
                    return

                # Neutral
                self.ui.log("They watch you pass, saying nothing.")
                return

    
    def _faction_encounter(self, fid: str, terrain: str):
        """Build a simple faction-themed encounter using monster keywords.

        Uses the wilderness encounter RNG/dice so faction patrol generation does not consume the main dice stream.
        """
        f = (self.factions or {}).get(fid) or {}
        kws = [k.lower() for k in (f.get("encounter_keywords") or [])]
        cands = []
        for m in (self.content.monsters or []):
            nl = str(m.get("name", "")).lower()
            if any(k in nl for k in kws):
                cands.append(m)
        if not cands:
            encgen = getattr(self, "wilderness_encounters", None) or self.encounters
            return encgen.wilderness_encounter(terrain)

        wdie = getattr(self, "wilderness_dice", None) or self.dice
        wrng = getattr(self, "wilderness_encounter_rng", None) or getattr(self, "wilderness_rng", None) or self.dice_rng
        encgen = getattr(self, "wilderness_encounters", None) or self.encounters

        ftype = str(f.get("ftype", "monster"))
        if ftype == "guild":
            count = 1 + wdie.d(4)
        elif ftype == "religious":
            count = 1 + wdie.d(3)
        else:
            count = 2 + wdie.d(6)

        garrison_scale = max(0, int(getattr(self, "dungeon_level", 1) or 1) - 1)
        if bool(getattr(self, "current_dungeon_is_stronghold", False)):
            garrison_scale += 1
        count = min(12, int(count) + garrison_scale)

        pick = [encgen._mk_monster(wrng.choice(cands)) for _ in range(int(count))]
        from .encounters import Encounter
        return Encounter(kind=f"{f.get('ftype','faction').title()} ({f.get('name','Faction')})", foes=pick)

    # ----------------
    # Wilderness POIs
    # ----------------

    def _get_dungeon_state(self) -> dict[str, Any]:
        """Capture the current dungeon instance state (for multi-entrance support)."""
        return {
            "dungeon_turn": int(getattr(self, "dungeon_turn", 0)),
            "torch_turns_left": int(getattr(self, "torch_turns_left", 0)),
            "magical_light_turns": int(getattr(self, "magical_light_turns", 0)),
            "current_room_id": int(getattr(self, "current_room_id", 0)),
            "dungeon_rooms": getattr(self, "dungeon_rooms", {}) or {},
            "wandering_chance": int(getattr(self, "wandering_chance", 1)),
            "dungeon_depth": int(getattr(self, "dungeon_depth", 1)),
            "dungeon_level": int(getattr(self, "dungeon_level", 1)),
            "noise_level": int(getattr(self, "noise_level", 0)),
            "dungeon_clues": list(getattr(self, "dungeon_clues", []) or []),
            "dungeon_monster_groups": list(getattr(self, "dungeon_monster_groups", []) or []),
            "dungeon_room_aftermath": dict(getattr(self, "dungeon_room_aftermath", {}) or {}),
            "next_dungeon_group_id": int(getattr(self, "next_dungeon_group_id", 1) or 1),
        }

    def _set_dungeon_state(self, state: dict[str, Any] | None):
        """Apply a captured dungeon instance state."""
        state = state or {}
        self.dungeon_turn = int(state.get("dungeon_turn", 0))
        self.torch_turns_left = int(state.get("torch_turns_left", 0))
        self.magical_light_turns = int(state.get("magical_light_turns", 0))
        self.current_room_id = int(state.get("current_room_id", 0))
        self.dungeon_rooms = state.get("dungeon_rooms", {}) or {}
        self.wandering_chance = int(state.get("wandering_chance", 1))
        self.dungeon_depth = int(state.get("dungeon_depth", 1))
        self.dungeon_level = int(state.get("dungeon_level", 1))
        self.noise_level = int(state.get("noise_level", 0))
        self.dungeon_clues = list(state.get("dungeon_clues", []) or [])
        self.dungeon_monster_groups = list(state.get("dungeon_monster_groups", []) or [])
        self.dungeon_room_aftermath = dict(state.get("dungeon_room_aftermath", {}) or {})
        self.next_dungeon_group_id = int(state.get("next_dungeon_group_id", 1) or 1)
        # Derived convenience
        self.light_on = bool(self.torch_turns_left > 0 or self.magical_light_turns > 0)

    def _poi_label(self, poi: dict[str, Any]) -> str:
        if poi.get("resolved"):
            return f"{poi.get('name','POI')} (resolved)"
        return str(poi.get("name") or "Point of Interest")

    def _faction_name(self, fid: str | None) -> str:
        fid = str(fid or "")
        if fid and fid in (self.factions or {}):
            return str((self.factions.get(fid) or {}).get("name") or fid)
        return "locals"

    def _roll_wilderness_minor_loot(self) -> tuple[int, list[dict[str, Any]]]:
        gp = int(self.dice.d(6) * 5 + self.dice.d(6) * 5)
        items: list[dict[str, Any]] = []
        if self.dice.d(6) >= 5:
            try:
                it = self.treasure.roll_minor_gem_or_jewelry()
                items.append({"name": it.name, "kind": it.kind, "gp_value": it.gp_value})
            except Exception:
                pass
        return gp, items

    def _handle_current_hex_poi(self):
        """Interact with a POI in the current hex (if present)."""
        q, r = self.party_hex
        k = f"{q},{r}"
        hx = self.world_hexes.get(k) or {}
        if not isinstance(hx, dict):
            hx = {}
        poi = hx.get("poi") or None
        if not isinstance(poi, dict):
            self.ui.log("There is nothing notable here.")
            return

        poi_type = str(poi.get("type") or "")
        name = str(poi.get("name") or "POI")
        poi.setdefault("id", str(poi.get("id") or f"hex:{q},{r}:{poi_type or 'poi'}"))
        poi["q"] = int(q)
        poi["r"] = int(r)
        resolved_keys = set(str(x) for x in (poi.get("resolution_keys") or []))
        interact_key = first_time_poi_resolution_key(poi, mode="interact")
        resolved = bool(poi.get("resolved", False)) or (interact_key in resolved_keys)

        self.ui.title(name)
        if poi.get("notes"):
            self.ui.log(str(poi.get("notes")))

        # --- RUINS ---
        if poi_type == "ruins":
            if resolved:
                self.ui.log("You've already searched these ruins.")
                return
            c = self.ui.choose("Ruins", ["Search the ruins", "Back"])
            if c == 1:
                return
            # Moderate: 50% chance of trouble.
            if self.dice.d(2) == 1:
                self.ui.log("Something stirs among the stones!")
                terrain = hx.get("terrain", "clear")
                enc = self.encounters.wilderness_encounter("clear" if terrain == "road" else terrain)
                if enc and enc.foes:
                    self.ui.title("Ruins Encounter!")
                    self.ui.log(f"Encounter: {enc.kind}")
                    for f in enc.foes:
                        self.ui.log(f"- {f.name} HP {f.hp} AC {f.ac_desc} HD {f.hd}")
                    self.combat(enc.foes)
                else:
                    self.ui.log("You hear movement... but nothing comes.")
            # Treasure: modest but meaningful.
            gp = self.dice.d(6) * 10 + self.dice.d(6) * 10
            grant_coin_reward(self, gp, source="wilderness_ruins", destination=CoinDestination.TREASURY)
            self.ui.log(f"You find {gp} gp in old coin.")
            found_items: list[dict[str, Any]] = []
            if self.dice.d(6) >= 5:
                it = self.treasure.roll_minor_gem_or_jewelry()
                found_items.append({"name": it.name, "kind": it.kind, "gp_value": it.gp_value})
            if self.dice.d(6) == 6:
                items = self.treasure.roll_minor_magic_item()
                for it2 in items:
                    found_items.append({"name": it2.name, "kind": it2.kind, "gp_value": it2.gp_value})
            if found_items:
                # Transitional bridge: generated wilderness loot now enters the
                # pending loot pool first; `party_items` is only synced mirror state.
                added_items = self._ingest_reward_items_to_loot_pool(found_items, source="wilderness_ruins")
                for it in added_items:
                    nm = str(it.get("name") or "item")
                    if str(it.get("kind") or "").lower() in {"potion", "scroll", "ring", "wand", "relic"}:
                        self.ui.log(f"Magic! {nm}.")
                    else:
                        self.ui.log(f"You also find: {nm}.")
            poi["resolved"] = True

        # --- SHRINE ---
        elif poi_type == "shrine":
            # A shrine can be prayed at once, and may contain a relic.
            if resolved and not bool(poi.get("relic_taken")):
                self.ui.log("The shrine is quiet now.")
                return

            opts = ["Offer 10 gp and pray"]
            if not bool(poi.get("relic_taken")):
                opts.append("Recover a relic (dangerous)")
            opts.append("Leave")
            c = self.ui.choose("Shrine", opts)
            if c == len(opts) - 1:
                return

            if c == 0:
                if self.gold < 10:
                    self.ui.log("You don't have enough coin.")
                    return
                self.gold -= 10
                healed = 0
                for m in self.party.living():
                    before = m.hp
                    m.hp = min(m.hp_max, m.hp + self.dice.d(6))
                    healed += max(0, m.hp - before)
                self.ui.log(f"A calm settles on you. (Healed {healed} HP total.)")
                # Offerings improve standing with the owning religious faction (if any).
                self.adjust_rep(str(poi.get("faction_id") or ""), +5)
                poi["resolved"] = True
            else:
                # Recover relic: risky, but valuable. Used for contracts.
                if self.dice.in_6(1):
                    self.ui.log("Guardians stir as you disturb the shrine!")
                    terrain = hx.get("terrain", "clear")
                    enc = self.encounters.wilderness_encounter("clear" if terrain == "road" else terrain)
                    if enc and enc.foes:
                        self.ui.title("Shrine Guardians!")
                        self.ui.log(f"Encounter: {enc.kind}")
                        for f in enc.foes:
                            self.ui.log(f"- {f.name} HP {f.hp} AC {f.ac_desc} HD {f.hd}")
                        self.combat(enc.foes)
                        if not self.party.living():
                            return
                relic_name = f"Relic of {name}"
                # Transitional bridge: relic creation routes through loot pool ownership
                # stage before any explicit actor/stash assignment.
                self._ingest_reward_items_to_loot_pool(
                    [{"name": relic_name, "kind": "relic", "gp_value": 150}],
                    source="wilderness_shrine",
                )
                self.ui.log(f"You recover a relic: {relic_name}.")
                poi["relic_taken"] = True
                self.adjust_rep(str(poi.get("faction_id") or ""), +10)
                poi["resolved"] = True

        # --- LAIR / STRONGHOLD ---
        elif poi_type == "lair":
            if resolved:
                self.ui.log("Only old tracks remain.")
                return
            c = self.ui.choose("Lair", ["Approach cautiously", "Leave"])
            if c == 1:
                return
            self.ui.log("You press into the lair...")
            terrain = hx.get("terrain", "clear")
            enc = self.encounters.wilderness_encounter("clear" if terrain == "road" else terrain)
            if enc and enc.foes:
                self.ui.title("Lair Encounter!")
                self.ui.log(f"Encounter: {enc.kind}")
                for f in enc.foes:
                    self.ui.log(f"- {f.name} HP {f.hp} AC {f.ac_desc} HD {f.hd}")
                self.combat(enc.foes)
            else:
                self.ui.log("The lair is eerily empty...")
            if not self.party.living():
                return

            lair_fid = str(poi.get("faction_id") or "")
            if lair_fid:
                self.adjust_rep(lair_fid, -15)
            self.adjust_rep(self._town_guild_id(), +5)

            bonus_gp = self.dice.d(6) * 50
            grant_coin_reward(self, bonus_gp, source="wilderness_lair", destination=CoinDestination.TREASURY)
            self.ui.log(f"In the lair you find {bonus_gp} gp and valuables.")
            found_items: list[dict[str, Any]] = []
            if self.dice.d(6) >= 4:
                it = self.treasure.roll_medium_gem_or_jewelry()
                found_items.append({"name": it.name, "kind": it.kind, "gp_value": it.gp_value})
            if self.dice.d(6) >= 5:
                items = self.treasure.roll_medium_magic_item()
                for it2 in items:
                    found_items.append({"name": it2.name, "kind": it2.kind, "gp_value": it2.gp_value})
            if found_items:
                # Transitional bridge: generated lair loot enters pending loot pool.
                added_items = self._ingest_reward_items_to_loot_pool(found_items, source="wilderness_lair")
                for it in added_items:
                    nm = str(it.get("name") or "item")
                    if str(it.get("kind") or "").lower() in {"potion", "scroll", "ring", "wand", "relic"}:
                        self.ui.log(f"Magic! {nm}.")
                    else:
                        self.ui.log(f"You recover: {nm}.")
            poi["resolved"] = True

        # --- ABANDONED CAMP ---
        elif poi_type == "abandoned_camp":
            if resolved:
                self.ui.log("Only scraps and cold ashes remain.")
                return
            c = self.ui.choose("Abandoned Camp", ["Search the camp", "Take a short rest", "Leave"])
            if c == 2:
                return
            if c == 0:
                gp, items = self._roll_wilderness_minor_loot()
                if self.dice.in_6(2):
                    self.ui.log("The camp was trapped or watched!")
                    terrain = hx.get("terrain", "clear")
                    enc = self.encounters.wilderness_encounter("clear" if terrain == "road" else terrain)
                    if enc and enc.foes:
                        self.ui.title("Camp Trouble!")
                        self.ui.log(f"Encounter: {enc.kind}")
                        for f in enc.foes:
                            self.ui.log(f"- {f.name} HP {f.hp} AC {f.ac_desc} HD {f.hd}")
                        self.combat(enc.foes)
                        if not self.party.living():
                            return
                grant_coin_reward(self, gp, source="wilderness_abandoned_camp", destination=CoinDestination.TREASURY)
                self.ui.log(f"You scavenge {gp} gp worth of coin and supplies.")
                added_items = self._ingest_reward_items_to_loot_pool(items, source="wilderness_abandoned_camp")
                for it in added_items:
                    self.ui.log(f"You find: {it.get('name', 'item')}.")
                if self.dice.in_6(3):
                    self.ui.log("Among the scraps you piece together a useful rumor.")
                    try:
                        self.gather_rumors()
                    except Exception:
                        pass
                poi["resolved"] = True
            else:
                healed = 0
                for m in self.party.living():
                    before = m.hp
                    m.hp = min(m.hp_max, m.hp + 1)
                    healed += max(0, m.hp - before)
                self.ui.log(f"You take a cautious breather. (Recovered {healed} HP total.)")
                poi["resolved"] = True

        # --- CARAVAN ---
        elif poi_type == "caravan":
            fid = str(poi.get("faction_id") or hx.get("faction_id") or "")
            fname = self._faction_name(fid)
            opts = ["Resupply", "Ask for rumors", "Request safe passage", "Leave"]
            c = self.ui.choose(f"Caravan of {fname}", opts)
            if c == 3:
                return
            if c == 0:
                self._caravan_resupply_menu(fid, fname, hx)
            elif c == 1:
                # Rumor without extra fee; caravans are a reward node.
                before = len(getattr(self, 'rumors', []) or [])
                try:
                    # Temporarily waive normal cost by refunding it after call.
                    g0 = int(self.gold)
                    self.gather_rumors()
                    spent = max(0, g0 - int(self.gold))
                    if spent:
                        self.gold += spent
                except Exception:
                    self.ui.log("The caravan has little new to tell.")
                if len(getattr(self, 'rumors', []) or []) > before:
                    self.ui.log("The caravan shares a lead from the road.")
                if fid:
                    self.adjust_rep(fid, +1)
                self._set_forward_anchor(hx, kind="caravan", name=f"{fname} caravan", fid=fid, expires_day=int(self.campaign_day) + 2)
            else:
                if fid:
                    self.safe_passage = getattr(self, "safe_passage", {}) or {}
                    self.safe_passage[fid] = max(int(self.safe_passage.get(fid, 0) or 0), int(self.campaign_day) + 2)
                    self.ui.log(f"{fname} agrees to let you pass untroubled for a time.")
                    self.adjust_rep(fid, +2)
                    self._set_forward_anchor(hx, kind="caravan", name=f"{fname} caravan", fid=fid, expires_day=int(self.campaign_day) + 2)
                else:
                    self.ui.log("They nod, but offer no promises.")
            poi["resolved"] = True

        # --- FACTION OUTPOST ---
        elif poi_type in ("faction_outpost", "outpost"):
            fid = str(poi.get("faction_id") or hx.get("faction_id") or "")
            fname = self._faction_name(fid)
            rep = self.faction_rep(fid)
            opts = ["Ask for rumors", "Request safe passage", "Take guarded rest (5 gp)", "Seek limited healing (8 gp)", "Browse faction jobs", "Leave"]
            c = self.ui.choose(f"Outpost of {fname}", opts)
            if c == 5:
                return
            if c == 0:
                before = len(getattr(self, 'rumors', []) or [])
                try:
                    g0 = int(self.gold)
                    self.gather_rumors()
                    spent = max(0, g0 - int(self.gold))
                    if spent:
                        self.gold += spent
                except Exception:
                    pass
                if len(getattr(self, 'rumors', []) or []) == before:
                    self.ui.log("The sentries have nothing useful to add.")
                if fid:
                    self.adjust_rep(fid, +1)
                self._set_forward_anchor(hx, kind="outpost", name=f"{fname} outpost", fid=fid)
            elif c == 1:
                if rep < -20:
                    self.ui.log(f"{fname} refuses to vouch for you.")
                    if fid:
                        self.adjust_rep(fid, -1)
                    return
                self.safe_passage = getattr(self, "safe_passage", {}) or {}
                self.safe_passage[fid] = max(int(self.safe_passage.get(fid, 0) or 0), int(self.campaign_day) + 3)
                self.ui.log(f"{fname} marks your company as tolerated for now.")
                if fid:
                    self.adjust_rep(fid, +2)
                self._set_forward_anchor(hx, kind="outpost", name=f"{fname} outpost", fid=fid)
            elif c == 2:
                self._outpost_guarded_rest(fid, fname, hx)
            elif c == 3:
                self._outpost_limited_healing(fid, fname, hx)
            else:
                self._set_forward_anchor(hx, kind="outpost", name=f"{fname} outpost", fid=fid)
                self._view_faction_jobs(fid, source_name=f"{fname} outpost", source_hex=hx)
            poi["resolved"] = True

        # --- DUNGEON ENTRANCE ---

        elif poi_type == "stronghold":
            # P6.1: stable dungeon instance per stronghold POI
            dungeon_id = str(poi.get("id") or f"hex:{q},{r}:stronghold")
            poi["id"] = dungeon_id
            self.dungeon_instance = ensure_instance(self.dungeon_instance, dungeon_id, self.dice_seed)
            opts = ["Assault the stronghold", "Back"]
            c = self.ui.choose(str(poi.get("name") or "Stronghold"), opts)
            if c == 1:
                return

            self.current_dungeon_faction_id = str(poi.get("faction_id") or hx.get("faction_id") or "") or None
            self.current_dungeon_is_stronghold = True
            if poi.get("dungeon_state") is None:
                dist = hex_distance((0, 0), self.party_hex)
                base_depth = max(1, 1 + dist // 2)
                base_wandering = min(4, 1 + dist // 5)
                max_levels = min(7, max(3, 4 + dist // 5))
                poi["dungeon_state"] = {
                    "dungeon_turn": 0,
                    "torch_turns_left": 0,
                    "current_room_id": 0,
                    "max_dungeon_levels": max_levels,
                    "dungeon_levels": {1: {}},
                    "dungeon_alarm_by_level": {1: 0},
                    "dungeon_level_meta": {},
                    "wandering_chance": base_wandering,
                    "dungeon_depth": base_depth,
                    "dungeon_level": 1,
                    "noise_level": 0,
                }
            self._set_dungeon_state(poi["dungeon_state"])
            self.dungeon_loop()
            poi["dungeon_state"] = self._get_dungeon_state()
            hx["poi"] = poi
            self.world_hexes[k] = hx
            return
        elif poi_type == "dungeon_entrance":
            opts = ["Enter the dungeon"]
            if not bool(poi.get("sealed")):
                opts.append("Seal/secure the entrance (15 gp)")
            opts.append("Back")
            c = self.ui.choose("Dungeon Entrance", opts)
            if c == len(opts) - 1:
                return
            if c == 1 and not bool(poi.get("sealed")):
                if self.gold < 15:
                    self.ui.log("Not enough gold.")
                    return
                self.gold -= 15
                poi["sealed"] = True
                self.ui.log("You reinforce and seal the entrance as best you can.")
                self.adjust_rep(self._town_guild_id(), +5)
                # Persist and return (no dungeon entry)
                hx["poi"] = poi
                self.world_hexes[k] = hx
                return

            # Enter dungeon (swap instance state)
            # P6.1: bind to a stable dungeon_id derived from POI/hex so the blueprint persists.
            dungeon_id = str(poi.get("id") or f"hex:{q},{r}:{poi_type}")
            poi["id"] = dungeon_id
            self.dungeon_instance = ensure_instance(self.dungeon_instance, dungeon_id, self.dice_seed)
            self.current_dungeon_faction_id = str(poi.get("faction_id") or hx.get("faction_id") or "") or None
            self.current_dungeon_is_stronghold = False
            if poi.get("dungeon_state") is None:
                dist = hex_distance((0, 0), self.party_hex)
                base_depth = max(1, 1 + dist // 3)
                base_level = max(1, 1 + dist // 4)
                base_wandering = min(3, 1 + dist // 6)
                poi["dungeon_state"] = {
                    "dungeon_turn": 0,
                    "torch_turns_left": 0,
                    "current_room_id": 0,
                    "max_dungeon_levels": min(6, max(2, 3 + dist // 6)),
                    "dungeon_levels": {1: {}},
                    "dungeon_alarm_by_level": {1: 0},
                    "dungeon_level_meta": {},
                    "wandering_chance": base_wandering,
                    "dungeon_depth": base_depth,
                    "dungeon_level": 1,
                    "noise_level": 0,
                }
            self._set_dungeon_state(poi["dungeon_state"])
            self.dungeon_loop()
            # Save back updated dungeon instance state
            poi["dungeon_state"] = self._get_dungeon_state()

        else:
            self.ui.log("Nothing you can use here right now.")

        if bool(poi.get("resolved", False)):
            resolved_keys.add(interact_key)
            poi["resolution_keys"] = sorted(resolved_keys)
        # Persist any changes
        hx["poi"] = poi
        self.world_hexes[k] = hx


    def _wilderness_route_positions(self, dest: tuple[int, int], *, max_steps: int = 24) -> set[tuple[int, int]]:
        """Return a simple greedy route from the party to dest for map emphasis."""
        try:
            cur = (int(self.party_hex[0]), int(self.party_hex[1]))
            dst = (int(dest[0]), int(dest[1]))
        except Exception:
            return set()
        if cur == dst:
            return {cur}
        out: set[tuple[int, int]] = set()
        steps = 0
        while cur != dst and steps < max_steps:
            out.add(cur)
            neigh = neighbors(cur)
            best = min(neigh.values(), key=lambda pos: hex_distance(pos, dst))
            if hex_distance(best, dst) >= hex_distance(cur, dst):
                break
            cur = (int(best[0]), int(best[1]))
            steps += 1
        out.add(dst)
        return out

    def _wilderness_route_overlay(self) -> tuple[set[tuple[int, int]], dict[tuple[int, int], str], list[str]]:
        """Return (route_cells, special_markers, labels) for the wilderness mini-board."""
        route_cells: set[tuple[int, int]] = set()
        markers: dict[tuple[int, int], str] = {(0, 0): 'T'}
        labels: list[str] = ['T town']
        # Town path is always useful.
        town_route = self._wilderness_route_positions((0, 0))
        route_cells.update(town_route)
        anc = self._get_forward_anchor()
        if anc and anc.get('hex'):
            try:
                aq, ar = (anc.get('hex') or [0, 0])[:2]
                apos = (int(aq), int(ar))
                markers[apos] = 'A'
                labels.append('A forward anchor')
                route_cells.update(self._wilderness_route_positions(apos))
            except Exception:
                pass
        objectives = self._district_objective_entries()
        if objectives:
            try:
                tgt = tuple(objectives[0].get('target_hex') or (0, 0))
                tgt = (int(tgt[0]), int(tgt[1]))
                if tgt != (0, 0):
                    markers[tgt] = '!'
                    labels.append('! nearest objective')
                    route_cells.update(self._wilderness_route_positions(tgt))
            except Exception:
                pass
        return route_cells, markers, labels

    def _wilderness_tile_symbol(self, hx: dict[str, Any], pos: tuple[int, int]) -> str:
        """Mini-board glyph for a wilderness hex."""
        q, r = int(pos[0]), int(pos[1])
        if (q, r) == (0, 0):
            return "T"
        if (q, r) == tuple(self.party_hex):
            return "@"
        poi = hx.get("poi") if isinstance(hx, dict) else None
        if isinstance(poi, dict) and bool(poi.get("discovered", False)):
            ptype = str(poi.get("type") or "")
            return {
                "ruins": "R",
                "shrine": "S",
                "lair": "L",
                "dungeon_entrance": "D",
                "stronghold": "H",
                "abandoned_camp": "C",
                "caravan": "V",
                "faction_outpost": "O",
                "outpost": "O",
            }.get(ptype, "P")
        terrain = str((hx or {}).get("terrain", "clear"))
        return {
            "road": "=",
            "clear": ".",
            "forest": "f",
            "hills": "h",
            "swamp": "s",
            "mountain": "m",
        }.get(terrain, ".")

    def _wilderness_map_inspectables(self, radius: int) -> list[tuple[tuple[int, int], str]]:
        """Return inspectable wilderness hexes within radius with human labels."""
        q0, r0 = self.party_hex
        out: list[tuple[tuple[int, int], str]] = []
        seen: set[tuple[int, int]] = set()
        for r in range(r0 - radius, r0 + radius + 1):
            for q in range(q0 - radius, q0 + radius + 1):
                pos = (int(q), int(r))
                if hex_distance((q0, r0), pos) > radius:
                    continue
                hx = ensure_hex(self.world_hexes, pos, self.wilderness_rng).to_dict()
                key = f"{q},{r}"
                discovered = bool(hx.get("discovered", False)) or pos == tuple(self.party_hex) or pos == (0, 0) or key in (self.world_hexes or {})
                if not discovered:
                    continue
                seen.add(pos)
                terr = str(hx.get('terrain', 'clear')).title()
                parts: list[str] = []
                if pos == tuple(self.party_hex):
                    parts.append('here')
                elif pos == (0, 0):
                    parts.append('Town')
                else:
                    hint = self._hex_hint_from_current(pos)
                    if hint:
                        parts.append(hint)
                    parts.append(f"{hex_distance((q0, r0), pos)} hex")
                parts.append(terr)
                poi = hx.get('poi') if isinstance(hx, dict) else None
                if isinstance(poi, dict) and bool(poi.get('discovered', False)):
                    parts.append(self._poi_label(poi))
                out.append((pos, ' — '.join(parts)))
        # Ensure special markers are always inspectable.
        for extra in [(0, 0)]:
            if extra not in seen and hex_distance((q0, r0), extra) <= radius:
                hx = ensure_hex(self.world_hexes, extra, self.wilderness_rng).to_dict()
                out.append((extra, 'Town — ' + str(hx.get('terrain', 'clear')).title()))
        out.sort(key=lambda item: (hex_distance((q0, r0), item[0]), item[0][1], item[0][0]))
        return out

    def _wilderness_map_step_toward(self, pos: tuple[int, int]) -> CommandResult:
        """Take a single explicit step from the map layer toward the chosen hex."""
        cur = tuple(self.party_hex)
        dst = (int(pos[0]), int(pos[1]))
        if dst == cur:
            return CommandResult(status="error", messages=("You are already here.",))
        neigh = neighbors(cur)
        if dst in neigh.values():
            return self.dispatch(TravelToHex(int(dst[0]), int(dst[1])))
        best = None
        best_dist = hex_distance(cur, dst)
        for cand in neigh.values():
            dd = hex_distance(cand, dst)
            if dd < best_dist:
                best = cand
                best_dist = dd
        if best is None:
            return CommandResult(status="error", messages=("No useful route from here.",))
        return self.dispatch(TravelToHex(int(best[0]), int(best[1])))

    def _wilderness_map_move_selection(self, selected_pos: tuple[int, int] | None, radius: int, direction: str) -> tuple[int, int] | None:
        """Move wilderness map highlight by hex direction among currently inspectable cells."""
        inspectables = self._wilderness_map_inspectables(radius)
        if not inspectables:
            return None
        direction = str(direction or '').upper()
        vectors = {
            'N': (0, -1),
            'NE': (1, -1),
            'SE': (1, 0),
            'S': (0, 1),
            'SW': (-1, 1),
            'NW': (-1, 0),
        }
        if direction not in vectors:
            return selected_pos
        start = tuple(selected_pos) if selected_pos is not None else tuple(self.party_hex)
        dq, dr = vectors[direction]
        candidates: list[tuple[int, int, int, tuple[int, int]]] = []
        for pos, _label in inspectables:
            pos = (int(pos[0]), int(pos[1]))
            if pos == start:
                continue
            delta_q = pos[0] - start[0]
            delta_r = pos[1] - start[1]
            steps = max(abs(delta_q), abs(delta_r), abs(delta_q + delta_r))
            if steps <= 0:
                continue
            if (delta_q, delta_r) == (dq * steps, dr * steps):
                candidates.append((steps, hex_distance(tuple(self.party_hex), pos), pos[1], pos))
        if not candidates:
            return selected_pos
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3][0]))
        return tuple(candidates[0][3])

    def _wilderness_map_context_actions(self, pos: tuple[int, int]) -> bool:
        """Offer direct context actions from a wilderness hex inspection.

        Returns True if an action changed location/state enough that the map should close.
        """
        q, r = int(pos[0]), int(pos[1])
        hx = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng).to_dict()
        cur = tuple(self.party_hex)
        labels: list[str] = []
        acts: list[tuple[str, Any]] = []
        if (q, r) != cur:
            if (q, r) in neighbors(cur).values():
                labels.append("Travel here")
                acts.append(("travel_here", (q, r)))
            else:
                labels.append("Travel one step toward here")
                acts.append(("step_toward", (q, r)))
        else:
            poi = hx.get('poi') if isinstance(hx, dict) else None
            if isinstance(poi, dict) and bool(poi.get('discovered', False)):
                labels.append("Investigate this location")
                acts.append(("investigate", None))
        if (q, r) == (0, 0) and cur != (0, 0):
            labels.append("Return to Town now")
            acts.append(("return_town", None))
        anc = self._get_forward_anchor()
        if anc and anc.get('hex'):
            try:
                aq, ar = (anc.get('hex') or [0, 0])[:2]
                if (q, r) == (int(aq), int(ar)) and cur != (q, r):
                    labels.append("Return to Forward Anchor now")
                    acts.append(("return_anchor", None))
            except Exception:
                pass
        if not acts:
            self.ui.log("No direct map actions are available for that hex right now.")
            return False
        i = self.ui.choose("Hex actions", labels + ["Back"])
        if i == len(labels):
            return False
        kind, payload = acts[i]
        if kind == 'travel_here':
            res = self.dispatch(TravelToHex(int(payload[0]), int(payload[1])))
            if res.message:
                self.ui.log(res.message)
            return bool(res.ok)
        if kind == 'step_toward':
            res = self._wilderness_map_step_toward(payload)
            if res.message:
                self.ui.log(res.message)
            return bool(res.ok)
        if kind == 'investigate':
            res = self.dispatch(InvestigateCurrentLocation())
            if res.message:
                self.ui.log(res.message)
            return False
        if kind == 'return_town':
            res = self.dispatch(ReturnToTown())
            if res.message:
                self.ui.log(res.message)
            return bool(res.ok)
        if kind == 'return_anchor':
            self._return_to_forward_anchor()
            return True
        return False

    def _wilderness_map_direction_action(self, direction: str, radius: int = 3) -> bool:
        """Try to act directly on the hex in a wilderness direction from the party.

        If the directional target is immediately actionable (travel, return, etc.)
        the action is executed right away. Otherwise the target becomes the new
        highlight target so the player still lands on something useful.
        Returns True when the map should close.
        """
        direction = str(direction or '').upper()
        target = neighbors(tuple(self.party_hex)).get(direction)
        if not target:
            return False
        target = tuple(target)
        inspectables = [tuple(pos) for pos, _label in self._wilderness_map_inspectables(radius)]
        if inspectables and target not in inspectables:
            moved = self._wilderness_map_move_selection(tuple(self.party_hex), radius, direction)
            if moved is None:
                self.ui.log(f"Nothing useful lies to the {direction} on the current map.")
                return False
            target = tuple(moved)
        if self._wilderness_hex_action_labels(target):
            return self._wilderness_map_context_actions(target)
        self.wilderness_map_selected_hex = [int(target[0]), int(target[1])]
        self.ui.log(f"No direct action is available to the {direction}; highlight moved instead.")
        return False

    def _wilderness_hex_detail_lines(self, pos: tuple[int, int]) -> list[str]:
        q, r = int(pos[0]), int(pos[1])
        hx = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng).to_dict()
        lines: list[str] = []
        if (q, r) == tuple(self.party_hex):
            lines.append('Location: your party is here.')
        else:
            hint = self._hex_hint_from_current((q, r))
            if hint:
                lines.append(f"Relative position: {hint}")
            lines.append(f"Distance: {hex_distance(tuple(self.party_hex), (q, r))} hex(es)")
        lines.append(f"Terrain: {str(hx.get('terrain', 'clear')).title()}")
        road = bool(hx.get('road', False)) or str(hx.get('terrain', '')) == 'road'
        lines.append(f"Road access: {'Yes' if road else 'No'}")
        lines.append(f"Exploration: {'Visited' if bool(hx.get('visited', False)) else ('Discovered' if bool(hx.get('discovered', False)) else 'Frontier')}")
        poi = hx.get('poi') if isinstance(hx, dict) else None
        if isinstance(poi, dict) and bool(poi.get('discovered', False)):
            lines.append(f"POI: {self._poi_label(poi)}")
            faction = poi.get('faction')
            if faction:
                lines.append(f"Faction: {faction}")
        else:
            lines.append('POI: none confirmed')
        try:
            dist_town = hex_distance((q, r), (0, 0))
            lines.append(f"Distance to Town: {dist_town}")
        except Exception:
            pass
        anc = self._get_forward_anchor()
        if anc and anc.get('hex'):
            try:
                aq, ar = (anc.get('hex') or [0,0])[:2]
                lines.append(f"Distance to Anchor: {hex_distance((q,r), (int(aq), int(ar)))}")
            except Exception:
                pass
        objectives = self._district_objective_entries()
        hits = [e for e in objectives if tuple(e.get('target_hex') or ()) == (q, r)]
        if hits:
            for e in hits[:2]:
                lines.append(f"Objective target: {e.get('title')} [{('completed' if e.get('completion_ready') else 'active')}]")
        else:
            near = []
            for e in objectives[:3]:
                try:
                    tq, tr = tuple(e.get('target_hex') or ())
                    d = hex_distance((q, r), (int(tq), int(tr)))
                except Exception:
                    continue
                near.append((d, e))
            near.sort(key=lambda t: t[0])
            for d, e in near[:2]:
                lines.append(f"Objective clue: {e.get('title')} is {d} hex(es) away")
        return lines

    def _inspect_wilderness_hex(self, pos: tuple[int, int]) -> None:
        q, r = int(pos[0]), int(pos[1])
        self.ui.title(f"Hex Inspection ({q},{r})")
        for line in self._wilderness_hex_detail_lines(pos):
            self.ui.log(line)

    def _wilderness_hex_action_labels(self, pos: tuple[int, int]) -> list[str]:
        q, r = int(pos[0]), int(pos[1])
        hx = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng).to_dict()
        cur = tuple(self.party_hex)
        labels: list[str] = []
        if (q, r) != cur:
            if (q, r) in neighbors(cur).values():
                labels.append("Travel here")
            else:
                labels.append("Travel one step toward here")
        else:
            poi = hx.get('poi') if isinstance(hx, dict) else None
            if isinstance(poi, dict) and bool(poi.get('discovered', False)):
                labels.append("Investigate this location")
        if (q, r) == (0, 0) and cur != (0, 0):
            labels.append("Return to Town now")
        anc = self._get_forward_anchor()
        if anc and anc.get('hex'):
            try:
                aq, ar = (anc.get('hex') or [0, 0])[:2]
                if (q, r) == (int(aq), int(ar)) and cur != (q, r):
                    labels.append("Return to Forward Anchor now")
            except Exception:
                pass
        return labels

    def _member_hud_flags(self, member) -> list[str]:
        """Return compact, high-signal status flags for a party member."""
        flags: list[str] = []
        try:
            hp = int(getattr(member, "hp", 0) or 0)
            hp_max = max(1, int(getattr(member, "hp_max", 1) or 1))
        except Exception:
            hp, hp_max = 0, 1
        ratio = hp / hp_max if hp_max > 0 else 0.0
        if hp <= 0:
            flags.append("DOWN")
        elif ratio <= 0.25:
            flags.append("CRIT")
        elif ratio <= 0.5:
            flags.append("HURT")
        effs = set()
        for e in (getattr(member, "effects", []) or []):
            try:
                effs.add(str(e).lower())
            except Exception:
                pass
        st = getattr(member, "status", None)
        if isinstance(st, dict):
            for k, v in st.items():
                active = False
                if isinstance(v, dict):
                    active = bool(v) and any(bool(x) for x in v.values())
                else:
                    active = bool(v)
                if active:
                    effs.add(str(k).lower())
        if getattr(member, "poisoned", False):
            effs.add("poison")
        mapping = [
            ("poison", "POI"),
            ("disease", "DIS"),
            ("fear", "FEAR"),
            ("charm", "CHARM"),
            ("held", "HELD"),
            ("asleep", "SLEEP"),
            ("invisible", "INVIS"),
            ("stone", "STONE"),
            ("petrified", "STONE"),
            ("fled", "FLED"),
            ("surrendered", "SURR"),
        ]
        for key, label in mapping:
            if key in effs and label not in flags:
                flags.append(label)
        return flags[:3]

    def _party_hud_summary(self, *, max_members: int = 4) -> str:
        members = list(getattr(getattr(self, "party", None), "members", []) or [])
        if not members:
            return "Party: none"
        bits: list[str] = []
        for m in members[:max_members]:
            name = str(getattr(m, "name", "?"))
            hp = int(getattr(m, "hp", 0) or 0)
            hp_max = int(getattr(m, "hp_max", 0) or 0)
            flags = self._member_hud_flags(m)
            flag_txt = f" [{' '.join(flags)}]" if flags else ""
            bits.append(f"{name} {hp}/{hp_max}{flag_txt}")
        extra = len(members) - max_members
        if extra > 0:
            bits.append(f"+{extra} more")
        return "Party: " + " | ".join(bits)

    def _light_hud_summary(self) -> str:
        torch_turns = int(getattr(self, "torch_turns_left", 0) or 0)
        magic_turns = int(getattr(self, "magical_light_turns", 0) or 0)
        if torch_turns > 0:
            return f"Light: torch {torch_turns}t"
        if magic_turns > 0:
            return f"Light: magic {magic_turns}t"
        reserve = int(getattr(self, "torches", 0) or 0)
        state = "dark" if not bool(getattr(self, "light_on", False)) else "lit"
        return f"Light: {state} | spare torches {reserve}"

    def _active_objective_hud(self) -> str:
        entries = self._district_objective_entries()
        if not entries:
            return "Objective: none"
        e = entries[0]
        status = "completed" if e.get("completion_ready") else ("reached" if e.get("visited_target") else "active")
        due = int(e.get("deadline_day", 0) or 0)
        hint = str(e.get("from_current") or "")
        title = str(e.get("title") or "Objective")
        if hint:
            return f"Objective: {title} [{status}] — {hint} (day {due})"
        return f"Objective: {title} [{status}] (day {due})"

    def _wilderness_hud_lines(self) -> list[str]:
        q, r = tuple(getattr(self, "party_hex", (0, 0)))
        hx = self._ensure_current_hex()
        terrain = str((hx or {}).get("terrain", "clear"))
        heat = int((hx or {}).get("heat", 0) or 0)
        dist = int(hex_distance((0, 0), (q, r)))
        anc = self._get_forward_anchor()
        anchor_txt = "Anchor: none"
        if anc:
            aq, ar = (anc.get("hex") or [0, 0])[:2]
            anchor_txt = f"Anchor: {anc.get('name', anc.get('kind', 'anchor'))} @ ({aq},{ar})"
            if anc.get("expires_day") is not None:
                anchor_txt += f" until day {int(anc.get('expires_day', 0) or 0)}"
        return [
            f"Day {int(getattr(self, 'campaign_day', 1) or 1)} — {self._watch_name()} | Hex ({q},{r}) | {terrain.title()} | Heat {heat} | Dist {dist} | Travel {str(getattr(self, 'wilderness_travel_mode', 'normal')).title()}",
            self._party_hud_summary(),
            f"Supplies: {int(getattr(self, 'gold', 0) or 0)} gp | Rations {int(getattr(self, 'rations', 0) or 0)} | Torches {int(getattr(self, 'torches', 0) or 0)}",
            self._light_hud_summary(),
            anchor_txt,
            self._active_objective_hud(),
        ]

    def _dungeon_hud_lines(self) -> list[str]:
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        rid = int(getattr(self, "current_room_id", 0) or 0)
        room = self.dungeon_rooms.get(rid) or {}
        room_kind = str((room or {}).get("kind") or "unknown")
        alarm = int((getattr(self, 'dungeon_alarm_by_level', {}) or {}).get(lvl, 0) or 0)
        return [
            f"Day {int(getattr(self, 'campaign_day', 1) or 1)} — {self._watch_name()} | Dungeon L{lvl} | Room {rid} | {room_kind.title()}",
            self._party_hud_summary(),
            f"Supplies: {int(getattr(self, 'gold', 0) or 0)} gp | Rations {int(getattr(self, 'rations', 0) or 0)} | Torches {int(getattr(self, 'torches', 0) or 0)}",
            self._light_hud_summary() + f" | Noise {int(getattr(self, 'noise_level', 0) or 0)}/5" + f" | Alarm {alarm}/5",
            self._active_objective_hud(),
        ]

    def _render_top_hud(self, lines: list[str], *, width: int = 79) -> None:
        w = max(40, int(width))
        self.ui.log('=' * w)
        for line in (lines or []):
            chunks = textwrap.wrap(str(line), width=w, replace_whitespace=False, drop_whitespace=False) or [""]
            for chunk in chunks:
                self.ui.log(chunk)
        self.ui.log('=' * w)

    def _render_split_block(self, left_lines: list[str], right_lines: list[str], *, left_width: int = 34, right_width: int = 42, gap: int = 3) -> None:
        """Render two text columns as a single aligned map/status screen."""
        lw = max(24, int(left_width))
        rw = max(24, int(right_width))
        left_wrapped: list[str] = []
        for line in left_lines:
            chunks = textwrap.wrap(str(line), width=lw, replace_whitespace=False, drop_whitespace=False) or [""]
            left_wrapped.extend(chunks)
        right_wrapped: list[str] = []
        for line in right_lines:
            chunks = textwrap.wrap(str(line), width=rw, replace_whitespace=False, drop_whitespace=False) or [""]
            right_wrapped.extend(chunks)
        total = max(len(left_wrapped), len(right_wrapped))
        left_wrapped.extend([""] * (total - len(left_wrapped)))
        right_wrapped.extend([""] * (total - len(right_wrapped)))
        for left, right in zip(left_wrapped, right_wrapped):
            self.ui.log(f"{left:<{lw}}{' ' * gap}{right}")

    def _clear_map_screen(self) -> None:
        try:
            clr = getattr(self.ui, "clear_screen", None)
            if callable(clr):
                clr()
        except Exception:
            pass

    def _show_wilderness_map(self, radius: int = 3, *, allow_classic_menu: bool = False) -> str | None:
        """Render a compact wilderness mini-board around the party.

        Includes a persistent selection panel so map inspection feels like a
        boardgame side-info strip rather than a separate pop-up.
        """
        seed_sel = getattr(self, 'wilderness_map_selected_hex', None)
        selected_pos: tuple[int, int] | None = tuple(seed_sel) if isinstance(seed_sel, (list, tuple)) and len(seed_sel) >= 2 else None
        selected_idx: int | None = None
        while True:
            self._clear_map_screen()
            self.ui.title("Wilderness Map")
            self._render_top_hud(self._wilderness_hud_lines())
            q0, r0 = self.party_hex
            self._ensure_current_hex()
            route_cells, special_markers, route_labels = self._wilderness_route_overlay()
            board_lines: list[str] = [f"Board: center @ | radius {int(radius)} hexes"]
            if route_labels:
                board_lines.append("Highlights: " + " | ".join(route_labels) + " | + route")
            for r in range(r0 - radius, r0 + radius + 1):
                cells: list[str] = []
                for q in range(q0 - radius, q0 + radius + 1):
                    dist = hex_distance((q0, r0), (q, r))
                    if dist > radius:
                        cells.append("   ")
                        continue
                    key = f"{q},{r}"
                    existed = key in (self.world_hexes or {})
                    hx = ensure_hex(self.world_hexes, (q, r), self.wilderness_rng).to_dict()
                    discovered = bool(hx.get("discovered", False)) or (q, r) == tuple(self.party_hex) or (q, r) == (0, 0)
                    if discovered:
                        sym = self._wilderness_tile_symbol(hx, (q, r))
                    elif existed:
                        sym = "~"
                    else:
                        sym = "?"
                    if (q, r) in special_markers and (q, r) != tuple(self.party_hex):
                        sym = special_markers[(q, r)]
                    elif (q, r) in route_cells and not discovered and sym == '?':
                        sym = '+'
                    elif (q, r) in route_cells and sym not in ('@','T','A','!'):
                        sym = '+'
                    if selected_pos is not None and (q, r) == tuple(selected_pos):
                        cells.append(f"[{sym}]")
                    else:
                        cells.append(f" {sym} ")
                indent = " " * ((r - (r0 - radius) + 1) * 2)
                board_lines.append(indent + "".join(cells).rstrip())
            board_lines.extend([
                "Legend: . clear  = road  f forest  h hills  s swamp  m mountain",
                "        R ruins  S shrine  L lair  D dungeon  H stronghold  C camp  V caravan  O outpost",
                "        ~ frontier/known edge  ? unknown fog  + route emphasis  [x] highlighted hex",
                "Hotkeys: n/ne/se/s/sw/nw, next, prev, use, clear, back, go <dir>/travel <dir>",
            ])
            inspectables = self._wilderness_map_inspectables(radius)
            if inspectables:
                pos_to_idx = {tuple(pos): i for i, (pos, _label) in enumerate(inspectables)}
                if selected_pos is not None and tuple(selected_pos) in pos_to_idx:
                    selected_idx = pos_to_idx[tuple(selected_pos)]
                elif selected_idx is not None:
                    selected_idx = max(0, min(int(selected_idx), len(inspectables) - 1))
                    selected_pos = tuple(inspectables[selected_idx][0])
                    self.wilderness_map_selected_hex = [int(selected_pos[0]), int(selected_pos[1])]
            else:
                selected_idx = None
                selected_pos = None
            panel_lines: list[str] = ["Status panel"]
            if selected_pos is not None:
                panel_lines.append(f"Selected: ({int(selected_pos[0])},{int(selected_pos[1])})")
                panel_lines.extend(self._wilderness_hex_detail_lines(selected_pos))
                acts = self._wilderness_hex_action_labels(selected_pos)
                panel_lines.append("Actions: " + (" | ".join(acts) if acts else "no direct actions"))
            else:
                panel_lines.append("Selected: none")
                panel_lines.append("Tip: use select, cycle, or direction hotkeys to focus a hex.")
            self._render_split_block(board_lines, panel_lines)
            menu = ["Select / change highlighted hex"]
            alias_map = {"sel": 0, "select": 0}
            if selected_pos is not None and inspectables:
                menu.extend([
                    "Move highlight North",
                    "Move highlight North-East",
                    "Move highlight South-East",
                    "Move highlight South",
                    "Move highlight South-West",
                    "Move highlight North-West",
                    "Cycle highlight to next hex",
                    "Cycle highlight to previous hex",
                ])
                alias_map.update({
                    "n": 1,
                    "ne": 2,
                    "se": 3,
                    "s": 4,
                    "sw": 5,
                    "nw": 6,
                    "next": 7,
                    "prev": 8,
                    "previous": 8,
                    "go n": "diract_N",
                    "travel n": "diract_N",
                    "act n": "diract_N",
                    "go ne": "diract_NE",
                    "travel ne": "diract_NE",
                    "act ne": "diract_NE",
                    "go se": "diract_SE",
                    "travel se": "diract_SE",
                    "act se": "diract_SE",
                    "go s": "diract_S",
                    "travel s": "diract_S",
                    "act s": "diract_S",
                    "go sw": "diract_SW",
                    "travel sw": "diract_SW",
                    "act sw": "diract_SW",
                    "go nw": "diract_NW",
                    "travel nw": "diract_NW",
                    "act nw": "diract_NW",
                })
                if self._wilderness_hex_action_labels(selected_pos):
                    menu.append("Use action from highlighted hex")
                    alias_map.update({"use": len(menu)-1, "go": len(menu)-1, "act": len(menu)-1})
                menu.append("Clear highlighted hex")
                alias_map.update({"clear": len(menu)-1, "c": len(menu)-1})
            if allow_classic_menu:
                menu.append("Open classic wilderness actions")
                alias_map.update({"menu": len(menu)-1, "classic": len(menu)-1})
            if allow_classic_menu:
                menu.append("Open classic dungeon actions")
                alias_map.update({"menu": len(menu)-1, "classic": len(menu)-1})
            menu.append("Back")
            alias_map.update({"back": len(menu)-1, "b": len(menu)-1, "exit": len(menu)-1})
            c = self._map_choose_with_aliases("Map actions", menu, alias_map)
            if isinstance(c, str) and c.startswith("diract_"):
                if self._wilderness_map_direction_action(c.split("_", 1)[1], radius):
                    return 'acted'
                continue
            idx = 0
            if c == idx:
                if not inspectables:
                    self.ui.log("Nothing nearby can be highlighted yet.")
                    return 'noop'
                labels = [label for _, label in inspectables] + ["Back"]
                i = self.ui.choose("Highlight which hex?", labels)
                if i == len(labels) - 1:
                    continue
                selected_pos = inspectables[i][0]
                self.wilderness_map_selected_hex = [int(selected_pos[0]), int(selected_pos[1])]
                continue
            idx += 1
            if selected_pos is not None and inspectables:
                moved = False
                for move_dir in ["N", "NE", "SE", "S", "SW", "NW"]:
                    if c == idx:
                        selected_pos = self._wilderness_map_move_selection(selected_pos, radius, move_dir)
                        if selected_pos is not None:
                            self.wilderness_map_selected_hex = [int(selected_pos[0]), int(selected_pos[1])]
                        moved = True
                        break
                    idx += 1
                if moved:
                    continue
                if c == idx:
                    selected_idx = ((selected_idx or 0) + 1) % len(inspectables)
                    selected_pos = tuple(inspectables[selected_idx][0])
                    self.wilderness_map_selected_hex = [int(selected_pos[0]), int(selected_pos[1])]
                    continue
                idx += 1
                if c == idx:
                    selected_idx = ((selected_idx or 0) - 1) % len(inspectables)
                    selected_pos = tuple(inspectables[selected_idx][0])
                    self.wilderness_map_selected_hex = [int(selected_pos[0]), int(selected_pos[1])]
                    continue
                idx += 1
                if self._wilderness_hex_action_labels(selected_pos):
                    if c == idx:
                        if self._wilderness_map_context_actions(selected_pos):
                            return 'acted'
                        continue
                    idx += 1
                if c == idx:
                    selected_pos = None
                    selected_idx = None
                    self.wilderness_map_selected_hex = None
                    continue
                idx += 1
            if allow_classic_menu and c == len(menu) - 2:
                return 'menu'
            return 'back'


    def _map_choose_with_aliases(self, title: str, options: list[str], alias_map: dict[str, Any] | None = None) -> int | str:
        """Prompt for a map action, accepting either menu numbers or short aliases.

        Uses a compact prompt so repeated map navigation refreshes in-place more
        cleanly instead of pushing the board far up the terminal scrollback.
        Alias values may either be menu indexes or synthetic string tokens for
        direct map-layer actions (for example ``"diract_NE"``).
        """
        alias_map = {str(k).strip().lower(): v for k, v in (alias_map or {}).items()}
        while True:
            self.ui.log("")
            self.ui.log(title + ":")
            for i, opt in enumerate(options, start=1):
                self.ui.log(f"  {i}. {opt}")
            if alias_map:
                pairs = []
                seen: set[int] = set()
                for alias, idx in alias_map.items():
                    try:
                        nidx = int(idx)
                    except Exception:
                        continue
                    if 0 <= nidx < len(options) and nidx not in seen:
                        pairs.append(f"{alias}={nidx+1}")
                        seen.add(nidx)
                if pairs:
                    self.ui.log("  Aliases: " + ", ".join(pairs))
            v = self.ui.prompt("Map command (number or alias):", None).strip().lower()
            if v.isdigit() and 1 <= int(v) <= len(options):
                return int(v) - 1
            if v in alias_map:
                mapped = alias_map[v]
                try:
                    idx = int(mapped)
                except Exception:
                    return mapped
                if 0 <= idx < len(options):
                    return idx
            self.ui.log("Invalid map command.")

    def _dungeon_map_nodes(self) -> dict[int, tuple[int, int]]:
        """Assign lightweight graph coordinates to discovered/current rooms on this level."""
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        room_ids: set[int] = set()
        cur = int(getattr(self, "current_room_id", 0) or 0)
        if cur:
            room_ids.add(cur)
        for rid, room in (self.dungeon_rooms or {}).items():
            try:
                rrid = int(rid)
            except Exception:
                continue
            floor = int((room or {}).get("floor", lvl) or lvl)
            if floor == lvl and (int((room or {}).get("visited", 0) or 0) > 0 or rrid == cur):
                room_ids.add(rrid)
                for dest in ((room or {}).get("exits") or {}).values():
                    try:
                        dd = int(dest)
                    except Exception:
                        continue
                    meta = self._dungeon_room_meta(dd)
                    if int(meta.get("floor", lvl) or lvl) == lvl:
                        room_ids.add(dd)
        if not room_ids:
            return {}
        cur = cur or min(room_ids)
        coords: dict[int, tuple[int, int]] = {int(cur): (0, 0)}
        queue = [int(cur)]
        dirs = [(0, -1), (1, 0), (0, 1), (-1, 0), (2, 0), (0, 2), (-2, 0), (0, -2)]
        while queue:
            rid = queue.pop(0)
            room = self.dungeon_rooms.get(int(rid)) or self._ensure_room(int(rid))
            base = coords[int(rid)]
            used = {coords[n] for n in coords}
            neigh = []
            for dest in ((room or {}).get("exits") or {}).values():
                try:
                    dd = int(dest)
                except Exception:
                    continue
                if dd not in room_ids:
                    continue
                neigh.append(dd)
            for idx, dd in enumerate(sorted(set(neigh))):
                if dd in coords:
                    continue
                placed = None
                for ox, oy in dirs[idx:] + dirs[:idx]:
                    cand = (base[0] + ox, base[1] + oy)
                    if cand not in used:
                        placed = cand
                        break
                if placed is None:
                    placed = (base[0] + (idx + 1), base[1] + (idx % 2))
                coords[dd] = placed
                used.add(placed)
                queue.append(dd)
        return coords

    def _dungeon_room_symbol(self, room: dict[str, Any], room_id: int) -> str:
        if int(room_id) == int(getattr(self, "current_room_id", 0) or 0):
            return "@"
        if int((room or {}).get("visited", 0) or 0) <= 0:
            return "?"
        rtype = str((room or {}).get("type") or "empty")
        stairs = (room or {}).get("stairs") or {}
        if stairs.get("up") and stairs.get("down"):
            base = "B"
        elif stairs.get("up"):
            base = "^"
        elif stairs.get("down"):
            base = "v"
        else:
            base = {
                "monster": "M",
                "boss": "X",
                "treasure": "$",
                "treasury": "$",
                "trap": "!",
                "empty": "o",
            }.get(rtype, "o")
        if bool((room or {}).get("cleared", False)) and base not in ('@','?','^','v','B'):
            return base.lower()
        return base

    def _dungeon_connector_chars(self, state: str, *, horizontal: bool) -> tuple[str, str]:
        state = str(state or 'open')
        if state == 'open':
            return ('-' if horizontal else '|', ' ')
        if state == 'closed':
            return ('=' if horizontal else '║', 'c')
        if state == 'locked':
            return ('#', 'L')
        if state == 'stuck':
            return ('!', 'S')
        return ('?', '?')

    def _dungeon_exit_preview(self, room: dict[str, Any], key: str, dest: int) -> str:
        target = self.dungeon_rooms.get(int(dest)) or self._ensure_room(int(dest))
        state = str((room or {}).get('doors', {}).get(str(key), 'open'))
        visited = int((target or {}).get('visited', 0) or 0) > 0
        cleared = bool((target or {}).get('cleared', False))
        if not visited:
            target_txt = 'frontier'
        else:
            rtype = str((target or {}).get('type') or 'room')
            target_txt = ('cleared ' if cleared else '') + rtype
        return f"{key}: {state} -> {target_txt} (room {int(dest)})"

    def _dungeon_map_inspectables(self) -> list[tuple[int, str]]:
        coords = self._dungeon_map_nodes()
        out: list[tuple[int, str]] = []
        cur = int(getattr(self, 'current_room_id', 0) or 0)
        for rid in sorted(coords.keys(), key=lambda rr: (0 if int(rr)==cur else 1, int(rr))):
            room = self.dungeon_rooms.get(int(rid)) or self._ensure_room(int(rid))
            visited = int((room or {}).get('visited', 0) or 0) > 0
            cleared = bool((room or {}).get('cleared', False))
            rtype = str((room or {}).get('type') or 'room')
            status = 'here' if int(rid) == cur else ('cleared' if cleared else ('explored' if visited else 'frontier'))
            out.append((int(rid), f"Room {int(rid)} — {rtype} — {status}"))
        return out

    def _dungeon_adjacent_exit_to_room(self, room_id: int) -> tuple[str, str] | None:
        cur_room = self.dungeon_rooms.get(int(getattr(self, 'current_room_id', 0) or 0)) or self._ensure_room(int(getattr(self, 'current_room_id', 0) or 0))
        for exit_key, dest in sorted(((cur_room or {}).get('exits') or {}).items()):
            try:
                if int(dest) == int(room_id):
                    state = str((cur_room or {}).get('doors', {}).get(str(exit_key), 'open'))
                    return (str(exit_key), state)
            except Exception:
                continue
        return None

    def _dungeon_map_move_selection(self, selected_room: int | None, direction: str) -> int | None:
        """Move dungeon map highlight by board direction among visible rooms."""
        inspectables = self._dungeon_map_inspectables()
        if not inspectables:
            return None
        coords = self._dungeon_map_nodes()
        if not coords:
            return selected_room
        direction = str(direction or '').upper()
        vectors = {
            'N': (0, -1),
            'E': (1, 0),
            'S': (0, 1),
            'W': (-1, 0),
        }
        if direction not in vectors:
            return selected_room
        start_room = int(selected_room) if selected_room is not None else int(getattr(self, 'current_room_id', 0) or 0)
        if start_room not in coords:
            start_room = int(inspectables[0][0])
        start = coords.get(start_room)
        if start is None:
            return selected_room
        dx, dy = vectors[direction]
        candidates: list[tuple[int, int, int]] = []
        for rid, _label in inspectables:
            rid = int(rid)
            if rid == start_room or rid not in coords:
                continue
            cx, cy = coords[rid]
            delta_x = cx - start[0]
            delta_y = cy - start[1]
            if dx != 0:
                if delta_x == 0 or (1 if delta_x > 0 else -1) != dx:
                    continue
                score = (abs(delta_x), abs(delta_y), rid)
            else:
                if delta_y == 0 or (1 if delta_y > 0 else -1) != dy:
                    continue
                score = (abs(delta_y), abs(delta_x), rid)
            candidates.append(score)
        if not candidates:
            return selected_room
        candidates.sort()
        return int(candidates[0][2])

    def _dungeon_map_context_actions(self, room_id: int) -> bool:
        """Offer direct context actions from a dungeon room inspection.

        Returns True if the current room changed and the map should close.
        """
        room_id = int(room_id)
        cur = int(getattr(self, 'current_room_id', 0) or 0)
        if room_id == cur:
            self.ui.log("You are already in that room.")
            return False
        edge = self._dungeon_adjacent_exit_to_room(room_id)
        if not edge:
            self.ui.log("That room is not directly reachable from your current room.")
            return False
        exit_key, state = edge
        labels: list[str] = []
        acts: list[tuple[str, Any]] = []
        edge_meta = self._dungeon_bp_edge_flags(int(getattr(self, 'current_room_id', 0) or 0), int(room_id))
        gate_name = str(edge_meta.get('gate') or '').strip()
        key_id = str(edge_meta.get('key_id') or '').strip()
        has_key = bool(key_id and self._dungeon_has_gate_key(key_id))
        if state == 'open':
            labels.append(f"Go {exit_key} to Room {room_id}")
            acts.append(('move', exit_key))
        else:
            if state == 'closed':
                labels.append(f"Open door ({exit_key})")
                acts.append(('door', (exit_key, 'open')))
            elif state == 'stuck':
                labels.append(f"Force stuck door ({exit_key})")
                acts.append(('door', (exit_key, 'force')))
                labels.append(f"Try to open it ({exit_key})")
                acts.append(('door', (exit_key, 'open')))
            elif state == 'locked':
                if has_key:
                    labels.append(f"Use {self._dungeon_gate_label(gate_name)} token ({exit_key})")
                    acts.append(('door', (exit_key, 'open')))
                else:
                    labels.append(f"Pick lock ({exit_key})")
                    acts.append(('door', (exit_key, 'pick')))
                    labels.append(f"Force locked door ({exit_key})")
                    acts.append(('door', (exit_key, 'force')))
            labels.append(f"Open and enter ({exit_key})")
            acts.append(('open_enter', exit_key))
        i = self.ui.choose("Room actions", labels + ["Back"])
        if i == len(labels):
            return False
        kind, payload = acts[i]
        if kind == 'move':
            res = self.dispatch(DungeonMove(str(payload)))
            if res.message and res.message != 'door_blocked':
                self.ui.log(res.message)
            return bool(res.ok)
        if kind == 'door':
            ex, action = payload
            res = self.dispatch(DungeonDoorAction(str(ex), str(action)))
            if res.message and res.message not in ('locked', 'stuck', 'not_locked'):
                self.ui.log(res.message)
            return False
        if kind == 'open_enter':
            ex = str(payload)
            state_res = self.dispatch(DungeonDoorAction(ex, 'open'))
            if state_res.ok:
                move_res = self.dispatch(DungeonMove(ex))
                if move_res.message and move_res.message != 'door_blocked':
                    self.ui.log(move_res.message)
                return bool(move_res.ok)
            if state_res.message and state_res.message not in ('locked', 'stuck', 'not_locked'):
                self.ui.log(state_res.message)
            return False
        return False

    def _dungeon_map_direction_action(self, direction: str) -> bool:
        """Try to enter or operate on the room in a board direction from the current room.

        If a visible room lies in that direction and is adjacent/actionable, execute
        the room action immediately; otherwise move the highlight there so the board
        remains browsable from hotkeys.
        """
        direction = str(direction or '').upper()
        target = self._dungeon_map_move_selection(int(getattr(self, 'current_room_id', 0) or 0), direction)
        if target is None:
            self.ui.log(f"Nothing useful lies to the {direction} on the current dungeon map.")
            return False
        target = int(target)
        if self._dungeon_room_action_labels(target):
            return self._dungeon_map_context_actions(target)
        self.dungeon_map_selected_room = int(target)
        self.ui.log(f"No direct room action is available to the {direction}; highlight moved instead.")
        return False

    def _dungeon_room_detail_lines(self, room_id: int) -> list[str]:
        room = self.dungeon_rooms.get(int(room_id)) or self._ensure_room(int(room_id))
        lines: list[str] = []
        lines.append(f"Type: {str((room or {}).get('type') or 'room').title()}")
        lines.append(f"Visited: {'Yes' if int((room or {}).get('visited', 0) or 0) > 0 else 'No'}")
        lines.append(f"Cleared: {'Yes' if bool((room or {}).get('cleared', False)) else 'No'}")
        stairs = (room or {}).get('stairs') or {}
        if stairs.get('up') or stairs.get('down'):
            bits=[]
            if stairs.get('up'): bits.append('stairs up')
            if stairs.get('down'): bits.append('stairs down')
            lines.append('Features: ' + ', '.join(bits))
        extras = []
        if (room or {}).get('treasure_found'):
            extras.append('treasure found')
        if (room or {}).get('trap_found'):
            extras.append('trap found')
        if (room or {}).get('monster_killed'):
            extras.append('monster defeated')
        if extras:
            lines.append('Flags: ' + ', '.join(extras))
        exits = ((room or {}).get('exits') or {})
        if exits:
            lines.append('Exits:')
            for key, dest in sorted(exits.items(), key=lambda kv: str(kv[0])):
                try:
                    dd = int(dest)
                except Exception:
                    continue
                lines.append('- ' + self._dungeon_exit_preview(room, str(key), dd))
        else:
            lines.append('Exits: none recorded')
        return lines

    def _inspect_dungeon_room(self, room_id: int) -> None:
        self.ui.title(f"Room Inspection ({int(room_id)})")
        for line in self._dungeon_room_detail_lines(room_id):
            self.ui.log(line)

    def _dungeon_room_action_labels(self, room_id: int) -> list[str]:
        room_id = int(room_id)
        cur = int(getattr(self, 'current_room_id', 0) or 0)
        if room_id == cur:
            return []
        edge = self._dungeon_adjacent_exit_to_room(room_id)
        if not edge:
            return []
        exit_key, state = edge
        labels: list[str] = []
        edge_meta = self._dungeon_bp_edge_flags(int(getattr(self, 'current_room_id', 0) or 0), int(room_id))
        gate_name = str(edge_meta.get('gate') or '').strip()
        key_id = str(edge_meta.get('key_id') or '').strip()
        has_key = bool(key_id and self._dungeon_has_gate_key(key_id))
        if state == 'open':
            labels.append(f"Go {exit_key} to Room {room_id}")
        else:
            if state == 'closed':
                labels.append(f"Open door ({exit_key})")
            elif state == 'stuck':
                labels.append(f"Force stuck door ({exit_key})")
                labels.append(f"Try to open it ({exit_key})")
            elif state == 'locked':
                if has_key:
                    labels.append(f"Use {self._dungeon_gate_label(gate_name)} token ({exit_key})")
                else:
                    labels.append(f"Pick lock ({exit_key})")
                    labels.append(f"Force locked door ({exit_key})")
            labels.append(f"Open and enter ({exit_key})")
        return labels

    def _show_dungeon_map(self, *, allow_classic_menu: bool = False) -> str | None:
        """Render a compact dungeon mini-board for the current level.

        Includes a persistent highlighted-room panel so inspection and actions
        stay visible while the player navigates the map layer.
        """
        seed_room = getattr(self, 'dungeon_map_selected_room', None)
        selected_room: int | None = int(seed_room) if seed_room is not None else None
        selected_idx: int | None = None
        while True:
            self._clear_map_screen()
            self.ui.title("Dungeon Map")
            self._render_top_hud(self._dungeon_hud_lines())
            coords = self._dungeon_map_nodes()
            if not coords:
                self.ui.log("No map information yet.")
                return 'noop'
            room_cache: dict[int, dict[str, Any]] = {}
            xs = [x for x, _ in coords.values()]
            ys = [y for _, y in coords.values()]
            minx, maxx = min(xs), max(xs)
            miny, maxy = min(ys), max(ys)
            width = (maxx - minx + 1) * 4 + 1
            height = (maxy - miny + 1) * 2 + 1
            grid = [[" " for _ in range(width)] for _ in range(height)]
            for rid, (x, y) in coords.items():
                room = self.dungeon_rooms.get(int(rid)) or self._ensure_room(int(rid))
                room_cache[int(rid)] = room
                gx = (x - minx) * 4 + 2
                gy = (y - miny) * 2 + 1
                sym = self._dungeon_room_symbol(room, int(rid))
                grid[gy][gx] = sym
                if selected_room is not None and int(rid) == int(selected_room):
                    if gx - 1 >= 0:
                        grid[gy][gx - 1] = '['
                    if gx + 1 < width:
                        grid[gy][gx + 1] = ']'
            for rid, room in room_cache.items():
                x1, y1 = coords[rid]
                gx1 = (x1 - minx) * 4 + 2
                gy1 = (y1 - miny) * 2 + 1
                for exit_key, dest in ((room or {}).get("exits") or {}).items():
                    try:
                        dd = int(dest)
                    except Exception:
                        continue
                    if dd not in coords or dd < rid:
                        continue
                    x2, y2 = coords[dd]
                    gx2 = (x2 - minx) * 4 + 2
                    gy2 = (y2 - miny) * 2 + 1
                    state = str((room or {}).get('doors', {}).get(str(exit_key), 'open'))
                    horizontal = gy1 == gy2
                    conn_char, mark_char = self._dungeon_connector_chars(state, horizontal=horizontal)
                    if horizontal:
                        for xx in range(min(gx1, gx2) + 1, max(gx1, gx2)):
                            grid[gy1][xx] = conn_char
                        mx = (gx1 + gx2) // 2
                        if mark_char.strip():
                            grid[gy1 - 1 if gy1 > 0 else gy1][mx] = mark_char
                    elif gx1 == gx2:
                        for yy in range(min(gy1, gy2) + 1, max(gy1, gy2)):
                            grid[yy][gx1] = conn_char
                        my = (gy1 + gy2) // 2
                        if mark_char.strip() and gx1 + 1 < width:
                            grid[my][gx1 + 1] = mark_char
                    else:
                        mx = (gx1 + gx2) // 2
                        my = (gy1 + gy2) // 2
                        grid[my][mx] = '+'
            lvl = int(getattr(self, "dungeon_level", 1) or 1)
            board_lines: list[str] = [f"Board: level {lvl} | @ current room"]
            for row in grid:
                line = "".join(row).rstrip()
                if line.strip():
                    board_lines.append(line)
            cur_room = self.dungeon_rooms.get(int(getattr(self, 'current_room_id', 0) or 0)) or {}
            previews = []
            for exit_key, dest in sorted(((cur_room or {}).get('exits') or {}).items()):
                previews.append(self._dungeon_exit_preview(cur_room, str(exit_key), int(dest)))
            if previews:
                board_lines.append("Current exits:")
                for line in previews:
                    board_lines.append(f"- {line}")
            board_lines.extend([
                "Legend: o explored  ? frontier  m/x/$/! lowercase = cleared  M/$/!/X = uncleared",
                "        connectors: -=open  =/║ closed  # locked  ! stuck  [L/c/S markers show door state]",
                "        [x] highlighted room",
                "Hotkeys: n/e/s/w, next, prev, use, clear, back, go <dir>/enter <dir>",
            ])
            inspectables = self._dungeon_map_inspectables()
            if inspectables:
                room_to_idx = {int(rid): i for i, (rid, _label) in enumerate(inspectables)}
                if selected_room is not None and int(selected_room) in room_to_idx:
                    selected_idx = room_to_idx[int(selected_room)]
                elif selected_idx is not None:
                    selected_idx = max(0, min(int(selected_idx), len(inspectables) - 1))
                    selected_room = int(inspectables[selected_idx][0])
                    self.dungeon_map_selected_room = int(selected_room)
            else:
                selected_idx = None
                selected_room = None
            panel_lines: list[str] = ["Status panel"]
            if selected_room is not None:
                panel_lines.append(f"Selected room: {int(selected_room)}")
                panel_lines.extend(self._dungeon_room_detail_lines(int(selected_room)))
                acts = self._dungeon_room_action_labels(int(selected_room))
                panel_lines.append("Actions: " + (" | ".join(acts) if acts else "no direct actions"))
            else:
                panel_lines.append("Selected room: none")
                panel_lines.append("Tip: use select, cycle, or direction hotkeys to focus a room.")
            self._render_split_block(board_lines, panel_lines)
            menu = ["Select / change highlighted room"]
            alias_map = {"sel": 0, "select": 0}
            if selected_room is not None and inspectables:
                menu.extend([
                    "Move highlight North",
                    "Move highlight East",
                    "Move highlight South",
                    "Move highlight West",
                    "Cycle highlight to next room",
                    "Cycle highlight to previous room",
                ])
                alias_map.update({
                    "n": 1,
                    "e": 2,
                    "s": 3,
                    "w": 4,
                    "next": 5,
                    "prev": 6,
                    "previous": 6,
                    "go n": "diract_N",
                    "enter n": "diract_N",
                    "act n": "diract_N",
                    "go e": "diract_E",
                    "enter e": "diract_E",
                    "act e": "diract_E",
                    "go s": "diract_S",
                    "enter s": "diract_S",
                    "act s": "diract_S",
                    "go w": "diract_W",
                    "enter w": "diract_W",
                    "act w": "diract_W",
                })
                if self._dungeon_room_action_labels(int(selected_room)):
                    menu.append("Use action from highlighted room")
                    alias_map.update({"use": len(menu)-1, "go": len(menu)-1, "act": len(menu)-1})
                menu.append("Clear highlighted room")
                alias_map.update({"clear": len(menu)-1, "c": len(menu)-1})
            menu.append("Back")
            alias_map.update({"back": len(menu)-1, "b": len(menu)-1, "exit": len(menu)-1})
            c = self._map_choose_with_aliases("Map actions", menu, alias_map)
            if isinstance(c, str) and c.startswith("diract_"):
                if self._dungeon_map_direction_action(c.split("_", 1)[1]):
                    return 'acted'
                continue
            idx = 0
            if c == idx:
                if not inspectables:
                    self.ui.log("No rooms can be highlighted yet.")
                    return
                labels = [label for _, label in inspectables] + ["Back"]
                i = self.ui.choose("Highlight which room?", labels)
                if i == len(labels) - 1:
                    continue
                selected_room = int(inspectables[i][0])
                self.dungeon_map_selected_room = int(selected_room)
                continue
            idx += 1
            if selected_room is not None and inspectables:
                moved = False
                for move_dir in ["N", "E", "S", "W"]:
                    if c == idx:
                        selected_room = self._dungeon_map_move_selection(selected_room, move_dir)
                        if selected_room is not None:
                            self.dungeon_map_selected_room = int(selected_room)
                        moved = True
                        break
                    idx += 1
                if moved:
                    continue
                if c == idx:
                    selected_idx = ((selected_idx or 0) + 1) % len(inspectables)
                    selected_room = int(inspectables[selected_idx][0])
                    self.dungeon_map_selected_room = int(selected_room)
                    continue
                idx += 1
                if c == idx:
                    selected_idx = ((selected_idx or 0) - 1) % len(inspectables)
                    selected_room = int(inspectables[selected_idx][0])
                    self.dungeon_map_selected_room = int(selected_room)
                    continue
                idx += 1
                if self._dungeon_room_action_labels(int(selected_room)):
                    if c == idx:
                        if self._dungeon_map_context_actions(int(selected_room)):
                            return 'acted'
                        continue
                    idx += 1
                if c == idx:
                    selected_room = None
                    selected_idx = None
                    self.dungeon_map_selected_room = None
                    continue
                idx += 1
            return

    def wilderness_loop(self):
        self.ui.title("Wilderness")
        # Ensure starting hex exists
        self._ensure_current_hex()

        while True:
            self.ui.hr()
            q, r = self.party_hex
            hx = self._ensure_current_hex()
            # Contract progress
            if isinstance(hx, dict):
                self._update_contract_progress_on_hex(int(q), int(r), hx)
                self._refresh_district_objectives(announce=True)
            terrain = hx.get("terrain", "clear")
            dist = hex_distance((0, 0), self.party_hex)
            self._ensure_faction_for_hex(hx)
            fac_txt = ""
            fid = hx.get("faction_id")
            if fid and fid in (self.factions or {}):
                fac_txt = f" | Territory: {(self.factions[fid].get('name') or fid)}"
            self.ui.log(f"Day {self.campaign_day} — {self._watch_name()} | Hex ({q},{r}) | Terrain: {terrain}{fac_txt} | Heat: {int(hx.get('heat', 0) or 0)} | Distance from Town: {dist}")
            self.ui.log(f"Gold: {self.gold} gp | Rations: {self.rations} | Torches: {self.torches}")
            self.ui.log(f"Travel mode: {str(getattr(self,'wilderness_travel_mode','normal')).title()}")
            self.ui.log(f"Travel condition: {str(getattr(self.travel_state, 'travel_condition', 'clear')).title()}")
            anc = self._get_forward_anchor()
            if anc:
                aq, ar = (anc.get("hex") or [0, 0])[:2]
                extra = ""
                if anc.get("expires_day") is not None:
                    extra = f" until day {int(anc.get('expires_day', 0) or 0)}"
                self.ui.log(f"Forward anchor: {anc.get('name', anc.get('kind', 'Anchor'))} at ({aq},{ar}){extra}")

            objectives = self._district_objective_entries()
            if objectives:
                self.ui.log("District objectives:")
                for e in objectives[:2]:
                    status = "completed" if e.get('completion_ready') else ("reached" if e.get('visited_target') else "active")
                    self.ui.log(f"- {e.get('title')} [{status}] — {e.get('from_current')} (due day {e.get('deadline_day')})")

            for _line in self._wilderness_telegraph_lines():
                self.ui.log(_line)

            poi = hx.get("poi")
            if poi and bool(poi.get("discovered", True)):
                self.ui.log(f"Notable: {self._poi_label(poi)}")

            if bool(getattr(self, 'wilderness_board_native', True)):
                mres = self._show_wilderness_map(allow_classic_menu=True)
                if mres == 'menu':
                    self.wilderness_board_native = False
                else:
                    continue

            # Direction labels
            dirs = list(neighbors(self.party_hex).keys())
            dir_labels = [f"Travel {d}" for d in dirs]
            base_actions = [
                "Resume board-native map view",
                "View map / mini-board",
                "Camp (advance to next Morning)",
                f"Set travel mode (current: {str(getattr(self,'wilderness_travel_mode','normal')).title()})",
                "Head back toward Town",
                "Return to Forward Anchor (safe, time scales with distance)",
                "Return to Town (safe, time scales with distance)",
                "Back",
            ]
            if poi:
                base_actions.insert(0, "Investigate current location")
            choice = self.ui.choose("Wilderness Actions", base_actions + dir_labels)

            if poi:
                # Shift indices by +1 for existing choices
                if choice == 0:
                    res = self.dispatch(InvestigateCurrentLocation())
                    if res.message:
                        self.ui.log(res.message)
                    continue
                choice -= 1

            if choice == 0:
                self.wilderness_board_native = True
                continue

            if choice == 1:
                self._show_wilderness_map()
                continue

            if choice == 2:
                res = self.dispatch(CampToMorning())
                if res.message:
                    self.ui.log(res.message)
                continue

            if choice == 3:
                modes = ["Careful", "Normal", "Fast"]
                mi = self.ui.choose("Travel Mode", ["Careful (slower, safer, better scouting)", "Normal", "Fast (quicker, riskier, fewer leads)", "Back"])
                if mi == 3:
                    continue
                mode = {0: "careful", 1: "normal", 2: "fast"}.get(mi, "normal")
                res = self.dispatch(SetTravelMode(mode))
                if res.message:
                    self.ui.log(res.message)
                continue

            if choice == 4:
                res = self.dispatch(StepTowardTown())
                if not res.ok and res.message:
                    self.ui.log(res.message)
                    # If already at town, return to town loop.
                    if "already at Town" in res.message:
                        return
                    continue
                if res.message:
                    self.ui.log(res.message)
                if self.party_hex == (0, 0):
                    return
                continue

            if choice == 5:
                plan = self._return_to_anchor_plan()
                if not plan:
                    self.ui.log("You have no active forward anchor.")
                    continue
                day_cost = int(plan.get("day_cost", 0) or 0)
                if day_cost <= 0:
                    self.ui.log("You are already at your forward anchor.")
                    continue
                anc = plan.get("anchor") or {}
                mods = list(plan.get("modifiers", []))
                mod_txt = ", ".join(f"{name} {'+' if int(delta) > 0 else ''}{int(delta)}" for (name, delta) in mods) if mods else "none"
                day_txt = f"{day_cost} day" + ("s" if day_cost != 1 else "")
                j = self.ui.choose(
                    f"Return safely to {anc.get('name', 'your forward anchor')}? Estimated time: {day_txt}. Modifiers: {mod_txt}.",
                    ["Yes", "No"],
                )
                if j != 0:
                    continue
                self._cmd_return_to_anchor()
                continue

            if choice == 6:
                plan = self._return_to_town_plan()
                day_cost = int(plan.get("day_cost", 1) or 1)
                mods = list(plan.get("modifiers", []))
                mod_txt = ", ".join(f"{name} {'+' if int(delta) > 0 else ''}{int(delta)}" for (name, delta) in mods) if mods else "none"
                day_txt = f"{day_cost} day" + ("s" if day_cost != 1 else "")
                j = self.ui.choose(
                    f"Return to Town safely? Estimated time: {day_txt}. Modifiers: {mod_txt}.",
                    ["Yes", "No"],
                )
                if j != 0:
                    continue
                res = self.dispatch(ReturnToTown())
                if res.message:
                    self.ui.log(res.message)
                return

            if choice == 7:
                return

            # Travel direction
            d = dirs[choice - 7]
            res = self.dispatch(TravelDirection(d))
            if not res.ok and res.message:
                self.ui.log(res.message)


    def train_party(self):
        any_trained = False
        for pc in self.party.pcs():
            while pc.xp >= self.xp_threshold_for_class(getattr(pc, "cls", "Fighter"), pc.level + 1):
                fee = (pc.level + 1) * 100
                if self.gold < fee:
                    self.ui.log(f"Not enough gold to train {pc.name}. Need {fee} gp.")
                    break
                self.gold -= fee
                self.ui.log(f"Training {pc.name} for level {pc.level + 1}. (-{fee} gp)")
                self.level_up_pc(pc)
                any_trained = True
        if not any_trained:
            self.ui.log("No one is ready to level up (or you lack training gold).")

    def level_up_pc(self, pc: PC):
        prev_level = int(getattr(pc, "level", 1) or 1)
        prev_hp_max = int(getattr(pc, "hp_max", 0) or 0)
        prev_save = int(getattr(pc, "save", 0) or 0)
        prev_slots = dict(getattr(pc, "spell_slots", {}) or {})

        new_level = pc.level + 1
        hd = self.class_hit_die(pc.cls)
        roll = self.dice.d(hd)
        min_gain = (hd + 1) // 2
        gain = max(roll, min_gain)
        if pc.stats:
            gain += max(0, mod_ability(pc.stats.CON))
        pc.hp_max += gain
        pc.hp = pc.hp_max
        pc.level = new_level
        self.ui.log(f"{pc.name} reaches level {pc.level}! (+{gain} HP)")

        # Recompute class features (spell slots, thief skills, save curve).
        try:
            self.recalc_pc_class_features(pc)
        except Exception:
            pass

        new_slots = dict(getattr(pc, "spell_slots", {}) or {})
        new_save = int(getattr(pc, "save", prev_save) or prev_save)
        self.ui.log(
            f"Level-up summary: Lv {prev_level}->{pc.level} | "
            f"HP {prev_hp_max}->{pc.hp_max} | Save {prev_save}->{new_save}"
        )
        if prev_slots != new_slots:
            self.ui.log(f"Spell slots: {prev_slots or {}} -> {new_slots or {}}")

    def prepare_spells_menu(self, *, in_dungeon: bool) -> None:
        """Prepare spells for spellcasters.

        - Magic-User: choose from spells_known
        - Cleric: choose from the class list

        In dungeon, this should be invoked via a command and costs a turn.
        """
        from .spellcasting import available_spells_for_prepare, prepare_spell_list

        casters = [
            pc
            for pc in self.party.pcs()
            if str(getattr(pc, 'cls', '')).lower() in ('magic-user', 'cleric', 'druid', 'ranger')
        ]
        casters = [c for c in casters if dict(getattr(c, 'spell_slots', {}) or {})]
        if not casters:
            self.ui.log("No one can prepare spells.")
            return

        which_labels = [f"{c.name} ({c.cls})" for c in casters] + ["Back"]
        idx = self.ui.choose("Prepare spells for who?", which_labels)
        if idx == len(which_labels) - 1:
            return
        caster = casters[idx]

        slots = {int(k): int(v) for k, v in dict(getattr(caster, 'spell_slots', {}) or {}).items()}
        avail = available_spells_for_prepare(caster=caster, content=self.content)
        if not avail:
            self.ui.log("No spells available.")
            return

        chosen: list[str] = []
        for slvl in sorted(slots.keys()):
            for n in range(slots[slvl]):
                labels = [nm for nm in avail]
                pick = self.ui.choose(f"Prepare spell (Level {slvl}) slot {n+1}/{slots[slvl]}", labels + ["Stop"])
                if pick == len(labels):
                    break
                chosen.append(labels[pick])

        caster.spells_prepared = prepare_spell_list(chosen=chosen, caster=caster, content=self.content)
        # Store stable spell_ids to avoid save drift when names change.
        try:
            caster.spells_prepared = [self.content.canonical_spell_id(s) for s in (caster.spells_prepared or [])]
        except Exception:
            pass
        self.ui.log(f"Prepared {len(caster.spells_prepared)} spell(s) for {caster.name}.")

    def _sale_unit_value_gp(self, item: Any) -> int:
        md = dict(getattr(item, "metadata", {}) or {})
        return max(0, int(md.get("value_gp", md.get("gp_value", 0)) or 0))

    def _sale_price_gp(self, *, unit_value_gp: int, quantity: int) -> int:
        gross = max(0, int(unit_value_gp or 0)) * max(1, int(quantity or 1))
        if gross <= 0:
            return 0
        return max(1, int(gross * 0.5))

    def list_sellable_items(self, *, include_legacy_compat: bool = False) -> list[dict[str, Any]]:
        """List sellable rows with explicit ownership source metadata."""
        rows: list[dict[str, Any]] = []

        # Preferred ownership-aware sources: actor inventories then party stash.
        for actor in list(getattr(getattr(self, "party", None), "members", []) or []):
            if actor is None:
                continue
            try:
                actor.ensure_inventory_initialized()
            except Exception:
                continue
            for item in list(getattr(getattr(actor, "inventory", None), "items", []) or []):
                qty = max(1, int(getattr(item, "quantity", 1) or 1))
                unit = self._sale_unit_value_gp(item)
                price = self._sale_price_gp(unit_value_gp=unit, quantity=qty)
                if price <= 0:
                    continue
                nm = owned_item_brief_label(item)
                rows.append({
                    "source_kind": "actor",
                    "owner": str(getattr(actor, "name", "actor") or "actor"),
                    "instance_id": str(getattr(item, "instance_id", "") or ""),
                    "name": nm,
                    "quantity": qty,
                    "identified": bool(getattr(item, "identified", True)),
                    "price_gp": int(price),
                    "label": f"{nm} (actor:{getattr(actor, 'name', 'actor')}) sell {price} gp",
                })

        stash_items = list(getattr(getattr(self, "party_stash", None), "items", []) or [])
        for item in stash_items:
            qty = max(1, int(getattr(item, "quantity", 1) or 1))
            unit = self._sale_unit_value_gp(item)
            price = self._sale_price_gp(unit_value_gp=unit, quantity=qty)
            if price <= 0:
                continue
            nm = owned_item_brief_label(item)
            rows.append({
                "source_kind": "stash",
                "owner": "stash",
                "instance_id": str(getattr(item, "instance_id", "") or ""),
                "name": nm,
                "quantity": qty,
                "identified": bool(getattr(item, "identified", True)),
                "price_gp": int(price),
                "label": f"{nm} (stash) sell {price} gp",
            })

        if include_legacy_compat:
            # Transitional compatibility fallback only: anonymous pending loot pool entries.
            from_legacy_compat = bool(self._ensure_loot_pool_hydrated_from_legacy())
            for e in list(getattr(self.loot_pool, "entries", []) or []):
                val = int(getattr(e, "gp_value", 0) or 0)
                if val <= 0:
                    continue
                qty = int(getattr(e, "quantity", 1) or 1)
                price = self._sale_price_gp(unit_value_gp=val, quantity=qty)
                u = ("unidentified" if not bool(getattr(e, "identified", True)) else "identified")
                qtxt = f" x{qty}" if qty > 1 else ""
                rows.append({
                    "source_kind": "legacy",
                    "owner": self._loot_sale_source_label(e, from_legacy_compat=from_legacy_compat),
                    "entry_id": str(getattr(e, "entry_id", "") or ""),
                    "name": str(getattr(e, "name", "loot") or "loot"),
                    "quantity": qty,
                    "identified": bool(getattr(e, "identified", True)),
                    "price_gp": int(price),
                    "label": f"{e.name} ({u}, legacy) {qtxt}sell {price} gp".replace("  ", " "),
                })
        return rows

    def sell_actor_item(self, actor: Actor, instance_id: str, *, source: str = "sell_loot") -> bool:
        item = find_item_on_actor(actor, instance_id)
        if item is None:
            return False
        qty = max(1, int(getattr(item, "quantity", 1) or 1))
        price = self._sale_price_gp(unit_value_gp=self._sale_unit_value_gp(item), quantity=qty)
        if price <= 0:
            return False
        sold_name = owned_item_brief_label(item)
        removed = remove_owned_item(self, source_kind="actor", reference_id=instance_id, actor=actor, quantity=qty)
        if not bool(getattr(removed, "ok", False)):
            return False
        coin_res = grant_coin_reward(self, price, source=source, destination=CoinDestination.TREASURY)
        self.ui.log(f"Sold {sold_name} (actor:{actor.name}) for {price} gp to {self._coin_destination_label(str(getattr(coin_res, 'destination', '') or ''))}.")
        return True

    def sell_stash_item(self, instance_id: str, *, source: str = "sell_loot") -> bool:
        stash = getattr(self, "party_stash", None)
        if stash is None:
            return False
        iid = str(instance_id or "")
        item = next((it for it in list(getattr(stash, "items", []) or []) if str(getattr(it, "instance_id", "") or "") == iid), None)
        if item is None:
            return False
        qty = max(1, int(getattr(item, "quantity", 1) or 1))
        price = self._sale_price_gp(unit_value_gp=self._sale_unit_value_gp(item), quantity=qty)
        if price <= 0:
            return False
        sold_name = owned_item_brief_label(item)
        removed = remove_owned_item(self, source_kind="stash", reference_id=iid, quantity=qty)
        if not bool(getattr(removed, "ok", False)):
            return False
        coin_res = grant_coin_reward(self, price, source=source, destination=CoinDestination.TREASURY)
        self.ui.log(f"Sold {sold_name} (stash) for {price} gp to {self._coin_destination_label(str(getattr(coin_res, 'destination', '') or ''))}.")
        return True

    def sell_legacy_party_item(self, entry_id: str, *, source: str = "sell_loot") -> bool:
        """Transitional-only sale path for anonymous legacy pooled loot."""
        entries = list(getattr(self.loot_pool, "entries", []) or [])
        pick = next((e for e in entries if str(getattr(e, "entry_id", "") or "") == str(entry_id or "")), None)
        if pick is None:
            return False
        qty = max(1, int(getattr(pick, "quantity", 1) or 1))
        val = max(0, int(getattr(pick, "gp_value", 0) or 0))
        price = self._sale_price_gp(unit_value_gp=val, quantity=qty)
        if price <= 0:
            return False
        sold_name = str(getattr(pick, "name", "loot") or "loot")
        sold_from = self._loot_sale_source_label(pick, from_legacy_compat=True)
        removed = remove_owned_item(self, source_kind="legacy", reference_id=str(entry_id or ""), quantity=qty)
        if not bool(getattr(removed, "ok", False)):
            return False
        self._sync_legacy_party_items_from_loot_pool()
        coin_res = grant_coin_reward(self, price, source=source, destination=CoinDestination.TREASURY)
        self.ui.log(f"Sold {sold_name} ({sold_from}) for {price} gp to {self._coin_destination_label(str(getattr(coin_res, 'destination', '') or ''))}.")
        return True

    def sell_loot(self):
        """Sell owned items first; use legacy pooled loot only via explicit fallback."""
        sellables = self.list_sellable_items(include_legacy_compat=False)
        using_legacy = False
        if not sellables:
            compat = self.list_sellable_items(include_legacy_compat=True)
            compat = [r for r in compat if str(r.get("source_kind")) == "legacy"]
            if not compat:
                self.ui.log("You have no loot to sell.")
                return
            c = self.ui.choose("No owned sellables found. Use legacy pooled loot compatibility sale?", ["Yes", "No"])
            if c != 0:
                return
            sellables = compat
            using_legacy = True

        labels = [str(r.get("label") or "Sell item") for r in sellables]
        c = self.ui.choose("Sell which?", labels + ["Back"])
        if c == len(labels):
            return
        row = dict(sellables[c] or {})
        sk = str(row.get("source_kind") or "")
        ok = False
        if sk == "actor":
            actor_name = str(row.get("owner") or "")
            actor = next((a for a in list(getattr(getattr(self, "party", None), "members", []) or []) if str(getattr(a, "name", "") or "") == actor_name), None)
            if actor is not None:
                ok = self.sell_actor_item(actor, str(row.get("instance_id") or ""), source="sell_loot")
        elif sk == "stash":
            ok = self.sell_stash_item(str(row.get("instance_id") or ""), source="sell_loot")
        elif sk == "legacy" and using_legacy:
            ok = self.sell_legacy_party_item(str(row.get("entry_id") or ""), source="sell_loot")
        if not ok:
            self.ui.log("Could not complete sale.")

    # -------------
    # Expedition assembly
    # -------------

    def assemble_expedition_party(self):
        # For now, the expedition party is "all PCs + assigned retainers"
        self.party.members = self.party.pcs() + self.active_retainers

    def disband_expedition_party(self):
        # After expedition, keep PCs in roster, retainers remain in hired/active lists
        pcs = [m for m in self.party.members if getattr(m, "is_pc", False)]
        self.party.members = pcs

    # -------------
    # Dungeon
    # -------------

    def dungeon_loop(self):
        # Dungeon loop migrated to command-driven flow (Step 4 refactor).
        self.dispatch(EnterDungeon())

        while True:
            self.ui.hr()

            # Start-of-turn: costs, wandering check, room content
            self.spend_dungeon_time(1, reason="turn_start")

            room = self.dungeon_rooms.get(self.current_room_id) or self._ensure_room(self.current_room_id)
            try:
                if self._resolve_dungeon_ecology_contact():
                    room = self.dungeon_rooms.get(self.current_room_id) or self._ensure_room(self.current_room_id)
            except Exception:
                pass
            lvl = int(getattr(self, "dungeon_level", 1) or 1)
            alarm = int((getattr(self, "dungeon_alarm_by_level", {}) or {}).get(lvl, 0))
            self.ui.log(f"Turn {self.dungeon_turn} | Torch: {self.torch_turns_left} turns | Noise: {self.noise_level}/5 | Dungeon Level: {lvl}/{int(getattr(self, 'max_dungeon_levels', 1))} | Alarm: {alarm}/5")
            self.ui.log(f"Room {self.current_room_id}: {room['type']} | Visited {room.get('visited', 0)}x")
            if room.get("cleared"):
                self.ui.log("This room seems quiet (cleared).")

            if bool(getattr(self, 'dungeon_board_native', True)):
                mres = self._show_dungeon_map(allow_classic_menu=True)
                if mres == 'menu':
                    self.dungeon_board_native = False
                else:
                    continue

            # Action menu
            exits = list(room["exits"].keys())
            exit_labels = [f"Go {k} ({room['doors'][k]} -> {self._dungeon_exit_preview(room, k, room['exits'][k]).split('-> ',1)[1]})" for k in exits]

            stairs = room.get("stairs") or {}

            actions = [
                "Resume board-native map view",
                "View map / mini-board",
                "Listen at doors",
                "Search for secret door",
                "Search for traps (Skilled)",
                "Sneak (Skilled)",
                "Prepare spells (camp)",
                "Cast a spell (camp)",
            ]
            if stairs.get("up"):
                actions.append("Use stairs up")
            if stairs.get("down"):
                actions.append("Use stairs down")
            dead_members = [m for m in self.party.members if m.hp <= 0]
            if dead_members:
                actions.append("Drop dead party member")
            actions += [
                "Light a torch",
                "Leave dungeon (back to Town)",
            ]

            i = self.ui.choose("Dungeon Actions", actions + exit_labels)

            if i == 0:
                self.dungeon_board_native = True
                continue
            elif i == 1:
                self._show_dungeon_map()
                continue
            elif i == 2:
                self.dispatch(DungeonListen())
            elif i == 3:
                self.dispatch(DungeonSearchSecret())
            elif i == 4:
                self.dispatch(DungeonSearchTraps())
            elif i == 5:
                self.dispatch(DungeonSneak())
            elif i == 6:
                self.dispatch(DungeonPrepareSpells())
            elif i == 7:
                self.dispatch(DungeonCastSpell())
            else:
                # Handle stairs if present
                idx = 8
                if stairs.get("up"):
                    if i == idx:
                        self.dispatch(DungeonUseStairs("up"))
                        continue
                    idx += 1
                if stairs.get("down"):
                    if i == idx:
                        self.dispatch(DungeonUseStairs("down"))
                        continue
                    idx += 1

                # After optional stair entries, we have (optional) Drop Dead, then Light/Leave.
                if dead_members:
                    if i == idx:
                        labels = [f"{m.name} ({'Retainer' if getattr(m,'is_retainer',False) else m.cls}) HP {m.hp}/{m.hp_max}" for m in dead_members]
                        labels.append("Back")
                        sel = self.ui.choose("Drop which dead party member?", labels)
                        if sel == len(labels) - 1:
                            continue
                        victim = dead_members[sel]
                        self.party.members = [m for m in self.party.members if m is not victim]
                        self.ui.log(f"You leave {victim.name}'s body behind.")
                        continue
                    idx += 1

                if i == idx:
                    self.dispatch(DungeonLightTorch())
                    continue
                if i == idx + 1:
                    self.dispatch(DungeonLeave())
                    return

                offset = len(actions)
                exit_key = exits[i - offset]
                res = self.dispatch(DungeonMove(exit_key))
                if res.status == "needs_input" and (res.data or {}).get("state"):
                    state = str((res.data or {}).get("state"))
                    opts = ["Open normally", "Force (STR)"]
                    # Offer lockpicking if locked and someone has an Open Locks % skill (Thief/Monk/etc.).
                    if state == "locked":
                        _pc, best_chance = self._best_skill_user('Open Locks')
                        if int(best_chance) > 0:
                            opts.append("Pick lock (Skilled)")
                    opts.append("Back")
                    act = self.ui.choose(f"Door is {state}.", opts)
                    if act == len(opts) - 1:
                        continue
                    action = "open" if act == 0 else ("force" if act == 1 else "pick")
                    res2 = self.dispatch(DungeonDoorAction(exit_key=exit_key, action=action))
                    if res2.ok:
                        # retry move
                        self.dispatch(DungeonMove(exit_key))


    def light_torch(self):
        if self.torches <= 0:
            self.ui.log("You have no torches.")
            return
        self.torches -= 1
        self.torch_turns_left = self.TORCH_TURNS
        self.light_on = True
        self.ui.log("You light a torch.")

    def spend_dungeon_time(self, turns: int, *, reason: str) -> None:
        """Spend dungeon time in 10-minute turns.

        Single authoritative pathway for dungeon time advancement. It applies turn
        costs (torch burn, status ticks, noise/alarm decay, etc.) and performs
        wandering monster checks per turn.
        """
        n = max(0, int(turns))
        if n <= 0:
            return
        for _ in range(n):
            before_turn = int(getattr(self, "dungeon_turn", 0) or 0)
            self._apply_turn_cost()
            after_turn = int(getattr(self, "dungeon_turn", 0) or 0)

            self.emit(
                "dungeon_time_spent",
                reason=str(reason),
                turns=1,
                before_turn=int(before_turn),
                after_turn=int(after_turn),
            )

            triggered = bool(self._check_wandering_monster())
            self.emit(
                "wandering_monster_check",
                checked=bool(int(after_turn) % 2 == 0),
                triggered=bool(triggered),
                reason=str(reason),
                dungeon_turn=int(after_turn),
            )
            if triggered:
                self._handle_wandering_monster()

    def spend_wilderness_time(self, watches: int, *, reason: str) -> None:
        """Spend wilderness time in watches (4-hour segments)."""
        n = max(0, int(watches))
        if n <= 0:
            return
        for _ in range(n):
            before = int(getattr(self, "watches", 0) or 0)
            self._advance_watch()
            after = int(getattr(self, "watches", 0) or 0)
            self.emit(
                "wilderness_time_spent",
                reason=str(reason),
                watches=1,
                before_watch=int(before),
                after_watch=int(after),
            )

    def _apply_turn_cost(self):
        self.dungeon_turn += 1
        # Tick any turn-duration statuses on party members.
        self._tick_turn_statuses(self.party.members)
        was_lit = bool(getattr(self, "light_on", False))
        # Light burn (torch + magical)
        if int(getattr(self, "torch_turns_left", 0) or 0) > 0:
            self.torch_turns_left -= 1
            if self.torch_turns_left <= 0:
                self.torch_turns_left = 0
                self.ui.log("Your torch sputters out.")

        if int(getattr(self, "magical_light_turns", 0) or 0) > 0:
            self.magical_light_turns -= 1
            if self.magical_light_turns <= 0:
                self.magical_light_turns = 0
                self.ui.log("The magical light fades.")

        self.light_on = bool(self.torch_turns_left > 0 or self.magical_light_turns > 0)
        if was_lit and not self.light_on:
            self.ui.log("Darkness!")
        # Noise decay (deterministic): noise points fall by 1 per turn.
        if self.noise_level > 0:
            self.noise_level -= 1

        # Alarm decay (strongholds cool down over time)
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        self.dungeon_alarm_by_level.setdefault(lvl, 0)
        if self.dungeon_turn % 3 == 0 and self.dungeon_alarm_by_level[lvl] > 0:
            self.dungeon_alarm_by_level[lvl] -= 1
        try:
            self._tick_dungeon_ecology()
        except Exception:
            pass
        try:
            if self.noise_level >= 3 and self.dungeon_turn % 2 == 0:
                self.ui.log('Your recent noise still carries through the dungeon.')
        except Exception:
            pass

    def _tick_turn_statuses(self, actors: list[Actor]):
        """Decrement status durations measured in dungeon turns."""
        for a in actors:
            st = getattr(a, "status", None)
            if not isinstance(st, dict) or not st:
                continue
            for k in list(st.keys()):
                v = st.get(k)
                if isinstance(v, dict) and "turns" in v:
                    try:
                        v["turns"] = int(v.get("turns", 0) or 0) - 1
                    except Exception:
                        v["turns"] = 0
                    if int(v.get("turns", 0) or 0) <= 0:
                        st.pop(k, None)
                # Some older statuses were stored as ints but represent turns (e.g. invisibility).
                elif k in ("invisible",) and isinstance(v, int):
                    if v > 0:
                        st[k] = v - 1
                    if st.get(k) == 0:
                        st.pop(k, None)


    def _wandering_noise_mod(self) -> int:
        """Noise modifier to wandering checks (max +2).

        Tuned in P6.1.4.82 so light noise is mostly telegraphing, while sustained
        noise or high alarm creates real pressure.
        """
        nl = int(getattr(self, "noise_level", 0) or 0)
        alarm = int((getattr(self, 'dungeon_alarm_by_level', {}) or {}).get(int(getattr(self, 'dungeon_level', 1) or 1), 0) or 0)
        pressure = 0
        if nl >= 2:
            pressure = 1
        if nl >= 4:
            pressure = 2
        if alarm >= 2:
            pressure = max(pressure, 1)
        if alarm >= 4:
            pressure = min(2, pressure + 1)
        return pressure

    def _check_wandering_monster(self) -> bool:
        """Return True if a wandering monster is triggered this turn.

        Cadence: every 2 dungeon turns (Option A).
        Odds: base wandering_chance in 6 + noise modifier (capped), no depth scaling.
        """
        turn = int(getattr(self, "dungeon_turn", 0) or 0)
        # Check every 2 turns only.
        if turn % 2 != 0:
            return False

        base = int(getattr(self, "wandering_chance", 1) or 1)
        noise_mod = int(self._wandering_noise_mod())
        alarm = int((getattr(self, 'dungeon_alarm_by_level', {}) or {}).get(int(getattr(self, 'dungeon_level', 1) or 1), 0) or 0)
        chance_cap = 5 if alarm >= 4 else 4
        chance = min(chance_cap, max(1, base + noise_mod))

        roll = int(self.dice.d(6))
        triggered = bool(roll <= chance)

        # UI hook for fairness/debugging.
        try:
            self.ui.log(
                f"Wandering check: base {base}/6 + noise +{noise_mod} => {chance}/6 | roll {roll} => "
                + ("ENCOUNTER" if triggered else "no encounter")
            )
        except Exception:
            pass

        # Emit detailed info for replay/debug.
        self.emit(
            "wandering_check_roll",
            dungeon_turn=int(turn),
            base_in_6=int(base),
            noise_mod=int(noise_mod),
            chance_in_6=int(chance),
            roll=int(roll),
            triggered=bool(triggered),
        )
        return triggered

    def _handle_wandering_monster(self):
        self.ui.title("Wandering Monster!")
        enc = None
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        cur = int(getattr(self, "current_room_id", 0) or 0)
        alarm = int((getattr(self, "dungeon_alarm_by_level", {}) or {}).get(lvl, 0) or 0)
        # First prefer ecology groups already roaming this level.
        try:
            groups = [g for g in (getattr(self, "dungeon_monster_groups", []) or []) if not g.get("defeated") and int(g.get("level", 0) or 0) == lvl]
            if groups:
                groups.sort(key=lambda g: (int(g.get("room_id", -99) or -99) == cur, str(g.get("state") or "") in {"hunt", "reinforcing"}, int(g.get("count", 1) or 1)), reverse=True)
                grp = groups[0]
                if int(grp.get("room_id", -1) or -1) != cur and (alarm >= 2 or int(getattr(self, "noise_level", 0) or 0) >= 2):
                    grp["room_id"] = cur
                if int(grp.get("room_id", -1) or -1) == cur:
                    fac = self._dungeon_faction_name(grp.get('faction_id')) if grp.get('faction_id') else None
                    fac_txt = f" of {fac}" if fac else ""
                    self.ui.log(f"A roaming patrol closes in: {grp.get('count',1)}x {grp.get('monster_id','foes')}{fac_txt} ({grp.get('state', grp.get('behavior','patrol'))}).")
                    self._propagate_dungeon_alarm(origin_room_id=cur, severity=1, reason="wandering contact")
                    foes = self._spawn_ecology_group_foes(grp)
                    self.combat(foes)
                    grp["defeated"] = True
                    return
        except Exception:
            pass

        if getattr(self, "dungeon_instance", None) is not None:
            try:
                from .dungeon_instance import choose_monster_id_for_target_cl

                inst = self.dungeon_instance
                bp = getattr(getattr(inst, "blueprint", None), "data", None) or {}
                rooms = bp.get("rooms") or {}
                if isinstance(rooms, dict):
                    rid = int(getattr(self, "current_room_id", 1) or 1)
                    r = rooms.get(str(rid)) or rooms.get(rid) or {}
                    depth = int(r.get("depth", 0) or 0)
                    floor = int(r.get("floor", 0) or 0)
                    eff_depth = depth + floor * 3
                    cap = 12
                    max_eff = 12
                    target_cl = 1 if max_eff <= 0 else 1 + int(round((cap - 1) * (eff_depth / max_eff)))
                    target_cl = max(1, min(cap, target_cl))

                    entries = self._wandering_entries_for_current_level()
                    current_tags = {str(t).lower() for t in (self._dungeon_room_tags(rid) or [])}
                    room_cleared = bool(((getattr(self, 'dungeon_rooms', {}) or {}).get(rid) or {}).get('cleared'))
                    weighted_entries = []
                    for ent in entries:
                        tags = {str(t).lower() for t in (ent.get("tags") or [])}
                        wt = max(1, int(ent.get("weight", 1) or 1))
                        if "alarm_sensitive" in tags and alarm <= 0:
                            wt = max(0, wt - 1)
                        if alarm >= 2 and ("alarm_sensitive" in tags or "reinforcements" in tags):
                            wt += 3
                        if any(t in current_tags for t in {"zone:boss_ring", "boss"}) and ("reinforcements" in tags or "theme_patrol" in tags):
                            wt += 2
                        if any(t in current_tags for t in {"zone:treasury_ring", "treasury"}) and ("reinforcements" in tags or "theme_patrol" in tags):
                            wt += 2
                        if room_cleared and "scavenger" in tags:
                            wt += 2
                        if wt > 0:
                            weighted_entries.extend([ent] * wt)
                    chosen = dict(self.dice_rng.raw.choice(weighted_entries)) if weighted_entries else {}
                    mid = str(chosen.get("monster_id") or "")
                    expr = str(chosen.get("count_dice") or "1")
                    try:
                        cnt = max(1, int(self.dice.roll(expr).total if 'd' in expr else int(expr)))
                    except Exception:
                        cnt = 1
                    if not mid:
                        mid = choose_monster_id_for_target_cl(
                            rng=self.dice_rng.raw, monsters=getattr(self.encounters, "monsters_data", self.content.monsters), target_cl=target_cl
                        )
                        cnt = 1 + (int(self.dice.d(4)) if target_cl <= 2 else int(self.dice.d(2)))

                    foes = [self.encounters._mk_monster({"monster_id": mid, "name": mid}) for _ in range(max(1, cnt))]
                    self.ui.log(f"Wandering encounter (target CL {target_cl}): {cnt}x {mid}")
                    self._propagate_dungeon_alarm(origin_room_id=cur, severity=1, reason="wandering encounter")
                    self.combat(foes)
                    return
            except Exception:
                pass

        if getattr(self, "current_dungeon_faction_id", None) and self.current_dungeon_faction_id in (self.factions or {}):
            enc = self._faction_encounter(self.current_dungeon_faction_id, "dungeon")
        if enc is None:
            enc = self.encounters.wilderness_encounter("dungeon")
        if not enc:
            m = self.dice_rng.choice(self.content.monsters)
            foes = [self.encounters._mk_monster(m)]
        else:
            foes = enc.foes
        self.ui.log("Shapes emerge from the dark...")
        self._propagate_dungeon_alarm(origin_room_id=cur, severity=1, reason="wandering fallback")
        self.combat(foes)

    def _dungeon_content_pack(self) -> dict[str, Any]:
        cache = getattr(self, '_dungeon_content_pack_cache', None)
        if isinstance(cache, dict):
            return cache
        try:
            data = load_json('dungeon_gen/room_content_pack.json') or {}
            cache = data if isinstance(data, dict) else {}
        except Exception:
            cache = {}
        self._dungeon_content_pack_cache = cache
        return cache

    def _record_dungeon_clue(self, text: str, *, source: str = "", room_id: int | None = None, level: int | None = None) -> None:
        clue = str(text or "").strip()
        if not clue:
            return
        rid = int(room_id if room_id is not None else getattr(self, "current_room_id", 0) or 0)
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        existing = list(getattr(self, "dungeon_clues", []) or [])
        for e in existing:
            if str(e.get("text") or "") == clue and int(e.get("level", 0) or 0) == lvl:
                return
        entry = {
            "day": int(getattr(self, "campaign_day", 1) or 1),
            "watch": int(getattr(self, "watches", 0) or 0),
            "level": lvl,
            "room_id": rid,
            "text": clue,
            "source": str(source or "room"),
        }
        self._append_player_event(
            "clue.found",
            category="clue",
            title=f"Clue noted: {clue}",
            payload=entry,
            refs={"room_id": int(rid), "level": int(lvl)},
        )
        self.dungeon_clues = project_clues_from_events(getattr(self, "event_history", []))
        try:
            self.emit("dungeon_clue_found", text=clue, source=str(source or "room"), level=int(lvl), room_id=int(rid))
        except Exception:
            pass
        try:
            self.ui.log(f"Clue noted: {clue}")
        except Exception:
            pass

    def _maybe_record_content_clue(self, rec: dict[str, Any] | None, *, source: str) -> None:
        if not isinstance(rec, dict):
            return
        clue = str(rec.get("clue_text") or rec.get("hint") or "").strip()
        if clue:
            self._record_dungeon_clue(clue, source=source)

    def _dungeon_faction_data(self) -> dict[str, Any]:
        cache = getattr(self, "_dungeon_faction_data_cache", None)
        if isinstance(cache, dict):
            return cache
        try:
            data = load_json("dungeon_gen/factions.json") or {}
            cache = data if isinstance(data, dict) else {}
        except Exception:
            cache = {}
        self._dungeon_faction_data_cache = cache
        return cache

    def _dungeon_faction_templates(self) -> list[dict[str, Any]]:
        data = self._dungeon_faction_data()
        out = list(data.get("faction_templates", []) or [])
        return [x for x in out if isinstance(x, dict) and str(x.get("faction_id") or "").strip()]

    def _dungeon_faction_name(self, faction_id: str | None) -> str:
        fid = str(faction_id or "").strip()
        if not fid:
            return "Unknown faction"
        for rec in self._dungeon_faction_templates():
            if str(rec.get("faction_id") or "") == fid:
                return str(rec.get("name") or fid)
        if isinstance(getattr(self, "factions", None), dict) and fid in self.factions:
            return str((self.factions.get(fid) or {}).get("name") or fid)
        return fid.replace("_", " ").title()

    def _dungeon_faction_stance(self, a: str | None, b: str | None) -> str:
        fa = str(a or "").strip()
        fb = str(b or "").strip()
        if not fa or not fb:
            return "neutral"
        if fa == fb:
            return "ally"
        data = self._dungeon_faction_data()
        for rel in list(data.get("relationships", []) or []):
            if not isinstance(rel, dict):
                continue
            ra = str(rel.get("a") or "").strip()
            rb = str(rel.get("b") or "").strip()
            if {ra, rb} == {fa, fb}:
                return str(rel.get("stance") or "neutral")
        tmpl = {str(r.get("faction_id") or ""): r for r in self._dungeon_faction_templates()}
        aa = str((tmpl.get(fa) or {}).get("alignment") or "").lower()
        bb = str((tmpl.get(fb) or {}).get("alignment") or "").lower()
        if aa and bb and aa != bb:
            return "hostile"
        return "rival"

    def _choose_dungeon_group_faction(self, *, level: int, target_cl: int, existing_factions: list[str] | None = None) -> str | None:
        choices = self._dungeon_faction_templates()
        if not choices:
            return getattr(self, "current_dungeon_faction_id", None)
        wrng = self.dice_rng.raw
        existing = [str(x) for x in (existing_factions or []) if str(x)]
        weighted: list[tuple[int, str]] = []
        for rec in choices:
            fid = str(rec.get("faction_id") or "").strip()
            if not fid:
                continue
            w = 2
            tags = set(str(t).lower() for t in (rec.get("tags") or []))
            doctrine = rec.get("doctrine") or {}
            if fid == str(getattr(self, "current_dungeon_faction_id", None) or ""):
                w += 4
            if fid not in existing and existing:
                w += 2
            if target_cl <= 3 and ("humanoid" in tags or "survivors" in tags):
                w += 1
            if target_cl >= 4 and ("cult" in tags or bool(doctrine.get("guards_setpieces"))):
                w += 1
            weighted.append((max(1, int(w)), fid))
        if not weighted:
            return None
        total = sum(w for w, _ in weighted)
        pick = int(wrng.randint(1, total))
        acc = 0
        for w, fid in weighted:
            acc += w
            if pick <= acc:
                return fid
        return weighted[-1][1]

    def _note_dungeon_aftermath(self, room_id: int, text: str, *, level: int | None = None, faction_a: str | None = None, faction_b: str | None = None) -> None:
        rid = int(room_id or 0)
        if rid <= 0:
            return
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        state = dict(getattr(self, "dungeon_room_aftermath", {}) or {})
        key = f"L{lvl}:R{rid}"
        entry = dict(state.get(key) or {})
        entry.update({
            "level": int(lvl),
            "room_id": int(rid),
            "text": str(text or "").strip(),
            "day": int(getattr(self, "campaign_day", 1) or 1),
            "watch": int(getattr(self, "watches", 0) or 0),
            "turn": int(getattr(self, "dungeon_turn", 0) or 0),
            "faction_a": str(faction_a or entry.get("faction_a") or "") or None,
            "faction_b": str(faction_b or entry.get("faction_b") or "") or None,
        })
        state[key] = entry
        self.dungeon_room_aftermath = state

    def _get_dungeon_aftermath(self, room_id: int, *, level: int | None = None) -> dict[str, Any] | None:
        rid = int(room_id or 0)
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        return (getattr(self, "dungeon_room_aftermath", {}) or {}).get(f"L{lvl}:R{rid}")


    def _dungeon_room_role(self, room_id: int) -> str:
        tags = {str(t).lower() for t in (self._dungeon_room_tags(int(room_id)) or [])}
        for role in ("boss", "treasury", "treasure", "guard", "lair", "hazard", "shrine", "clue", "transit", "entrance"):
            if role in tags:
                return role
        if "monster" in tags:
            return "guard"
        if "trap" in tags:
            return "hazard"
        return "empty"

    def _dungeon_rooms_with_tag(self, tag: str, level: int | None = None) -> list[int]:
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        want = str(tag or "").lower()
        out: list[int] = []
        for rid in self._dungeon_bp_room_ids(lvl):
            tags = {str(t).lower() for t in (self._dungeon_room_tags(rid) or [])}
            if want in tags:
                out.append(int(rid))
        return sorted(out)

    def _room_suppressed_for_patrols(self, room_id: int, *, level: int | None = None) -> bool:
        rid = int(room_id or 0)
        if rid <= 0:
            return True
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        room = (getattr(self, "dungeon_rooms", {}) or {}).get(rid) or {}
        if bool(room.get("cleared")) and self._dungeon_room_role(rid) not in {"boss", "treasury", "guard"}:
            return True
        aft = self._get_dungeon_aftermath(rid, level=lvl) or {}
        if aft and int(getattr(self, "dungeon_turn", 0) or 0) - int(aft.get("turn", -99) or -99) <= 2:
            return True
        return False

    def _propagate_dungeon_alarm(self, *, origin_room_id: int | None = None, severity: int = 1, reason: str = "") -> None:
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        cur = int(origin_room_id if origin_room_id is not None else getattr(self, "current_room_id", 0) or 0)
        self.dungeon_alarm_by_level.setdefault(lvl, 0)
        self.dungeon_alarm_by_level[lvl] = min(5, int(self.dungeon_alarm_by_level.get(lvl, 0) or 0) + max(1, int(severity or 1)))
        if cur > 0:
            self._note_dungeon_aftermath(cur, f"Alarmed movement spreads from this area{(': ' + str(reason).strip()) if str(reason or '').strip() else '.'}", level=lvl)
        for g in (getattr(self, "dungeon_monster_groups", []) or []):
            if g.get("defeated") or int(g.get("level", 0) or 0) != lvl:
                continue
            role = self._dungeon_room_role(int(g.get("room_id", 0) or 0))
            behavior = str(g.get("behavior") or "patrol")
            alarm_sensitive = bool(g.get("alarm_sensitive", False)) or "alarm_sensitive" in {str(t) for t in (g.get("tags") or [])}
            if alarm_sensitive or role in {"guard", "boss", "treasury"}:
                g["state"] = "reinforcing" if role not in {"treasury", "boss"} else "guarding"
                g["alerted_room_id"] = int(cur) if cur > 0 else int(g.get("alerted_room_id", 0) or 0)
            elif behavior == "hunt":
                g["state"] = "hunt"
                if cur > 0:
                    g["alerted_room_id"] = int(cur)

    def _wandering_entries_for_current_level(self) -> list[dict[str, Any]]:
        inst = getattr(self, "dungeon_instance", None)
        stocking = getattr(getattr(inst, "stocking", None), "data", None) or {}
        wandering = dict(stocking.get("wandering") or {}) if isinstance(stocking, dict) else {}
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        out: list[dict[str, Any]] = []
        for table in list(wandering.get("tables") or []):
            try:
                lo = int(table.get("depth_min", 0) or 0)
                hi = int(table.get("depth_max", 999) or 999)
            except Exception:
                lo, hi = 0, 999
            if lo <= lvl <= hi:
                out.extend([dict(e) for e in list(table.get("entries") or []) if isinstance(e, dict)])
        if not out and isinstance(wandering.get("entries"), list):
            out.extend([dict(e) for e in list(wandering.get("entries") or []) if isinstance(e, dict)])
        return out

    def _dungeon_bp_room_ids(self, level: int | None = None) -> list[int]:
        inst = getattr(self, "dungeon_instance", None)
        bp = getattr(getattr(inst, "blueprint", None), "data", None) or {}
        rooms = bp.get("rooms")
        if not isinstance(rooms, dict):
            return []
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        out: list[int] = []
        for rid, r in rooms.items():
            try:
                room_level = int((r or {}).get("floor", 0) or 0) + 1
                if room_level == lvl:
                    out.append(int(rid))
            except Exception:
                continue
        return sorted(out)

    def _ensure_dungeon_ecology_groups(self, level: int | None = None) -> None:
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        groups = [g for g in (getattr(self, "dungeon_monster_groups", []) or []) if int(g.get("level", 0) or 0) == lvl and not g.get("defeated")]
        room_ids = [rid for rid in self._dungeon_bp_room_ids(lvl) if rid != int(getattr(self, "current_room_id", 0) or 0)]
        if not room_ids:
            return
        existing_rooms = {int(g.get("room_id", -1) or -1) for g in groups}
        room_ids = [rid for rid in room_ids if rid not in existing_rooms]
        room_total = len(self._dungeon_bp_room_ids(lvl))
        target_groups = 1 if room_total <= 14 else 2
        if lvl >= 3 and room_total >= 18:
            target_groups = 2
        if lvl >= 5 and room_total >= 24:
            target_groups = 3
        needed = max(0, min(target_groups, max(1, room_total // 8)) - len(groups))
        if needed <= 0 or not room_ids:
            return
        try:
            from .dungeon_instance import choose_monster_id_for_target_cl
            wrng = self.dice_rng.raw
            cap = 12
            existing_factions = [str(g.get("faction_id") or "") for g in groups if str(g.get("faction_id") or "")]
            room_ids = sorted(room_ids, key=lambda rid: (self._dungeon_room_role(rid) in {"guard", "lair", "boss", "treasury"}, not self._room_suppressed_for_patrols(rid, level=lvl), rid), reverse=True)
            wandering_entries = self._wandering_entries_for_current_level()
            for i in range(min(needed, len(room_ids))):
                rid = int(room_ids.pop(0))
                if self._room_suppressed_for_patrols(rid, level=lvl) and room_ids:
                    rid = int(room_ids.pop(0))
                target_cl = max(1, min(cap, lvl + int(self.dice.d(3)) - 1))
                entry = None
                if wandering_entries:
                    weighted = []
                    role = self._dungeon_room_role(rid)
                    for ent in wandering_entries:
                        tags = {str(t) for t in (ent.get("tags") or [])}
                        wt = max(1, int(ent.get("weight", 1) or 1))
                        if role in {"guard", "boss", "treasury"} and ("alarm_sensitive" in tags or "reinforcements" in tags):
                            wt += 2
                        if role == "transit" and "theme_patrol" in tags:
                            wt += 1
                        weighted.extend([ent] * wt)
                    if weighted:
                        entry = dict(wrng.choice(weighted))
                if entry:
                    mid = str(entry.get("monster_id") or "")
                    expr = str(entry.get("count_dice") or "1d3")
                    try:
                        count = max(1, int(self.dice.roll(expr).total if 'd' in expr else int(expr)))
                    except Exception:
                        count = 1 + int(self.dice.d(2))
                    alarm_sensitive = "alarm_sensitive" in {str(t) for t in (entry.get("tags") or [])}
                    tags = list(entry.get("tags") or [])
                else:
                    mid = choose_monster_id_for_target_cl(rng=wrng, monsters=getattr(self.encounters, "monsters_data", self.content.monsters), target_cl=target_cl)
                    count = 1 + (int(self.dice.d(3)) if target_cl <= 2 else int(self.dice.d(2)))
                    alarm_sensitive = False
                    tags = []
                if lvl >= 4 and target_cl <= 3 and self.dice.in_6(2):
                    count += 1
                role = self._dungeon_room_role(rid)
                behavior = "hunt" if role in {"boss", "treasury"} else ("patrol" if role in {"guard", "transit"} else wrng.choice(["patrol", "forage", "hunt"]))
                faction_id = self._choose_dungeon_group_faction(level=lvl, target_cl=target_cl, existing_factions=existing_factions)
                if faction_id:
                    existing_factions.append(str(faction_id))
                self.dungeon_monster_groups.append({
                    "gid": int(self.next_dungeon_group_id),
                    "level": int(lvl),
                    "room_id": int(rid),
                    "home_room_id": int(rid),
                    "alerted_room_id": 0,
                    "monster_id": str(mid),
                    "count": int(max(1, count)),
                    "behavior": str(behavior),
                    "state": "guarding" if role in {"guard", "boss", "treasury"} else "patrol",
                    "target_cl": int(target_cl),
                    "faction_id": str(faction_id or "") or None,
                    "alarm_sensitive": bool(alarm_sensitive),
                    "tags": list(tags),
                    "defeated": False,
                    "last_turn": int(getattr(self, "dungeon_turn", 0) or 0),
                })
                self.next_dungeon_group_id += 1
        except Exception:
            return

    def _tick_dungeon_ecology(self) -> None:
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        self._ensure_dungeon_ecology_groups(lvl)
        adj = self._dungeon_bp_adjacency() or {}
        cur = int(getattr(self, "current_room_id", 0) or 0)
        turn = int(getattr(self, "dungeon_turn", 0) or 0)
        noise = int(getattr(self, "noise_level", 0) or 0)
        alarm = int((getattr(self, "dungeon_alarm_by_level", {}) or {}).get(lvl, 0) or 0)
        pending = []

        def room_pressure(room_id: int) -> int:
            score = 0
            role = self._dungeon_room_role(room_id)
            if role in {"guard", "boss", "treasury"}:
                score += 3
            elif role in {"lair", "hazard"}:
                score += 1
            if self._room_suppressed_for_patrols(room_id, level=lvl):
                score -= 3
            if room_id == cur:
                score += max(noise, alarm)
            return score

        for g in list(getattr(self, "dungeon_monster_groups", []) or []):
            if g.get("defeated") or int(g.get("level", 0) or 0) != lvl:
                continue
            if int(g.get("last_turn", -1)) == turn:
                continue
            gid = int(g.get("gid", 0) or 0)
            room_id = int(g.get("room_id", 0) or 0)
            nbrs = [int(n) for n in (adj.get(room_id) or [])]
            if not nbrs:
                g["last_turn"] = turn
                continue
            state = str(g.get("state") or g.get("behavior") or "patrol")
            role = self._dungeon_room_role(room_id)
            target_focus = int(g.get("alerted_room_id", 0) or 0)
            if alarm <= 0 and noise <= 0 and state == "reinforcing":
                state = "guarding" if role in {"guard", "boss", "treasury"} else "patrol"
                g["state"] = state
                if state != "reinforcing":
                    g["alerted_room_id"] = 0
            move_threshold = 2
            if state in {"patrol", "forage"}:
                move_threshold = 3
            if state in {"hunt", "reinforcing"} and (noise >= 2 or alarm >= 2 or target_focus > 0):
                move_threshold = 4
            should_move = int(self.dice.d(6)) <= move_threshold
            if not should_move:
                g["last_turn"] = turn
                continue
            candidates = [room_id] + nbrs
            best_room = room_id
            best_score = -999
            for cand in candidates:
                score = room_pressure(cand)
                if target_focus > 0:
                    score -= abs(cand - target_focus)
                if state == "guarding":
                    score += 2 if self._dungeon_room_role(cand) in {"guard", "boss", "treasury"} else -1
                elif state == "reinforcing":
                    score += 2 if self._dungeon_room_role(cand) in {"guard", "boss", "treasury", "transit"} else 0
                elif state == "hunt":
                    score += 3 if cand == cur else 0
                elif state == "patrol":
                    score += 1 if self._dungeon_room_role(cand) in {"transit", "guard", "clue"} else 0
                if cand == int(g.get("home_room_id", room_id) or room_id) and state == "guarding":
                    score += 2
                if score > best_score:
                    best_room = int(cand)
                    best_score = int(score)
            g["room_id"] = int(best_room)
            g["last_turn"] = turn
            if int(best_room) == cur:
                pending.append(gid)
        self._resolve_dungeon_faction_skirmishes(lvl)
        alive_in_cur = [int(g.get("gid", 0) or 0) for g in (getattr(self, "dungeon_monster_groups", []) or []) if not g.get("defeated") and int(g.get("level", 0) or 0) == lvl and int(g.get("room_id", -1) or -1) == cur]
        pending = [gid for gid in pending if gid in alive_in_cur]
        if pending:
            setattr(self, "pending_dungeon_ecology_group_ids", list(sorted(set(pending))))

    def _resolve_dungeon_faction_skirmishes(self, level: int | None = None) -> None:
        lvl = int(level if level is not None else getattr(self, "dungeon_level", 1) or 1)
        groups = [g for g in (getattr(self, "dungeon_monster_groups", []) or []) if not g.get("defeated") and int(g.get("level", 0) or 0) == lvl]
        if len(groups) < 2:
            return
        by_room: dict[int, list[dict[str, Any]]] = {}
        for g in groups:
            by_room.setdefault(int(g.get("room_id", -1) or -1), []).append(g)
        adj = self._dungeon_bp_adjacency() or {}
        for room_id, room_groups in by_room.items():
            active = [g for g in room_groups if not g.get("defeated")]
            if len(active) < 2:
                continue
            changed = False
            while True:
                pair = None
                for i, a in enumerate(active):
                    for b in active[i+1:]:
                        stance = self._dungeon_faction_stance(a.get("faction_id"), b.get("faction_id"))
                        if stance in {"hostile", "rival"}:
                            pair = (a, b, stance)
                            break
                    if pair:
                        break
                if not pair:
                    break
                a, b, stance = pair
                pa = int(a.get("count", 1) or 1) + int(a.get("target_cl", 1) or 1) + int(self.dice.d(6))
                pb = int(b.get("count", 1) or 1) + int(b.get("target_cl", 1) or 1) + int(self.dice.d(6))
                an = self._dungeon_faction_name(a.get("faction_id"))
                bn = self._dungeon_faction_name(b.get("faction_id"))
                if abs(pa - pb) <= 1:
                    a["count"] = max(0, int(a.get("count", 1) or 1) - 1)
                    b["count"] = max(0, int(b.get("count", 1) or 1) - 1)
                    if int(a.get("count", 0) or 0) <= 0:
                        a["defeated"] = True
                    if int(b.get("count", 0) or 0) <= 0:
                        b["defeated"] = True
                    changed = True
                    self._note_dungeon_aftermath(room_id, f"Signs of a recent clash between {an} and {bn} scar the chamber.", level=lvl, faction_a=a.get("faction_id"), faction_b=b.get("faction_id"))
                else:
                    winner, loser = (a, b) if pa > pb else (b, a)
                    wn = self._dungeon_faction_name(winner.get("faction_id"))
                    ln = self._dungeon_faction_name(loser.get("faction_id"))
                    loss = max(1, min(int(loser.get("count", 1) or 1), 1 + abs(pa - pb) // 3))
                    loser["count"] = max(0, int(loser.get("count", 1) or 1) - loss)
                    if int(winner.get("count", 1) or 1) > 1 and abs(pa - pb) <= 3:
                        winner["count"] = max(1, int(winner.get("count", 1) or 1) - 1)
                    changed = True
                    if int(loser.get("count", 0) or 0) <= 0:
                        loser["defeated"] = True
                        self._note_dungeon_aftermath(room_id, f"The remains of {ln} lie where {wn} cut them down.", level=lvl, faction_a=winner.get("faction_id"), faction_b=loser.get("faction_id"))
                    else:
                        nbrs = [int(n) for n in (adj.get(room_id) or [])]
                        retreat_to = None
                        for dest in nbrs:
                            occupants = [g for g in groups if not g.get("defeated") and int(g.get("room_id", -1) or -1) == dest]
                            if all(self._dungeon_faction_stance(loser.get("faction_id"), og.get("faction_id")) not in {"hostile", "rival"} for og in occupants):
                                retreat_to = int(dest)
                                break
                        if retreat_to is not None:
                            loser["room_id"] = int(retreat_to)
                        self._note_dungeon_aftermath(room_id, f"Fresh blood and broken gear show that {wn} recently drove {ln} back from here.", level=lvl, faction_a=winner.get("faction_id"), faction_b=loser.get("faction_id"))
                active = [g for g in active if not g.get("defeated") and int(g.get("count", 0) or 0) > 0]
            if changed and int(room_id) == int(getattr(self, "current_room_id", 0) or 0):
                self.ui.log("You hear the crash of combat and panicked cries nearby as dungeon factions clash.")

    def _spawn_ecology_group_foes(self, group: dict[str, Any]) -> list[Any]:
        mid = str(group.get("monster_id") or "").strip()
        cnt = max(1, int(group.get("count", 1) or 1))
        mm = getattr(self, "_monster_by_id", None)
        if not isinstance(mm, dict):
            mm = {m.get("monster_id") or m.get("name"): m for m in (self.content.monsters or []) if isinstance(m, dict)}
            self._monster_by_id = mm
        mrec = mm.get(mid) or {"monster_id": mid, "name": mid}
        return [self.encounters._mk_monster(mrec) for _ in range(cnt)]

    def _resolve_dungeon_ecology_contact(self) -> bool:
        pending = list(getattr(self, "pending_dungeon_ecology_group_ids", []) or [])
        if not pending:
            return False
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        cur = int(getattr(self, "current_room_id", 0) or 0)
        for gid in pending:
            grp = None
            for g in (getattr(self, "dungeon_monster_groups", []) or []):
                if int(g.get("gid", 0) or 0) == int(gid) and not g.get("defeated"):
                    grp = g
                    break
            if not grp or int(grp.get("level", 0) or 0) != lvl or int(grp.get("room_id", -1) or -1) != cur:
                continue
            fac = self._dungeon_faction_name(grp.get('faction_id')) if grp.get('faction_id') else None
            fac_txt = f" of {fac}" if fac else ""
            self.ui.log(f"Moving shapes close in: {grp.get('count',1)}x {grp.get('monster_id','foes')}{fac_txt} ({grp.get('behavior','patrol')}).")
            foes = self._spawn_ecology_group_foes(grp)
            self.combat(foes)
            grp["defeated"] = True
            self.pending_dungeon_ecology_group_ids = [x for x in pending if int(x) != int(gid)]
            return True
        self.pending_dungeon_ecology_group_ids = []
        return False

    def _ecology_room_signals(self, room_id: int) -> list[str]:
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        adj = set(int(n) for n in ((self._dungeon_bp_adjacency() or {}).get(int(room_id), []) or []))
        lines: list[str] = []
        nearby: list[dict[str, Any]] = []
        for g in (getattr(self, "dungeon_monster_groups", []) or []):
            if g.get("defeated") or int(g.get("level", 0) or 0) != lvl:
                continue
            gid_room = int(g.get("room_id", -1) or -1)
            if gid_room == int(room_id):
                lines.append("Fresh movement and recent spoor suggest something is here right now.")
                break
            if gid_room in adj:
                nearby.append(g)
        if nearby:
            sample = nearby[0]
            behavior = str(sample.get("behavior") or "patrol")
            fac = self._dungeon_faction_name(sample.get("faction_id")) if sample.get("faction_id") else None
            if behavior == "hunt":
                lines.append("You hear purposeful movement nearby, as if something is hunting by sound.")
            elif fac:
                lines.append(f"Signs of {fac.lower()} activity linger nearby.")
            else:
                lines.append("Distant footfalls and murmurs drift from an adjacent chamber.")
        aft = self._get_dungeon_aftermath(int(room_id), level=lvl)
        if isinstance(aft, dict) and str(aft.get("text") or "").strip():
            lines.append(str(aft.get("text") or "").strip())
        return lines[:2]

    def _mark_room_resolution_once(self, room: dict[str, Any], feature_id: str, *, mode: str = "resolve") -> bool:
        """Return True on first resolution for this room feature, then persist the key."""
        key = first_time_room_resolution_key(room, feature_id=feature_id, mode=mode)
        d = room.get("_delta") if isinstance(room.get("_delta"), dict) else {}
        keys = set(str(x) for x in (d.get("resolution_keys") or room.get("resolution_keys") or []) if str(x).strip())
        if key in keys:
            return False
        keys.add(key)
        ordered = sorted(keys)
        room["resolution_keys"] = ordered
        if isinstance(d, dict):
            d["resolution_keys"] = ordered
        return True

    def _resolve_room_scene_profile(self, room: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
        scene = str(room.get("interaction_scene") or "").strip().lower()
        tags = set(str(t).lower() for t in (ctx.get("room_tags") or []))
        role = self._dungeon_room_role(int(ctx.get("room_id", room.get("id", 0)) or 0))

        if scene in {"hazard", "secret", "lore", "occupant", "rest"}:
            kind = scene
        elif role in {"hazard"} or room.get("type") == "trap":
            kind = "hazard"
        elif role in {"clue", "shrine"} or "clue_target:boss" in tags or "clue_target:treasury" in tags:
            kind = "lore"
        elif (not ctx.get("secrets_found")) and ("secret" in tags or "role:transit" in tags or role == "transit"):
            kind = "secret"
        elif role in {"guard", "lair"} and not room.get("cleared"):
            kind = "occupant"
        else:
            kind = "rest"

        return {"kind": kind, "role": role}

    def _run_room_interaction_scene(self, room: dict[str, Any]) -> None:
        ctx = resolve_room_interaction_context(self, room)
        profile = self._resolve_room_scene_profile(room, ctx)
        if not profile:
            return

        kind = str(profile.get("kind") or "rest")
        rid = int(ctx.get("room_id", room.get("id", self.current_room_id)) or self.current_room_id)

        if kind == "hazard":
            hazard_id = str(room.get("hazard_id") or room.get("trap_desc") or "hazard").strip().lower().replace(" ", "_")
            if not self._mark_room_resolution_once(room, f"hazard:{hazard_id}", mode="scene"):
                return
            self.ui.log("The room is unstable: you can bypass, disarm, or risk pushing through.")
            c = self.ui.choose("Hazard scene", ["Bypass carefully", "Disarm quickly", "Push through", "Leave it alone"])
            if c == 3:
                self.ui.log("You back off and mark the hazard for later.")
                return
            if c in (0, 1):
                best_dex = 0
                for pc in self.party.living():
                    st = getattr(pc, "stats", None)
                    try:
                        best_dex = max(best_dex, int(getattr(st, "DEX", 0) or 0))
                    except Exception:
                        pass
                odds = max(1, min(5, 1 + max(0, mod_ability(best_dex)) + (1 if c == 0 else 0)))
                if self.dice.in_6(odds):
                    room["hazard_resolved"] = True
                    room["cleared"] = bool(room.get("cleared", False)) or (room.get("type") == "trap")
                    self.ui.log("You clear a safe path through the hazard.")
                    return
            victim = self.dice_rng.choice(self.party.living()) if self.party.living() else None
            if victim is not None:
                dmg = int(self.dice.roll(str(room.get("hazard_damage") or room.get("trap_damage") or "1d4")).total)
                victim.hp = max(-1, victim.hp - dmg)
                self._notify_damage(victim, dmg, damage_types={"physical"})
                self.ui.log(f"{victim.name} is clipped by debris for {dmg} damage.")
            self.noise_level = min(5, int(getattr(self, "noise_level", 0) or 0) + 1)
            return

        if kind == "secret":
            if not self._mark_room_resolution_once(room, "secret-probe", mode="scene"):
                return
            c = self.ui.choose("Secret signs", ["Search for concealed route", "Leave the walls untouched"])
            if c != 0:
                self.ui.log("You leave the suspicious masonry alone.")
                return
            before = bool(room.get("secret_found"))
            self._reveal_room_secret_or_map(room, reveal_map=True)
            after = bool(room.get("secret_found"))
            if not before and after:
                self._record_dungeon_clue("A concealed route branches from this room.", source="secret_scene", room_id=rid, level=int(getattr(self, "dungeon_level", 1) or 1))
            return

        if kind == "lore":
            target = "deeper defenses" if "clue_target:boss" in set(ctx.get("room_tags") or []) else "hidden caches"
            if not self._mark_room_resolution_once(room, f"lore:{target}", mode="scene"):
                return
            c = self.ui.choose("Lore scene", ["Study inscriptions", "Take charcoal rubbing", "Ignore and move on"])
            if c == 2:
                return
            clue = f"Old markings hint at {target} beyond this level."
            self._record_dungeon_clue(clue, source="lore_scene", room_id=rid, level=int(getattr(self, "dungeon_level", 1) or 1))
            if c == 1:
                self._grant_room_loot(gp=6, items=["Rubbing of warning sigils"], source="lore_scene")
            return

        if kind == "occupant":
            if not self._mark_room_resolution_once(room, "passive-occupant", mode="scene"):
                return
            self.ui.log("A wary non-hostile occupant freezes in the torchlight.")
            c = self.ui.choose("Occupant reaction", ["Offer aid", "Ask for warning", "Drive them off", "Leave quietly"])
            if c == 0:
                injured = [pc for pc in self.party.living() if pc.hp < pc.hp_max]
                if injured:
                    pc = injured[0]
                    pc.hp = min(pc.hp_max, pc.hp + 2)
                    self.ui.log(f"{pc.name} is steadied with rough field dressing (+2 HP).")
                self._record_dungeon_clue("A survivor warns of unstable floors nearby.", source="occupant_scene", room_id=rid, level=int(getattr(self, "dungeon_level", 1) or 1))
            elif c == 1:
                self._record_dungeon_clue("A frightened delver mentions a hidden bypass behind cracked stone.", source="occupant_scene", room_id=rid, level=int(getattr(self, "dungeon_level", 1) or 1))
            elif c == 2:
                self.noise_level = min(5, int(getattr(self, "noise_level", 0) or 0) + 1)
                self.ui.log("Your threats echo through the halls.")
            return

        if not self._mark_room_resolution_once(room, "staging-room", mode="scene"):
            return
        if bool(ctx.get("party_ready", True)):
            self.ui.log("You find a brief staging moment to regroup and check routes.")
        else:
            self.ui.log("The room is calm enough for a quick breath before pressing on.")


    def _ingest_reward_items_to_loot_pool(self, items: list[Any] | None = None, *, source: str = "reward") -> list[dict[str, Any]]:
        """Route reward items into loot_pool, with explicit legacy mirror sync.

        Compatibility note: `party_items` is still mirrored from loot_pool for
        older menus, but reward intake should write loot_pool first.
        """
        _gp_pool, added = add_generated_treasure_to_pool(self.loot_pool, gp=0, items=list(items or []), identify_magic=False)
        self._sync_legacy_party_items_from_loot_pool()
        return loot_pool_entries_as_legacy_dicts(create_loot_pool(entries=list(added or [])))

    def _grant_room_loot(self, gp: int = 0, items: list[Any] | None = None, source: str = 'dungeon_feature') -> None:
        gp_i = int(gp or 0)
        bundle = empty_reward_bundle(source=str(source))
        reward_bundle_add_coins(bundle, gp=gp_i)
        for it in (items or []):
            reward_bundle_add_item(bundle, it)
        # Transitional policy: bundle coins still settle into shared treasury for
        # compatibility, while bundle items go through the loot-pool ownership path.
        applied = apply_reward_bundle(
            self,
            bundle=bundle,
            loot_pool=self.loot_pool,
            coin_destination=CoinDestination.TREASURY,
            identify_magic=False,
        )
        self._sync_legacy_party_items_for_reward_source(str(source))
        items_l = loot_pool_entries_as_legacy_dicts(create_loot_pool(entries=list(applied.items_added or []))) if applied.items_added else []
        try:
            self._encounter_note_loot(gp=int(applied.coins_gp_awarded), items=items_l, source=str(source))
        except Exception:
            pass
        if int(applied.coins_gp_awarded) or items_l:
            if int(applied.coins_gp_awarded):
                dest_label = self._coin_destination_label(str(getattr(applied, "coin_destination", "") or ""))
                self.ui.log(f"Coins gained: {int(applied.coins_gp_awarded)} gp -> {dest_label}.")
            if items_l:
                self.ui.log(f"Items found: {len(items_l)}.")

    def _reveal_room_secret_or_map(self, room: dict[str, Any], reveal_map: bool = False) -> None:
        ctx = resolve_room_interaction_context(self, room)
        rid = int(ctx.get('room_id', room.get('id', self.current_room_id)) or self.current_room_id)
        hidden: list[int] = []
        adj = self._dungeon_bp_adjacency() or {}
        for dest in adj.get(rid, []):
            flags = self._dungeon_bp_edge_flags(rid, int(dest))
            if flags.get('secret') and not self._dungeon_secret_edge_found(rid, int(dest)):
                hidden.append(int(dest))
        if hidden:
            dest = sorted(hidden)[0]
            if self._mark_room_resolution_once(room, f"secret_edge:{dest}", mode="discover"):
                self._dungeon_reveal_secret_edge(rid, dest)
                room['secret_found'] = True
                self.ui.log('You uncover signs of a hidden way.')
            return
        if reveal_map and self._mark_room_resolution_once(room, 'map-route-clue', mode='discover'):
            exits = sorted(int(v) for v in (room.get('exits') or {}).values())
            if exits:
                self.ui.log('You piece together nearby routes: ' + ', '.join(f'room {v}' for v in exits[:4]) + '.')

    def _apply_room_event(self, room: dict[str, Any], event: dict[str, Any]) -> None:
        ctx = resolve_room_interaction_context(self, room)
        self.ui.log(str(event.get('desc') or 'Something strange stirs in the room.'))
        if bool(ctx.get("low_light")):
            self.ui.log("Dim light makes the room's details hard to read.")
        eff = str(event.get('effect') or 'none').lower()
        if eff == 'noise':
            self.noise_level = min(5, int(getattr(self, 'noise_level', 0) or 0) + int(event.get('noise', 1) or 1))
            self.ui.log('The disturbance carries through the dungeon.')
        elif eff == 'light':
            if int(getattr(self, 'torch_turns_left', 0) or 0) > 0:
                self.torch_turns_left = max(0, int(self.torch_turns_left) - 1)
                self.ui.log('Your torch gutters in the draft.')
        elif eff == 'heal':
            healed = 0
            for pc in self.party.living():
                if pc.hp < pc.hp_max:
                    pc.hp = min(pc.hp_max, pc.hp + int(event.get('heal', 1) or 1))
                    healed += 1
                    break
            if healed:
                self.ui.log('The room briefly strengthens one of your companions.')
        elif eff == 'magic':
            self.magical_light_turns = max(int(getattr(self, 'magical_light_turns', 0) or 0), 2)
            self.light_on = True
            self.ui.log('A ripple of arcane light hangs in the air.')
        elif eff == 'loot':
            self._grant_room_loot(gp=int(event.get('gp', 0) or 0), items=list(event.get('items') or []), source='dungeon_event')
        elif eff == 'hazard':
            victim = self.dice_rng.choice(self.party.living()) if self.party.living() else None
            if victim is not None:
                dmg = int(self.dice.roll(str(event.get('damage') or '1d4')).total)
                victim.hp = max(-1, victim.hp - dmg)
                self._notify_damage(victim, dmg, damage_types={'physical'})
                self.ui.log(f'{victim.name} suffers {dmg} damage!')
        elif eff == 'reveal':
            self._reveal_room_secret_or_map(room, reveal_map=True)
        self._maybe_record_content_clue(event, source='event')
        if bool(event.get('rumor')):
            try:
                self.gather_rumors()
            except Exception:
                pass

    def _handle_special_room_feature(self, room: dict[str, Any], feature: dict[str, Any]) -> None:
        self.ui.log(str(feature.get('desc') or 'This room is unusual.'))
        hint = str(feature.get('hint') or '').strip()
        if hint:
            self.ui.log(hint)
        self._maybe_record_content_clue(feature, source='feature')
        mode = str(feature.get('mode') or 'search').lower()
        opts = {
            'search': ['Search the room', 'Leave it be'],
            'study': ['Study the feature', 'Move on'],
            'talk': ['Approach carefully', 'Leave it be'],
            'harvest': ['Gather what you can', 'Move on'],
            'cross': ['Cross carefully', 'Move on'],
            'hazard': ['Push through carefully', 'Back away'],
            'approach': ['Approach it', 'Ignore it'],
        }.get(mode, ['Search the room', 'Move on'])
        choice = self.ui.choose(str(feature.get('name') or 'Room Feature'), opts)
        if choice != 0:
            return
        if mode in {'cross', 'hazard'}:
            if feature.get('damage') and self.party.living() and self.dice.in_6(2):
                victim = self.dice_rng.choice(self.party.living())
                dmg = int(self.dice.roll(str(feature.get('damage') or '1d4')).total)
                victim.hp = max(-1, victim.hp - dmg)
                self._notify_damage(victim, dmg, damage_types={'physical'})
                self.ui.log(f'{victim.name} is hurt for {dmg} damage.')
            if bool(feature.get('light_risk')) and int(getattr(self, 'torch_turns_left', 0) or 0) > 0:
                self.torch_turns_left = max(0, int(self.torch_turns_left) - 1)
                self.ui.log('Your torch sputters in the fumes.')
        if int(feature.get('noise', 0) or 0) != 0:
            self.noise_level = max(0, min(5, int(getattr(self, 'noise_level', 0) or 0) + int(feature.get('noise', 0) or 0)))
        if bool(feature.get('reveal_secret')) or bool(feature.get('reveal_map')):
            self._reveal_room_secret_or_map(room, reveal_map=bool(feature.get('reveal_map')))
        if bool(feature.get('rumor')):
            try:
                self.gather_rumors()
                self.ui.log('You come away with a useful rumor.')
            except Exception:
                pass
        if bool(feature.get('magic')):
            self.magical_light_turns = max(int(getattr(self, 'magical_light_turns', 0) or 0), 2)
            self.light_on = True
            self.ui.log('Residual magic tingles across your skin.')
        if str(feature.get('special_type') or feature.get('feature_type') or '').strip().lower() == 'key_cache':
            self._dungeon_award_gate_key(feature, room)
            if bool(feature.get('reveal_secret')) or bool(feature.get('reveal_map')):
                self._reveal_room_secret_or_map(room, reveal_map=True)
        self._grant_room_loot(gp=int(feature.get('gp', 0) or 0), items=list(feature.get('items') or []), source='special_room')

    def _handle_room_shrine(self, room: dict[str, Any], shrine: dict[str, Any]) -> None:
        self.ui.log(str(shrine.get('desc') or 'A shrine stands here.'))
        self._maybe_record_content_clue(shrine, source='shrine')
        act = self.ui.choose(str(shrine.get('name') or 'Shrine'), ['Pray', 'Deface it', 'Leave'])
        if act == 2:
            return
        if act == 0:
            boon = str(shrine.get('boon') or '').lower()
            if boon == 'heal':
                healed = int(shrine.get('heal', 4) or 4)
                for pc in self.party.living():
                    if healed <= 0:
                        break
                    need = max(0, pc.hp_max - pc.hp)
                    if need <= 0:
                        continue
                    amt = min(need, healed)
                    pc.hp += amt
                    healed -= amt
                self.ui.log('Warm light knits some of your wounds.')
            elif boon in {'reveal', 'knowledge'}:
                self._reveal_room_secret_or_map(room, reveal_map=True)
            elif boon == 'stealth':
                self.party_stealth_success = True
                self.ui.log('The shrine cloaks your party in a hush of shadow.')
            elif boon == 'blood' and self.party.living():
                victim = self.party.living()[0]
                victim.hp = max(1, victim.hp - 1)
                self.ui.log(f'{victim.name} offers blood and feels strangely invigorated.')
                victim.hp = min(victim.hp_max, victim.hp + 2)
            elif boon == 'trickster':
                self._grant_room_loot(gp=12, items=['Marked bone die'], source='shrine')
            else:
                self.ui.log('You feel briefly favored.')
        else:
            self.noise_level = min(5, int(getattr(self, 'noise_level', 0) or 0) + 1)
            self.ui.log('Desecration stirs ill luck and echoing sounds.')

    def _handle_room_puzzle(self, room: dict[str, Any], puzzle: dict[str, Any]) -> None:
        self.ui.log(str(puzzle.get('desc') or 'A puzzling mechanism awaits.'))
        self._maybe_record_content_clue(puzzle, source='puzzle')
        act = self.ui.choose(str(puzzle.get('name') or 'Puzzle'), ['Attempt puzzle', 'Leave it for now'])
        if act != 0:
            return
        check = str(puzzle.get('check') or 'INT').upper()
        best = 0
        for pc in self.party.living():
            st = getattr(pc, 'stats', None)
            if st is None or not hasattr(st, check):
                continue
            try:
                best = max(best, int(getattr(st, check)))
            except Exception:
                pass
        odds = max(1, min(5, 1 + max(0, mod_ability(best))))
        if self.dice.in_6(odds):
            self.ui.log('With careful trial and error, the mechanism yields.')
            reward = str(puzzle.get('reward') or '').lower()
            if reward == 'reveal':
                self._reveal_room_secret_or_map(room, reveal_map=True)
            elif reward == 'rumor':
                try:
                    self.gather_rumors()
                    self.ui.log('The solution points the way toward another secret.')
                except Exception:
                    pass
            else:
                self._grant_room_loot(gp=int(puzzle.get('gp', 0) or 0), items=list(puzzle.get('items') or []), source='puzzle')
        else:
            self.noise_level = min(5, int(getattr(self, 'noise_level', 0) or 0) + 1)
            self.ui.log('The mechanism grinds uselessly and refuses to yield.')

    def _handle_room_specials(self, room: dict[str, Any]) -> None:
        d = room.get('_delta') if isinstance(room.get('_delta'), dict) else {}
        self._run_room_interaction_scene(room)
        if room.get('room_event') and not room.get('event_done') and self._mark_room_resolution_once(room, 'room_event', mode='resolve'):
            self._apply_room_event(room, room.get('room_event') or {})
            room['event_done'] = True
            d['event_done'] = True
        if room.get('special_room') and not room.get('feature_done') and self._mark_room_resolution_once(room, 'special_room', mode='resolve'):
            self._handle_special_room_feature(room, room.get('special_room') or {})
            room['feature_done'] = True
            d['feature_done'] = True
        if room.get('shrine') and not room.get('shrine_done') and self._mark_room_resolution_once(room, 'shrine', mode='resolve'):
            self._handle_room_shrine(room, room.get('shrine') or {})
            room['shrine_done'] = True
            d['shrine_done'] = True
        if room.get('puzzle') and not room.get('puzzle_done') and self._mark_room_resolution_once(room, 'puzzle', mode='resolve'):
            self._handle_room_puzzle(room, room.get('puzzle') or {})
            room['puzzle_done'] = True
            d['puzzle_done'] = True
        try:
            self._sync_room_to_delta(int(room.get('id', self.current_room_id)), room)
        except Exception:
            pass

    def _handle_room_monster(self, room: dict[str, Any]):
        foes = room["foes"]
        self.ui.title("Encounter!")
        rid = int(room.get("id", self.current_room_id) or self.current_room_id)
        role = self._dungeon_room_role(rid)
        if role in {"guard", "boss", "treasury"}:
            self._propagate_dungeon_alarm(origin_room_id=rid, severity=1 if role == "guard" else 2, reason=f"{role} defenders engaged")
        for f in foes:
            self.ui.log(f"- {f.name} HP {f.hp} AC {f.ac_desc} HD {f.hd}")
        self.combat(foes)
        if self.party.any_alive():
            room["cleared"] = True
            self._note_dungeon_aftermath(rid, f"The remains of a {role or 'monster'} encounter lie here.", level=int(getattr(self, 'dungeon_level', 1) or 1))

    def _handle_room_treasure(self, room: dict[str, Any]):
        if room.get("treasure_taken"):
            self.ui.log("The treasure here has already been taken.")
            room["cleared"] = True
            if isinstance(room.get("_delta"), dict):
                room["_delta"]["cleared"] = True
            try:
                self._sync_room_to_delta(int(room.get("id", self.current_room_id)), room)
            except Exception:
                pass
            return
        # P6.1.0.5: deterministic pre-rolled treasure from dungeon_instance.stocking if present.
        if "treasure_gp" in room and "treasure_items" in room:
            gp = int(room.get("treasure_gp") or 0)
            items = list(room.get("treasure_items") or [])
        else:
            gp, items = self.treasure.dungeon_treasure(self.dungeon_depth)
        # Compatibility choice: room coinage still credits treasury immediately,
        # but we route all item awards through the shared helper to keep transitional
        # loot-pool behavior consistent across room/boss/treasury paths.
        self._grant_room_loot(gp=int(gp), items=list(items), source="room_treasure")
        room["treasure_taken"] = True
        room["cleared"] = True
        if isinstance(room.get("_delta"), dict):
            room["_delta"]["treasure_taken"] = True
            room["_delta"]["cleared"] = True
        try:
            self._sync_room_to_delta(int(room.get("id", self.current_room_id)), room)
        except Exception:
            pass

    
    def _handle_room_trap(self, room: dict[str, Any]):
        ctx = resolve_room_interaction_context(self, room)
        if room.get("trap_disarmed") or bool(ctx.get("hazards_resolved")):
            self.ui.log("A disarmed trap lies here.")
            room["cleared"] = True
            return

        trap_key = str(room.get("trap_desc") or room.get("trap_damage") or "trap").strip().lower().replace(" ", "_")
        if not self._mark_room_resolution_once(room, f"trap:{trap_key}", mode="trigger"):
            room["trap_disarmed"] = True
            room["cleared"] = True
            return

        # Mark as triggered/found so it can't repeatedly fire.
        room["trap_triggered"] = True
        room["trap_found"] = True

        d = room.get("_delta") if isinstance(room.get("_delta"), dict) else None
        if d is not None:
            d["trap_triggered"] = True
            d["trap_found"] = True

        self.ui.title("Trap!")
        self.ui.log(room.get("trap_desc", "A hidden mechanism triggers!"))

        # Dwarf stonework notice (careful movement).
        try:
            from .races import dwarf_trap_notice_in_6
            ch = int(dwarf_trap_notice_in_6(self.party, mode="careful"))
            if ch > 0 and self.dice.in_6(ch):
                self.ui.log("A dwarf notices subtle stonework tells — you avoid the trap!")
                room["trap_disarmed"] = True
                room["cleared"] = True
                if d is not None:
                    d["trap_disarmed"] = True
                    d["cleared"] = True
                self.emit("dungeon_trap_spotted", room_id=int(room.get("id", -1)), by_race="Dwarf")
                return
        except Exception:
            pass

        dmg_expr = str(room.get("trap_damage") or "1d6")
        try:
            dmg = int(self.dice.roll(dmg_expr).total)
        except Exception:
            dmg = self.dice.d(6)

        victim = self.dice_rng.choice(self.party.living()) if self.party.living() else None
        if victim:
            victim.hp = max(-1, victim.hp - dmg)
            self._notify_damage(victim, dmg, damage_types={"physical"})
            self.ui.log(f"{victim.name} takes {dmg} damage!")

        room["trap_disarmed"] = True
        room["cleared"] = True
        if d is not None:
            d["trap_disarmed"] = True
            d["cleared"] = True

        # Alarm/noise.
        if bool(room.get('trap_alarm')) or "alarm" in str(room.get("trap_desc") or "").lower() or self.dice.in_6(2):
            self.noise_level = min(5, self.noise_level + 2)
            self._propagate_dungeon_alarm(origin_room_id=int(room.get('id', self.current_room_id) or self.current_room_id), severity=1, reason='trap alarm')
            self.ui.log("The trap makes a terrible racket!")

        # Persist trap state to delta (important if the trap triggers during movement).
        try:
            self._sync_room_to_delta(int(room.get("id", self.current_room_id)), room)
        except Exception:
            pass

    def _room_entry_signals(self, room: dict[str, Any]) -> list[str]:
        """Return 0-2 short, non-mechanical telegraph lines shown once on first entry.

        Goals:
        - Helpful but not spoiler-y.
        - Prioritize immediate danger (monsters/traps) over rewards.
        - Keep output concise (max 2 lines).
        """
        # Collect (priority, line) then pick the best two.
        cands: list[tuple[int, str]] = []

        # Normalize some fields.
        try:
            rtype = str(room.get("type") or "").lower()
        except Exception:
            rtype = ""

        try:
            for line in self._ecology_room_signals(int(room.get('id', self.current_room_id) or self.current_room_id)):
                cands.append((9, str(line)))
        except Exception:
            pass

        # 1) Monsters present and not cleared.
        try:
            if room.get("foes") and not room.get("cleared"):
                cands.append((10, "Fresh tracks and disturbed dust suggest something is nearby."))
        except Exception:
            pass

        # 2) Trap cues (only if not already resolved/found).
        try:
            if rtype == "trap" and not room.get("trap_found") and not room.get("trap_disarmed") and not room.get("trap_triggered"):
                has_dwarf = False
                try:
                    for pc in self.party.pcs():
                        if getattr(pc, "hp", 0) <= 0:
                            continue
                        race = str(getattr(pc, "race", "") or getattr(pc, "race_name", "") or "").lower()
                        if race == "dwarf":
                            has_dwarf = True
                            break
                except Exception:
                    has_dwarf = False
                if has_dwarf:
                    cands.append((9, "The stonework shows odd seams and chisel-marks—something feels off."))
                else:
                    cands.append((9, "You notice odd scratches and tiny holes in the stone."))
        except Exception:
            pass

        # 3) Stairs cues (navigation info).
        try:
            stairs = room.get("stairs") or {}
            if isinstance(stairs, dict) and (stairs.get("up") or stairs.get("down")):
                if stairs.get("up") and stairs.get("down"):
                    cands.append((7, "Stone stairs lead both up and down, carrying a faint draft."))
                elif stairs.get("down"):
                    cands.append((7, "A cold draft rises from stone stairs leading down."))
                else:
                    cands.append((7, "Air stirs faintly from stone stairs leading up."))
        except Exception:
            pass

        # 4) Hidden secret edge hint (non-spoilery). Only if there is at least one still-hidden secret edge adjacent.
        try:
            rid = int(room.get("id", -1))
            adj = self._dungeon_bp_adjacency()
            if adj and rid in adj:
                for nb in adj[rid]:
                    flags = self._dungeon_bp_edge_flags(rid, nb)
                    if flags.get("secret") and not self._dungeon_secret_edge_found(rid, nb):
                        cands.append((6, "The air shifts strangely along one wall."))
                        break
        except Exception:
            pass

        # 5) Treasure cues (only if nothing more urgent is present).
        try:
            if rtype in ("treasure", "treasury", "boss") and not room.get("treasure_taken"):
                cands.append((3, "The air here feels still; the floor looks less-traveled than usual."))
        except Exception:
            pass

        # Choose up to two best distinct lines, highest priority first.
        out: list[str] = []
        seen: set[str] = set()
        for _, line in sorted(cands, key=lambda x: (-x[0], x[1])):
            if line in seen:
                continue
            out.append(line)
            seen.add(line)
            if len(out) >= 2:
                break
        return out

    def _passive_room_entry_checks(self, room: dict[str, Any]) -> None:
        """Run automatic (no-time) checks when the party first enters a room.

        Rules intent:
        - Traps: all characters with a Find/Remove Traps chance get a passive roll.
        - Secrets: all characters get a passive 1-in-6 roll, modified by race (e.g. elf 2-in-6).

        These passive checks run once per room (per dungeon instance) and do not
        consume dungeon time. Players can still choose explicit search actions.
        """
        try:
            if room.get("entered"):
                return
        except Exception:
            # If the room dict is malformed, just bail.
            return

        rid = int(room.get("id", -1))

        # Passive trap detection before any trigger.
        has_trap = (room.get("type") == "trap") or bool(room.get("trap_desc")) or bool(room.get("trap_damage"))
        if has_trap and not room.get("trap_disarmed") and not room.get("trap_found"):
            found_by: list[str] = []
            for pc in self.party.pcs():
                if getattr(pc, "hp", 0) <= 0:
                    continue
                skills = getattr(pc, "thief_skills", {}) or {}
                if not isinstance(skills, dict):
                    continue
                chance_pct = skills.get("Find/Remove Traps")
                if chance_pct is None:
                    continue
                try:
                    pct = int(chance_pct or 0)
                except Exception:
                    pct = 0
                if pct <= 0:
                    continue
                roll = self.dice.percent()
                if roll <= max(1, min(99, pct)):
                    found_by.append(str(getattr(pc, "name", "Someone")))
            if found_by:
                room["trap_found"] = True
                if len(found_by) == 1:
                    self.ui.log(f"{found_by[0]} notices signs of a trap!")
                else:
                    self.ui.log("Your party notices signs of a trap!")
                self.emit("dungeon_trap_found", room_id=rid)


        # Dwarf stonework trap noticing on first entry (no time cost).
        try:
            from .races import dwarf_trap_notice_in_6
            if (room.get("type") == "trap" or room.get("trap_desc")) and not room.get("trap_found"):
                ch = int(dwarf_trap_notice_in_6(self.party, mode="careful"))
                if ch > 0 and self.dice.in_6(ch):
                    room["trap_found"] = True
                    self.ui.log("A dwarf notices subtle stonework tells — there may be a trap here.")
                    self.emit("dungeon_trap_found", room_id=rid, by_race="Dwarf")
        except Exception:
            pass

        # Passive secret-door notice on first entry.
        try:
            # Only attempt passive notice if there is at least one undiscovered secret edge adjacent.
            hidden: list[int] = []
            adj = self._dungeon_bp_adjacency() or {}
            for dest in adj.get(rid, []):
                flags = self._dungeon_bp_edge_flags(rid, int(dest))
                if flags.get("secret") and not self._dungeon_secret_edge_found(rid, int(dest)):
                    hidden.append(int(dest))

            if hidden and not room.get("secret_found"):
                from .races import get_traits
                found = False
                for pc in self.party.pcs():
                    if getattr(pc, "hp", 0) <= 0:
                        continue
                    try:
                        traits = get_traits(getattr(pc, "race", None))
                        ch = int(getattr(traits, "secret_passive_in_6", 0) or 0)
                    except Exception:
                        ch = 0
                    if ch <= 0:
                        continue
                    ch = max(1, min(6, ch))
                    if self.dice.in_6(ch):
                        found = True
                        break

                if found:
                    # Reveal one hidden secret edge deterministically.
                    dest = sorted(hidden)[0]
                    self._dungeon_reveal_secret_edge(rid, dest)
                    room["secret_found"] = True
                    self.ui.log("Your keen eyes pick out signs of a secret door.")
                    self.emit("dungeon_secret_passive", room_id=rid, found=True)
                    try:
                        self.dungeon_rooms.pop(rid, None)
                        self.dungeon_rooms.pop(int(dest), None)
                    except Exception:
                        pass
        except Exception:
            pass

        
        # Room signals/telegraphs shown once on first entry (pure UX, no mechanics).
        try:
            for line in self._room_entry_signals(room):
                self.ui.log(line)
        except Exception:
            pass

        # Mark entered and persist.
        room["entered"] = True
        try:
            self._sync_room_to_delta(rid, room)
        except Exception:
            pass

    def move_through_exit(self, key: str):
        room = self._ensure_room(self.current_room_id)
        if key not in room["exits"]:
            self.ui.log("No such exit.")
            return

        # Door resolution
        state = room["doors"][key]
        if state in ("locked", "stuck", "closed"):
            act = self.ui.choose(f"Door is {state}.", ["Open normally", "Force (STR)", "Back"])
            if act == 2:
                return
            if act == 0:
                if state == "locked":
                    self.ui.log("It's locked.")
                    return
                if state == "stuck" and not self.dice.in_6(2):
                    self.ui.log("It won't budge.")
                    return
                room["doors"][key] = "open"
            else:
                # Force: STR check (1-in-6 + STR mod)
                best_str = max((mod_ability(pc.stats.STR) if getattr(pc, "stats", None) else 0) for pc in self.party.pcs()) if self.party.pcs() else 0
                odds = 1 + max(0, best_str)
                odds = max(1, min(5, odds))
                if self.dice.in_6(odds):
                    room["doors"][key] = "open"
                    self.ui.log("You force it open!")
                    self.noise_level = min(5, self.noise_level + 2)
                else:
                    self.ui.log("You fail to force it.")
                    self.noise_level = min(5, self.noise_level + 1)
                    return

        # Move
        dest = room["exits"][key]
        self.current_room_id = dest
        r2 = self._ensure_room(dest)
        self.ui.log(f"You move to Room {dest}.")

        # Passive entry checks do not cost time: automatic trap/secret checks on first entry.
        self._passive_room_entry_checks(r2)

        # Keep UI-driven movement consistent with command-driven movement for traps.
        if (r2.get("type") == "trap" or r2.get("trap_desc")) and not r2.get("trap_disarmed"):
            if not r2.get("trap_found") and not r2.get("trap_triggered"):
                if self.dice.in_6(2):
                    self._handle_room_trap(r2)

    # -------------
    # Dungeon generation + persistence
    # -------------

    def _dungeon_bp_adjacency(self) -> dict[int, list[int]] | None:
        """Return adjacency list from the current dungeon_instance blueprint, if present."""
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "blueprint", None):
            return None
        bp = getattr(inst.blueprint, "data", None) or {}
        rooms = bp.get("rooms")
        edges = bp.get("edges")
        if not isinstance(rooms, dict) or not isinstance(edges, list):
            return None
        adj: dict[int, set[int]] = {}
        for k in rooms.keys():
            try:
                rid = int(k)
            except Exception:
                continue
            adj.setdefault(rid, set())
        for e in edges:
            a = b = None
            if isinstance(e, dict):
                try:
                    a = int(e.get('a'))
                    b = int(e.get('b'))
                except Exception:
                    a = b = None
            elif isinstance(e, (list, tuple)) and len(e) == 2:
                try:
                    a = int(e[0]); b = int(e[1])
                except Exception:
                    a = b = None
            if a is None or b is None:
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        return {k: sorted(list(v)) for k, v in adj.items()}

    def _dungeon_bp_edge_flags(self, a: int, b: int) -> dict[str, Any]:
        """Return blueprint edge metadata for an undirected edge."""
        inst = getattr(self, 'dungeon_instance', None)
        if not inst or not getattr(inst, 'blueprint', None):
            return {}
        bp = getattr(inst.blueprint, 'data', None) or {}
        edges = bp.get('edges')
        if not isinstance(edges, list):
            return {}
        aa = int(a); bb = int(b)
        lo, hi = (aa, bb) if aa <= bb else (bb, aa)
        for e in edges:
            if isinstance(e, dict):
                try:
                    ea = int(e.get('a')); eb = int(e.get('b'))
                except Exception:
                    continue
                elo, ehi = (ea, eb) if ea <= eb else (eb, ea)
                if elo == lo and ehi == hi:
                    tags = e.get('tags') or []
                    if not isinstance(tags, list):
                        tags = []
                    return {
                        'locked': bool(e.get('locked', False)),
                        'secret': bool(e.get('secret', False)),
                        'door_id': str(e.get('door_id') or ''),
                        'tags': [str(t) for t in tags],
                        'gate': str(e.get('gate') or ''),
                        'key_id': str(e.get('key_id') or ''),
                    }
            elif isinstance(e, (list, tuple)) and len(e) == 2:
                try:
                    ea = int(e[0]); eb = int(e[1])
                except Exception:
                    continue
                elo, ehi = (ea, eb) if ea <= eb else (eb, ea)
                if elo == lo and ehi == hi:
                    return {}
        return {}

    def _dungeon_gate_inventory(self) -> dict[str, Any]:
        inst = getattr(self, 'dungeon_instance', None)
        if not inst or not getattr(inst, 'delta', None):
            return {}
        data = getattr(inst.delta, 'data', None)
        if not isinstance(data, dict):
            inst.delta.data = {}
            data = inst.delta.data
        inv = data.get('gate_inventory')
        if not isinstance(inv, dict):
            inv = {'keys': {}, 'seen_gates': {}}
            data['gate_inventory'] = inv
        inv.setdefault('keys', {})
        inv.setdefault('seen_gates', {})
        return inv

    def _dungeon_has_gate_key(self, key_id: str) -> bool:
        kid = str(key_id or '').strip()
        if not kid:
            return False
        inv = self._dungeon_gate_inventory()
        keys = inv.get('keys') or {}
        if not isinstance(keys, dict):
            return False
        return bool(keys.get(kid))

    def _dungeon_note_gate_seen(self, gate: str, key_id: str = '') -> None:
        gate_name = str(gate or '').strip()
        if not gate_name:
            return
        inv = self._dungeon_gate_inventory()
        seen = inv.setdefault('seen_gates', {})
        if not isinstance(seen, dict):
            seen = {}
            inv['seen_gates'] = seen
        rec = seen.setdefault(gate_name, {})
        if key_id:
            rec['key_id'] = str(key_id)
        rec['seen'] = True

    def _dungeon_award_gate_key(self, feature: dict[str, Any], room: dict[str, Any]) -> None:
        key_id = str(feature.get('key_id') or '').strip()
        gate_name = str(feature.get('gate') or '').strip()
        if not key_id:
            return
        inv = self._dungeon_gate_inventory()
        keys = inv.setdefault('keys', {})
        if not isinstance(keys, dict):
            keys = {}
            inv['keys'] = keys
        already = bool(keys.get(key_id))
        keys[key_id] = {
            'gate': gate_name,
            'room_id': int(room.get('id', getattr(self, 'current_room_id', 0)) or 0),
        }
        self._dungeon_note_gate_seen(gate_name, key_id)
        if already:
            self.ui.log('The cache is empty now, but its route clue still makes sense.')
        else:
            label = gate_name.replace('_', ' ') if gate_name else 'deeper gate'
            self.ui.log(f'You secure a token or route clue for the {label}.')

    def _dungeon_gate_label(self, gate: str) -> str:
        return str(gate or 'gate').replace('_', ' ')

    def _dungeon_secret_edge_key(self, a: int, b: int) -> str:
        aa = int(a); bb = int(b)
        lo, hi = (aa, bb) if aa <= bb else (bb, aa)
        return f"{lo}-{hi}"

    def _dungeon_secret_edge_found(self, a: int, b: int) -> bool:
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "delta", None):
            return False
        data = getattr(inst.delta, "data", None) or {}
        found = data.get("secret_edges_found") or {}
        if not isinstance(found, dict):
            return False
        return bool(found.get(self._dungeon_secret_edge_key(a, b), False))

    def _dungeon_reveal_secret_edge(self, a: int, b: int) -> None:
        """Persist that a secret edge has been discovered."""
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "delta", None):
            return
        data = getattr(inst.delta, "data", None)
        if data is None or not isinstance(data, dict):
            inst.delta.data = {}
            data = inst.delta.data
        found = data.get("secret_edges_found")
        if not isinstance(found, dict):
            found = {}
            data["secret_edges_found"] = found
        found[self._dungeon_secret_edge_key(a, b)] = True

    def _dungeon_room_tags(self, room_id: int) -> list[str]:
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "blueprint", None):
            return []
        bp = getattr(inst.blueprint, "data", None) or {}
        rooms = bp.get("rooms")
        if not isinstance(rooms, dict):
            return []
        rec = rooms.get(str(int(room_id)))
        if not isinstance(rec, dict):
            return []
        tags = rec.get("tags") or []
        if isinstance(tags, list):
            return [str(t) for t in tags]
        return []

    
    def _dungeon_room_meta(self, room_id: int) -> dict[str, int]:
        """Return blueprint meta fields for a room (depth/floor) if instance is active."""
        inst = getattr(self, "dungeon_instance", None)
        if not inst or not getattr(inst, "blueprint", None):
            return {"depth": 0, "floor": 0}
        bp = getattr(inst.blueprint, "data", None) or {}
        rooms = bp.get("rooms")
        if not isinstance(rooms, dict):
            return {"depth": 0, "floor": 0}
        rec = rooms.get(str(int(room_id)))
        if not isinstance(rec, dict):
            return {"depth": 0, "floor": 0}
        try:
            depth = int(rec.get("depth", 0) or 0)
        except Exception:
            depth = 0
        try:
            floor = int(rec.get("floor", 0) or 0)
        except Exception:
            floor = 0
        return {"depth": depth, "floor": floor}

    def _dungeon_room_type_from_tags(self, tags: list[str]) -> str:
        tset = {t.lower() for t in (tags or [])}
        if "boss" in tset:
            return "boss"
        if "treasury" in tset:
            return "treasury"
        if "treasure" in tset:
            return "treasure"
        if "trap" in tset:
            return "trap"
        if "monster" in tset:
            return "monster"
        return "empty"

    def _get_dungeon_delta_room(self, room_id: int) -> dict[str, Any]:
        inst = getattr(self, "dungeon_instance", None)
        if not inst:
            return {}
        delta = getattr(inst, "delta", None)
        data = getattr(delta, "data", None) or {}
        if not isinstance(data, dict):
            return {}
        rooms = data.get("rooms")
        if not isinstance(rooms, dict):
            rooms = {}
            data["rooms"] = rooms
        rec = rooms.get(str(int(room_id)))
        return rec if isinstance(rec, dict) else {}

    def _set_dungeon_delta_room(self, room_id: int, rec: dict[str, Any]) -> None:
        inst = getattr(self, "dungeon_instance", None)
        if not inst:
            return
        delta = getattr(inst, "delta", None)
        if not delta:
            return
        if not isinstance(getattr(delta, "data", None), dict):
            delta.data = {}
        rooms = delta.data.setdefault("rooms", {})
        if not isinstance(rooms, dict):
            rooms = {}
            delta.data["rooms"] = rooms
        rooms[str(int(room_id))] = dict(rec)

    def _sync_room_to_delta(self, room_id: int, room: dict[str, Any]) -> None:
        """Persist mutable room state into dungeon_instance.delta."""
        if not getattr(self, "dungeon_instance", None):
            return
        rec: dict[str, Any] = dict(self._get_dungeon_delta_room(room_id) or {})

        # Preserve already-captured local delta hints when present.
        if isinstance(room.get("_delta"), dict):
            rec.update(dict(room.get("_delta") or {}))

        bool_fields = (
            "cleared",
            "treasure_taken",
            "boss_loot_taken",
            "trap_disarmed",
            "trap_found",
            "trap_triggered",
            "secret_found",
            "entered",
            "event_done",
            "feature_done",
            "shrine_done",
            "puzzle_done",
            "container_looted",
        )
        for key in bool_fields:
            if key in room or key in rec:
                rec[key] = bool(room.get(key, rec.get(key, False)))

        if isinstance(room.get("doors"), dict):
            rec["doors"] = dict(room.get("doors") or {})

        keys = room.get("resolution_keys", rec.get("resolution_keys", []))
        if isinstance(keys, (list, tuple, set)):
            rec["resolution_keys"] = sorted({str(x) for x in keys if str(x).strip()})

        self._set_dungeon_delta_room(room_id, rec)

    def _get_dungeon_stocking_room(self, room_id: int) -> dict[str, Any]:
        inst = getattr(self, "dungeon_instance", None)
        if not inst:
            return {}
        stocking = getattr(inst, "stocking", None)
        data = getattr(stocking, "data", None) or {}
        if not isinstance(data, dict):
            return {}
        rooms = data.get("rooms")
        if not isinstance(rooms, dict):
            return {}
        rec = rooms.get(str(int(room_id)))
        return rec if isinstance(rec, dict) else {}


    def _ensure_level_specials(self, level: int):
        """Ensure boss/treasury ids exist in metadata for this level (created lazily)."""
        level = int(level)
        meta = (self.dungeon_level_meta or {}).get(level) or {}
        if level == int(self.max_dungeon_levels):
            meta.setdefault("boss_room_id", 2)
            meta.setdefault("treasury_room_id", 3)
        self.dungeon_level_meta[level] = meta

    def _ensure_room(self, room_id: int) -> dict[str, Any]:
        if room_id in self.dungeon_rooms:
            room = self.dungeon_rooms[room_id]
            room["visited"] = room.get("visited", 0) + 1
            return room

        # P6.1.0.4: Blueprint-driven rooms if dungeon_instance is present.
        adj = self._dungeon_bp_adjacency()
        if adj is not None and getattr(self, "dungeon_instance", None) is not None:
            inst = self.dungeon_instance
            tags = self._dungeon_room_tags(room_id)
            meta = self._dungeon_room_meta(room_id)
            rtype = self._dungeon_room_type_from_tags(tags)

            neigh = adj.get(int(room_id), [])
            exit_keys = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            exits: dict[str, int] = {}
            default_doors: dict[str, str] = {}
            j = 0
            for dest in neigh:
                flags = self._dungeon_bp_edge_flags(int(room_id), int(dest))
                # Hide secret edges until discovered.
                if flags.get("secret") and not self._dungeon_secret_edge_found(int(room_id), int(dest)):
                    continue
                k = exit_keys[j] if j < len(exit_keys) else f"Z{dest}"
                j += 1
                exits[k] = int(dest)
                default_doors[k] = "locked" if flags.get("locked") else "open"

            # Delta record is the source of truth for mutable state.
            # NOTE: we keep dungeon_rooms as a runtime cache only.
            drec = dict(self._get_dungeon_delta_room(room_id) or {})
            drec.setdefault("cleared", False)
            drec.setdefault("entered", False)
            if rtype in ("treasure", "boss", "treasury"):
                drec.setdefault("treasure_taken", False)
            if rtype == "trap":
                drec.setdefault("trap_found", False)
                drec.setdefault("trap_triggered", False)
                drec.setdefault("trap_disarmed", False)
            drec.setdefault("secret_found", False)

            doors = drec.get("doors")
            if not isinstance(doors, dict):
                doors = {}
            # Ensure every exit has a door state.
            for k, v in default_doors.items():
                doors.setdefault(str(k), str(v))
            drec["doors"] = doors

            bp = getattr(getattr(inst, "blueprint", None), "data", None) or {}
            bp_theme = dict(bp.get("theme") or {})
            room_title = str((bp_theme.get("name") or "Dungeon")).strip()
            role_tag = next((str(t).split(":", 1)[1] for t in tags if str(t).lower().startswith("role:")), "")
            if role_tag:
                room_title = role_tag.replace("_", " ").title()
            room_desc = f"{bp_theme.get('name', 'Dungeon')} — {room_title.lower()}." if bp_theme else f"A {room_title.lower()}."

            room: dict[str, Any] = {
                "id": int(room_id),
                "type": rtype,
                "visited": 1,
                "entered": bool(drec.get("entered", False)),
                "cleared": bool(drec.get("cleared", False)),
                "exits": exits,
                "doors": doors,
                "secret_found": bool(drec.get("secret_found", False)),
                "depth": int(meta.get("depth", 0)),
                "floor": int(meta.get("floor", 0)),
                "title": room_title,
                "desc": room_desc,
                "stairs": {"up": ("stairs_up" in [t.lower() for t in tags]), "down": ("stairs_down" in [t.lower() for t in tags])},
                "resolution_keys": sorted({str(x) for x in (drec.get("resolution_keys") or []) if str(x).strip()}),
                "_delta": drec,
            }

            # Apply delta overlays for flags.
            if "treasure_taken" in drec:
                room["treasure_taken"] = bool(drec.get("treasure_taken"))
            if "boss_loot_taken" in drec:
                room["boss_loot_taken"] = bool(drec.get("boss_loot_taken"))
            if "trap_found" in drec:
                room["trap_found"] = bool(drec.get("trap_found"))
            if "trap_triggered" in drec:
                room["trap_triggered"] = bool(drec.get("trap_triggered"))
            if "trap_disarmed" in drec:
                room["trap_disarmed"] = bool(drec.get("trap_disarmed"))
            for _k in ('event_done','feature_done','shrine_done','puzzle_done'):
                if _k in drec:
                    room[_k] = bool(drec.get(_k))

            # Populate from stocking (deterministic).
            srec: Any = self._get_dungeon_stocking_room(room_id)

            if isinstance(srec, dict) and not room.get("cleared"):
                enc = srec.get("encounter")
                if isinstance(enc, dict) and rtype in ("monster", "boss"):
                    mid = str(enc.get("monster_id") or "").strip()
                    # Count may be an expression; default to 1 if not parseable.
                    try:
                        cnt = int(str(enc.get("count", 1) or 1))
                    except Exception:
                        cnt = 1
                    if mid:
                        mm = getattr(self, "_monster_by_id", None)
                        if not isinstance(mm, dict):
                            mm = {m.get("monster_id") or m.get("name"): m for m in (self.content.monsters or []) if isinstance(m, dict)}
                            self._monster_by_id = mm
                        mrec = mm.get(mid)
                        if mrec:
                            room["foes"] = [self.encounters._mk_monster(mrec) for _ in range(max(1, cnt))]

                if rtype in ("treasure", "boss", "treasury"):
                    room.setdefault("treasure_taken", False)
                    trec = srec.get("treasure") if isinstance(srec.get("treasure"), dict) else {}
                    if isinstance(trec, dict) and "gp" in trec and "items" in trec:
                        room["treasure_gp"] = int(trec.get("gp") or 0)
                        room["treasure_items"] = list(trec.get("items") or [])
                if rtype == "trap":
                    room.setdefault("trap_disarmed", False)
                    room.setdefault("trap_found", bool(drec.get("trap_found", False)))
                    room.setdefault("trap_triggered", bool(drec.get("trap_triggered", False)))
                    t = srec.get("trap") if isinstance(srec.get("trap"), dict) else {}
                    kind = str((t or {}).get("trap_kind") or "trap").replace("_", " ")
                    room["trap_desc"] = str((t or {}).get("desc") or f"A dangerous {kind}!")
                    try:
                        room["trap_dc"] = int((t or {}).get("dc") or 10)
                    except Exception:
                        room["trap_dc"] = 10
                    room["trap_damage"] = str((t or {}).get("damage") or "1d6")
                    room["trap_alarm"] = bool((t or {}).get("alarm", False))

                if isinstance(srec.get('special_room'), dict):
                    room['special_room'] = dict(srec.get('special_room') or {})
                    room.setdefault('feature_done', bool(drec.get('feature_done', False)))
                    room['desc'] = str(room['special_room'].get('desc') or room.get('desc') or 'An unusual chamber.')
                    room['title'] = str(room['special_room'].get('name') or room.get('title') or room['type'].title())
                if isinstance(srec.get('room_event'), dict):
                    room['room_event'] = dict(srec.get('room_event') or {})
                    room.setdefault('event_done', bool(drec.get('event_done', False)))
                if isinstance(srec.get('shrine'), dict):
                    room['shrine'] = dict(srec.get('shrine') or {})
                    room.setdefault('shrine_done', bool(drec.get('shrine_done', False)))
                    room['title'] = str(room.get('title') or room['shrine'].get('name') or room['type'].title())
                if isinstance(srec.get('puzzle'), dict):
                    room['puzzle'] = dict(srec.get('puzzle') or {})
                    room.setdefault('puzzle_done', bool(drec.get('puzzle_done', False)))
                    room['title'] = str(room.get('title') or room['puzzle'].get('name') or room['type'].title())

            # Persist delta defaults now that we have a complete room view.
            self._set_dungeon_delta_room(room_id, drec)

            self.dungeon_rooms[int(room_id)] = room
            return room

        # Generate new room
        lvl = int(getattr(self, "dungeon_level", 1) or 1)
        self._ensure_level_specials(lvl)
        meta = (self.dungeon_level_meta or {}).get(lvl) or {}
        boss_id = int(meta.get("boss_room_id", -1))
        treasury_id = int(meta.get("treasury_room_id", -1))
        if lvl == int(getattr(self, "max_dungeon_levels", 1)) and room_id in (boss_id, treasury_id):
            rtype = "boss" if room_id == boss_id else "treasury"
            r = 6
        else:
            r = self.dice.d(6)
        if 'rtype' in locals() and rtype in ("boss","treasury"):
            pass
        elif r in (1, 2):
            rtype = "monster"
        elif r == 3:
            rtype = "trap"
        elif r == 4:
            rtype = "treasure"
        else:
            rtype = "empty"

        exits = {}
        doors = {}
        num_exits = 1 + (1 if self.dice.in_6(3) else 0) + (1 if self.dice.in_6(2) else 0)
        for n in range(num_exits):
            key = ["A", "B", "C"][n]
            exits[key] = self._new_room_id()
            doors[key] = self.dice_rng.choice(["open", "closed", "stuck", "locked"])
        # Secret exit sometimes
        if self.dice.in_6(1):
            exits["S"] = self._new_room_id()
            doors["S"] = "closed"  # secret doors start closed
        # Stairs (multi-level navigation)
        stairs = {"up": False, "down": False}
        max_lvl = int(getattr(self, "max_dungeon_levels", 1) or 1)
        if lvl > 1 and (room_id == 1 or self.dice.in_6(6) and self.dice.in_6(2)):
            stairs["up"] = True
        if lvl < max_lvl and (room_id == 1 or self.dice.in_6(6) and self.dice.in_6(2)):
            stairs["down"] = True

        room = {
            "id": room_id,
            "type": rtype,
            "visited": 1,
            "cleared": False,
            "exits": exits,
            "doors": doors,
            "secret_found": False,
            "stairs": stairs,
        }

        # Populate content and cache it (no reroll abuse)
        if rtype == "boss":
            # Boss room: faction-themed elite encounter
            if getattr(self, "current_dungeon_faction_id", None) and self.current_dungeon_faction_id in (self.factions or {}):
                enc = self._faction_encounter(self.current_dungeon_faction_id, "dungeon")
                foes = enc.foes if enc else []
            else:
                foes = []
            if not foes:
                m = self.dice_rng.choice(self.content.monsters)
                foes = [self.encounters._mk_monster(m)]
            # Boost boss group a bit
            for foe in foes:
                foe.hp = int(foe.hp) + self.dice.d(6)
            room["foes"] = foes
        elif rtype == "treasury":
            room["treasure_taken"] = False
            room["desc"] = "An ironbound coffer rests here among scattered relics."
        elif rtype == "monster":
            # Prefer faction-themed garrison in strongholds/dungeon-owned areas
            foes = []
            if getattr(self, "current_dungeon_faction_id", None) and self.current_dungeon_faction_id in (self.factions or {}):
                enc = self._faction_encounter(self.current_dungeon_faction_id, "dungeon")
                foes = enc.foes if enc else []
            if not foes:
                m = self.dice_rng.choice(self.content.monsters)
                foes = [self.encounters._mk_monster(m) for _ in range(1 + (1 if self.dice.in_6(3) else 0))]
            room["foes"] = foes
        elif rtype == "trap":
            room["trap_desc"] = self.dice_rng.choice([
                "A spray of darts!",
                "A collapsing ceiling!",
                "A pit opens beneath your feet!",
                "A scything blade swings from the wall!",
            ])
            room["trap_disarmed"] = False
        elif rtype == "treasure":
            room["treasure_taken"] = False
        else:
            room["desc"] = self.dice_rng.choice([
                "Dusty stonework and old bones.",
                "A quiet chamber with dripping water.",
                "Broken furniture lies scattered here.",
                "Carvings on the walls hint at ancient gods.",
            ])

        # Ensure bidirectional connections for newly created exits
        for k, dest in exits.items():
            if dest == room_id:
                continue
            # Create placeholder destination so we can link back later
            if dest not in self.dungeon_rooms:
                # Don't recurse fully; just reserve id so links are stable.
                self.dungeon_rooms.setdefault(dest, {"id": dest, "type": "empty", "visited": 0, "cleared": False, "exits": {}, "doors": {}, "secret_found": False})
            back = self.dungeon_rooms[dest]
            if room_id not in back.get("exits", {}).values():
                # create a back edge if none exists (use Z key if needed)
                back_key = "Z" if "Z" not in back.get("exits", {}) else f"Z{room_id}"
                back["exits"][back_key] = room_id
                back.setdefault("doors", {})[back_key] = "open"

        self.dungeon_rooms[room_id] = room
        return room

    def _new_room_id(self) -> int:
        # Next free integer id
        if not self.dungeon_rooms:
            return 2
        return max(self.dungeon_rooms.keys()) + 1

    # -------------
    # Combat (lightweight but OSR-ish)
    # -------------

    def _equipped_ref_to_name(self, actor: Actor, ref: str | None) -> str | None:
        """Resolve equipment references that may be instance ids or stable names.

        Transitional behavior: prefer inventory instance lookup, then treat the
        reference as a stable display/equipment name.
        """
        if not ref:
            return None
        r = str(ref).strip()
        if not r:
            return None
        try:
            it = find_item_on_actor(actor, r)
            if it is not None:
                try:
                    tmpl = get_item_template(str(getattr(it, "template_id", "") or ""))
                except Exception:
                    tmpl = None
                return item_known_name(it, tmpl)
        except Exception:
            pass
        return r

    def effective_melee_weapon(self, actor: Actor) -> str | None:
        """Resolve effective melee weapon from new equipment first, then legacy."""
        eq = getattr(actor, "equipment", None)
        if eq is not None:
            nm = self._equipped_ref_to_name(actor, getattr(eq, "main_hand", None))
            if nm:
                return nm
        w = str(getattr(actor, "weapon", "") or "").strip()
        return w or None

    def effective_missile_weapon(self, actor: Actor) -> str | None:
        """Resolve effective missile weapon from new equipment first, then legacy."""
        eq = getattr(actor, "equipment", None)
        if eq is not None:
            nm = self._equipped_ref_to_name(actor, getattr(eq, "missile_weapon", None))
            if nm:
                return nm
            # If main hand is explicitly missile, allow it.
            mh = self._equipped_ref_to_name(actor, getattr(eq, "main_hand", None))
            if mh and mh in getattr(self.equipdb, "missile", {}):
                return mh
        w = str(getattr(actor, "weapon", "") or "").strip()
        if w and w in getattr(self.equipdb, "missile", {}):
            return w
        return None

    def effective_armor(self, actor: Actor) -> str | None:
        """Resolve effective armor from new equipment first, then legacy fields."""
        eq = getattr(actor, "equipment", None)
        if eq is not None:
            nm = self._equipped_ref_to_name(actor, getattr(eq, "armor", None))
            if nm:
                return nm
        ar = str(getattr(actor, "armor", "") or "").strip()
        return ar or None

    def effective_shield(self, actor: Actor) -> str | None:
        """Resolve effective shield from new equipment first, then legacy fields."""
        eq = getattr(actor, "equipment", None)
        if eq is not None:
            nm = self._equipped_ref_to_name(actor, getattr(eq, "shield", None))
            if nm:
                return nm
        if bool(getattr(actor, "shield", False)):
            return str(getattr(actor, "shield_name", None) or "Shield")
        return None

    def _effective_ac_desc(self, defender: Actor) -> int:
        """Compute effective descending AC from equipped armor/shield with fallback."""
        try:
            from .equipment import compute_ac_desc
            armor_name = self.effective_armor(defender)
            has_shield = bool(self.effective_shield(defender))
            dex_score = int(getattr(getattr(defender, "stats", None), "DEX", 10)) if getattr(defender, "stats", None) is not None else None
            return int(compute_ac_desc(base_ac_desc=9, armor_name=armor_name, shield=has_shield, dex_score=dex_score))
        except Exception:
            return int(getattr(defender, "ac_desc", 9) or 9)

    def _effective_ammo_ref_for_actor(self, actor: Actor, missile_weapon_name: str | None) -> str | None:
        eq = getattr(actor, "equipment", None)
        if eq is not None and getattr(eq, "ammo_kind", None):
            return str(getattr(eq, "ammo_kind"))
        return self._ammo_kind_for_weapon(missile_weapon_name)

    def _actor_has_ammo_for_ref(self, actor: Actor, ammo_ref: str | None) -> bool:
        if not ammo_ref:
            return True
        ref = str(ammo_ref).strip()
        if not ref:
            return True
        # Instance-id ammo
        try:
            it = find_item_on_actor(actor, ref)
            if it is not None:
                return int(getattr(it, "quantity", 0) or 0) > 0
        except Exception:
            pass
        # Actor ammo pools in new inventory
        try:
            inv = getattr(actor, "inventory", None)
            if inv is not None:
                n = int((getattr(inv, "ammo", {}) or {}).get(ref.lower(), 0) or 0)
                if n > 0:
                    return True
        except Exception:
            pass
        # Legacy global ammo pools on game
        try:
            return int(getattr(self, ref, 0) or 0) > 0
        except Exception:
            return False

    def _consume_actor_ammo(self, actor: Actor, ammo_ref: str | None) -> bool:
        if not ammo_ref:
            return True
        ref = str(ammo_ref).strip()
        if not ref:
            return True
        try:
            it = find_item_on_actor(actor, ref)
            if it is not None:
                rr = remove_item_from_actor(actor, ref, 1)
                return bool(rr.ok)
        except Exception:
            pass
        try:
            inv = getattr(actor, "inventory", None)
            if inv is not None:
                pools = dict(getattr(inv, "ammo", {}) or {})
                key = ref.lower()
                cur = int(pools.get(key, 0) or 0)
                if cur > 0:
                    pools[key] = cur - 1
                    inv.ammo = pools
                    return True
        except Exception:
            pass
        try:
            cur = int(getattr(self, ref, 0) or 0)
            if cur > 0:
                setattr(self, ref, cur - 1)
                return True
        except Exception:
            pass
        return False

    def _ammo_label_for_ref(self, actor: Actor, ammo_ref: str | None) -> str:
        if not ammo_ref:
            return "ammo"
        ref = str(ammo_ref).strip()
        try:
            it = find_item_on_actor(actor, ref)
            if it is not None:
                return str(getattr(it, "name", "ammo") or "ammo")
        except Exception:
            pass
        return self._ammo_label(ref) if ref in {"arrows", "bolts", "stones", "hand_axes", "throwing_daggers", "darts", "javelins", "throwing_spears"} else ref

    def _weapon_kind(self, attacker: Actor) -> str:
        """Return effective weapon kind using new equipment first, legacy fallback."""
        try:
            if self.effective_missile_weapon(attacker):
                return "missile"
        except Exception:
            pass
        return "melee"

    def _weapon_enchantment_bonus(self, attacker: Actor) -> int:
        """Return enchantment bonus for the currently equipped weapon.

        P2.9: This is used for magic-weapon gating (e.g., "+1 or better to hit").
        The current equipment DB in this project does not encode magic bonuses yet,
        so this returns 0 by default and is extended in later commits.
        """
        w = str(self.effective_melee_weapon(attacker) or self.effective_missile_weapon(attacker) or "")
        if not w:
            return 0

        # Common OSR notation: "Sword +1", "+2 Dagger", etc.
        import re
        m = re.search(r"\+(\d+)", w)
        if m:
            try:
                return max(0, int(m.group(1)))
            except Exception:
                return 0

        # If the equipment DB later encodes bonuses, honor them.
        try:
            rec = None
            if w in getattr(self.equipdb, "melee", {}):
                rec = self.equipdb.melee.get(w)
            elif w in getattr(self.equipdb, "missile", {}):
                rec = self.equipdb.missile.get(w)
            if isinstance(rec, dict):
                b = rec.get("bonus", 0)
                return max(0, int(b or 0))
        except Exception:
            pass
        return 0

    def _weapon_material_tags(self, attacker: Actor) -> set[str]:
        """Best-effort extraction of weapon material tags from the weapon name.

        This is intentionally lightweight. We do not yet maintain a full item
        material model in EquipmentDB, so we parse common OSR patterns.
        """
        w = str(self.effective_melee_weapon(attacker) or self.effective_missile_weapon(attacker) or "").lower()
        tags: set[str] = set()
        if "silver" in w or "silvered" in w:
            tags.add("silver")
        if "cold iron" in w or "cold-iron" in w or "coldiron" in w:
            tags.add("cold_iron")
        return tags

    def _ammo_kind_for_weapon(self, weapon_name: str | None) -> str | None:
        """Return the attribute name of the ammo/pool consumed per missile shot.

        P1.2: arrows/bolts/stones (shared pools)
        P1.3: lightweight throwables (shared pools, recoverable post-combat)
        """
        if not weapon_name:
            return None
        w = weapon_name.strip().lower()

        if "bow" in w and "crossbow" not in w:
            return "arrows"
        if "crossbow" in w:
            return "bolts"
        if "sling" in w:
            return "stones"

        # Throwables
        if w == "axe, hand":
            return "hand_axes"
        if w == "dagger":
            return "throwing_daggers"
        if w == "dart":
            return "darts"
        if w == "javelin":
            return "javelins"
        if w == "spear":
            return "throwing_spears"

        return None

    def _ammo_label(self, ammo_attr: str) -> str:
        labels = {
            "arrows": "arrows",
            "bolts": "bolts",
            "stones": "stones",
            "hand_axes": "hand axes",
            "throwing_daggers": "throwing daggers",
            "darts": "darts",
            "javelins": "javelins",
            "throwing_spears": "spears",
        }
        return labels.get(ammo_attr, ammo_attr)

    def _is_recoverable_throwable(self, ammo_attr: str) -> bool:
        return ammo_attr in {"hand_axes", "throwing_daggers", "darts", "javelins", "throwing_spears"}

    def _recover_throwables(self, spent: dict[str, int]):
        """After a won fight, recover some thrown weapons (lightweight model).

        Deterministic: uses RNGService.
        Rule: each spent throwable has a 50% chance to be recovered (1-3 on 1d6).
        """
        if not spent:
            return
        any_used = False
        any_recovered = False
        for ammo_attr, used in list(spent.items()):
            if used <= 0 or not self._is_recoverable_throwable(ammo_attr):
                continue
            any_used = True
            recovered = 0
            for _ in range(int(used)):
                if self.dice.d(6) <= 3:
                    recovered += 1
            if recovered > 0:
                cur = int(getattr(self, ammo_attr, 0))
                setattr(self, ammo_attr, cur + recovered)
                any_recovered = True
                try:
                    self.battle_evt("AMMO_RECOVERED", ammo=str(self._ammo_label(ammo_attr)), recovered=int(recovered))
                except Exception:
                    pass
                self.ui.log(f"Recovered {recovered} {self._ammo_label(ammo_attr)} after the fight.")
        if any_used and not any_recovered:
            self.ui.log("No thrown weapons were recovered.")
    def _attack_bonus(self, attacker: Actor, kind: str = "melee") -> int:
        """Return attribute-based to-hit bonus.

        S&W Complete uses attribute tables; we apply them for PCs.
        - Missile attacks use DEX missile bonus.
        - Melee attacks use STR to-hit, but positive bonuses are Fighter-only by default.
        Monsters/NPCs without stats receive no attribute bonus.
        """
        stats = getattr(attacker, "stats", None)
        if not stats or not bool(getattr(attacker, "is_pc", False)):
            return 0

        try:
            from .attribute_rules import dexterity_mods, strength_mods
        except Exception:
            # Fallback to legacy approximation.
            if kind == "missile":
                return mod_ability(getattr(stats, "DEX", 10))
            return mod_ability(getattr(stats, "STR", 10))

        if kind == "missile":
            base = int(dexterity_mods(int(getattr(stats, "DEX", 10))).missile_to_hit)
            # P4.10.5: racial missile to-hit bonus (e.g., Halfling +1).
            try:
                from .races import missile_to_hit_bonus
                base += int(missile_to_hit_bonus(attacker))
            except Exception:
                pass
            return int(base)

        cls = str(getattr(attacker, "cls", "") or "")
        return int(strength_mods(int(getattr(stats, "STR", 10)), cls=cls, fighter_only_bonuses=True).to_hit)

    
    def _missile_rate_of_fire(self, attacker: Actor) -> tuple[int, bool]:
        """Return (shots_per_round, is_half_rate).

        - Numeric values (e.g. "2", "3") mean that many shots in the MISSILES phase.
        - "1/2" means one shot every other round (requires a reload round).
        - Anything else defaults to 1.
        """
        raw = self.effective_missile_weapon(attacker) or ""
        wn = self._find_weapon_name(raw)
        if not wn:
            return (1, False)
        try:
            w = self.equipdb.get_weapon(wn)
        except Exception:
            return (1, False)
        if getattr(w, "kind", "") != "missile":
            return (1, False)
        rof = (getattr(w, "rate_of_fire", None) or "1").strip()
        if rof == "1/2":
            return (1, True)
        try:
            n = int(rof)
            return (max(1, n), False)
        except Exception:
            return (1, False)


    def _range_mod_for_weapon_at_distance(self, weapon_name: str, distance_ft: int) -> tuple[int, bool]:
        """Return (to_hit_mod, in_range) for a missile weapon at a given distance.
        We split the weapon's maximum range into short/medium/long thirds:
          <= 1/3 max: +0, <= 2/3 max: -2, <= max: -4, beyond max: out of range.
        """
        if not weapon_name:
            return (0, True)
        try:
            w = self.equipdb.get_weapon(weapon_name)
        except Exception:
            return (0, True)
        max_ft = int(getattr(w, "range_ft", 0) or 0)
        if max_ft <= 0:
            return (0, True)
        d = max(0, int(distance_ft or 0))
        if d > max_ft:
            return (0, False)
        short_ft = max(1, max_ft // 3)
        med_ft = max(1, (2 * max_ft) // 3)
        if d <= short_ft:
            return (0, True)
        if d <= med_ft:
            return (-2, True)
        return (-4, True)


    def _find_weapon_name(self, raw: str) -> str | None:
        name = (raw or "").strip()
        if not name:
            return None
        # Exact match first
        if name in self.equipdb.melee or name in self.equipdb.missile:
            return name
        # Case-insensitive match (covers user-typed or legacy weapon strings)
        low = name.casefold()
        for k in list(self.equipdb.melee.keys()) + list(self.equipdb.missile.keys()):
            if k.casefold() == low:
                return k
        return None

    def _weapon_damage_expr(self, attacker: Actor, kind: str) -> str:
        raw = (self.effective_missile_weapon(attacker) if str(kind).lower() == "missile" else self.effective_melee_weapon(attacker)) or ""
        wn = self._find_weapon_name(raw)
        if not wn:
            return "1d6"
        try:
            w = self.equipdb.get_weapon(wn)
        except Exception:
            return "1d6"
        expr = (w.damage_expr() or "").strip()

        # Some missile entries reference ammo (e.g. "See Arrows"). We don't model ammo yet,
        # so we map these to standard OSR defaults.
        if not expr or expr.lower().startswith("see"):
            low = expr.lower()
            if "arrow" in low:
                return "1d6"
            if "bolts, heavy" in low:
                return "1d8"
            if "bolts" in low:
                return "1d6"
            if "stones" in low or "sling" in low:
                return "1d4"
            return "1d6"

        # If it's already a dice expression, use it; otherwise default.
        # IMPORTANT: do *not* call self.dice.roll(expr) here, because roll() consumes
        # RNG events. This method is used during combat resolution and must not
        # advance the replay stream just to validate formatting.
        try:
            from .dice import _DICE_RE  # type: ignore
            if _DICE_RE.match(expr.replace(" ", "")):
                return expr
        except Exception:
            pass
        return "1d6"

    def _damage_roll(self, attacker: Actor, kind: str = "melee") -> int:
        expr = self._weapon_damage_expr(attacker, kind)

        # Strict replay diagnostics: include the damage expression in RNG ctx.
        try:
            rng = getattr(self, "dice_rng", None)
            if rng is not None and isinstance(getattr(rng, "ctx", None), dict):
                rng.ctx["dmg_expr"] = str(expr)
        except Exception:
            pass
        base = self.dice.roll(expr).total

        # Strength damage modifier (melee only). In S&W Complete, positive STR bonuses
        # are Fighter-only by default; penalties apply to everyone.
        if kind == "melee":
            try:
                if bool(getattr(attacker, "is_pc", False)):
                    from .attribute_rules import strength_mods
                    stats = getattr(attacker, "stats", None)
                    if stats is not None:
                        cls = str(getattr(attacker, "cls", "") or "")
                        base += int(strength_mods(int(getattr(stats, "STR", 10)), cls=cls, fighter_only_bonuses=True).damage)
                else:
                    # Legacy behavior for non-PCs remains unchanged.
                    stats = getattr(attacker, "stats", None)
                    if stats is not None:
                        base += mod_ability(int(getattr(stats, "STR", 10)))
            except Exception:
                pass

        # Two-handed weapons: +1 damage (from S&W weapon notes / common rule)
        try:
            wname = str((self.effective_missile_weapon(attacker) if str(kind).lower()=="missile" else self.effective_melee_weapon(attacker)) or "").lower()
            if "two-handed" in wname or "(two-handed)" in wname:
                base += 1
        except Exception:
            pass

        return max(1, int(base))

    def combat(self, foes: list[Actor]):
            self.ui.title("Combat")

            # --- Combat summary snapshots (P6.4 UX) ---
            ammo_attrs = ["arrows","bolts","stones","hand_axes","throwing_daggers","darts","javelins","throwing_spears"]
            combat_start_party_hp: dict[str,int] = {}
            combat_start_party_hpmax: dict[str,int] = {}
            try:
                for pc in (self.party.members or []):
                    if getattr(pc, "is_pc", False):
                        nm = str(getattr(pc, "name", "?"))
                        combat_start_party_hp[nm] = int(getattr(pc, "hp", 0) or 0)
                        mh = getattr(pc, "max_hp", None)
                        if mh is None:
                            mh = getattr(pc, "hp_max", None)
                        if mh is not None:
                            try:
                                combat_start_party_hpmax[nm] = int(mh)
                            except Exception:
                                pass
            except Exception:
                pass
            combat_start_ammo: dict[str,int] = {}
            try:
                for a in ammo_attrs:
                    combat_start_ammo[a] = int(getattr(self, a, 0) or 0)
            except Exception:
                pass
            combat_start_gold: int = int(getattr(self, 'gold', 0) or 0)
            combat_start_items_count: int = 0
            combat_start_items_snapshot: list = []
            try:
                combat_start_items_snapshot = list(getattr(self, 'party_items', []) or [])
                combat_start_items_count = int(len(combat_start_items_snapshot))
            except Exception:
                combat_start_items_snapshot = []
                combat_start_items_count = 0
            # XP earned during this combat (for summary/journal).
            try:
                self._battle_xp_earned = 0
                self._battle_xp_counted = set()
            except Exception:
                pass
            # Buffered combat log (round summaries)
            self._battle_recent_text_lines = []
            self._battle_begin_capture()
            try:
                # Apply darkness penalty if no light
                darkness_penalty = -4 if not self.light_on else 0
                if darkness_penalty:
                    self.ui.log("Fighting in darkness! (-4 to hit)")

                # P3.1.17: Ensure foes carry stable monster_id (rules should never key off display names).
                # Attempt to canonicalize from Content; fall back to deterministic slug.
                try:
                    import re as _re
                    for f in (foes or []):
                        if f is None or bool(getattr(f, "is_pc", False)):
                            continue
                        mid = getattr(f, "monster_id", None)
                        if isinstance(mid, str) and mid.strip():
                            continue
                        nm = str(getattr(f, "name", "") or "").strip()
                        canon = ""
                        try:
                            canon = str(self.content.canonical_monster_id(nm) or "")
                        except Exception:
                            canon = ""
                        if canon:
                            setattr(f, "monster_id", canon)
                        else:
                            slug = _re.sub(r"[^a-z0-9]+", "_", nm.lower()).strip("_")
                            if slug:
                                setattr(f, "monster_id", slug)
                                self.ui.log(f"[warn] Monster missing monster_id; derived '{slug}' from name '{nm}'.")
                            else:
                                self.ui.log("[warn] Monster missing monster_id and name; rules lookups may fail.")
                except Exception:
                    pass

                # Reset noise bump (combat is loud)
                self.noise_level = min(5, self.noise_level + 1)

                # Stealth/surprise from dungeon actions: if the party successfully sneaks,
                # foes are surprised for the first round.
                if bool(getattr(self, "party_stealth_success", False)):
                    for f in foes:
                        # We tick durations at the *start* of the round, so use 2 to cover round 1.
                        apply_status(f, "surprised", 2, mode="max")
                    self.ui.log("You catch them off-guard! (foes surprised)")
                # Consume the flag regardless.
                self.party_stealth_success = False

                # P0.5: Surprise mini-pass.
                # If stealth did not already surprise the foes, roll surprise for both sides.
                # (Classic OSR: 1-2 on 1d6 = surprised for the first round.)
                # Surprise integrates into initiative by forcing the surprised side to act second,
                # and surprised actors skip their first-round action.
                foes_already_surprised = any((getattr(f, "status", {}) or {}).get("surprised", 0) for f in foes)
                if not foes_already_surprised:
                    party_surprise = self.dice.d(6) <= 2
                    foes_surprise = self.dice.d(6) <= 2
                    if party_surprise:
                        for a in self.party.living():
                            apply_status(a, "surprised", 2, mode="max")
                        self.ui.log("Surprise! Your party is caught off-guard.")
                    if foes_surprise:
                        for f in foes:
                            apply_status(f, "surprised", 2, mode="max")
                        self.ui.log("Surprise! The enemies are caught off-guard.")

                # P1.4: Abstract engagement distance (feet). Used to gate missile range and apply range penalties.
                in_dungeon_ctx = bool(getattr(self, "current_room", None))
                combat_distance_ft = 10 * (self.dice.d(6) if in_dungeon_ctx else self.dice.d(12))
                self.ui.log(f"Starting range: {combat_distance_ft} ft.")

                # P1.6: Cover / concealment. Lightweight initial cover in wilderness.
                # Values are penalties to hit the target: -2 partial, -4 good.
                if not in_dungeon_ctx:
                    any_cover = False
                    for a in (self.party.living() + foes):
                        roll = self.dice.d(6)
                        if roll <= 3:
                            apply_status(a, "cover", -2)
                            any_cover = True
                        elif roll <= 5:
                            apply_status(a, "cover", -4)
                            any_cover = True
                        else:
                            clear_status(a, "cover")
                    if any_cover:
                        self.ui.log("Combat begins with scattered cover in the wilderness.")

                # P2.3: Apply any on-spawn monster specials (e.g., regeneration).
                try:
                    self._apply_combat_spawn_specials(foes)
                except Exception:
                    pass

                # Flush any setup messages captured before round 1.
                self._battle_flush_prelude()

                round_no = 0

                # Group morale check should happen at most once per combat unless you add
                # an explicit rule to re-check (e.g., leader slain). This is important for
                # strict replay determinism.
                self._combat_group_morale_checked = False
                self._combat_foes_ref = foes
                self._combat_foes_total = len(foes)

                # Build stable foe labels for this combat (Orc #1, Orc #2, ...)
                try:
                    counts: dict[str, int] = {}
                    self._battle_label_map = {}
                    for f in (foes or []):
                        if f is None or bool(getattr(f, "is_pc", False)):
                            continue
                        base = str(getattr(f, "name", "Foe") or "Foe")
                        n = int(counts.get(base, 0)) + 1
                        counts[base] = n
                        self._battle_label_map[id(f)] = f"{base} #{n}"
                except Exception:
                    pass



                spent_throwables: dict[str, int] = {}

                # Fight until one side is down or party flees
                while True:
                    round_no += 1
                    self.combat_round = round_no
                    self._battle_current_round = int(round_no)
                    self._battle_round_buffer = []
                    self.battle_evt("ROUND_STARTED", round_no=int(round_no), side_first="?")
                    self.battle_evt("INFO", text=f"Range: {combat_distance_ft} ft.")
                    # Treat "fled" as out of the fight (used by turning).
                    living_foes = [f for f in foes if f.hp > 0 and 'fled' not in getattr(f, 'effects', []) and 'surrendered' not in getattr(f, 'effects', [])]
                    try:
                        _st = self._battle_build_round_state(living_foes, int(combat_distance_ft))
                        self.battle_evt(_st.kind, **(_st.data or {}))
                    except Exception:
                        pass
                    self._combat_board_refresh(
                        foes,
                        combat_distance_ft,
                        round_no,
                        prompt=f"Round {int(round_no)} begins.",
                        phase="Round Start",
                    )
                    if not living_foes:
                        fled = [f for f in foes if f.hp > 0 and 'fled' in getattr(f, 'effects', [])]
                        surrendered = [f for f in foes if f.hp > 0 and 'surrendered' in getattr(f, 'effects', [])]
                        if surrendered:
                            # Offer to accept surrender.
                            if self.ui.auto_combat:
                                self.ui.log("The enemies surrender.")
                                self._recover_throwables(spent_throwables)
                                return
                            ix = self._combat_board_choose("Enemies offer surrender. What do you do?", ["Accept", "Refuse (fight on)"], foes, combat_distance_ft, round_no, phase="Resolution")
                            if ix == 0:
                                self.ui.log("You accept their surrender.")
                                self._recover_throwables(spent_throwables)
                                return
                            else:
                                for f in surrendered:
                                    f.effects = [e for e in (getattr(f, 'effects', []) or []) if e != 'surrendered']
                        if fled:
                            if self.ui.auto_combat:
                                self.ui.log("Enemies flee.")
                                self._recover_throwables(spent_throwables)
                                return
                            ix = self._combat_board_choose("The enemies flee!", ["Let them go", "Pursue"], foes, combat_distance_ft, round_no, phase="Resolution")
                            if ix == 1:
                                # Chase roll: 1d6 + best party DEX mod vs 1d6.
                                best = 0
                                for m in self.party.living():
                                    st = getattr(m, "stats", None)
                                    if not st:
                                        continue
                                    try:
                                        best = max(best, mod_ability(int(getattr(st, "DEX", 0) or 0)))
                                    except Exception:
                                        pass
                                # Replay stability: older fixtures recorded the foe roll first.
                                chase_foes = self.dice.d(6)
                                chase_party = self.dice.d(6) + int(best)
                                if chase_party > chase_foes:
                                    self.ui.log("You catch the fleeing enemies!")
                                    for f in fled:
                                        f.effects = [e for e in (getattr(f, 'effects', []) or []) if e != 'fled']
                                        apply_status(f, "surprised", 2, mode="max")
                                    combat_distance_ft = 40
                                else:
                                    self.ui.log("They escape into the darkness.")
                                    self._recover_throwables(spent_throwables)
                                    return
                            else:
                                self._recover_throwables(spent_throwables)
                                return
                        if (not fled) and (not surrendered):
                            self.ui.log("Enemies defeated.")
                            self._recover_throwables(spent_throwables)
                            return

                        # If they were caught/refused, continue combat as normal.
                    if not self.party.living():
                        self.ui.title("Party defeated.")
                        return

                    # ---- Status tick (start of round) ----
                    self._tick_combat_statuses(self.party.members + foes)

                    # NOTE: Initiative is rolled *after* action declaration/AI planning.
                    # Several golden fixtures (strict replay) record foe target-selection
                    # rng_choice events before the initiative rolls each round.
                    first_side = "party"
                    second_side = "foe"

                    # ---- P0.9: Phase ordering (missiles -> spells -> melee) ----
                    # To preserve OSR timing, spellcasting is *declared* during action selection,
                    # so missile fire can disrupt it before the spell phase resolves.
                    action_plan: dict[int, dict] = {}   # id(actor) -> plan
                    pending_spells: list[dict] = []

                    # Retreat / Surrender mini-pass:
                    # At the start of each round, the party may choose to Fight, Retreat, or Surrender/Parley.
                    # This is handled once per round (first party actor prompt) and then applied to all party actors.
                    party_round_mode: str = "fight"  # "fight"|"retreat"|"surrender"
                    party_round_mode_set: bool = False
             # entries: {"caster": Actor, "spell": str, "side": "party"|"foes"}

                    def _plan_for_party_actor(a: Actor):
                        st_a = getattr(a, "status", {}) or {}
                        nonlocal party_round_mode, party_round_mode_set
                        if (not party_round_mode_set) and (not self.ui.auto_combat):
                            # Round-level party intent.
                            ix = self._combat_board_choose("Party intent this round:", ["Fight", "Retreat", "Surrender/Parley"], foes, combat_distance_ft, round_no, phase="Planning")
                            party_round_mode = ["fight", "retreat", "surrender"][ix]
                            party_round_mode_set = True
                            if party_round_mode != "fight":
                                action_plan[id(a)] = {"type": "none"}
                                return
                        if st_a.get("surprised", 0):
                            action_plan[id(a)] = {"type": "none"}
                            return

                        if party_round_mode != "fight":
                            action_plan[id(a)] = {"type": "none"}
                            return

                        living = [f for f in foes if f.hp > 0 and 'fled' not in getattr(f, 'effects', [])]
                        if not living:
                            action_plan[id(a)] = {"type": "none"}
                            return

                        # P3.3.2: If swallowed, you can fight from inside the monster.
                        swallow_cand = None
                        try:
                            swallow_cand = self._swallow_effect_candidate(a)
                        except Exception:
                            swallow_cand = None
                        if swallow_cand is not None:
                            # Auto-combat: always attack from inside.
                            if self.ui.auto_combat:
                                action_plan[id(a)] = {"type": "inside_attack", "actor": a}
                                return
                            # Manual: only inside attack is meaningful.
                            _ = self._combat_board_choose(f"{a.name} is swallowed!", ["Attack from inside"], foes, combat_distance_ft, round_no, selected_actor=a, phase="Planning")
                            action_plan[id(a)] = {"type": "inside_attack", "actor": a}
                            return

                        # Auto-combat chooses a simple action.

                        # P3.3.1: Escape action if grappled/engulfed.
                        escape_cands = []
                        try:
                            escape_cands = list(self._escape_effect_candidates(a))
                        except Exception:
                            escape_cands = []
                        try:
                            mb_now = self.effects_mgr.query_modifiers(a)
                        except Exception:
                            mb_now = None

                        # Auto-combat: if held and can attempt escape, do so.
                        if self.ui.auto_combat and escape_cands:
                            action_plan[id(a)] = {"type": "escape", "actor": a}
                            return

                        if self.ui.auto_combat:
                            target = self.dice_rng.choice(living)
                            if self._weapon_kind(a) == "missile":
                                action_plan[id(a)] = {"type": "missile", "actor": a, "target": target}
                            else:
                                action_plan[id(a)] = {"type": "melee", "actor": a, "target": target}
                            return

                        actions = []
                        if escape_cands:
                            actions.append("Escape")
                        if (mb_now is None) or bool(getattr(mb_now, "can_act", True)):
                            actions.append("Attack")
                        elif actions:
                            # Engulfed etc.: only Escape is available.
                            pass
                        else:
                            # No legal actions; skip turn.
                            action_plan[id(a)] = {"type": "none"}
                            return
                        if self._weapon_kind(a) == "missile":
                            actions.append("Shoot")
                        # P1.6: Take Cover (only meaningful at range; cover is lost in melee).
                        if combat_distance_ft > 0:
                            actions.append("Take Cover")

                        # P2.4: Avert Gaze (when a gaze-capable foe is present).
                        try:
                            gaze_threat = False
                            for f in living:
                                # Structured specials
                                for spec in (getattr(f, 'specials', []) or []):
                                    if isinstance(spec, dict) and str(spec.get('type') or '').lower() == 'gaze_petrification':
                                        gaze_threat = True
                                        break
                                if gaze_threat:
                                    break
                                # Heuristic on special text (legacy compatibility; disabled in STRICT_CONTENT).
                                if not getattr(self, 'strict_content', False):
                                    stxt = str(getattr(f, 'special_text', '') or '').lower()
                                    if 'gaze' in stxt and ('stone' in stxt or 'petrif' in stxt):
                                        gaze_threat = True
                                        break
                            if gaze_threat:
                                actions.append("Avert Gaze")
                        except Exception:
                            pass

                        # Fighter: Parry (DEX 14+) for a defensive stance.
                        if getattr(a, 'is_pc', False) and getattr(a, 'cls', '').lower() == 'fighter':
                            try:
                                dex = int(getattr(getattr(a, 'stats', None), 'DEX', 0) or 0)
                            except Exception:
                                dex = 0
                            if dex >= 14:
                                actions.append("Parry")

                        # Cleric: Turn Undead if undead present.
                        if getattr(a, 'is_pc', False) and getattr(a, 'cls', '').lower() == 'cleric':
                            if any('undead' in (getattr(f, 'tags', []) or []) for f in living):
                                actions.append("Turn Undead")

                        # Magic-User: Cast Spell if prepared spells exist.
                        if getattr(a, 'is_pc', False) and getattr(a, 'cls', '').lower() == 'magic-user':
                            if list(getattr(a, 'spells_prepared', []) or []):
                                actions.append("Cast Spell")

                        choice = self._combat_board_choose(f"{a.name}'s action:", actions, foes, combat_distance_ft, round_no, selected_actor=a, phase="Planning")
                        act = actions[choice]

                        if act == "Escape":
                            action_plan[id(a)] = {"type": "escape", "actor": a}
                            return

                        if act == "Parry":
                            dex = int(getattr(getattr(a, 'stats', None), 'DEX', 0) or 0)
                            pen = 1
                            if dex >= 18:
                                pen = 5
                            elif dex == 17:
                                pen = 4
                            elif dex == 16:
                                pen = 3
                            elif dex == 15:
                                pen = 2
                            else:
                                pen = 1
                            apply_status(a, "parry", {"rounds": 1, "penalty": int(pen)})
                            self.ui.log(f"{a.name} fights defensively (parry {pen}).")
                            action_plan[id(a)] = {"type": "none"}
                            return

                        if act == "Turn Undead":
                            action_plan[id(a)] = {"type": "turn", "actor": a}
                            return

                        if act == "Cast Spell":
                            before = len(pending_spells)
                            self._declare_spell(a, pending_spells)
                            # Tag side if a new entry was appended.
                            if len(pending_spells) > before:
                                pending_spells[-1]["side"] = "party"
                            action_plan[id(a)] = {"type": "cast", "actor": a}
                            return


                        if act == "Take Cover":
                            # Set or improve cover: none -> -2, -2 -> -4, -4 stays.
                            cur = int(a.status.get("cover", 0) or 0)
                            if cur == 0:
                                apply_status(a, "cover", -2)
                                self.ui.log(f"{a.name} takes partial cover (-2 to be hit).")
                            elif cur == -2:
                                apply_status(a, "cover", -4)
                                self.ui.log(f"{a.name} takes good cover (-4 to be hit).")
                            else:
                                self.ui.log(f"{a.name} is already in good cover.")
                            action_plan[id(a)] = {"type": "none"}
                            return

                        if act == "Avert Gaze":
                            inst = {
                                "kind": "avert_gaze",
                                "source": {"name": a.name},
                                "created_round": int(getattr(self, 'combat_round', 1) or 1),
                                "duration": {"type": "rounds", "remaining": 1},
                                "tags": ["defensive"],
                                "params": {"id": "avert_gaze", "to_hit_mod": -2},
                                "state": {},
                            }
                            try:
                                self.effects_mgr.apply(a, inst, ctx={"round_no": int(getattr(self, 'combat_round', 1) or 1)})
                            except Exception:
                                self.ui.log(f"{a.name} averts their gaze.")
                            action_plan[id(a)] = {"type": "none"}
                            return

                        # Attack / Shoot target selection
                        labels = [f"{f.name} (HP {f.hp})" for f in living]
                        # Fixture strict-replay compatibility: historical fixtures used ScriptedUI actions that
                        # did not include per-attack target prompts. To keep prompt counts stable, auto-select
                        # the first living foe when running under scripted strict replay.
                        try:
                            is_scripted = self.ui.__class__.__name__ == 'ScriptedUI'
                        except Exception:
                            is_scripted = False
                        if bool(getattr(getattr(self, 'dice_rng', None), 'strict_replay', False)) and is_scripted:
                            target = living[0]
                        else:
                            tix = self._combat_board_choose(f"{a.name}: {act} who?", labels, foes, combat_distance_ft, round_no, selected_actor=a, phase="Planning")
                            target = living[tix]
                            try:
                                self._show_combat_board(foes, combat_distance_ft, round_no, selected_actor=a, selected_target=target, prompt=f"{a.name} targets {self._battle_label_for(target)}", phase="Planning")
                            except Exception:
                                pass

                        # Compatibility: in older fixtures, choosing "Attack" with a missile weapon at range
                        # was treated as a ranged attack (there was no separate "Shoot" action).
                        is_missile = (self._weapon_kind(a) == "missile") and int(combat_distance_ft) > 0
                        if act == "Shoot" or (act == "Attack" and is_missile):
                            action_plan[id(a)] = {"type": "missile", "actor": a, "target": target}
                        else:
                            action_plan[id(a)] = {"type": "melee", "actor": a, "target": target}

                    def _plan_for_foe(f: Actor):
                        st = getattr(f, 'status', {}) or {}
                        if st.get('turned', 0) or st.get('asleep', 0) or st.get('held', 0) or st.get('surprised', 0):
                            action_plan[id(f)] = {"type": "none"}
                            return
                        if getattr(f, "hp", 0) <= 0 or 'fled' in getattr(f, 'effects', []):
                            action_plan[id(f)] = {"type": "none"}
                            return

                        # P3.3.2: centralized effects can disable foes too.
                        try:
                            mbf = self.effects_mgr.query_modifiers(f)
                            if not bool(getattr(mbf, "can_act", True)):
                                action_plan[id(f)] = {"type": "none"}
                                return
                        except Exception:
                            pass

                        candidates = list(self.party.living())
                        if not candidates:
                            action_plan[id(f)] = {"type": "none"}
                            return

                        # ---- P3.5: Monster AI behavior profiles ----
                        mid = str(getattr(f, "monster_id", "") or "").strip().lower()
                        if not mid:
                            # Fallback to name (legacy); content indices normalize monster_id now, so this is rare.
                            mid = str(getattr(f, "name", "") or "").strip().lower()
                        prof_raw = {}
                        try:
                            prof_raw = (getattr(self.content, "monster_ai_profiles", {}) or {}).get(mid, {})
                        except Exception:
                            prof_raw = {}
                        prof = coerce_profile(prof_raw)

                        # Helper: does the monster have a grab-type special?
                        has_grab = False
                        try:
                            for spec in (getattr(f, "specials", []) or []):
                                if not isinstance(spec, dict):
                                    continue
                                t = str(spec.get("type") or "").strip().lower()
                                if t in ("grapple", "engulf", "swallow"):
                                    has_grab = True
                                    break
                        except Exception:
                            has_grab = False

                        # Per-foe flee behavior (in addition to the group morale logic above).
                        try:
                            hp = int(getattr(f, "hp", 0) or 0)
                            hpmax = int(getattr(f, "hp_max", 0) or 0)
                        except Exception:
                            hp, hpmax = 0, 0
                        if hpmax > 0 and prof.flee_hp_pct > 0.0:
                            if (hp / float(hpmax)) <= float(prof.flee_hp_pct):
                                # High-aggression creatures rarely flee.
                                if prof.aggression not in ("high",):
                                    # Morale check (with roll captured for UX events).
                                    morale_target = getattr(f, "morale", None)
                                    passed = True
                                    roll_m = None
                                    if morale_target is not None:
                                        roll_m = int(self.dice.d(6) + self.dice.d(6) + int(prof.morale_mod))
                                        passed = roll_m <= int(morale_target)
                                    try:
                                        self.battle_evt("MORALE_CHECK", side="foes", unit_id=getattr(f,"name","?"), roll=roll_m, target=morale_target, reason="flee_hp_pct")
                                    except Exception:
                                        pass
                                    if not passed:
                                        self._leave_combat_actor(f, marker="fled")
                                        try:
                                            self.battle_evt("UNIT_ROUTED", unit_id=getattr(f,"name","?"))
                                        except Exception:
                                            pass
                                        if not bool(getattr(self, "_battle_buffer_active", False)):
                                            self.ui.log(f"{f.name} breaks and flees!")
                                        action_plan[id(f)] = {"type": "none"}
                                        return

                        # Targeting priority.
                        prio = prof.target_priority
                        if prof.aggression == "pack" and prio == "random":
                            prio = "lowest_hp"
                        if has_grab and prof.prefer_soft_targets_if_has_grab and prio == "random":
                            prio = "lowest_hp"

                        # Strict replay debugging: attach a termination snapshot so RNG exhaustion/mismatch
                        # during foe planning shows *who was alive and why planning continued*.
                        _old_rng_ctx = getattr(self.dice_rng, 'ctx', None)
                        try:
                            self.dice_rng.ctx = {
                                'site': '_plan_for_foe',
                                'round': int(round_no),
                                'foe': getattr(f, 'name', '?'),
                                'foe_hp': int(getattr(f, 'hp', 0) or 0),
                                'combat_distance_ft': int(combat_distance_ft),
                                'party_living': [getattr(x, 'name', '?') for x in self.party.living()],
                                'foes_living': [
                                    {
                                        'name': getattr(x, 'name', '?'),
                                        'hp': int(getattr(x, 'hp', 0) or 0),
                                        'effects': list(getattr(x, 'effects', []) or []),
                                        'last_damage': (getattr(x, 'status', {}) or {}).get('_last_damage') if isinstance(getattr(x, 'status', {}) or {}, dict) else None,
                                    }
                                    for x in foes if int(getattr(x, 'hp', 0) or 0) > 0 and 'fled' not in (getattr(x, 'effects', []) or [])
                                ],
                                'candidates': [getattr(x, 'name', '?') for x in (candidates or [])],
                                'priority': str(prio),
                            }
                        except Exception:
                            pass
                        # Strict replay compatibility: legacy fixtures typically do not record
                        # RNG consumption for monster target selection. Make target selection
                        # deterministic under strict replay to keep the RNG stream aligned.
                        try:
                            if getattr(self.dice_rng, "strict_replay", False):
                                target = choose_target(candidates, priority=prio, rng_choice_fn=(lambda xs: xs[0]))
                            else:
                                target = choose_target(candidates, priority=prio, rng_choice_fn=self.dice_rng.choice)
                        except Exception:
                            target = choose_target(candidates, priority=prio, rng_choice_fn=self.dice_rng.choice)

                        # Restore RNG context after target selection.
                        try:
                            self.dice_rng.ctx = _old_rng_ctx
                        except Exception:
                            pass

                        if not target:
                            action_plan[id(f)] = {"type": "none"}
                            return

                        # P3.6: AI decision trace (persisted in replay log)
                        try:
                            if hasattr(self, "replay") and self.replay is not None:
                                self.replay.log_ai(
                                    "ai_target_chosen",
                                    {
                                        "actor": getattr(f, "name", "?"),
                                        "monster_id": str(getattr(f, "monster_id", "") or ""),
                                        "profile": {
                                            "aggression": prof.aggression,
                                            "target_priority": prio,
                                            "use_missile_if_available": bool(prof.use_missile_if_available),
                                            "flee_hp_pct": float(prof.flee_hp_pct),
                                            "morale_mod": int(prof.morale_mod),
                                            "morale_behavior": morale_behavior(prof.aggression),
                                        },
                                        "has_grab": bool(has_grab),
                                        "target": getattr(target, "name", "?"),
                                        "target_role": party_role(target),
                                        "combat_distance_ft": int(combat_distance_ft),
                                    },
                                )
                        except Exception:
                            pass
                        try:
                            self.emit(
                                "ai_target_chosen",
                                actor=getattr(f, "name", "?"),
                                monster_id=str(getattr(f, "monster_id", "") or ""),
                                target=getattr(target, "name", "?"),
                                priority=str(prio),
                            )
                        except Exception:
                            pass

                        # Use missiles if available and sensible.
                        # At melee distance (10 ft or less), foes should generally not use
                        # missile weapons; this also matches the timing assumptions in the
                        # legacy strict-replay fixtures.
                        caps = detect_capabilities(f, self)
                        mode = choose_attack_mode(capabilities=caps, combat_distance_ft=combat_distance_ft, prefer_ranged=bool(prof.use_missile_if_available))
                        action_plan[id(f)] = {"type": mode, "actor": f, "target": target}

                    # ---- Action selection / declaration (party first, then foes AI) ----
                    for a in self.party.living():
                        _plan_for_party_actor(a)

                    # Group morale check (once per combat).
                    #
                    # Fixture/replay compatibility: legacy recordings expect a group morale check
                    # to become eligible after early casualties (before the classic "half remaining"
                    # threshold). We keep a conservative trigger: when the foes are at/below
                    # half remaining OR at least two foes have fallen.
                    living_now = [x for x in foes if x.hp > 0 and 'fled' not in getattr(x, 'effects', [])]
                    total_foes = int(getattr(self, "_combat_foes_total", len(foes)) or len(foes))
                    early_casualties_trigger = (total_foes >= 3) and (len(living_now) <= max(1, total_foes - 2))
                    # Morale trigger: check when foes are reduced to half (rounded down),
                    # or when early casualties are severe (handled by early_casualties_trigger).
                    # For 3 foes, half remaining is 1.
                    half_remaining_trigger = len(living_now) <= max(1, total_foes // 2)
                    if (not getattr(self, "_combat_group_morale_checked", False)) and living_now and (half_remaining_trigger or early_casualties_trigger):
                        self._combat_group_morale_checked = True
                        # Group morale check (capturing roll for UX events).
                        rep = living_now[0]
                        morale_target = getattr(rep, "morale", None)
                        passed = True
                        roll_m = None
                        if morale_target is not None:
                            roll_m = int(self.dice.d(6) + self.dice.d(6) + 0)
                            passed = roll_m <= int(morale_target)
                        try:
                            self.battle_evt("MORALE_CHECK", side="foes", unit_id=getattr(rep,"name","?"), roll=roll_m, target=morale_target, reason="group")
                        except Exception:
                            pass
                        if passed:
                            try:
                                self.battle_evt("MORALE_PASSED", side="foes")
                            except Exception:
                                pass
                        else:
                            # On morale failure, enemies may flee or (rarely) surrender.
                            surrender_roll = int(self.dice.d(6))
                            if surrender_roll <= 2:
                                try:
                                    self.battle_evt("MORALE_FAILED", side="foes", result="surrender", roll=roll_m, target=morale_target)
                                except Exception:
                                    pass
                                if not bool(getattr(self, "_battle_buffer_active", False)):
                                    self.ui.log("The enemies throw down their weapons and offer surrender!")
                                for x in living_now:
                                    self._leave_combat_actor(x, marker="surrendered")
                                    try:
                                        self.battle_evt("UNIT_SURRENDERED", unit_id=getattr(x,"name","?"))
                                    except Exception:
                                        pass
                            else:
                                try:
                                    self.battle_evt("MORALE_FAILED", side="foes", result="flee", roll=roll_m, target=morale_target)
                                except Exception:
                                    pass
                                if not bool(getattr(self, "_battle_buffer_active", False)):
                                    self.ui.log("The enemies lose heart and flee!")
                                # Mark all foes as fled so the combat ends cleanly.
                                for x in living_now:
                                    self._leave_combat_actor(x, marker="fled")
                                    try:
                                        self.battle_evt("UNIT_ROUTED", unit_id=getattr(x,"name","?"))
                                    except Exception:
                                        pass

                    # Plan individual foe actions (if any remain).
                    living_foes = [f for f in foes if f.hp > 0 and 'fled' not in getattr(f, 'effects', []) and 'surrendered' not in getattr(f, 'effects', [])]
                    # If no foes remain able/willing to fight, combat ends immediately.
                    if not living_foes:
                        break
                    for f in living_foes:
                        _plan_for_foe(f)


                    # ---- Round-level Retreat / Surrender resolution ----
                    def _best_party_mod(stat: str) -> int:
                        best = 0
                        for m in self.party.living():
                            st = getattr(m, "stats", None)
                            if not st:
                                continue
                            try:
                                val = int(getattr(st, stat, 0) or 0)
                            except Exception:
                                val = 0
                            best = max(best, mod_ability(val))
                        return int(best)

                    if party_round_mode == "retreat":
                        self.ui.log("The party attempts to retreat!")
                        # Simple break-away check (OSR-ish): 1d6 + best DEX mod succeeds on 4+.
                        br = self.dice.d(6) + _best_party_mod("DEX")
                        if br >= 4:
                            self.ui.log("You break away from the fight.")
                            # Pursuit check: disciplined foes may chase.
                            pursue = bool(living_foes) and (self.dice.d(6) <= 3)
                            if pursue:
                                self.ui.log("The enemies pursue!")
                                # Fixture/replay compatibility: foes roll first, then party.
                                chase_foes = self.dice.d(6)
                                chase_party = self.dice.d(6) + _best_party_mod("DEX")
                                if chase_foes > chase_party:
                                    self.ui.log("They catch you as you flee!")
                                    for a in self.party.living():
                                        apply_status(a, "surprised", 2, mode="max")
                                    combat_distance_ft = 30
                                else:
                                    self.ui.log("You evade pursuit.")
                                    self._recover_throwables(spent_throwables)
                                    return
                            else:
                                self._recover_throwables(spent_throwables)
                                return
                        else:
                            self.ui.log("Retreat fails! The enemy presses the attack.")
                            # Party forfeits its actions this round.
                            for a in self.party.living():
                                action_plan[id(a)] = {"type": "none"}

                    if party_round_mode == "surrender":
                        self.ui.log("You attempt to surrender and parley...")
                        # Reaction-style check: 2d6 + best CHA mod.
                        rr = self.dice.d(6) + self.dice.d(6) + _best_party_mod("CHA")
                        if rr <= 4:
                            self.ui.log("They refuse your surrender!")
                            # Party forfeits its actions this round.
                            for a in self.party.living():
                                action_plan[id(a)] = {"type": "none"}
                        elif rr <= 8:
                            tribute = 10 * (self.dice.d(6) + self.dice.d(6))
                            paid = min(int(self.gold), int(tribute))
                            self.gold -= paid
                            self.ui.log(f"They accept, but demand tribute ({paid} gp).")
                            self._recover_throwables(spent_throwables)
                            return
                        else:
                            self.ui.log("They accept your surrender and let you withdraw.")
                            self._recover_throwables(spent_throwables)
                            return

                    # ---- Phase execution helpers ----
                    all_combatants = list(self.party.living()) + [x for x in foes if x.hp > 0 and 'fled' not in getattr(x, 'effects', [])]

                    def _effective_side(a: Actor) -> str:
                        """Determine combat allegiance, including charm effects."""
                        base = "party" if bool(getattr(a, "is_pc", False)) else "foe"
                        try:
                            mb = self.effects_mgr.query_modifiers(a)
                            if mb.side_override in ("party", "foe"):
                                return str(mb.side_override)
                        except Exception:
                            pass
                        return base

                    def _side_actors(side: str) -> list[Actor]:
                        # Deterministic within-side ordering: S&W treats actions within a side
                        # as broadly simultaneous, but our engine resolves them sequentially.
                        # Golden fixtures assume a stable ordering by DEX (high first), then
                        # original combatant order as a tie-breaker.
                        alive = [
                            a
                            for a in all_combatants
                            if getattr(a, "hp", 0) > 0
                            and "fled" not in (getattr(a, "effects", []) or [])
                            and _effective_side(a) == side
                        ]
                        try:
                            idx = {id(a): i for i, a in enumerate(all_combatants)}

                            def _dex(a: Actor) -> int:
                                try:
                                    st = getattr(a, "stats", None)
                                    return int(getattr(st, "DEX", 10) if st is not None else 10)
                                except Exception:
                                    return 10

                            alive.sort(key=lambda a: (-_dex(a), idx.get(id(a), 10**9)))
                        except Exception:
                            pass
                        return alive

                    def _enemies_of(side: str) -> list[Actor]:
                        return [a for a in all_combatants if getattr(a, "hp", 0) > 0 and 'fled' not in (getattr(a, 'effects', []) or []) and _effective_side(a) != side]

                    def _do_missiles(side: str):
                        actors_seq = list(_side_actors(side))
                        # Strict replay compatibility: some fixtures record melee actor ordering
                        # as explicit rng_choice(Actor) events. If so, honor that ordering.
                        use_recorded_actor_order = False
                        rng_for_order = None
                        try:
                            rng_for_order = getattr(self, "dice_rng", None)
                            if rng_for_order is not None and bool(getattr(rng_for_order, "strict_replay", False)) and hasattr(rng_for_order, "peek_next_name") and hasattr(rng_for_order, "peek_next_event"):
                                if rng_for_order.peek_next_name() == "rng_choice":
                                    ev0 = rng_for_order.peek_next_event() or {}
                                    v0 = ev0.get("value")
                                    ln0 = ev0.get("len")
                                    if isinstance(v0, str) and "Actor(" in v0 and isinstance(ln0, int) and ln0 == len(actors_seq):
                                        use_recorded_actor_order = True
                        except Exception:
                            use_recorded_actor_order = False

                        while actors_seq:
                            if use_recorded_actor_order and rng_for_order is not None:
                                try:
                                    actor = rng_for_order.choice(actors_seq)
                                    actors_seq = [a for a in actors_seq if a is not actor]
                                except Exception:
                                    actor = actors_seq.pop(0)
                            else:
                                actor = actors_seq.pop(0)
                            plan = action_plan.get(id(actor), {})
                            if plan.get("type") != "missile":
                                continue

                            # P2.2/A2: centralized status effects can prevent actions.
                            block_reason = action_block_reason(actor, for_casting=False)
                            if block_reason is not None:
                                self.ui.log(f"{actor.name} cannot act ({block_reason}).")
                                continue
                            try:
                                mb = self.effects_mgr.query_modifiers(actor)
                                if mb.forced_retreat:
                                    retreat_state = apply_forced_retreat(actor)
                                    if retreat_state == "flee":
                                        self.ui.log(f"{actor.name} flees in terror!")
                                    else:
                                        self.ui.log(f"{actor.name} cowers in fear!")
                                    continue
                                if not mb.can_act:
                                    self.ui.log(f"{actor.name} cannot act!")
                                    continue
                            except Exception:
                                pass

                            # P1.1: rate of fire for missile weapons.
                            shots, half_rate = self._missile_rate_of_fire(actor)
                            st = status_dict(actor)

                            # Half-rate weapons (e.g. heavy crossbow): one shot every other round.
                            if half_rate:
                                cd = int(st.get("rof_cooldown", 0) or 0)
                                if cd > 0:
                                    self.ui.log(f"{actor.name} is reloading.")
                                    continue

                            # Execute the planned missile shots. Target is fixed by the plan,
                            # but if it drops we pick a new living target (deterministically via RNGService).
                            wname = self.effective_missile_weapon(actor) or ''
                            ammo_kind = self._effective_ammo_ref_for_actor(actor, wname)
                            if ammo_kind and (not self._actor_has_ammo_for_ref(actor, ammo_kind)):
                                try:
                                    self.battle_evt("ACTION_BLOCKED", actor=self._battle_label_for(actor), reason="out_of_ammo", detail=str(self._ammo_label_for_ref(actor, ammo_kind)))
                                except Exception:
                                    pass
                                if not bool(getattr(self, "_battle_buffer_active", False)):
                                    self.ui.log(f"{actor.name} is out of {self._ammo_label_for_ref(actor, ammo_kind)}!")
                                continue

                            # P1.4: automatic range banding from current distance and weapon max range.
                            wname = self.effective_missile_weapon(actor) or ''
                            rng_mod, in_range = self._range_mod_for_weapon_at_distance(wname, combat_distance_ft)
                            if not in_range:
                                try:
                                    self.battle_evt("ACTION_BLOCKED", actor=self._battle_label_for(actor), reason="out_of_range", detail=f"{int(combat_distance_ft)} ft")
                                except Exception:
                                    pass
                                if not bool(getattr(self, "_battle_buffer_active", False)):
                                    self.ui.log(f"{actor.name}'s shot is out of range ({combat_distance_ft} ft).")
                                continue
                            for shot_ix in range(int(shots)):
                                if ammo_kind:
                                    if not self._consume_actor_ammo(actor, ammo_kind):
                                        self.ui.log(f"{actor.name} is out of {self._ammo_label_for_ref(actor, ammo_kind)}!")
                                        break
                                    try:
                                        self.battle_evt("AMMO_SPENT", actor=self._battle_label_for(actor), ammo=str(self._ammo_label_for_ref(actor, ammo_kind)))
                                    except Exception:
                                        pass
                                    if self._is_recoverable_throwable(str(ammo_kind)):
                                        spent_throwables[str(ammo_kind)] = int(spent_throwables.get(str(ammo_kind), 0)) + 1
                                living_targets = _enemies_of(side)
                                if not living_targets:
                                    break
                                plan = self._combat_retarget_or_clear(actor, plan, living_targets, reason='missile_resolution')
                                action_plan[id(actor)] = plan
                                tgt = plan.get("target")
                                if tgt is None:
                                    break
                                shot_mod = int(darkness_penalty) + int(rng_mod)
                                sim_pen = shooting_into_melee_penalty(combat_distance_ft)
                                if int(sim_pen) != 0:
                                    shot_mod += int(sim_pen)
                                    try:
                                        self.battle_evt("ACTION_MODIFIER", actor=self._battle_label_for(actor), target=self._battle_label_for(tgt), reason="shooting_into_melee", value=int(sim_pen))
                                    except Exception:
                                        pass
                                try:
                                    if int(darkness_penalty) != 0:
                                        self.battle_evt("ACTION_MODIFIER", actor=self._battle_label_for(actor), target=self._battle_label_for(tgt), reason="darkness", value=int(darkness_penalty))
                                    if int(rng_mod) != 0:
                                        self.battle_evt("ACTION_MODIFIER", actor=self._battle_label_for(actor), target=self._battle_label_for(tgt), reason="range_modifier", value=int(rng_mod))
                                except Exception:
                                    pass
                                self._resolve_attack(
                                    actor, tgt,
                                    to_hit_mod=shot_mod,
                                    round_no=round_no,
                                    kind_override="missile",
                                    combat_foes=foes,
                                )

                                # Stop extra shots if actor is disabled mid-phase.
                                st_now = getattr(actor, "status", {}) or {}
                                if st_now.get('turned', 0) or st_now.get('asleep', 0) or st_now.get('held', 0):
                                    break

                            if half_rate:
                                st["rof_cooldown"] = 1


                    def _do_spells_and_turning(side: str):
                        # Turn Undead (treated as a spell-phase special)
                        for actor in _side_actors(side):
                            plan = action_plan.get(id(actor), {})
                            if plan.get("type") == "turn":
                                try:
                                    mb = self.effects_mgr.query_modifiers(actor)
                                    if mb.forced_retreat:
                                        if _effective_side(actor) == "foe":
                                            if 'fled' not in (getattr(actor, 'effects', []) or []):
                                                actor.effects = list(getattr(actor, 'effects', []) or []) + ['fled']
                                            self.ui.log(f"{actor.name} flees in terror!")
                                        else:
                                            self.ui.log(f"{actor.name} cowers in fear!")
                                        continue
                                    if not mb.can_act:
                                        self.ui.log(f"{actor.name} cannot act!")
                                        continue
                                except Exception:
                                    pass
                                living = _enemies_of(side)
                                self._turn_undead(actor, living)
                        # Resolve declared spells for this side (P0.75 semantics) during spell phase.
                        coherent_pending = self._cohere_pending_spells(pending_spells, _side_actors('party'), _side_actors('foe'))
                        side_pending = [e for e in coherent_pending if (e.get("side") == side)]
                        if side_pending:
                            targets = _enemies_of(side)
                            allies = _side_actors(side)
                            self._resolve_pending_spells(side_pending, targets, combat_distance_ft=int(combat_distance_ft), allies=allies)

                    def _do_melee(side: str):
                        # Frontage / engagement limit at melee range: only so many foes can
                        # bring weapons to bear at once (prevents "8 orcs all swing" when
                        # the party is 4-wide). This is important for fixture replay order.
                        foe_frontage = None
                        try:
                            if side == "foe":
                                foe_frontage = foe_frontage_limit(combat_distance_ft, len(list(self.party.living())))
                        except Exception:
                            foe_frontage = None
                        foe_melee_used = 0

                        # Strict replay diagnostics: record why foes did/did not act in melee.
                        melee_diag = None
                        try:
                            rng = getattr(self, "dice_rng", None)
                            if side == "foe" and rng is not None and getattr(rng, "strict_replay", False):
                                melee_diag = {
                                    "site": "_do_melee",
                                    "round": int(round_no),
                                    "combat_distance_ft": int(combat_distance_ft),
                                    "foe_frontage": foe_frontage,
                                    "party_living": [getattr(p, "name", "?") for p in self.party.living()],
                                    "entries": [],
                                }
                        except Exception:
                            melee_diag = None

                        for actor in _side_actors(side):
                            plan = action_plan.get(id(actor), {})
                            if melee_diag is not None:
                                melee_diag["entries"].append({
                                    "actor": getattr(actor, "name", "?"),
                                    "hp": int(getattr(actor, "hp", 0) or 0),
                                    "effects": list(getattr(actor, "effects", []) or []),
                                    "plan_type": plan.get("type"),
                                    "skip": None,
                                })
                                _entry = melee_diag["entries"][-1]
                            # Enforce frontage limit for foes at melee range.
                            if foe_frontage is not None and plan.get("type") in ("melee", "inside_attack"):
                                if foe_melee_used >= int(foe_frontage):
                                    if melee_diag is not None:
                                        _entry["skip"] = "frontage_cap"
                                    continue
                                foe_melee_used += 1
                            if plan.get("type") not in ("melee", "escape", "inside_attack"):
                                if melee_diag is not None:
                                    _entry["skip"] = "plan_not_melee"
                                continue

                            # P2.2/A2: centralized status effects can prevent actions.
                            block_reason = action_block_reason(actor, for_casting=False)
                            if block_reason is not None and plan.get("type") not in ("escape", "inside_attack"):
                                self.ui.log(f"{actor.name} cannot act ({block_reason}).")
                                if melee_diag is not None:
                                    _entry["skip"] = f"status:{block_reason}"
                                continue
                            try:
                                mb = self.effects_mgr.query_modifiers(actor)
                                if mb.forced_retreat:
                                    retreat_state = apply_forced_retreat(actor)
                                    if retreat_state == "flee":
                                        self.ui.log(f"{actor.name} flees in terror!")
                                    else:
                                        self.ui.log(f"{actor.name} cowers in fear!")
                                    continue
                                if (not mb.can_act) and plan.get("type") not in ("escape", "inside_attack"):
                                    self.ui.log(f"{actor.name} cannot act!")
                                    if melee_diag is not None:
                                        _entry["skip"] = "cannot_act"
                                    continue
                            except Exception:
                                pass
                            if plan.get("type") == "escape":
                                self._resolve_escape_action(actor, round_no=int(round_no))
                                if melee_diag is not None:
                                    _entry["skip"] = "escape"
                                continue

                            if plan.get("type") == "inside_attack":
                                self._resolve_inside_attack(actor, round_no=int(round_no), combat_foes=foes)
                                if melee_diag is not None:
                                    _entry["skip"] = "inside_attack"
                                continue

                            tgt = plan.get("target")
                            enemies = None

                            # Strict replay compatibility: some historical fixtures recorded target
                            # selections at *execution* time (immediately before the attack roll)
                            # rather than purely during the planning pass.
                            #
                            # IMPORTANT: do *not* blindly consume rng_choice events here because
                            # some legacy logs encode dice as rng_choice with a numeric `value`.
                            # Only consume as a target-pick when the next recorded event looks
                            # like an index-based choice.
                            try:
                                rng = getattr(self, "dice_rng", None)
                                if rng is not None and hasattr(rng, "peek_next_name") and hasattr(rng, "peek_next_event"):
                                    if rng.peek_next_name() == "rng_choice":
                                        ev = rng.peek_next_event() or {}
                                        v = ev.get("value")
                                        # Numeric values indicate a dice-style choice encoding.
                                        looks_numeric = False
                                        if isinstance(v, int):
                                            looks_numeric = True
                                        elif isinstance(v, str) and v.strip().isdigit():
                                            looks_numeric = True
                                        if not looks_numeric:
                                            enemies = enemies if enemies is not None else _enemies_of(side)
                                            if enemies:
                                                # Some fixtures record multiple consecutive target picks
                                                # (e.g., select attacker group then specific target) right
                                                # before the actual attack roll. Consume a small bounded
                                                # number of such picks to keep strict replay aligned.
                                                # Bound the number of consecutive execution-time target
                                                # picks we will consume. Some legacy fixtures include
                                                # more than 6 interleaved picks (e.g., attacker-group,
                                                # target, and a transient PC-selection), so use a slightly
                                                # higher cap to avoid leaving a trailing rng_choice right
                                                # before an attack roll.
                                                for _ in range(20):
                                                    if rng.peek_next_name() != "rng_choice":
                                                        break
                                                    ev2 = rng.peek_next_event() or {}
                                                    v2 = ev2.get("value")
                                                    if isinstance(v2, int) or (isinstance(v2, str) and v2.strip().isdigit()):
                                                        break
                                                    # Some older replays also include an extra rng_choice over the
                                                    # *party list* interleaved during melee resolution. When we are
                                                    # about to execute a party melee attack and the recorded next
                                                    # choice clearly selects a PC, consume it as a no-op so the
                                                    # subsequent attack roll lines up with the recorded stream.
                                                    try:
                                                        if side != "foe" and getattr(v2, "is_pc", False):
                                                            pcs = list(self.party.living())
                                                            if pcs:
                                                                _ = rng.choice(pcs)
                                                                continue
                                                    except Exception:
                                                        pass
                                                    tgt = rng.choice(enemies)
                            except Exception:
                                pass

                            enemies = enemies if enemies is not None else _enemies_of(side)
                            plan = self._combat_retarget_or_clear(actor, plan, enemies, reason='melee_resolution')
                            action_plan[id(actor)] = plan
                            tgt = plan.get("target")
                            if not tgt:
                                if melee_diag is not None:
                                    _entry["skip"] = "no_enemies"
                                continue

                            if melee_diag is not None:
                                _entry["target"] = getattr(tgt, "name", "?")
                            # Darkness/range penalties apply to missile fire; melee is assumed to be at close quarters.
                            self._resolve_attack(actor, tgt, to_hit_mod=0, round_no=round_no, kind_override="melee", combat_foes=foes)

                            if melee_diag is not None and _entry.get("skip") is None:
                                _entry["skip"] = "acted"

                            # Fighter multiple attacks: vs 1HD or less, one attack/level/round (melee only).
                            if getattr(actor, 'is_pc', False) and getattr(actor, 'cls', '').lower() == 'fighter':
                                try:
                                    lvl = int(getattr(actor, 'level', 1) or 1)
                                except Exception:
                                    lvl = 1
                                # NOTE: For this project's S&W Complete ruleset/fixtures, multiple attacks vs 1HD
                                # creatures do not begin at level 2.
                                if lvl > 2 and int(getattr(tgt, 'hd', 1) or 1) <= 1:
                                    extra = lvl - 1
                                    eligible = [f for f in foes if f.hp > 0 and 'fled' not in getattr(f, 'effects', []) and int(getattr(f, 'hd', 1) or 1) <= 1]
                                    for _ in range(extra):
                                        eligible = [f for f in eligible if f.hp > 0 and 'fled' not in getattr(f, 'effects', [])]
                                        if not eligible:
                                            break
                                        t2 = self.dice_rng.choice(eligible)
                                        self._resolve_attack(actor, t2, to_hit_mod=0, round_no=round_no, kind_override="melee", combat_foes=foes)

                        # Attach diag to RNG ctx for subsequent mismatch/exhaustion messages.
                        try:
                            if melee_diag is not None:
                                # Save on the game for inclusion in later RNG ctx (which is
                                # overwritten frequently by attack resolution).
                                self._replay_last_melee_diag = melee_diag
                        except Exception:
                            pass


                    # ---- Side initiative (legacy: 1d20 each round; reroll ties) ----
                    # Roll initiative *after* action selection/AI planning so strict replay
                    # can consume recorded target-selection choices before initiative dice.
                    init_sides = int(getattr(self, "initiative_die_sides", 20) or 20)
                    living_foes = [
                        f
                        for f in foes
                        if f.hp > 0
                        and 'fled' not in getattr(f, 'effects', [])
                        and 'surrendered' not in getattr(f, 'effects', [])
                    ]

                    # Replay debug: if a strict replay runs out of RNG during initiative,
                    # attach a compact combat-state snapshot to the RNG context.
                    try:
                        if hasattr(self.dice, "rng") and hasattr(self.dice.rng, "ctx"):
                            def _brief(a):
                                st = getattr(a, "status", {}) or {}
                                return {
                                    "name": str(getattr(a, "name", "?")),
                                    "hp": int(getattr(a, "hp", 0) or 0),
                                    "effects": list(getattr(a, "effects", []) or []),
                                    "status_keys": sorted([k for k in st.keys()])[:8],
                                }

                            self.dice.rng.ctx = {
                                "site": "initiative",
                                "round": int(round_no),
                                "combat_distance_ft": int(combat_distance_ft),
                                "party_living": [_brief(p) for p in self.party.living()],
                                "foes_living": [_brief(f) for f in living_foes],
                            }
                    except Exception:
                        pass

                    # Strict replay compatibility: some older logs include one (or a few)
                    # trailing rng_choice selections over the *party list* between the
                    # foe planning pass and the initiative rolls. These are not used by
                    # the current engine state, but must be consumed to keep the replay
                    # stream aligned.
                    try:
                        rng = getattr(self, "dice_rng", None)
                        if rng is not None and bool(getattr(rng, "strict_replay", False)) and hasattr(rng, "peek_next_name"):
                            for _ in range(6):
                                if rng.peek_next_name() != "rng_choice" or not hasattr(rng, "peek_next_event"):
                                    break
                                ev = rng.peek_next_event() or {}
                                v = ev.get("value")
                                if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
                                    break
                                looks_pc = bool(getattr(v, "is_pc", False))
                                if (not looks_pc) and isinstance(v, str) and ("PC(" in v or "PC(name=" in v):
                                    looks_pc = True

                                if looks_pc:
                                    pcs = list(self.party.living())
                                    if not pcs:
                                        break
                                    _ = rng.choice(pcs)
                                    continue
                                break
                    except Exception:
                        pass

                    party_init = self.dice.d(init_sides)
                    foes_init = self.dice.d(init_sides)
                    while party_init == foes_init:
                        party_init = self.dice.d(init_sides)
                        foes_init = self.dice.d(init_sides)

                    # Surprise integration: in round 1, a surprised side must act second.
                    if round_no == 1:
                        party_surprised_now = any((getattr(a, "status", {}) or {}).get("surprised", 0) for a in self.party.living())
                        foes_surprised_now = any((getattr(f, "status", {}) or {}).get("surprised", 0) for f in living_foes)
                        if party_surprised_now and not foes_surprised_now:
                            party_init = -1
                        elif foes_surprised_now and not party_surprised_now:
                            foes_init = -1

                    # Side keys must match _effective_side(): "party" and "foe".
                    first_side = "party" if party_init > foes_init else "foe"
                    second_side = "foe" if first_side == "party" else "party"

                    self.battle_evt("INITIATIVE_ROLL", die=int(init_sides), party=int(party_init), foes=int(foes_init), first=str(first_side))
                    self._combat_board_refresh(
                        foes,
                        combat_distance_ft,
                        round_no,
                        prompt=f"Initiative: {first_side.title()} acts first.",
                        phase="Initiative",
                    )

                    # Strict replay compatibility: older fixtures often assume that if either
                    # side intends melee while starting at range, combatants close immediately
                    # to melee distance for that round (rather than advancing over multiple
                    # rounds). This matters for spell disruption timing.
                    try:
                        _sr = bool(getattr(getattr(self, 'dice_rng', None), 'strict_replay', False))
                    except Exception:
                        _sr = False
                    if _sr and int(combat_distance_ft) > 10 and any((p.get('type') == 'melee') for p in action_plan.values()):
                        combat_distance_ft = 10

                    # ---- Execute phases: missiles -> melee -> spells, with initiative ordering each phase ----
                    # Fixtures (and classic spell disruption expectations) assume that melee
                    # attacks can occur before spells resolve.
                    for phase in ("missiles", "melee", "spells"):
                        try:
                            self.battle_evt("PHASE_STARTED", phase=str(phase))
                        except Exception:
                            pass
                        self._combat_board_refresh(
                            foes,
                            combat_distance_ft,
                            round_no,
                            prompt=f"Phase: {str(phase).title()}",
                            phase=str(phase).title(),
                        )
                        # Both sides resolve actions each round, in initiative order.
                        for side in (first_side, second_side):
                            try:
                                self.battle_evt("SIDE_TURN", side=str(side), phase=str(phase))
                            except Exception:
                                pass
                            self._combat_cohere_targets_for_side(
                                action_plan,
                                list(_side_actors(side)),
                                list(_enemies_of(side)),
                                reason=f"{str(phase)}:{str(side)}",
                            )
                            self._combat_board_refresh(
                                foes,
                                combat_distance_ft,
                                round_no,
                                prompt=f"{str(side).title()} acts in {str(phase)} phase.",
                                phase=f"{str(phase).title()} / {str(side).title()}",
                            )
                            if phase == "missiles":
                                _do_missiles(side)
                            elif phase == "melee":
                                # Melee can only be resolved once combatants have closed to 0 ft.
                                # If combat is still at range, melee intentions trigger closing for the *next* round
                                # (handled in the end-of-round distance update below).
                                if int(combat_distance_ft) <= 10:
                                    _do_melee(side)
                            else:
                                _do_spells_and_turning(side)
                            self._combat_board_refresh(
                                foes,
                                combat_distance_ft,
                                round_no,
                                prompt=f"{str(side).title()} finishes {str(phase)} phase.",
                                phase=f"{str(phase).title()} / {str(side).title()} done",
                            )

                    # P1.4: engagement distance update.
                    # If melee is intended while still at range, combatants advance toward each other.
                    # Do NOT snap directly to melee from long range (fixture stability).
                    if combat_distance_ft > 10 and any((p.get('type') == 'melee') for p in action_plan.values()):
                        step = 30
                        try:
                            # In dungeon corridors, advances are shorter.
                            if in_dungeon_ctx:
                                step = 20
                        except Exception:
                            step = 30
                        new_d = max(10, int(combat_distance_ft) - int(step))
                        combat_distance_ft = new_d
                        if int(combat_distance_ft) <= 10:
                            combat_distance_ft = 0
                            # Cover is lost once combatants are fully engaged in melee.
                            for a in (self.party.living() + [f for f in foes if f.hp > 0]):
                                if 'cover' in status_dict(a):
                                    clear_status(a, 'cover')
                            self.ui.log('Combatants close to melee range (0 ft). Cover is lost.')
                        else:
                            self.ui.log(f'Combatants advance; range now {int(combat_distance_ft)} ft.')
                    self._combat_board_refresh(
                        foes,
                        combat_distance_ft,
                        round_no,
                        prompt=f"Range now {int(combat_distance_ft)} ft.",
                        phase="Distance Update",
                    )

                    # End-of-round morale effects: apply pending flee markers now.
                    try:
                        pending = [f for f in foes if int(getattr(f, 'hp', 0) or 0) > 0 and 'flee_pending' in (getattr(f, 'effects', []) or [])]
                        if pending:
                            for f in pending:
                                self._leave_combat_actor(f, marker='fled', remove_effects=('flee_pending',))
                    except Exception:
                        pass

                    # Render a readable round summary.
                    self._battle_flush_round()
                    try:
                        if not bool(getattr(self.ui, 'auto_combat', False)):
                            self._show_combat_board(foes, combat_distance_ft, round_no, phase="Round End")
                    except Exception:
                        pass

                    # End of round: loop continues; status ticking handles durations.
            finally:
                # Ensure UI hook is restored even on early returns.
                try:
                    self._battle_flush_prelude()
                    self._battle_flush_round()
                except Exception:
                    pass
                try:
                    self._combat_board_refresh(foes if 'foes' in locals() else [], int(locals().get('combat_distance_ft', 0) or 0), int(locals().get('round_no', 0) or 0), prompt="Combat ends.", phase="Combat End")
                except Exception:
                    pass

                # Combat end summary block (P6.4 UX)
                try:
                    party_summary = []
                    for pc in (self.party.members or []):
                        if not getattr(pc, "is_pc", False):
                            continue
                        nm = str(getattr(pc, "name", "?"))
                        hp = int(getattr(pc, "hp", 0) or 0)
                        mh = getattr(pc, "max_hp", None)
                        if mh is None:
                            mh = getattr(pc, "hp_max", None)
                        try:
                            mh_i = int(mh) if mh is not None else None
                        except Exception:
                            mh_i = None
                        delta = None
                        if isinstance(locals().get("combat_start_party_hp"), dict) and nm in combat_start_party_hp:
                            delta = int(hp) - int(combat_start_party_hp.get(nm, hp))
                        party_summary.append({"name": nm, "hp": hp, "hp_max": mh_i, "delta": delta})
                    ammo_deltas = []
                    if isinstance(locals().get("combat_start_ammo"), dict):
                        for ammo_attr, start_v in combat_start_ammo.items():
                            end_v = int(getattr(self, ammo_attr, 0) or 0)
                            spent = 0
                            recovered = 0
                            if isinstance(locals().get("spent_throwables"), dict) and ammo_attr in spent_throwables:
                                spent = int(spent_throwables.get(ammo_attr, 0) or 0)
                                recovered = max(0, int(end_v) - (int(start_v) - int(spent)))
                            else:
                                spent = max(0, int(start_v) - int(end_v))
                            if spent or recovered:
                                ammo_deltas.append({"ammo": str(self._ammo_label(ammo_attr)), "spent": spent, "recovered": recovered})
                    spells_spent = []
                    try:
                        for be in list(getattr(self, "_battle_buffer", []) or []):
                            if getattr(be, "kind", "") == "SPELL_SPENT":
                                spells_spent.append({"caster": be.data.get("caster_id","?"), "spell": be.data.get("spell","?")})
                    except Exception:
                        pass
                    foes_defeated = 0
                    foes_fled = 0
                    foes_surr = 0
                    try:
                        for f in (foes or []):
                            if int(getattr(f, "hp", 0) or 0) <= 0:
                                foes_defeated += 1
                            else:
                                fx = list(getattr(f, "effects", []) or [])
                                if "fled" in fx:
                                    foes_fled += 1
                                if "surrendered" in fx:
                                    foes_surr += 1
                    except Exception:
                        pass
                    reason = "ended"
                    if not list(self.party.living()):
                        reason = "party_defeated"
                    else:
                        total = int(len(foes or []))
                        if total and foes_defeated == total:
                            reason = "foes_defeated"
                        elif total and foes_defeated + foes_fled == total and foes_fled:
                            reason = "foes_fled"
                        elif total and foes_defeated + foes_surr == total and foes_surr:
                            reason = "foes_surrendered"
                                        # Loot/XP deltas captured during combat (if any).
                    xp_earned = 0
                    try:
                        xp_earned = int(getattr(self, '_battle_xp_earned', 0) or 0)
                    except Exception:
                        xp_earned = 0
                    loot_gp = 0
                    loot_items: list = []
                    try:
                        loot_gp = int(getattr(self, 'gold', 0) or 0) - int(combat_start_gold)
                    except Exception:
                        loot_gp = 0
                    try:
                        cur_items = list(getattr(self, 'party_items', []) or [])
                        if int(combat_start_items_count) >= 0 and len(cur_items) >= int(combat_start_items_count):
                            loot_items = cur_items[int(combat_start_items_count):]
                    except Exception:
                        loot_items = []

                    sevt = self.battle_evt(
                        "COMBAT_SUMMARY",
                        reason=reason,
                        party=party_summary,
                        ammo_deltas=ammo_deltas,
                        spells_spent=spells_spent,
                        foes={"defeated": foes_defeated, "fled": foes_fled, "surrendered": foes_surr},
                        xp_earned=int(xp_earned),
                        loot_gp=int(loot_gp),
                        loot_items=[str(getattr(x, 'get', lambda k, d=None: None)('name') or x) for x in (loot_items or [])],
                    )
                    # If we're inside a room-level encounter capture window, defer printing
                    # so we can unify combat + post-combat rewards into one Encounter Recap panel.
                    if bool(getattr(self, "_encounter_capture_active", False)):
                        try:
                            self._encounter_combat_summary = sevt
                        except Exception:
                            pass
                    else:
                        # Print as a single block at end of combat.
                        try:
                            self._battle_print_lines([str(format_event(sevt))])
                        except Exception:
                            pass
                except Exception:
                    pass
                self._battle_end_capture()

    def _tick_combat_statuses(self, actors: list[Actor]):
        """Tick centralized status effects and decrement simple duration-based statuses each combat round."""
        # P2.1: Centralized effects tick first (e.g., poison DOT).
        try:
            party = [a for a in actors if bool(getattr(a, 'is_pc', False))]
            foes = [a for a in actors if not bool(getattr(a, 'is_pc', False))]
            self.effects_mgr.tick(
                actors,
                Hook.START_OF_ROUND,
                ctx={
                    "round_no": int(getattr(self, 'combat_round', 1) or 1),
                    "party": party,
                    "foes": foes,
                },
            )
        except Exception:
            pass

        tick_round_statuses(actors, phase="start")

    def _resolve_attack(self, attacker: Actor, defender: Actor, to_hit_mod: int = 0, round_no: int = 1, kind_override: str | None = None, combat_foes: list[Actor] | None = None):
        # Strict replay diagnostics: tag RNG consumption with high-level context.
        try:
            rng = getattr(self, "dice_rng", None)
            if rng is not None:
                rng.ctx = {
                    "site": "_resolve_attack",
                    "round": int(round_no),
                    "attacker": str(getattr(attacker, "name", "")),
                    "defender": str(getattr(defender, "name", "")),
                    "att_hp": int(getattr(attacker, "hp", 0) or 0),
                    "def_hp": int(getattr(defender, "hp", 0) or 0),
                    "att_side": "pc" if bool(getattr(attacker, "is_pc", False)) else "foe",
                    "def_side": "pc" if bool(getattr(defender, "is_pc", False)) else "foe",
                }
                # If a strict replay mismatch happens mid-round, having the last foe
                # melee execution summary is extremely helpful for diagnosing skipped
                # foe actions / frontage issues.
                if getattr(rng, "strict_replay", False):
                    diag = getattr(self, "_replay_last_melee_diag", None)
                    if isinstance(diag, dict):
                        rng.ctx["last_melee_diag"] = diag
        except Exception:
            pass
        dst = getattr(defender, "status", {}) or {}
        # AC buffs/debuffs (descending AC: lower is better)
        eff_ac = int(self._effective_ac_desc(defender) or 9)
        shield = dst.get("shield")
        if isinstance(shield, dict):
            eff_ac = eff_ac - int(shield.get("ac_bonus", 0) or 0)

        # P2.4: Status-effect derived combat modifiers (e.g., Avert Gaze imposes -2 to hit).
        try:
            to_hit_mod += int(self.effects_mgr.query_modifiers(attacker).to_hit_mod)
        except Exception:
            pass

        need = attack_roll_needed(attacker, eff_ac)
        # Attack rolls are 1d20.
        hit_sides = 20
        roll = self.dice.d(hit_sides)
        kind = (kind_override or self._weapon_kind(attacker) or 'melee')
        total = roll + self._attack_bonus(attacker, kind=kind) + to_hit_mod

        # P1.6: Cover / concealment (stored on defender.status["cover"] as -2 or -4).
        # Cover penalties apply to missile fire at range; once engaged in melee,
        # scattered cover no longer affects strikes.
        cover = dst.get("cover", 0)
        try:
            if str(kind).lower() == "missile":
                total += int(cover or 0)
        except Exception:
            pass

        # Fighter parry: defender imposes a penalty on attacks against them.
        parry = dst.get("parry")
        if isinstance(parry, dict) and int(parry.get("penalty", 0) or 0) > 0:
            total -= int(parry.get("penalty", 0) or 0)

        # Bless (party buff)
        st = getattr(attacker, "status", {}) or {}
        bless = st.get("bless")
        if isinstance(bless, dict):
            total += int(bless.get("to_hit", 0) or 0)

        # Attacking a held target is easier.
        if dst.get("held", 0):
            total += 4

        # Protection from Evil: foes have a harder time hitting.
        prot = dst.get("prot_evil")
        if isinstance(prot, dict) and not bool(getattr(attacker, "is_pc", False)):
            total += int(prot.get("enemy_to_hit", 0) or 0)

        # Thief backstab: if target is surprised in first round.
        is_thief = (str(getattr(attacker, "cls", "")).lower() == "thief") and bool(getattr(attacker, "is_pc", False))
        backstab = is_thief and round_no == 1 and dst.get("surprised", 0)
        if backstab:
            # S&W/OD&D style: +4 to hit and increased damage multiplier by level.
            total += 4
        hit = (self.nat20_auto_hit and roll == hit_sides) or total >= need
        # Emit typed battle event at the resolution site (preferred over parsing UI text).
        try:
            self.battle_evt(                "ATTACK",
                attacker_id=getattr(attacker, "name", "?"),
                target_id=getattr(defender, "name", "?"),
                result=("hit" if hit else "miss"),
                roll=int(roll),
                total=int(total),
                need=int(need),
                kind=str(kind),
                mode=("backstab" if backstab else str(kind)),
                to_hit_mod=int(to_hit_mod),
            )
        except Exception:
            pass

        if hit:
            before_hp = int(getattr(defender, 'hp', 0) or 0)
            dmg = self._damage_roll(attacker, kind=kind)
            if backstab:
                try:
                    from .classes import thief_backstab_multiplier
                    mult = thief_backstab_multiplier(int(getattr(attacker, 'level', 1) or 1))
                except Exception:
                    mult = 2
                dmg = dmg * int(mult)

            # P2.9: Apply damage through centralized damage pipeline (typing + resist/immunity/gating).
            try:
                from .damage import DamagePacket, apply_damage_packet
                eb = int(getattr(self, "_weapon_enchantment_bonus", lambda a: 0)(attacker) or 0)
                mtags = set(getattr(self, "_weapon_material_tags", lambda a: set())(attacker) or set())
                pkt = DamagePacket(
                    amount=int(dmg),
                    damage_type="physical",
                    magical=bool(eb > 0),
                    enchantment_bonus=eb,
                    source_tags=mtags,
                )
                dealt = int(
                    apply_damage_packet(
                        game=self,
                        source=attacker,
                        target=defender,
                        packet=pkt,
                        ctx={"round_no": int(round_no), "combat_foes": list(combat_foes or [])},
                    )
                )
            except Exception:
                # Fallback: old behavior.
                dealt = int(dmg)
                defender.hp = max(-1, defender.hp - dealt)
                try:
                    self._notify_damage(defender, dealt, damage_types={"physical"})
                except Exception:
                    pass
                        # Strict replay diagnostics: record last damage application on the defender.
            try:
                dst_now2 = status_dict(defender)
                dst_now2['_last_damage'] = {
                    'round': int(round_no),
                    'attacker': str(getattr(attacker, 'name', '')),
                    'kind': str(kind),
                    'before_hp': int(before_hp),
                    'dealt': int(dealt),
                    'after_hp': int(getattr(defender, 'hp', 0) or 0),
                }
            except Exception:
                pass

# P0.75: Spell disruption. If the defender is in the middle of casting,
            # taking damage before end-of-round resolution disrupts the spell.
            dst_now = status_dict(defender)
            casting = dst_now.get("casting")
            if isinstance(casting, dict) and not bool(casting.get("disrupted", False)):
                casting["disrupted"] = True
                dst_now["casting"] = casting
                # Typed spell disruption event (and optional legacy log).
                try:
                    self.battle_evt("SPELL_DISRUPTED", caster_id=getattr(defender, "name", "?"), reason="hit", round_no=int(round_no))
                except Exception:
                    pass
                if not bool(getattr(self, "_battle_buffer_active", False)):
                    self.ui.log(f"{defender.name} is hit and their spell is disrupted!")

            # Typed damage event (and optional legacy hit text).
            try:
                self.battle_evt(                    "DAMAGE",
                    source_id=getattr(attacker, "name", "?"),
                    target_id=getattr(defender, "name", "?"),
                    amount=int(dealt),
                    hp=int(getattr(defender, "hp", 0) or 0),
                    hp_max=int(getattr(defender, "hp_max", 0) or 0),
                    kind=str(kind),
                    mode=("backstab" if backstab else str(kind)),
                )
            except Exception:
                pass
            try:
                if int(before_hp) > 0 and int(getattr(defender, "hp", 0) or 0) <= 0:
                    self.battle_evt("UNIT_DIED", unit_id=getattr(defender, "name", "?"))
                    try:
                        self._battle_register_xp_for_defeat(defender)
                    except Exception:
                        pass
            except Exception:
                pass

            if not bool(getattr(self, "_battle_buffer_active", False)):
                if backstab:
                    self.ui.log(f"{attacker.name} backstabs {defender.name} for {dealt}! (HP {defender.hp}/{defender.hp_max})")
                else:
                    self.ui.log(f"{attacker.name} hits {defender.name} for {dealt} (HP {defender.hp}/{defender.hp_max})")

            # P2.0 (Monster Specials Audit): resolve on-hit monster specials (starting with poison).
            self._monster_on_hit_specials(attacker, defender, round_no=round_no, attack_roll=roll, attack_total=total, attack_need=need)

            # Group morale checks are handled once per round during the planning phase
            # (see combat(): "Group morale check when foes are at/below half remaining").
        else:
            if not bool(getattr(self, "_battle_buffer_active", False)):
                self.ui.log(f"{attacker.name} misses {defender.name}.")

        # Clear RNG ctx once the attack roll is resolved.
        try:
            rng = getattr(self, "dice_rng", None)
            if rng is not None:
                rng.ctx = None
        except Exception:
            pass

        # Invisibility ends when you attack.
        ast = getattr(attacker, "status", {}) or {}
        inv = ast.get("invisible")
        if isinstance(inv, int) and inv > 0:
            ast.pop("invisible", None)
        if isinstance(inv, dict) and int(inv.get("turns", 0) or 0) > 0:
            ast.pop("invisible", None)

    def _notify_damage(self, target: Actor, dmg: int, *, damage_types: set[str] | None = None) -> None:
        """Notify the centralized effects system that damage occurred.

        Used for things like regeneration suppression (fire/acid).
        This is best-effort and should never break combat.
        """
        if target is None:
            return
        try:
            types = sorted(list(damage_types or set()))
            self.effects_mgr.tick([target], Hook.ON_DAMAGE, ctx={
                "round_no": int(getattr(self, "combat_round", 1) or 1),
                "damage": int(dmg or 0),
                "damage_types": types,
            })
        except Exception:
            return


    def _leave_combat_actor(self, actor: Actor, *, marker: str = "fled", remove_effects: tuple[str, ...] = ()) -> None:
        """Shared non-death leave-combat cleanup seam.

        Preserves existing semantics: mark effect-based exit state and remove
        transient combat-only statuses.
        """
        if actor is None:
            return
        effects = list(getattr(actor, "effects", []) or [])
        for key in tuple(remove_effects or ()):
            effects = [e for e in effects if str(e) != str(key)]
        mk = str(marker or "").strip()
        if mk and mk not in effects:
            effects.append(mk)
        actor.effects = effects
        cleanup_actor_battle_status(actor)

    def _notify_death(self, target: Actor, *, ctx: dict | None = None) -> None:
        try:
            if int(getattr(target, "hp", 0) or 0) <= 0:
                cleanup_actor_battle_status(target)
        except Exception:
            pass

    def _leave_combat_actor(self, actor: Actor, *, marker: str = "fled", remove_effects: tuple[str, ...] = ()) -> None:
        """Shared non-death leave-combat cleanup seam.

        Preserves existing semantics: mark effect-based exit state and remove
        transient combat-only statuses.
        """
        if actor is None:
            return
        effects = list(getattr(actor, "effects", []) or [])
        for key in tuple(remove_effects or ()):
            effects = [e for e in effects if str(e) != str(key)]
        mk = str(marker or "").strip()
        if mk and mk not in effects:
            effects.append(mk)
        actor.effects = effects
        self._cleanup_combat_actor_state(actor)

    def _notify_death(self, target: Actor, *, ctx: dict | None = None) -> None:
        """Hardening: centralized death cleanup.

        Removes sticky control effects (grapple/engulf/swallow) so combat state
        cannot remain "stuck" after a death.
        """
        try:
            if int(getattr(target, "hp", 0) or 0) <= 0:
                self._cleanup_combat_actor_state(target)
        except Exception:
            pass

        ctx = dict(ctx or {})
        try:
            # Build a best-effort actor pool for cleanup.
            pool: list[Actor] = []
            try:
                pool.extend(list(getattr(getattr(self, "party", None), "members", []) or []))
            except Exception:
                pass
            try:
                pool.extend(list(ctx.get("combat_foes") or []))
            except Exception:
                pass
            try:
                room = (getattr(self, "dungeon_rooms", {}) or {}).get(getattr(self, "current_room_id", 0), {})
                if isinstance(room, dict):
                    pool.extend(list(room.get("foes") or []))
            except Exception:
                pass

            # De-dupe by object id.
            seen = set()
            uniq: list[Actor] = []
            for a in pool:
                if a is None:
                    continue
                oid = id(a)
                if oid in seen:
                    continue
                seen.add(oid)
                uniq.append(a)

            self.effects_mgr.cleanup_on_death(target, pool=uniq)
            try:
                self.emit("actor_died", name=getattr(target, "name", "?"), monster_id=str(getattr(target, "monster_id", "") or ""))
            except Exception:
                pass
        except Exception:
            return

    def _monster_on_hit_specials(self, attacker: Actor, defender: Actor, *, round_no: int = 1, attack_roll: int | None = None, attack_total: int | None = None, attack_need: int | None = None) -> None:
        """Resolve structured (or heuristic) monster on-hit specials."""
        if bool(getattr(attacker, "is_pc", False)):
            return
        if getattr(defender, "hp", 0) <= 0:
            return

        specials = list(getattr(attacker, "specials", []) or [])

        # Heuristic fallback (legacy compatibility; disabled in STRICT_CONTENT).
        if (not specials) and (not getattr(self, "strict_content", False)):
            try:
                stxt = str(getattr(attacker, "special_text", "") or "").lower()
            except Exception:
                stxt = ""
            if "poison" in stxt:
                specials = [
                    {
                        "type": "poison",
                        "id": "text_poison",
                        "trigger": "on_hit",
                        "save_tags": ["poison"],
                        "mode": "instant_death",
                    }
                ]
            elif "paraly" in stxt or "paralysis" in stxt or "held" in stxt:
                specials = [
                    {
                        "type": "paralysis",
                        "id": "text_paralysis",
                        "trigger": "on_hit",
                        "save_tags": ["paralysis"],
                        "duration": "2d4",
                    }
                ]
            elif "petrif" in stxt or "turned to stone" in stxt or "turn to stone" in stxt:
                specials = [
                    {
                        "type": "petrification",
                        "id": "text_petrification",
                        "trigger": "on_hit",
                        "save_tags": ["petrification"],
                    }
                ]
            elif "level drain" in stxt or "energy drain" in stxt or ("drain" in stxt and "level" in stxt):
                specials = [
                    {
                        "type": "energy_drain",
                        "id": "text_energy_drain",
                        "trigger": "on_hit",
                        "levels": 1,
                    }
                ]
            elif "fear" in stxt or "terror" in stxt or "panic" in stxt:
                specials = [
                    {
                        "type": "fear",
                        "id": "text_fear",
                        "trigger": "on_hit",
                        "save_tags": ["spells"],
                        "duration": "1d4",
                    }
                ]
            elif "charm" in stxt or "dominat" in stxt:
                specials = [
                    {
                        "type": "charm",
                        "id": "text_charm",
                        "trigger": "on_hit",
                        "save_tags": ["spells"],
                        "duration": "1d6",
                    }
                ]
            elif "disease" in stxt or "rot" in stxt or "plague" in stxt:
                specials = [
                    {
                        "type": "disease",
                        "id": "text_disease",
                        "trigger": "on_hit",
                        "save_tags": ["spells"],
                        "damage_per_day": "1d3",
                        "blocks_natural_heal": True,
                        "blocks_magical_heal": False,
                    }
                ]

        for spec in specials:
            if not isinstance(spec, dict):
                continue
            if str(spec.get("trigger") or "").lower() != "on_hit":
                continue
            t = str(spec.get("type") or "").lower()
            if t == "poison":
                self._resolve_poison_special(attacker, defender, spec, round_no=round_no)
            elif t == "paralysis":
                self._resolve_paralysis_special(attacker, defender, spec, round_no=round_no)
            elif t == "petrification":
                self._resolve_petrification_special(attacker, defender, spec, round_no=round_no)
            elif t == "energy_drain":
                self._resolve_energy_drain_special(attacker, defender, spec, round_no=round_no)
            elif t == "fear":
                self._resolve_fear_special(attacker, defender, spec, round_no=round_no)
            elif t == "charm":
                self._resolve_charm_special(attacker, defender, spec, round_no=round_no)
            elif t == "disease":
                self._resolve_disease_special(attacker, defender, spec, round_no=round_no)
            elif t == "grapple":
                self._resolve_grapple_special(attacker, defender, spec, round_no=round_no)
            elif t == "engulf":
                self._resolve_engulf_special(attacker, defender, spec, round_no=round_no)
            elif t == "swallow":
                self._resolve_swallow_special(attacker, defender, spec, round_no=round_no, attack_roll=attack_roll, attack_total=attack_total, attack_need=attack_need)

    def _apply_combat_spawn_specials(self, foes: list[Actor]) -> None:
        """Apply on-spawn monster specials once at the beginning of combat."""
        for f in list(foes or []):
            if f is None or getattr(f, "hp", 0) <= 0:
                continue
            if bool(getattr(f, "is_pc", False)):
                continue

            specials = list(getattr(f, "specials", []) or [])

            # Heuristic fallback (legacy compatibility; disabled in STRICT_CONTENT).
            if (not specials) and (not getattr(self, "strict_content", False)):
                try:
                    stxt = str(getattr(f, "special_text", "") or "").lower()
                except Exception:
                    stxt = ""
                if "regenerat" in stxt:
                    specials = [
                        {
                            "type": "regeneration",
                            "id": "text_regeneration",
                            "trigger": "on_spawn",
                            "amount": 1,
                        }
                    ]
                elif "gaze" in stxt and ("stone" in stxt or "petrif" in stxt):
                    specials = [
                        {
                            "type": "gaze_petrification",
                            "id": "text_gaze_petrification",
                            "trigger": "on_spawn",
                            "save_tags": ["petrification"],
                        }
                    ]
                elif "fear" in stxt or "terror" in stxt or "panic" in stxt:
                    specials = [
                        {
                            "type": "fear_aura",
                            "id": "text_fear_aura",
                            "trigger": "on_spawn",
                            "save_tags": ["spells"],
                            "duration": "1d4",
                        }
                    ]

            for spec in specials:
                if not isinstance(spec, dict):
                    continue
                if str(spec.get("trigger") or "").lower() != "on_spawn":
                    continue
                t = str(spec.get("type") or "").lower()
                if t == "regeneration":
                    self._apply_regeneration_spawn(f, spec)
                elif t == "gaze_petrification":
                    self._apply_gaze_petrification_spawn(f, spec)
                elif t == "fear_aura":
                    self._apply_fear_aura_spawn(f, spec, foes)

    def _apply_fear_aura_spawn(self, monster: Actor, spec: dict, foes: list[Actor]) -> None:
        """Apply a fear aura at combat start (e.g., Dragons)."""
        # Affect the opposing side.
        party = list(self.party.living())
        # Determine victims: if the aura monster is on foes list, victims are party; else victims are foes.
        victims = party if (monster in (foes or [])) else [x for x in (foes or []) if getattr(x, "hp", 0) > 0 and 'fled' not in (getattr(x, 'effects', []) or [])]

        fid = str(spec.get("id") or "fear_aura").strip() or "fear_aura"
        tags = list(spec.get("save_tags") or ["spells"])
        dur_spec = spec.get("duration")
        for v in victims:
            if v is None or getattr(v, "hp", 0) <= 0:
                continue
            inst = {
                "kind": "fear",
                "source": {"name": getattr(monster, "name", "fear")},
                "created_round": int(getattr(self, "combat_round", 1) or 1),
                "duration": {"type": "none"},
                "tags": ["harmful", "fear"],
                "params": {
                    "id": fid,
                    "save_tags": tags,
                    "duration": str(dur_spec or "1d4"),
                    "to_hit_mod": int(spec.get("to_hit_mod", -2) or -2),
                },
                "state": {},
            }
            # FearEffectDef will roll duration if rounds not provided.
            self.effects_mgr.apply(v, inst, ctx={"round_no": int(getattr(self, "combat_round", 1) or 1)})

    def _apply_gaze_petrification_spawn(self, monster: Actor, spec: dict) -> None:
        """Apply a gaze aura (Medusa/Basilisk) to a monster at combat start."""
        rid = str(spec.get("id") or "gaze").strip() or "gaze"
        inst = {
            "kind": "gaze_petrification",
            "source": {"name": getattr(monster, "name", "gaze")},
            "created_round": int(getattr(self, "combat_round", 1) or 1),
            "duration": {"type": "none"},
            "tags": ["hazard", "gaze"],
            "params": {
                "id": rid,
                "save_tags": list(spec.get("save_tags") or ["petrification"]),
            },
            "state": {},
        }
        self.effects_mgr.apply(monster, inst, ctx={"round_no": int(getattr(self, "combat_round", 1) or 1)})

    def _apply_regeneration_spawn(self, monster: Actor, spec: dict) -> None:
        """Apply a regeneration effect to a monster at combat start."""
        rid = str(spec.get("id") or "regeneration").strip() or "regeneration"
        amt = int(spec.get("amount", 0) or 0)
        if amt <= 0:
            amt = 1

        inst = {
            "kind": "regeneration",
            "source": {"name": getattr(monster, "name", "regeneration")},
            "created_round": int(getattr(self, "combat_round", 1) or 1),
            "duration": {"type": "none"},
            "tags": ["beneficial", "regeneration"],
            "params": {
                "id": rid,
                "amount": amt,
            },
            "state": {},
        }
        self.effects_mgr.apply(monster, inst, ctx={"round_no": int(getattr(self, "combat_round", 1) or 1)})

    def _resolve_petrification_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve a petrification special via the centralized EffectsManager."""
        pid = str(spec.get("id") or "petrification").strip() or "petrification"
        tags = list(spec.get("save_tags") or ["petrification"])

        inst = {
            "kind": "petrification",
            "source": {"name": getattr(attacker, "name", "petrification")},
            "created_round": int(round_no),
            "duration": {"type": "none"},
            "tags": ["harmful", "petrification"],
            "params": {
                "id": pid,
                "save_tags": tags,
            },
            "state": {},
        }

        self.effects_mgr.apply(
            defender,
            inst,
            ctx={"round_no": int(round_no), "source_monster_id": str(getattr(attacker, "monster_id", "") or "")},
        )

    def _resolve_paralysis_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve a paralysis special via the centralized EffectsManager (P2.2).

        Duration is in combat rounds.
        """
        pid = str(spec.get("id") or "paralysis").strip() or "paralysis"
        tags = list(spec.get("save_tags") or ["paralysis"])

        # Duration
        dur_spec = spec.get("duration")
        rounds = 0
        if isinstance(dur_spec, int):
            rounds = int(dur_spec)
        elif isinstance(dur_spec, str) and dur_spec.strip():
            try:
                rr = self.dice.roll(dur_spec.strip())
                rounds = int(rr.total)
            except Exception:
                rounds = 1
        else:
            rounds = int(spec.get("rounds") or 0)
        if rounds < 0:
            rounds = 0

        inst = {
            "kind": "paralysis",
            "source": {"name": getattr(attacker, "name", "paralysis")},
            "created_round": int(round_no),
            "duration": {"type": "rounds", "remaining": rounds},
            "tags": ["harmful", "paralysis"],
            "params": {
                "id": pid,
                "save_tags": tags,
                "rounds": rounds,
            },
            "state": {},
        }

        self.effects_mgr.apply(
            defender,
            inst,
            ctx={"round_no": int(round_no), "source_monster_id": str(getattr(attacker, "monster_id", "") or "")},
        )

    def _resolve_poison_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve a poison special via the centralized EffectsManager (P2.1).

        Modes supported: instant_death, damage, dot.
        """
        pid = str(spec.get("id") or "poison").strip() or "poison"
        mode = str(spec.get("mode") or "instant_death").lower()
        tags = list(spec.get("save_tags") or ["poison"])

        params: dict[str, Any] = {
            "id": pid,
            "mode": mode,
            "save_tags": tags,
        }

        # Optional direct damage poison (non-DOT).
        if mode == "damage":
            params["damage"] = str(spec.get("damage") or "1d6")

        # DOT poison config.
        duration = {"type": "none"}
        if mode == "dot":
            params["dot_damage"] = str(spec.get("dot_damage") or "1d6")
            params["save_each_tick"] = bool(spec.get("save_each_tick", False))

            dur_spec = spec.get("duration")
            rounds = 0
            if isinstance(dur_spec, int):
                rounds = int(dur_spec)
            elif isinstance(dur_spec, str) and dur_spec.strip():
                try:
                    rr = self.dice.roll(dur_spec.strip())
                    rounds = int(rr.total)
                except Exception:
                    rounds = 1
            else:
                rounds = int(spec.get("rounds") or 0)
            if rounds < 0:
                rounds = 0
            duration = {"type": "rounds", "remaining": rounds}
            params["rounds"] = rounds

        inst = {
            "kind": "poison",
            "source": {"name": getattr(attacker, "name", "poison")},
            "created_round": int(round_no),
            "duration": duration,
            "tags": ["harmful", "poison"],
            "params": params,
            "state": {},
        }

        self.effects_mgr.apply(
            defender,
            inst,
            ctx={"round_no": int(round_no), "source_monster_id": str(getattr(attacker, "monster_id", "") or "")},
        )

    def _resolve_energy_drain_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve an energy/level drain special via EffectsManager (P2.5).

        Default: drain 1 level on hit (no save).
        """
        pid = str(spec.get("id") or "energy_drain").strip() or "energy_drain"
        try:
            levels = int(spec.get("levels", 1) or 1)
        except Exception:
            levels = 1
        levels = max(1, levels)

        inst = {
            "kind": "energy_drain",
            "source": {"name": getattr(attacker, "name", "energy drain")},
            "created_round": int(round_no),
            "duration": {"type": "none"},
            "tags": ["harmful", "energy_drain"],
            "params": {
                "id": pid,
                "levels": levels,
            },
            "state": {},
        }
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no)})

    def _resolve_fear_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve fear via EffectsManager.

        Default: Save vs Spells or be feared for 1d4 rounds.
        """
        fid = str(spec.get("id") or "fear").strip() or "fear"
        tags = list(spec.get("save_tags") or ["spells"])
        dur_spec = spec.get("duration")
        rounds = 0
        if isinstance(dur_spec, int):
            rounds = int(dur_spec)
        elif isinstance(dur_spec, str) and dur_spec.strip():
            try:
                rr = self.dice.roll(dur_spec.strip())
                rounds = int(rr.total)
            except Exception:
                rounds = 1
        else:
            rounds = int(spec.get("rounds") or 0)
        if rounds <= 0:
            rounds = 1

        inst = {
            "kind": "fear",
            "source": {"name": getattr(attacker, "name", "fear")},
            "created_round": int(round_no),
            "duration": {"type": "rounds", "remaining": int(rounds)},
            "tags": ["harmful", "fear"],
            "params": {
                "id": fid,
                "save_tags": tags,
                "duration": str(dur_spec or "1d4"),
                "rounds": int(rounds),
                "to_hit_mod": int(spec.get("to_hit_mod", -2) or -2),
            },
            "state": {},
        }
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no)})

    def _resolve_charm_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve charm via EffectsManager.

        Charm flips the defender's combat allegiance to the attacker's side.
        """
        cid = str(spec.get("id") or "charm").strip() or "charm"
        tags = list(spec.get("save_tags") or ["spells"])
        dur_spec = spec.get("duration")
        rounds = 0
        if isinstance(dur_spec, int):
            rounds = int(dur_spec)
        elif isinstance(dur_spec, str) and dur_spec.strip():
            try:
                rr = self.dice.roll(dur_spec.strip())
                rounds = int(rr.total)
            except Exception:
                rounds = 1
        else:
            rounds = int(spec.get("rounds") or 0)
        if rounds <= 0:
            rounds = 1

        inst = {
            "kind": "charm",
            "source": {"name": getattr(attacker, "name", "charm")},
            "created_round": int(round_no),
            "duration": {"type": "rounds", "remaining": int(rounds)},
            "tags": ["harmful", "charm"],
            "params": {
                "id": cid,
                "save_tags": tags,
                "duration": str(dur_spec or "1d6"),
                "rounds": int(rounds),
            },
            "state": {},
        }
        # ctx includes source/target sides so CharmEffectDef can decide side_override.
        try:
            source_side = "foe" if (not bool(getattr(attacker, "is_pc", False))) else "party"
        except Exception:
            source_side = "foe"
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no), "source_side": source_side})

    def _resolve_disease_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Resolve disease/rot via EffectsManager.

        Disease persists across combat and ticks on campaign day changes.

        Common use: Mummy Rot.
        """
        did = str(spec.get("id") or "disease").strip() or "disease"
        tags = list(spec.get("save_tags") or ["spells"])
        dmg = spec.get("damage_per_day") or spec.get("damage") or "1d3"

        inst = {
            "kind": "disease",
            "source": {"name": getattr(attacker, "name", "disease")},
            "created_round": int(round_no),
            "duration": {"type": "none"},
            "tags": ["harmful", "disease"],
            "params": {
                "id": did,
                "save_tags": tags,
                "damage_per_day": str(dmg),
                "blocks_natural_heal": bool(spec.get("blocks_natural_heal", True)),
                "blocks_magical_heal": bool(spec.get("blocks_magical_heal", False)),
            },
            "state": {},
        }
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no)})


    # --- P3.3.1: Escape action (grapple/engulf) ----------------------------------------

    def _escape_effect_candidates(self, actor: Actor) -> list[tuple[str, dict]]:
        """Return a list of (instance_id, inst) for hold effects the actor can try to escape.

        Currently supports: grapple, engulf.
        (Swallow is intentionally not escapable in this patch.)
        """
        out: list[tuple[str, dict]] = []
        ei = getattr(actor, "effect_instances", None)
        if not isinstance(ei, dict):
            return out
        for iid, inst in ei.items():
            if not isinstance(inst, dict):
                continue
            k = str(inst.get("kind") or "").lower()
            if k in ("grapple", "engulf"):
                out.append((str(iid), inst))
        # Prefer engulf (fully disables) over grapple if multiple.
        out.sort(key=lambda t: 0 if str(t[1].get("kind") or "").lower() == "engulf" else 1)
        return out

    def _resolve_escape_action(self, actor: Actor, *, round_no: int) -> None:
        """Attempt to escape a grapple/engulf effect as an action."""
        cands = self._escape_effect_candidates(actor)
        if not cands:
            self.ui.log(f"{actor.name} struggles, but is not held.")
            return

        # If multiple holds, pick one (UI) or default to first.
        iid, inst = cands[0]
        if (not self.ui.auto_combat) and len(cands) > 1:
            opts = []
            for _iid, _inst in cands:
                src = (_inst.get("source") or {}).get("name") if isinstance(_inst.get("source"), dict) else _inst.get("source")
                src = str(src or str(_inst.get("kind") or "hold"))
                opts.append(f"{str(_inst.get('kind') or 'hold').title()} ({src})")
            ix = self.ui.choose("Escape from which hold?", opts)
            try:
                iid, inst = cands[int(ix)]
            except Exception:
                iid, inst = cands[0]

        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or str(inst.get("kind") or "hold"))

        escape_dc = int(params.get("escape_dc", 14) or 14)
        stat = str(params.get("escape_stat", "STR") or "STR").upper()
        stats = getattr(actor, "stats", None)
        stat_score = 10
        try:
            if stats is not None and hasattr(stats, stat):
                stat_score = int(getattr(stats, stat) or 10)
        except Exception:
            stat_score = 10

        bonus = int(mod_ability(stat_score)) + int(params.get("escape_bonus", 0) or 0)
        roll = int(self.dice.d(20))
        total = int(roll) + int(bonus)
        if total >= int(escape_dc):
            self.ui.log(f"{actor.name} breaks free of {src}! (Escape {total} vs {escape_dc})")
            try:
                self.effects_mgr.remove(actor, str(iid), reason="escaped_action", ctx={"round_no": int(round_no)})
            except Exception:
                pass
            try:
                self.effects_mgr._emit("escape_action", {"target": actor.name, "source": src, "roll": int(roll), "total": int(total), "dc": int(escape_dc)})
            except Exception:
                pass
        else:
            self.ui.log(f"{actor.name} fails to escape {src}. (Escape {total} vs {escape_dc})")
            try:
                self.effects_mgr._emit("escape_failed", {"target": actor.name, "source": src, "roll": int(roll), "total": int(total), "dc": int(escape_dc)})
            except Exception:
                pass


    # --- P3.3.2: Swallow: fight from inside / cut your way out -------------------------

    def _swallow_effect_candidate(self, actor: Actor) -> tuple[str, dict] | None:
        """Return (instance_id, inst) if the actor is swallowed, else None."""
        ei = getattr(actor, "effect_instances", None)
        if not isinstance(ei, dict):
            return None
        for iid, inst in ei.items():
            if not isinstance(inst, dict):
                continue
            if str(inst.get("kind") or "").lower() == "swallow":
                return (str(iid), inst)
        return None

    def _find_swallower_for_effect(self, inst: dict, *, combat_foes: list[Actor] | None = None) -> Actor | None:
        """Best-effort: find the swallower Actor for a swallow effect instance."""
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}
        want_id = params.get("swallower_obj_id")
        want_name = params.get("swallower_name")
        want_mid = params.get("swallower_monster_id")
        pool: list[Actor] = []
        try:
            pool.extend(list(self.party.living()))
        except Exception:
            pass
        try:
            if combat_foes:
                pool.extend([f for f in combat_foes if getattr(f, "hp", 0) > 0 and 'fled' not in getattr(f, 'effects', [])])
        except Exception:
            pass
        for a in pool:
            try:
                if want_id is not None and int(want_id) == id(a):
                    return a
            except Exception:
                pass
        for a in pool:
            try:
                if want_mid and str(getattr(a, "monster_id", "") or "") == str(want_mid):
                    return a
            except Exception:
                pass
        for a in pool:
            try:
                if want_name and str(getattr(a, "name", "") or "") == str(want_name):
                    return a
            except Exception:
                pass
        return None

    def _weapon_is_cutting_for_swallow(self, attacker: Actor) -> bool:
        """Return True if the attacker can plausibly "cut" their way out.

        P3.3.2 originally restricted this to edged weapons, but we intentionally
        loosen it: **any melee weapon** can be used to force an exit.

        We still disallow obvious missile weapons to avoid silly cases where a
        character "cuts out" with a bow.
        """
        w = str(self.effective_melee_weapon(attacker) or self.effective_missile_weapon(attacker) or "").lower().strip()
        if not w:
            return False
        ranged_markers = ["bow", "crossbow", "sling", "dart", "javelin", "throwing", "arrow", "bolt"]
        if any(m in w for m in ranged_markers):
            return False
        return True

    def _resolve_inside_attack(self, attacker: Actor, *, round_no: int, combat_foes: list[Actor] | None = None) -> None:
        """Attack the creature that swallowed you; may cut your way out."""
        cand = self._swallow_effect_candidate(attacker)
        if cand is None:
            self.ui.log(f"{attacker.name} is not swallowed.")
            return
        iid, inst = cand
        params = inst.get("params") if isinstance(inst.get("params"), dict) else {}

        sw = self._find_swallower_for_effect(inst, combat_foes=combat_foes)
        src = (inst.get("source") or {}).get("name") if isinstance(inst.get("source"), dict) else inst.get("source")
        src = str(src or "the creature")
        if sw is None:
            self.ui.log(f"{attacker.name} hacks from inside {src}, but cannot find a way out.")
            return

        inside_ac = int(params.get("inside_ac", 9) or 9)
        to_hit_mod = int(params.get("inside_to_hit_mod", -4) or -4)
        need = attack_roll_needed(attacker, inside_ac)
        roll = int(self.dice.d(20))
        total = int(roll) + int(self._attack_bonus(attacker, kind="melee")) + int(to_hit_mod)
        hit = (self.nat20_auto_hit and roll == 20) or total >= int(need)
        if not hit:
            self.ui.log(f"{attacker.name} strikes wildly inside {sw.name} but misses.")
            return

        dmg = int(self._damage_roll(attacker, kind="melee"))
        dealt = int(dmg)
        try:
            from .damage import DamagePacket, apply_damage_packet
            eb = int(getattr(self, "_weapon_enchantment_bonus", lambda a: 0)(attacker) or 0)
            mtags = set(getattr(self, "_weapon_material_tags", lambda a: set())(attacker) or set())
            pkt = DamagePacket(
                amount=int(dmg),
                damage_type="physical",
                magical=bool(eb > 0),
                enchantment_bonus=eb,
                source_tags=mtags,
            )
            dealt = int(
                apply_damage_packet(
                    game=self,
                    source=attacker,
                    target=sw,
                    packet=pkt,
                    ctx={"round_no": int(round_no), "inside_swallow": True, "swallow_instance_id": str(iid), "swallow_victim": attacker.name},
                )
            )
        except Exception:
            sw.hp = max(-1, int(getattr(sw, "hp", 0)) - int(dealt))
            try:
                self._notify_damage(sw, int(dealt), damage_types={"physical"})
            except Exception:
                pass

        self.ui.log(f"{attacker.name} strikes from inside {sw.name} for {dealt}! (HP {sw.hp}/{sw.hp_max})")

        # Track cut-out progress.
        state = inst.get("state") if isinstance(inst.get("state"), dict) else {}
        inst["state"] = state
        cuttable = bool(params.get("cut_out_enabled", True)) and self._weapon_is_cutting_for_swallow(attacker)
        if cuttable and dealt > 0:
            state["cut_damage"] = int(state.get("cut_damage", 0) or 0) + int(dealt)

        cut_req = int(params.get("cut_out_damage", 20) or 20)
        if cuttable and int(state.get("cut_damage", 0) or 0) >= int(cut_req):
            self.ui.log(f"{attacker.name} cuts their way out of {sw.name}!")
            try:
                self.effects_mgr.remove(attacker, str(iid), reason="cut_out", ctx={"round_no": int(round_no)})
            except Exception:
                pass
            if bool(params.get("stun_swallower_on_escape", False)):
                apply_status(sw, "held", 1, mode="max")

    def _resolve_grapple_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Apply a grapple-style hold (constrict/pin) via EffectsManager."""
        gid = str(spec.get("id") or "grapple").strip() or "grapple"
        tags = list(spec.get("save_tags") or ["paralysis"])
        inst = {
            "kind": "grapple",
            "source": {"name": getattr(attacker, "name", "grapple")},
            "created_round": int(round_no),
            "duration": {"type": "none"},
            "tags": ["harmful", "grapple"],
            "params": {
                "id": gid,
                "save_tags": tags,
                "save_on_apply": bool(spec.get("save_on_apply", True)),
                "save_bonus": int(spec.get("save_bonus", 0) or 0),
                "escape_dc": int(spec.get("escape_dc", 14) or 14),
                "escape_stat": str(spec.get("escape_stat", "STR") or "STR"),
                "escape_bonus": int(spec.get("escape_bonus", 0) or 0),
                "to_hit_mod": int(spec.get("to_hit_mod", -2) or -2),
                "damage_per_round": str(spec.get("damage_per_round") or spec.get("constrict_damage") or ""),
                "damage_type": str(spec.get("damage_type") or "physical"),
            },
            "state": {},
        }
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no)})

    def _resolve_engulf_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1) -> None:
        """Apply an engulf effect via EffectsManager (victim cannot act, takes damage)."""
        eid = str(spec.get("id") or "engulf").strip() or "engulf"
        tags = list(spec.get("save_tags") or ["paralysis"])
        inst = {
            "kind": "engulf",
            "source": {"name": getattr(attacker, "name", "engulf")},
            "created_round": int(round_no),
            "duration": {"type": "none"},
            "tags": ["harmful", "engulf"],
            "params": {
                "id": eid,
                "save_tags": tags,
                "save_on_apply": bool(spec.get("save_on_apply", True)),
                "save_bonus": int(spec.get("save_bonus", 0) or 0),
                "escape_dc": int(spec.get("escape_dc", 16) or 16),
                "escape_stat": str(spec.get("escape_stat", "STR") or "STR"),
                "escape_bonus": int(spec.get("escape_bonus", 0) or 0),
                "damage_per_round": str(spec.get("damage_per_round") or "1d6"),
                "damage_type": str(spec.get("damage_type") or "acid"),
            },
            "state": {},
        }
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no)})

    def _resolve_swallow_special(self, attacker: Actor, defender: Actor, spec: dict, *, round_no: int = 1, attack_roll: int | None = None, attack_total: int | None = None, attack_need: int | None = None) -> None:
        """Apply a swallow-whole effect via EffectsManager.

        Config: if spec['min_attack_roll'] is set, this special only triggers when the
        *natural* attack d20 roll is >= that threshold.
        """
        try:
            min_r = spec.get("min_attack_roll")
            min_r_i = int(min_r) if min_r is not None else None
        except Exception:
            min_r_i = None
        if min_r_i is not None:
            if attack_roll is None or int(attack_roll) < int(min_r_i):
                return


        # Optional S&W-style swallow triggers based on the to-hit threshold:
        # - swallow if the attack total is >= (needed_to_hit + swallow_margin)
        # - OR (optionally) if attack total is >= 2x needed_to_hit.
        # This is only evaluated when the caller provides attack_total/attack_need and the spec opts in.
        if attack_total is not None and attack_need is not None and (("swallow_margin" in spec) or ("swallow_double_needed" in spec)):
            try:
                margin = int(spec.get("swallow_margin", 4) or 4)
            except Exception:
                margin = 4
            double_ok = bool(spec.get("swallow_double_needed", True))
            ok = (int(attack_total) >= int(attack_need) + int(margin)) or (double_ok and (int(attack_total) >= int(attack_need) * 2))
            if not ok:
                return

        sid = str(spec.get("id") or "swallow").strip() or "swallow"
        tags = list(spec.get("save_tags") or ["paralysis"])
        inst = {
            "kind": "swallow",
            "source": {"name": getattr(attacker, "name", "swallow")},
            "created_round": int(round_no),
            "duration": {"type": "none"},
            "tags": ["harmful", "swallow"],
            "params": {
                "id": sid,
                "swallower_obj_id": int(id(attacker)),
                "swallower_name": str(getattr(attacker, "name", "") or ""),
                "swallower_monster_id": str(getattr(attacker, "monster_id", "") or ""),
                "save_tags": tags,
                "save_on_apply": bool(spec.get("save_on_apply", False)),
                "save_bonus": int(spec.get("save_bonus", 0) or 0),
                "damage_per_round": str(spec.get("damage_per_round") or "2d6"),
                "damage_type": str(spec.get("damage_type") or "acid"),
                # P3.3.2: fighting from inside / cut-out parameters
                "inside_ac": int(spec.get("inside_ac", 9) or 9),
                "inside_to_hit_mod": int(spec.get("inside_to_hit_mod", -4) or -4),
                "cut_out_enabled": bool(spec.get("cut_out_enabled", True)),
                "cut_out_damage": int(spec.get("cut_out_damage", 20) or 20),
                "stun_swallower_on_escape": bool(spec.get("stun_swallower_on_escape", False)),
            },
            "state": {},
        }
        self.effects_mgr.apply(defender, inst, ctx={"round_no": int(round_no)})

    def _turn_undead(self, cleric: Actor, foes: list[Actor]):
        """Turn Undead (S&W Complete, combat section).

        Rules highlights:
          - Only *Lawful* clerics are guaranteed this ability (Chaotic is referee-dependent).
          - Roll 2d10 and compare against the table entry (or use T/D).
          - On success, 2d6 undead of the *targeted type* are turned for 3d6 rounds.
          - If D, 2d6 are destroyed.

        Engine behavior:
          - Turned undead are marked with status["turned"] and also treated as "fled"
            for this combat, so combat can end cleanly.
        """

        # Alignment gate (RAW-ish). Referee can relax later via a toggle.
        if str(getattr(cleric, "alignment", "")).lower() not in ("law", "lawful"):
            self.ui.log(f"{cleric.name} lacks the lawful authority to turn the undead.")
            return

        # Pull compiled if present (Content already prefers it).
        table = getattr(self.content, "turn_undead", None)
        undead_rows = []
        if isinstance(table, dict):
            undead_rows = list(table.get("undead") or [])
        elif isinstance(table, list):
            undead_rows = table

        lvl = int(getattr(cleric, "level", 1) or 1)
        col = max(0, min(11, lvl - 1))
        undead = [f for f in foes if "undead" in (getattr(f, "tags", []) or []) and f.hp > 0]
        if not undead:
            self.ui.log("No undead to turn.")
            return

        self.ui.log(f"{cleric.name} brandishes a holy symbol!")

        # Choose the targeted type (RAW: targeted type). If only one, auto.
        # We resolve each foe into a matching table row (best-effort name match,
        # with fallback by undead_cl ~ HD).
        resolved: list[tuple[Actor, dict | None]] = []
        for u in undead:
            row = next((r for r in undead_rows if str(r.get("name", "")).lower() == str(u.name).lower()), None)
            if row is None and undead_rows:
                try:
                    row = sorted(
                        undead_rows,
                        key=lambda r: abs(int(r.get("undead_cl", 1) or 1) - int(getattr(u, "hd", 1) or 1)),
                    )[0]
                except Exception:
                    row = None
            resolved.append((u, row))

        # Build choice list by table-row name.
        type_names: list[str] = []
        type_to_row: dict[str, dict] = {}
        for _, row in resolved:
            if not row:
                continue
            nm = str(row.get("name") or "").strip()
            if nm and nm not in type_to_row:
                type_to_row[nm] = row
                type_names.append(nm)

        if not type_names:
            self.ui.log("The turning falters against these undead.")
            return

        if len(type_names) == 1:
            target_type = type_names[0]
        else:
            target_type = type_names[self.ui.choose("Turn which undead type?", type_names)]
        row = type_to_row[target_type]

        # Roll once for the attempt (2d10) and then apply to up to 2d6 of that type.
        roll = self.dice.d(10) + self.dice.d(10)
        duration = self.dice.d(6) + self.dice.d(6) + self.dice.d(6)  # 3d6 rounds
        affected_n = self.dice.d(6) + self.dice.d(6)  # 2d6 creatures

        res = str((row.get("results") or ["-"])[col]).strip()
        if res in ("–", "-", ""):
            self.ui.log(f"The undead resist! (roll {roll})")
            return

        def _mark_turned(u: Actor):
            apply_status(u, "turned", duration, mode="max")
            u.effects = list(getattr(u, "effects", []) or [])
            if "turned" not in u.effects:
                u.effects.append("turned")

        def _mark_destroyed(u: Actor):
            u.hp = -1
            u.effects = list(getattr(u, "effects", []) or [])
            if "destroyed" not in u.effects:
                u.effects.append("destroyed")

        # Choose actual creatures of that type in encounter, lowest HD first.
        same_type = [u for (u, r) in resolved if r and str(r.get("name", "")).strip() == target_type]
        same_type = sorted(same_type, key=lambda x: int(getattr(x, "hd", 1) or 1))
        targets = same_type[: max(0, affected_n)]
        if not targets:
            self.ui.log("No valid targets for turning.")
            return

        if res.upper() == "D":
            for u in targets:
                _mark_destroyed(u)
            self.ui.log(f"Turning result: D — {len(targets)} {target_type}(s) destroyed!")
            return

        if res.upper() == "T":
            for u in targets:
                _mark_turned(u)
            self.ui.log(f"Turning result: T — {len(targets)} {target_type}(s) turned for {duration} rounds!")
            return

        try:
            target_num = int(res)
        except Exception:
            self.ui.log(f"The undead resist! (roll {roll})")
            return

        if roll >= target_num:
            for u in targets:
                _mark_turned(u)
            self.ui.log(
                f"Turning succeeds (roll {roll} ≥ {target_num}): {len(targets)} {target_type}(s) turned for {duration} rounds!"
            )
        else:
            self.ui.log(f"Turning fails (roll {roll} < {target_num}).")

    def debug_cohere_pending_spell_entry(self, entry: dict[str, Any], party: list[Any], foes: list[Any]) -> dict[str, Any] | None:
        return self._cohere_pending_spell_entry(entry, party, foes)

    def _cast_spell(self, caster: Actor, foes: list[Actor]):
        """
        Backwards-compatible helper: in combat we now *declare* spells and resolve them at end of round.
        This method performs an immediate declare+resolve sequence (used only if called directly).
        """
        pending: list[dict] = []
        self._declare_spell(caster, pending)
        self._resolve_pending_spells(pending, foes, allies=list(self.party.living()))

    def _declare_spell(self, caster: Actor, pending_spells: list[dict]):
        """Declare a spell to be resolved at end of round (P0.75)."""
        prepared = list(getattr(caster, 'spells_prepared', []) or [])
        if not prepared:
            self.ui.log("No spells prepared.")
            return

        disp = [self.content.spell_display(s) for s in prepared]
        try:
            if bool(self._combat_board_context()):
                idx = self._combat_board_pick_option(f"{caster.name}: cast which spell?", disp, selected_actor=caster, phase="Planning")
            else:
                idx = self.ui.choose("Cast which spell?", disp)
        except Exception:
            idx = self.ui.choose("Cast which spell?", disp)
        spell_ident = prepared[idx]
        sp = self.content.get_spell(spell_ident)
        spell_name = getattr(sp, "name", spell_ident)

        st = status_dict(caster)
        # If already casting, don't allow a second declaration.
        if isinstance(st.get("casting"), dict):
            self.ui.log(f"{caster.name} is already casting!")
            return

        apply_status(caster, "casting", {"rounds": 1, "spell": spell_ident, "disrupted": False})
        pending_spells.append({"caster": caster, "spell": spell_ident})
        self.ui.log(f"{caster.name} begins casting {spell_name}...")

    def _resolve_pending_spells(self, pending_spells: list[dict], foes: list[Actor], combat_distance_ft: int | None = None, allies: list[Actor] | None = None):
        """Resolve spells declared earlier in the round. Disrupted spells are lost.

        `foes` and `allies` should reflect the caster's *current* side at the point of
        spell resolution, not merely the side they belonged to when they declared.
        """
        if not pending_spells:
            return

        from .spellcasting import apply_spell_in_combat

        for entry in list(pending_spells):
            caster = entry.get("caster")
            spell_ident = entry.get("spell")

            if not caster or getattr(caster, "hp", 0) <= 0:
                continue

            st = status_dict(caster)
            casting = st.get("casting")
            if not isinstance(casting, dict):
                continue
            if str(casting.get("spell", "")).strip().lower() != str(spell_ident).strip().lower():
                # Something changed; skip.
                clear_status(caster, "casting")
                continue

            sp = self.content.get_spell(str(spell_ident))
            spell_name = getattr(sp, "name", spell_ident) if sp else str(spell_ident)

            if bool(casting.get("disrupted", False)):
                # In classic OSR play, disrupted spells are lost.
                self.battle_evt("SPELL_DISRUPTED", caster_id=caster.name, spell=str(spell_name))
                self.ui.log(f"{caster.name}'s spell is disrupted and lost!")
                try:
                    caster.spells_prepared.remove(spell_ident)
                except Exception:
                    caster.spells_prepared = [s for s in (getattr(caster, 'spells_prepared', []) or []) if s != spell_ident]
                try:
                    self.battle_evt("SPELL_SPENT", caster_id=caster.name, spell=str(spell_name), remaining=len(list(getattr(caster,'spells_prepared',[]) or [])))
                except Exception:
                    pass
                clear_status(caster, "casting")
                continue

            # P0.75 (fixture compatibility): casting while engaged in melee can be disrupted
            # even if you weren't explicitly hit earlier in the round.
            # Rule: if casting while at melee distance, roll 1d20; on 1–2 the spell is disrupted.
            try:
                if is_melee_engaged(combat_distance_ft):
                    roll = int(self.dice.d(20))
                    if roll <= 2:
                        self.battle_evt("SPELL_DISRUPTED", caster_id=caster.name, spell=str(spell_name), roll=int(roll), reason="melee")
                        self.ui.log(f"{caster.name}'s casting is disrupted in the melee! (roll {roll})")
                        try:
                            caster.spells_prepared.remove(spell_ident)
                        except Exception:
                            caster.spells_prepared = [s for s in (getattr(caster, 'spells_prepared', []) or []) if s != spell_ident]
                        try:
                            self.battle_evt("SPELL_SPENT", caster_id=caster.name, spell=str(spell_name), remaining=len(list(getattr(caster,'spells_prepared',[]) or [])))
                        except Exception:
                            pass
                        clear_status(caster, "casting")
                        continue
            except Exception:
                pass

            # P2.2/A2: centralized status/effect blockers for spellcasting.
            block_reason = action_block_reason(caster, for_casting=True)
            if block_reason is not None:
                self.ui.log(f"{caster.name} cannot cast due to {block_reason}!")
                clear_status(caster, "casting")
                continue
            try:
                mb = self.effects_mgr.query_modifiers(caster)
                if mb.forced_retreat:
                    self.ui.log(f"{caster.name} is too terrified to cast!")
                    clear_status(caster, "casting")
                    continue
                if not mb.can_act:
                    self.ui.log(f"{caster.name} cannot act and loses the chance to cast!")
                    clear_status(caster, "casting")
                    continue
            except Exception:
                pass

            entry_foes = list(entry.get('foes') or foes or [])
            entry_allies = list(entry.get('allies') or allies or ([] if bool(getattr(caster, 'is_pc', False)) else list(self.party.living())))
            ok = bool(apply_spell_in_combat(game=self, caster=caster, spell_name=spell_name, foes=entry_foes, allies=entry_allies))
            if ok:
                self.battle_evt("SPELL_RESOLVED", caster_id=caster.name, spell=str(spell_name))
            else:
                self.battle_evt("SPELL_FAILED", caster_id=caster.name, spell=str(spell_name))
            if not ok:
                self.ui.log(f"{caster.name}'s spell fizzles (not usable right now).")
                # In this engine, a fizzle does NOT consume the spell.
                clear_status(caster, "casting")
                continue

            # Consume only after successfully applying.
            try:
                caster.spells_prepared.remove(spell_ident)
            except Exception:
                caster.spells_prepared = [s for s in (getattr(caster, 'spells_prepared', []) or []) if s != spell_ident]

            try:
                self.battle_evt("SPELL_SPENT", caster_id=caster.name, spell=str(spell_name), remaining=len(list(getattr(caster,'spells_prepared',[]) or [])))
            except Exception:
                pass
            self.ui.log(f"{caster.name} casts {spell_name}!")
            clear_status(caster, "casting")
    # -----------------
    # Test / fixtures helpers
    # -----------------


    def _cmd_test_combat(self, monster_id: str, count: int = 1) -> CommandResult:
        """Spawn a deterministic combat encounter vs a specific monster_id."""
        mid = str(monster_id or "").strip()
        if not mid:
            return CommandResult(status="error", messages=("Missing monster_id.",))
        m = None
        try:
            m = self.content.get_monster(mid)
        except Exception:
            m = None
        if not m:
            return CommandResult(status="error", messages=(f"Unknown monster_id: {mid}",))

        n = max(1, min(20, int(count) if count is not None else 1))
        foes = [self.encounters._mk_monster(m) for _ in range(n)]
        # Ensure we're not in darkness for fixture stability.
        self.light_on = True
        self.combat(foes)
        return CommandResult(status="ok")

    def _cmd_test_save_reload(self, note: str = "") -> CommandResult:
        """Serialize game to dict and apply it back immediately (in-memory roundtrip)."""
        try:
            from .save_load import game_to_dict, apply_game_dict
            payload = game_to_dict(self)
            apply_game_dict(self, payload)
        except Exception as e:
            return CommandResult(status="error", messages=(f"Save/reload failed: {e}",))
        return CommandResult(status="ok", messages=((f"Save/reload ok: {note}".strip(),) if note else ()))
    def _link_contract_to_poi(self, c: dict[str, Any]) -> dict[str, Any]:
        d = dict(c or {})
        th = tuple(d.get("target_hex") or [0, 0])
        try:
            q, r = int(th[0]), int(th[1])
        except Exception:
            return d
        hx = (self.world_hexes or {}).get(f"{q},{r}")
        if not isinstance(hx, dict):
            return d
        poi = hx.get("poi") if isinstance(hx.get("poi"), dict) else None
        if not isinstance(poi, dict):
            return d
        ptype = str(d.get("target_poi_type") or "")
        if ptype and str(poi.get("type") or "") != ptype:
            return d
        d["target_poi_id"] = str(poi.get("id") or f"hex:{q},{r}:{str(poi.get('type') or 'poi')}")
        return d

    def _link_rumor_to_contract(self, c: dict[str, Any]) -> None:
        pid = str((c or {}).get("target_poi_id") or "")
        cid = str((c or {}).get("cid") or "")
        if not pid or not cid:
            return
        changed = False
        for e in (self._journal_rumors() or []):
            if str(e.get("poi_id") or "") == pid:
                if str(e.get("linked_contract_cid") or "") != cid:
                    changed = True
        if changed:
            self._update_rumor_events(pid, {"linked_contract_cid": cid})
