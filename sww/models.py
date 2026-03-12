from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ItemInstance:
    """JSON-friendly owned item instance used during inventory migration.

    Transitional note:
    - `template_id` is the stable catalog/content identifier.
    - `instance_id` is a stable per-owned-item id for save-safe references.
    - Runtime systems may still rely on legacy actor fields until fully migrated.
    """

    instance_id: str
    template_id: str
    name: str
    category: str
    quantity: int = 1
    identified: bool = True
    equipped: bool = False
    magic_bonus: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class CharacterInventory:
    """Per-character carried inventory (transitional model).

    This model coexists with legacy party-level resource handling while systems are
    incrementally ported to per-character ownership.
    """

    items: list[ItemInstance] = field(default_factory=list)
    coins_gp: int = 0
    coins_sp: int = 0
    coins_cp: int = 0
    torches: int = 0
    rations: int = 0
    ammo: dict[str, int] = field(default_factory=dict)


@dataclass
class CharacterEquipment:
    """OSR-practical equipment abstraction for migration.

    Values may currently be stable names or item instance ids. We prefer stable
    names in this pass because most existing code resolves equipment by name.
    """

    armor: str | None = None
    shield: str | None = None
    main_hand: str | None = None
    off_hand: str | None = None
    missile_weapon: str | None = None
    ammo_kind: str | None = None
    worn_misc: list[str] = field(default_factory=list)


@dataclass
class Stats:
    STR: int
    DEX: int
    CON: int
    INT: int
    WIS: int
    CHA: int


def mod_ability(score: int) -> int:
    # S&W uses tables; we approximate standard OD&D-ish modifier curve as baseline.
    # This is a compromise: the full table can be swapped in later with extracted Table 1-5.
    if score <= 3:
        return -3
    if score <= 5:
        return -2
    if score <= 8:
        return -1
    if score <= 12:
        return 0
    if score <= 15:
        return 1
    if score <= 17:
        return 2
    return 3


@dataclass
class Actor:
    name: str
    hp: int
    hp_max: int
    ac_desc: int  # descending AC (9..-something)
    hd: int
    save: int

    # P4.10.5: race is a first-class field (previously duck-typed).
    # Valid values are content-driven (see data/races.json).
    race: str = "Human"

    # Stable monster identifier for non-PC actors (P3.1.15). Optional.
    monster_id: str | None = None
    morale: Optional[int] = None  # 2..12, None means fearless/GM decided
    alignment: str = "Neutrality"
    is_pc: bool = False
    effects: list[str] = field(default_factory=list)
    special_text: str = ""
    # Structured monster/actor specials (e.g., poison, level drain). Optional.
    specials: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    spells_known: list[str] = field(default_factory=list)
    spells_prepared: list[str] = field(default_factory=list)

    # Persistent memorization template (P4.10.36): the player's "default" prepared
    # loadout. After each rest, spells_prepared is rebuilt from this template.
    #
    # Design intent:
    # - Pure Vancian strict: spells may only be prepared at rest.
    # - QoL: if the template is unchanged, it is recycled automatically each day.
    #
    # Stored as stable spell_ids for save-game stability.
    spells_prep_template: list[str] = field(default_factory=list)

    # Spell preparation gating: a caster may only (re)prepare spells after a rest.
    # Default True so newly created PCs can prepare once without forcing an initial rest.
    spell_prep_ready: bool = True

    # Centralized status effects (P2.1+). Keyed by instance_id, JSON-friendly dict payloads.
    effect_instances: dict[str, dict] = field(default_factory=dict)

    # Damage profile (P2.9+): immunities/resistances/vulnerabilities and magic-weapon gating.
    # JSON-friendly defaults: no special defenses.
    damage_profile: dict = field(
        default_factory=lambda: {
            "immunities": [],
            "resistances": [],
            "vulnerabilities": [],
            "requires_magic_to_hit": False,
            "requires_enchantment_bonus": 0,
            "special_interactions": {},
        }
    )

    # Extensible status container (duration counters, temporary bonuses, conditions).
    # Example: {"turned": 3, "bless": {"rounds": 6, "to_hit": 1}}
    status: dict = field(default_factory=dict)

    # Retainers / hirelings
    is_retainer: bool = False
    loyalty: int = 7  # 2..12, also used as morale for retainers
    wage_gp: int = 0  # paid per town visit (simplified)
    treasure_share: int = 1
    on_expedition: bool = False

    # Legacy equipment fields (kept for compatibility during migration).
    weapon: str | None = None
    armor: str | None = None
    shield: bool = False
    # Optional shield display name (supports magic shields like "Shield +1").
    shield_name: str | None = None

    # New per-character model scaffolding.
    inventory: CharacterInventory = field(default_factory=CharacterInventory)
    equipment: CharacterEquipment = field(default_factory=CharacterEquipment)

    def ensure_inventory_initialized(self) -> CharacterInventory:
        """Ensure `inventory` exists and has expected shape (migration bridge)."""
        inv = getattr(self, "inventory", None)
        if not isinstance(inv, CharacterInventory):
            inv = CharacterInventory()
            self.inventory = inv
        return inv

    def ensure_equipment_initialized(self) -> CharacterEquipment:
        """Ensure `equipment` exists and has expected shape (migration bridge)."""
        eq = getattr(self, "equipment", None)
        if not isinstance(eq, CharacterEquipment):
            eq = CharacterEquipment()
            self.equipment = eq
        return eq

    def sync_legacy_equipment_to_new(self) -> CharacterEquipment:
        """Copy legacy fields into `equipment` when corresponding fields are unset.

        Transitional helper used to keep old call sites readable while new systems
        adopt `CharacterEquipment`.
        """
        eq = self.ensure_equipment_initialized()
        if not eq.main_hand and self.weapon:
            eq.main_hand = str(self.weapon)
        if not eq.armor and self.armor:
            eq.armor = str(self.armor)
        if not eq.shield and self.shield:
            eq.shield = str(self.shield_name or "Shield")
        return eq

    def sync_new_equipment_to_legacy(self) -> None:
        """Backfill legacy fields from `equipment` for non-migrated systems."""
        eq = self.ensure_equipment_initialized()
        if eq.main_hand is not None:
            self.weapon = eq.main_hand
        if eq.armor is not None:
            self.armor = eq.armor
        if eq.shield is not None:
            self.shield = True
            self.shield_name = eq.shield
        elif eq.shield is None and self.shield_name:
            self.shield = False

    def alive(self) -> bool:
        return self.hp > -1

    def unconscious(self) -> bool:
        return self.hp == 0


@dataclass
class Party:
    members: list[Actor] = field(default_factory=list)

    def pcs(self):
        return [m for m in self.members if getattr(m, "is_pc", False) and not getattr(m, "is_retainer", False)]

    def retainers(self):
        return [m for m in self.members if getattr(m, "is_retainer", False)]

    def living(self):
        return [m for m in self.members if m.hp > 0]

    def any_alive(self) -> bool:
        return any(m.hp > -1 for m in self.members)


@dataclass
class Spell:
    # Stable identifier (P3.1.13). Used for save-game stability and to avoid
    # breakage when spell display names change capitalization/wording.
    spell_id: str
    name: str
    spell_level: str
    range: str
    duration: str
    description: str
    # Optional legacy identifiers (previous names / ids). Resolution is handled
    # by Content.get_spell().
    aliases: list[str] = field(default_factory=list)
    # Optional semantic tags used for category-based spell interactions
    # (e.g., monster spell immunity to "fire" or "enchantment").
    tags: list[str] = field(default_factory=list)
