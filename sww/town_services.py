"""Town service helpers (deterministic, save-safe).

Current scope: item identification.
"""

from __future__ import annotations

from dataclasses import dataclass

from .inventory_service import find_item_on_actor
from .item_templates import get_item_template, item_is_magic
from .models import Actor, ItemInstance


@dataclass(frozen=True)
class TownIdentifyResult:
    ok: bool
    error: str | None = None
    gold_spent: int = 0
    party_gold_after: int = 0
    item: ItemInstance | None = None


def identify_cost_gp(item: ItemInstance) -> int:
    """Conservative baseline costs for town identification.

    - Magic consumables (potions/scrolls): 15 gp
    - Other magic items: 25 gp
    - Mundane unidentified oddities: 8 gp
    """
    cat = str(getattr(item, "category", "") or "").strip().lower()
    magic = False
    try:
        magic = bool(item_is_magic(item))
    except Exception:
        magic = bool((item.metadata or {}).get("magic", False))

    if not magic:
        return 8
    if cat in {"potion", "scroll", "ammo"}:
        return 15
    return 25


def identify_item_in_town(
    actor: Actor,
    instance_id: str,
    *,
    party_gold: int,
    reveal_curse: bool = False,
    reveal_charges: bool = False,
) -> TownIdentifyResult:
    """Identify one specific inventory item instance.

    Design choices for this pass:
    - Mundane unidentified items CAN be identified.
    - Curse status is NOT revealed by default (`reveal_curse=False`).
    - Charges are NOT revealed by default (`reveal_charges=False`).
    """
    item = find_item_on_actor(actor, str(instance_id or ""))
    if item is None:
        return TownIdentifyResult(ok=False, error="item not found", party_gold_after=int(party_gold or 0))

    cur_gold = int(party_gold or 0)
    if bool(getattr(item, "identified", False)):
        return TownIdentifyResult(ok=True, gold_spent=0, party_gold_after=cur_gold, item=item)

    cost = identify_cost_gp(item)
    if cur_gold < cost:
        return TownIdentifyResult(ok=False, error="not enough gold", party_gold_after=cur_gold, item=item)

    item.identified = True
    try:
        tmpl = get_item_template(str(item.template_id or ""))
    except Exception:
        tmpl = None

    if tmpl is not None:
        item.name = str(tmpl.identified_name or tmpl.name)
        if reveal_curse and bool(getattr(tmpl, "cursed", False)):
            item.cursed_known = True
    if reveal_charges:
        md = dict(item.metadata or {})
        if "charges" in md:
            md["charges_known"] = True
            item.metadata = md

    return TownIdentifyResult(ok=True, gold_spent=cost, party_gold_after=cur_gold - cost, item=item)
