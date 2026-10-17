from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "schema_voyager"

# v1's arena constants and wood species belong to v1 modules and normalize.py only.
GENERIC_MODULES = ("life.py", "curriculum.py", "agent.py", "planner.py", "verifier.py", "schema_store.py")
FORBIDDEN = ("oak_", "birch_", "spruce_", "GROUND_Y", "ARENA_RADIUS", "from .layouts import")


@pytest.mark.parametrize("module", GENERIC_MODULES)
def test_generic_modules_name_no_species_or_arena_constants(module):
    path = SRC / module
    if not path.exists():
        pytest.skip(f"{module} is written in another lane")
    source = path.read_text(encoding="utf-8")
    for forbidden in FORBIDDEN:
        assert forbidden not in source, f"{module} contains {forbidden!r}"
