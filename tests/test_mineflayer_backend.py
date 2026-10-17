from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action

INVENTORY = {"oak_planks": 3, "stick": 2}


class CannedBackend(MineflayerBackend):
    """Answers each bot route with a canned body instead of HTTP."""

    def __init__(self, bodies):
        super().__init__(arena=object())
        self.bodies = bodies
        self.paths = []

    def _post(self, path, payload=None):
        self.paths.append(path)
        return self.bodies[path]


def test_a_bot_timeout_keeps_the_inventory_the_bot_still_has():
    backend = CannedBackend({
        "/craft": {"ok": False, "error": "timeout", "blocks_walked": 4.0},
        "/observe": {"ok": True, "inventory": INVENTORY, "nearby_blocks": {}, "blocks_walked": 4.0},
    })
    outcome = backend.act(Action("craft", "wooden_pickaxe"))
    assert not outcome.ok and outcome.error == "timeout"
    assert outcome.inventory == INVENTORY
    assert backend.paths == ["/craft", "/observe"]


def test_a_reported_inventory_is_used_as_is():
    backend = CannedBackend({
        "/mine": {"ok": False, "error": "unreachable", "inventory": {}, "drops": {}, "target": {"x": 1, "y": -57, "z": 5}},
    })
    outcome = backend.act(Action("mine", "oak_log"))
    assert outcome.inventory == {} and outcome.detail == {"target": {"x": 1, "y": -57, "z": 5}}
    assert backend.paths == ["/mine"]
