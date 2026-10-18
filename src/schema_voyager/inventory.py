"""Inventory space (v3): stack sizes, the packed slot count, and the junk a nearly
full bot drops. Pure functions over raw Minecraft ids. Contract: docs/v3-interfaces.md.
"""

from __future__ import annotations

from collections.abc import Iterable

from . import curriculum, normalize
from .mineflayer_backend import SCAFFOLD_BLOCKS
from .models import Rule

# Names or suffixes of items that don't stack (stack size 1).
_UNSTACKABLE_NAMES = (
    "shears", "flint_and_steel", "fishing_rod", "bow", "crossbow", "shield", "trident", "elytra",
    "minecart", "potion", "splash_potion", "lingering_potion", "mushroom_stew", "rabbit_stew",
    "beetroot_soup", "suspicious_stew", "saddle", "totem_of_undying", "enchanted_book",
    "writable_book", "written_book",
)
_UNSTACKABLE_SUFFIXES = (
    "_pickaxe", "_axe", "_shovel", "_hoe", "_sword", "_helmet", "_chestplate", "_leggings", "_boots",
    "_bucket", "_bed", "_boat", "_raft", "_minecart",
)
_UNSTACKABLE_PREFIXES = ("music_disc_",)

# Items that stack to 16. "bucket" is the empty one; filled buckets don't stack.
_STACK_16_NAMES = ("bucket", "snowball", "egg", "ender_pearl", "honey_bottle", "armor_stand")
_STACK_16_SUFFIXES = ("_sign", "_banner")  # "_sign" covers hanging signs


def stack_size(item: str) -> int:
    """How many of a raw id fit in one slot: 1, 16 or 64."""
    if item in _STACK_16_NAMES or item.endswith(_STACK_16_SUFFIXES):
        return 16
    if item in _UNSTACKABLE_NAMES or item.endswith(_UNSTACKABLE_SUFFIXES) or item.startswith(_UNSTACKABLE_PREFIXES):
        return 1
    return 64


def slots_used(inventory: dict[str, int]) -> int:
    """The packed slot count: each item's count divided by its stack size, rounded up."""
    return sum(-(-count // stack_size(item)) for item, count in inventory.items() if count > 0)


def junk_to_toss(
    inventory: dict[str, int],
    rules: Iterable[Rule],
    wanted: Iterable[str],
    free_slots: int | None,
    min_free: int = 4,
    scaffold_reserve: int = 64,
) -> dict[str, int]:
    """Raw id -> count to drop so a bot with fewer than min_free free slots has room.

    Never drops tools, kept stations, wanted items or the first stack of an item a rule
    names. Drops all other junk, keeping a scaffold reserve; if that isn't enough, the
    surplus of rule items, the one freeing the most slots first. May fall short of min_free.
    """
    if free_slots is None or free_slots >= min_free:
        return {}

    wanted = set(wanted)
    rule_items = _rule_items(rules)
    kept: dict[str, int] = {}
    junk: dict[str, int] = {}
    rule_surplus: dict[str, int] = {}
    for item, count in inventory.items():
        if count <= 0:
            continue
        names = {item, normalize.family(item)}
        if stack_size(item) == 1 or any(curriculum.is_kept_item(n) for n in names) or names & wanted:
            kept[item] = count
        elif names & rule_items:
            kept[item] = min(count, stack_size(item))
            if count > kept[item]:
                rule_surplus[item] = count - kept[item]
        else:
            junk[item] = count

    toss = {item: count for item, count in junk.items() if item not in SCAFFOLD_BLOCKS}

    reserve_left = max(0, scaffold_reserve - sum(kept.get(block, 0) for block in SCAFFOLD_BLOCKS))
    for block in SCAFFOLD_BLOCKS:
        if block in junk:
            reserved = min(junk[block], reserve_left)
            reserve_left -= reserved
            if junk[block] > reserved:
                toss[block] = junk[block] - reserved

    def freed_by(item: str) -> int:
        return slots_used({item: inventory[item]}) - slots_used({item: inventory[item] - rule_surplus[item]})

    freed = slots_used({item: inventory[item] for item in toss}) - slots_used(
        {item: inventory[item] - count for item, count in toss.items()}
    )
    for item in sorted(rule_surplus, key=lambda item: (-freed_by(item), item)):
        if free_slots + freed >= min_free:
            break
        toss[item] = rule_surplus[item]
        freed += freed_by(item)
    return toss


def _rule_items(rules: Iterable[Rule]) -> set[str]:
    """Normalized names the rules consume, or need as a station, tool or fuel."""
    names: set[str] = set()
    for rule in rules:
        pre = rule.preconditions
        names.update(pre.get("consumes") or {})
        names.update(pre.get("tool_any") or [])
        names.update(pre.get("fuel_any") or [])
        if pre.get("station"):
            names.add(pre["station"])
    return names
