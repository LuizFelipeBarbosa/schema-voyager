"""Item groups and block variant families (v2): the only module that knows wood
species, groups such as "#log" and "#planks", and variants such as
deepslate_coal_ore. The bot reports raw Minecraft ids; rules use the
normalized names. Contract: docs/v2-interfaces.md.

Wave 0 stub: the rules lane implements these without changing the signatures.
"""

from __future__ import annotations

SPECIES: tuple[str, ...] = ()


def family(name: str) -> str:
    """Raw id -> its group or family name; other names map to themselves (idempotent)."""
    raise NotImplementedError("normalize.family: rules lane")


def members(name: str) -> list[str]:
    """Group or family -> its raw ids, sorted; any other id -> [id]."""
    raise NotImplementedError("normalize.members: rules lane")


def counts(raw: dict[str, int]) -> dict[str, int]:
    """Sum counts by family(), drop zeros, sort keys (idempotent)."""
    raise NotImplementedError("normalize.counts: rules lane")


def species(name: str) -> str | None:
    """The wood species of a raw id, or None."""
    raise NotImplementedError("normalize.species: rules lane")


def species_mismatch(raw: dict[str, int]) -> str | None:
    """A description when species-bearing ids on both sides of one step's counts don't share a species."""
    raise NotImplementedError("normalize.species_mismatch: rules lane")


def resolve(name: str, inventory: dict[str, int]) -> str:
    """A concrete raw id for a craft, smelt or fuel argument that may be a group."""
    raise NotImplementedError("normalize.resolve: rules lane")
