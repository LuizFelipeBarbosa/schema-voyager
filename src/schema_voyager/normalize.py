"""Item groups and block variant families (v2): the only module that knows wood
species, groups such as "#log" and "#planks", and variants such as
deepslate_coal_ore. The bot reports raw Minecraft ids; rules use the
normalized names. Contract: docs/v2-interfaces.md.
"""

from __future__ import annotations

SPECIES: tuple[str, ...] = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry")

_ORES = ("coal", "iron", "copper", "gold", "diamond", "redstone", "lapis", "emerald")

# Group or family name -> its raw ids, sorted.
_MEMBERS: dict[str, list[str]] = {
    "#log": sorted(f"{prefix}{s}_{kind}" for s in SPECIES for prefix in ("", "stripped_") for kind in ("log", "wood")),
    "#planks": sorted(f"{s}_planks" for s in SPECIES),
    "#leaves": sorted([f"{s}_leaves" for s in SPECIES] + ["azalea_leaves", "flowering_azalea_leaves"]),
    "#sapling": sorted([f"{s}_sapling" for s in SPECIES] + ["mangrove_propagule"]),
    "#cobblestone": ["blackstone", "cobblestone", "cobbled_deepslate"],
    **{f"{ore}_ore": sorted([f"{ore}_ore", f"deepslate_{ore}_ore"]) for ore in _ORES},
}
_FAMILY_OF: dict[str, str] = {raw: name for name, raws in _MEMBERS.items() for raw in raws}

# Raw id -> wood species, for the species-bearing groups.
_SPECIES_OF: dict[str, str] = {
    f"{prefix}{s}_{kind}": s
    for s in SPECIES
    for prefix, kind in (("", "log"), ("", "wood"), ("stripped_", "log"), ("stripped_", "wood"), ("", "planks"),
                         ("", "leaves"), ("", "sapling"))
}


def family(name: str) -> str:
    """Raw id -> its group or family name; other names map to themselves (idempotent)."""
    return _FAMILY_OF.get(name, name)


def members(name: str) -> list[str]:
    """Group or family -> its raw ids, sorted; any other id -> [id]."""
    return list(_MEMBERS.get(name, [name]))


def counts(raw: dict[str, int]) -> dict[str, int]:
    """Sum counts by family(), drop zeros, sort keys (idempotent)."""
    totals: dict[str, int] = {}
    for name, count in raw.items():
        totals[family(name)] = totals.get(family(name), 0) + count
    return {name: count for name, count in sorted(totals.items()) if count}


def species(name: str) -> str | None:
    """The wood species of a raw id, or None."""
    return _SPECIES_OF.get(name)


def species_mismatch(raw: dict[str, int]) -> str | None:
    """A description when species-bearing ids on both sides of one step's counts don't share a species."""
    consumed = sorted(name for name, count in raw.items() if count < 0 and species(name))
    produced = sorted(name for name, count in raw.items() if count > 0 and species(name))
    consumed_species = {species(name) for name in consumed}
    for name in produced:
        if consumed and species(name) not in consumed_species:
            return f"{consumed[0]} -> {name}"
    return None


def resolve(name: str, inventory: dict[str, int]) -> str:
    """A concrete raw id for a craft, smelt or fuel argument that may be a group."""
    if name not in _MEMBERS or not name.startswith("#"):
        return name
    held = {raw: inventory.get(raw, 0) for raw in _MEMBERS[name] if inventory.get(raw, 0) > 0}
    if name == "#planks":
        # Planks of the species whose logs we hold most; failing that, the planks we hold.
        logs_by_species: dict[str, int] = {}
        for raw, count in inventory.items():
            if family(raw) == "#log" and count > 0:
                logs_by_species[species(raw)] = logs_by_species.get(species(raw), 0) + count
        if logs_by_species:
            return f"{_most_held(logs_by_species)}_planks"
    if not held:
        raise ValueError(f"cannot resolve {name}: no matching item is held")
    return _most_held(held)


def _most_held(held: dict[str, int]) -> str:
    """The key with the highest count; ties by name."""
    return min(held, key=lambda key: (-held[key], key))
