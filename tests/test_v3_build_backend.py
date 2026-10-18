"""MineflayerBackend place, clear, scan and set_keep_out, against a fake HTTP bot (contracts/bot_api.md v3.4)."""

import pytest

from schema_voyager.inventory import SCAFFOLD_BLOCKS
from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend
from schema_voyager.models import Action
from tests.test_v3_toss_backend import FakeBot

TRAVEL = {"distance": 3.2, "dug": {}, "placed": {}, "picked_up": {}}
PLACED = {
    "ok": True,
    "placed": True,
    "block": "oak_planks",
    "found": "oak_planks",
    "inventory": {"oak_planks": 40},
    "inventory_before_step": {"oak_planks": 41},
    "travel": TRAVEL,
    "events": [{"seq": 4, "type": "note"}],
}


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture(params=[False, True], ids=["plain", "natural"])
def backend(request, bot):
    return MineflayerBackend(api_url=bot.url, natural=request.param, arena=object())


def place(pos=(10, 64, -3)):
    return Action("place", target="oak_planks", pos=pos)


def test_place_payload_without_keep_out(bot, backend):
    bot.responses["/place"] = PLACED
    backend.act(place())
    assert bot.requests == [
        ("/place", {"block": "oak_planks", "x": 10, "y": 64, "z": -3, "scaffold": list(SCAFFOLD_BLOCKS)})
    ]


def test_place_and_clear_payloads_with_keep_out(bot, backend):
    bot.responses["/place"] = PLACED
    bot.responses["/clear"] = {"ok": True, "cleared": True, "removed": "dirt", "found": "air", "inventory": {}}
    backend.set_keep_out(((8, 64, -5), (7, 4, 5)))
    backend.act(place())
    backend.act(Action("clear", pos=(1, 2, 3)))
    keep_out = {"origin": [8, 64, -5], "size": [7, 4, 5]}
    scaffold = list(SCAFFOLD_BLOCKS)
    assert bot.requests == [
        ("/place", {"block": "oak_planks", "x": 10, "y": 64, "z": -3, "scaffold": scaffold, "keep_out": keep_out}),
        ("/clear", {"x": 1, "y": 2, "z": 3, "scaffold": scaffold, "keep_out": keep_out}),
    ]


def test_set_keep_out_none_drops_the_key(bot, backend):
    bot.responses["/place"] = PLACED
    backend.set_keep_out(((0, 0, 0), (1, 1, 1)))
    backend.set_keep_out(None)
    backend.act(place())
    assert "keep_out" not in bot.requests[0][1]


def test_placed_block_outcome(bot, backend):
    bot.responses["/place"] = PLACED
    outcome = backend.act(place())
    assert outcome.ok and outcome.error is None
    assert outcome.inventory == {"oak_planks": 40}
    assert outcome.inventory_delta == {"oak_planks": -1}
    assert outcome.travel == TRAVEL
    assert outcome.blocks_walked == 3.2
    assert outcome.events == [{"seq": 4, "type": "note"}]
    assert outcome.block_removed is None and outcome.drops == {} and outcome.picked_up == {}
    assert outcome.detail == {
        "block": "oak_planks",
        "placed": True,
        "found": "oak_planks",
        "target": {"x": 10, "y": 64, "z": -3},
        "inventory_before_step": {"oak_planks": 41},
    }
    assert [path for path, _ in bot.requests] == ["/place"]  # no /explore, no /observe


def test_already_there_keeps_placed_false(bot, backend):
    bot.responses["/place"] = PLACED | {"placed": False, "inventory": {"oak_planks": 41}}
    outcome = backend.act(place())
    assert outcome.ok and outcome.detail["placed"] is False
    assert outcome.inventory_delta == {}


def test_missing_inventory_before_step_gives_empty_delta(bot, backend):
    bot.responses["/place"] = {k: v for k, v in PLACED.items() if k not in ("inventory_before_step", "travel")}
    outcome = backend.act(place())
    assert outcome.inventory_delta == {} and outcome.travel == {} and outcome.blocks_walked == 0.0


def test_occupied_keeps_found(bot, backend):
    bot.responses["/place"] = {"ok": False, "error": "occupied", "found": "stone", "inventory": {"oak_planks": 41}}
    outcome = backend.act(place())
    assert not outcome.ok and outcome.error == "occupied"
    assert outcome.detail["found"] == "stone"
    assert outcome.detail["target"] == {"x": 10, "y": 64, "z": -3}


def test_interrupted_is_flattened(bot, backend):
    bot.responses["/place"] = {
        "ok": False,
        "error": "interrupted",
        "detail": {"threat": "zombie", "outcome": "survived", "extra": 1},
        "inventory": {"oak_planks": 41},
    }
    outcome = backend.act(place())
    assert outcome.error == "interrupted"
    assert outcome.detail["threat"] == "zombie" and outcome.detail["outcome"] == "survived"
    assert "detail" not in outcome.detail


def test_clear_success(bot, backend):
    bot.responses["/clear"] = {
        "ok": True,
        "cleared": True,
        "removed": "dirt",
        "found": "air",
        "inventory": {"dirt": 1},
        "inventory_before_step": {},
        "travel": TRAVEL,
        "events": [],
    }
    outcome = backend.act(Action("clear", pos=(4, 65, 6)))
    assert outcome.ok
    assert outcome.inventory_delta == {"dirt": 1}
    assert outcome.blocks_walked == 3.2
    assert outcome.detail == {
        "cleared": True,
        "removed": "dirt",
        "found": "air",
        "target": {"x": 4, "y": 65, "z": 6},
        "inventory_before_step": {},
    }
    assert [path for path, _ in bot.requests] == ["/clear"]


def test_clear_unbreakable(bot, backend):
    bot.responses["/clear"] = {"ok": False, "error": "unbreakable", "found": "bedrock", "inventory": {}}
    outcome = backend.act(Action("clear", pos=(4, 0, 6)))
    assert outcome.error == "unbreakable" and outcome.detail["found"] == "bedrock"


@pytest.mark.parametrize("action", [Action("place", target="oak_planks"), Action("clear")])
def test_missing_pos_raises(bot, backend, action):
    with pytest.raises(ValueError):
        backend.act(action)
    assert bot.requests == []


def test_scan_converts_layers_to_world_positions(bot, backend):
    bot.responses["/scan"] = {
        "ok": True,
        "layers": [
            [["dirt", "dirt", "air"], ["dirt", "oak_planks", None]],
            [["air", "air", "air"], ["air", "torch", "air"]],
        ],
    }
    cells = backend.scan((8, 64, -5), (3, 2, 2))
    assert bot.requests == [("/scan", {"origin": [8, 64, -5], "size": [3, 2, 2]})]
    assert len(cells) == 12
    assert cells[(8, 64, -5)] == "dirt"
    assert cells[(10, 64, -5)] == "air"
    assert cells[(9, 64, -4)] == "oak_planks"
    assert cells[(10, 64, -4)] is None
    assert cells[(9, 65, -4)] == "torch"


def test_scan_http_error_raises_backend_error():
    with pytest.raises(BackendError):
        MineflayerBackend(api_url="http://127.0.0.1:9", natural=True).scan((0, 0, 0), (1, 1, 1))


def test_existing_actions_have_no_pos_key():
    assert "pos" not in Action("mine", target="oak_log").to_dict()
