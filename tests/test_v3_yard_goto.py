"""v3.0i: MineflayerBackend.reach and the `position` and `niche` answers, NaturalWorld.reach (contracts/bot_api.md v3.0i)."""

import pytest

from schema_voyager.environment import NaturalWorld
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action


@pytest.fixture
def backend():
    return MineflayerBackend(api_url="http://127.0.0.1:1", natural=True)


def _stub(backend, *answers):
    """Make _post answer in turn; returns the (path, payload) calls."""
    calls, replies = [], iter(answers)

    def post(path, payload=None, **kwargs):
        calls.append((path, payload))
        return next(replies)

    backend._post = post
    return calls


def test_reach_posts_range_3_and_stall_8000_with_protect(backend):
    calls = _stub(backend, {"ok": True, "arrived": True, "inventory": {"coal": 1}})
    backend.set_protected([((0, 0, 0), (2, 2, 2))])

    outcome = backend.reach((10, 64, -3))

    assert calls == [("/goto", {
        "x": 10, "y": 64, "z": -3, "range": 3, "stall_ms": 8000,
        "protect": [{"origin": [0, 0, 0], "size": [2, 2, 2]}],
    })]
    assert outcome.ok is True
    assert outcome.detail["arrived"] is True


def test_reach_maps_an_unreachable_answer_with_where_it_ended(backend):
    _stub(backend, {"ok": False, "error": "unreachable", "arrived": False, "position": {"x": 1.5, "y": 64, "z": 2.25}, "inventory": {}})

    outcome = backend.reach((10, 64, -3))

    assert (outcome.ok, outcome.error) == (False, "unreachable")
    assert outcome.detail["position"] == {"x": 1.5, "y": 64, "z": 2.25}


def test_a_plain_goto_sends_no_stall_and_carries_position(backend):
    calls = _stub(backend, {"ok": False, "error": "unreachable", "position": {"x": 0.5, "y": 64, "z": 0.5}, "inventory": {}})

    outcome = backend.act(Action("goto", pos=(4, 64, 4)))

    assert "stall_ms" not in calls[0][1]
    assert outcome.detail["position"] == {"x": 0.5, "y": 64, "z": 0.5}


def test_withdraw_unreachable_carries_position(backend):
    _stub(backend, {"ok": False, "error": "unreachable", "position": {"x": 3.0, "y": 70, "z": 1.0}, "inventory": {}})

    outcome = backend.act(Action("withdraw", target="coal", times=1, pos=(5, 70, 5)))

    assert outcome.detail["position"] == {"x": 3.0, "y": 70, "z": 1.0}


def test_deposit_unreachable_carries_position(backend):
    _stub(backend, {"ok": False, "error": "unreachable", "position": {"x": 3.0, "y": 70, "z": 1.0}})

    result = backend.deposit((5, 70, 5), {"coal": 1})

    assert result.error == "unreachable"
    assert result.detail["position"] == {"x": 3.0, "y": 70, "z": 1.0}


def test_craft_carries_position_and_niche(backend):
    backend._latest_inventory = {"oak_planks": 3, "stick": 2}
    _stub(backend, {
        "ok": False, "error": "unreachable", "station_unreachable": True, "position": {"x": 2.5, "y": 64, "z": 2.5},
        "niche": [3, 64, 2], "inventory": {"oak_planks": 3},
    })

    outcome = backend.act(Action("craft", target="wooden_pickaxe", times=1))

    assert outcome.detail["position"] == {"x": 2.5, "y": 64, "z": 2.5}
    assert outcome.detail["niche"] == [3, 64, 2]


def test_smelt_carries_position_and_niche(backend):
    backend._latest_inventory = {"raw_iron": 1, "coal": 1}
    _stub(backend, {
        "ok": False, "error": "unreachable", "station_unreachable": True, "position": {"x": 2.5, "y": 64, "z": 2.5},
        "niche": [3, 64, 2], "inventory": {"raw_iron": 1, "coal": 1},
    })

    outcome = backend.act(Action("smelt", target="iron_ingot", ingredient="raw_iron", fuel="coal", times=1))

    assert outcome.detail["position"] == {"x": 2.5, "y": 64, "z": 2.5}
    assert outcome.detail["niche"] == [3, 64, 2]


def test_natural_world_reach_is_ok():
    assert NaturalWorld(7).reach((0, 64, 0)).ok is True
