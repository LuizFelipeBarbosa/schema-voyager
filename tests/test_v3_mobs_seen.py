"""v3.0i Mobs seen: the mobs in every /status the life polls are recorded, and asked about before the next goal."""

import dataclasses
import http.client
import io
import json
import urllib.error

import pytest

from schema_voyager.environment import NaturalWorld
from schema_voyager.llm import LLM
from schema_voyager.life import LifeConfig, LifeHooks, _Life
from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend

NO_MOB_SEED = 8


class UnreadableBody(io.BytesIO):
    def read(self, *args):
        raise http.client.IncompleteRead(b"")


def new_life(tmp_path):
    config = LifeConfig(run_dir=tmp_path, world_id="life-sim-mobs-seen-test")
    return _Life(NaturalWorld(NO_MOB_SEED), LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), config, LifeHooks(), resume=False)


def report_entities(life_, entities):
    status = life_.backend.status
    life_.backend.status = lambda since=0: dataclasses.replace(status(since=since), entities=entities)


def test_a_status_parses_its_nearby_entities_and_drops_the_malformed(monkeypatch):
    backend = MineflayerBackend("http://127.0.0.1:9", natural=True)
    nearby = {"cow": {"count": 2, "nearest": 5.5, "kind": "animal"}, "zombie": "x", "item": None}
    monkeypatch.setattr(backend, "_post", lambda path, payload=None, **kwargs: {"nearby_entities": nearby, "last_seq": 3})

    status = backend.status()

    assert status.entities == {"cow": {"count": 2, "nearest": 5.5, "kind": "animal"}}


def test_a_status_without_nearby_entities_has_none(monkeypatch):
    backend = MineflayerBackend("http://127.0.0.1:9", natural=True)
    monkeypatch.setattr(backend, "_post", lambda path, payload=None, **kwargs: {})
    assert backend.status().entities == {}


def test_the_simulators_status_has_its_nearby_mobs():
    world = NaturalWorld(2)
    assert world.status().entities == world.observe().entities


def test_a_poll_records_the_animals_hostiles_and_mobs_but_not_other_kinds(tmp_path):
    watcher = new_life(tmp_path)
    report_entities(watcher, {
        "cow": {"count": 1, "nearest": 4.0, "kind": "animal"},
        "zombie": {"count": 1, "nearest": 9.0, "kind": "hostile"},
        "item": {"count": 3, "nearest": 2.0, "kind": "other"},
    })

    watcher._poll_bot()

    assert set(watcher.observed_mobs) == {"cow", "zombie"}


def test_a_mob_seen_only_in_a_poll_mid_goal_is_asked_at_the_next_learn_priors(tmp_path):
    watcher = new_life(tmp_path)
    mobs_asked = []
    ask = watcher.llm.propose_prior_rules_v2
    watcher.llm.propose_prior_rules_v2 = lambda families, blocks, world_id, mobs=(): (
        mobs_asked.append(list(mobs)) or ask(families, blocks, world_id, mobs=mobs)
    )
    watcher._learn_priors()
    assert watcher.observed_mobs == {}
    report_entities(watcher, {"cow": {"count": 1, "nearest": 4.0, "kind": "animal"}})
    watcher._poll_bot()  # during a goal
    mobs_asked.clear()

    watcher._learn_priors()  # the observation between goals sees no cow

    assert [mobs for mobs in mobs_asked if mobs] == [["cow"]]
    assert "kill_cow" in watcher.store.rules


@pytest.mark.parametrize("code, body", [(500, UnreadableBody()), (409, UnreadableBody())])
def test_post_with_an_unreadable_error_body_raises_backend_error(monkeypatch, code, body):
    backend = MineflayerBackend("http://127.0.0.1:9", natural=True)
    calls = []

    def urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, code, "boom", {}, body)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("schema_voyager.mineflayer_backend._sleep", lambda seconds: None)

    with pytest.raises(BackendError, match=f"returned {code}"):
        backend._post("/status")
    assert len(calls) == 1  # an unreadable 409 is not "busy"


def test_post_still_retries_a_readable_busy_409(monkeypatch):
    backend = MineflayerBackend("http://127.0.0.1:9", natural=True)
    busy = json.dumps({"error": "busy"})
    answers = [urllib.error.HTTPError("u", 409, "busy", {}, io.BytesIO(busy.encode())), None]

    class Response(io.BytesIO):
        __enter__ = lambda self: self
        __exit__ = lambda self, *exc: None

    def urlopen(request, timeout):
        error = answers.pop(0)
        if error:
            raise error
        return Response(b'{"ok": true}')

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("schema_voyager.mineflayer_backend._sleep", lambda seconds: None)

    assert backend._post("/status") == {"ok": True}
