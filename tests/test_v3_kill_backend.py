"""v3.3e/f: kill actions, mob looks and following in MineflayerBackend; Episode.act's retry policy for kills."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager import episode as episode_module
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Outcome, Task
from schema_voyager.trace import EpisodeLog


class FakeBot:
    """Serves canned JSON per path and records every request."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.requests: list[tuple[str, dict]] = []
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
                bot.requests.append((self.path, body))
                payload = json.dumps(bot.responses.get(self.path, {"ok": True})).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def bot():
    fake = FakeBot()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def backend(bot):
    return MineflayerBackend(api_url=bot.url, natural=True)


KILLED = {
    "ok": True, "killed": True, "mob": "cow", "weapon_used": "stone_sword", "death_position": {"x": 1, "y": 64, "z": 2},
    "drops": {"beef": 2}, "picked_up": {"beef": 2}, "inventory": {"beef": 2}, "blocks_walked": 5.5,
    "travel": {"distance": 5.5}, "events": [{"type": "fight_end", "outcome": "killed"}],
}


def test_a_kill_posts_attack_and_maps_the_answer(bot, backend):
    bot.responses["/attack"] = KILLED
    backend.set_protected([((0, 60, 0), (2, 2, 2))])

    outcome = backend.act(Action("kill", target="cow", tool="stone_sword"))

    assert bot.requests == [("/attack", {
        "mob": "cow", "weapon": "stone_sword", "max_distance": 32,
        "protect": [{"origin": [0, 60, 0], "size": [2, 2, 2]}],
    })]
    assert outcome.ok and outcome.block_removed is False
    assert outcome.drops == outcome.picked_up == outcome.inventory_delta == {"beef": 2}
    assert outcome.inventory == {"beef": 2} and outcome.blocks_walked == 5.5
    assert outcome.detail == {"killed": True, "mob": "cow", "weapon_used": "stone_sword", "death_position": {"x": 1, "y": 64, "z": 2}}
    assert outcome.travel == {"distance": 5.5} and outcome.events[0]["type"] == "fight_end"


def test_a_kill_without_weapon_or_protection_sends_a_null_weapon_only(bot, backend):
    bot.responses["/attack"] = {"ok": True, "inventory": {}}
    backend.act(Action("kill", target="pig"))
    assert bot.requests == [("/attack", {"mob": "pig", "weapon": None, "max_distance": 32})]


def test_failed_kills_carry_their_error(bot, backend):
    bot.responses["/attack"] = {"ok": False, "error": "escaped", "mob": "cow", "killed": False, "inventory": {}}
    outcome = backend.act(Action("kill", target="cow"))
    assert not outcome.ok and outcome.error == "escaped"
    assert outcome.detail == {"killed": False, "mob": "cow"}

    bot.responses["/attack"] = {"ok": False, "error": "not_found"}
    assert backend.act(Action("kill", target="cow")).error == "not_found"


def test_an_interrupted_kill_flattens_the_threat_and_outcome(bot, backend):
    bot.responses["/attack"] = {"ok": False, "error": "interrupted", "detail": {"threat": "zombie", "outcome": "fled"}}
    outcome = backend.act(Action("kill", target="cow"))
    assert outcome.error == "interrupted"
    assert outcome.detail == {"threat": "zombie", "outcome": "fled"}


def test_a_kill_outside_natural_mode_is_an_unknown_action_without_a_request(bot):
    outcome = MineflayerBackend(api_url=bot.url).act(Action("kill", target="cow"))
    assert outcome.ok is False and outcome.error == "unknown_action"
    assert bot.requests == []


def test_look_sends_mobs_only_when_asked(bot, backend):
    bot.responses["/look"] = {"directions": {"N": {"cow": {"x": 1, "y": 64, "z": -9, "distance": 9.0}}}}

    assert backend.look(["oak_log"], 32)["N"] == {"cow": {"x": 1, "y": 64, "z": -9, "distance": 9.0}}
    assert bot.requests[-1] == ("/look", {"blocks": ["oak_log"], "radius": 32})

    backend.look([], 64, mobs=["cow", "pig"])
    assert bot.requests[-1] == ("/look", {"blocks": [], "radius": 64, "mobs": ["cow", "pig"]})


def test_follow_returns_the_name_or_none(bot, backend):
    bot.responses["/follow"] = {"ok": True, "player": "Otopo"}
    assert backend.follow(None) == "Otopo"
    assert bot.requests[-1] == ("/follow", {"player": None, "distance": 3.0})

    bot.responses["/follow"] = {"ok": False, "error": "not_found"}
    assert backend.follow("Nobody", 5.0) is None
    assert bot.requests[-1] == ("/follow", {"player": "Nobody", "distance": 5.0})


def test_stop_follow_posts_to_follow_stop(bot, backend):
    backend.stop_follow()
    assert bot.requests == [("/follow/stop", {})]


def test_status_parses_follow(bot, backend):
    bot.responses["/status"] = {"health": 20, "food": 20, "follow": {"player": "Otopo", "distance": 3.0}}
    assert backend.status().follow == {"player": "Otopo", "distance": 3.0}
    bot.responses["/status"] = {"health": 20, "food": 20, "follow": None}
    assert backend.status().follow is None


# --- Episode.act ---------------------------------------------------------------


class ScriptedBackend:
    """Answers kills with the scripted outcomes, then succeeds; looks answer a mob to the east."""

    def __init__(self, errors, hits=None):
        self.errors = list(errors)
        self.hits = hits if hits is not None else {"E": {"cow": {"x": 10, "y": 64, "z": 0, "distance": 10.0}}}
        self.actions: list[Action] = []
        self.looks: list[tuple] = []

    def act(self, action):
        self.actions.append(action)
        if action.kind == "explore":
            return Outcome(ok=True, travel={"distance": 10})
        if self.errors:
            return Outcome(ok=False, error=self.errors.pop(0))
        return Outcome(ok=True)

    def look(self, blocks, radius=64, mobs=()):
        self.looks.append((list(blocks), list(mobs)))
        return {direction: self.hits.get(direction, {}) for direction in ("N", "NE", "E", "SE", "S", "SW", "W", "NW")}

    def observe(self):
        return type("Obs", (), {"position": (0, 64, 0), "inventory": {}})()


def kill_in(tmp_path, backend):
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), natural=True,
    )
    return episode, episode.act(Action("kill", target="cow"))


def kinds(backend):
    return [(action.kind, action.direction) for action in backend.actions]


def test_not_found_explores_toward_the_nearest_mob_sighting(tmp_path):
    backend = ScriptedBackend(["not_found"])
    _, outcome = kill_in(tmp_path, backend)

    assert outcome.ok
    assert backend.looks == [([], ["cow"])]
    assert kinds(backend) == [("kill", None), ("explore", "E"), ("kill", None)]


def test_escaped_is_retried_in_place_without_exploring(tmp_path):
    backend = ScriptedBackend(["escaped"])
    _, outcome = kill_in(tmp_path, backend)
    assert outcome.ok and kinds(backend) == [("kill", None), ("kill", None)]
    assert backend.looks == []


def test_escaped_retries_are_bounded(tmp_path):
    backend = ScriptedBackend(["escaped"] * 5)
    _, outcome = kill_in(tmp_path, backend)
    assert outcome.error == "escaped" and len(backend.actions) == 3


def test_unreachable_explores_toward_the_mob_then_retries(tmp_path):
    backend = ScriptedBackend(["unreachable"])
    _, outcome = kill_in(tmp_path, backend)
    assert outcome.ok and backend.looks == [([], ["cow"])]
    assert kinds(backend) == [("kill", None), ("explore", "E"), ("kill", None)]


def test_timeout_is_retried_in_place(tmp_path):
    backend = ScriptedBackend(["timeout"])
    _, outcome = kill_in(tmp_path, backend)
    assert outcome.ok and kinds(backend) == [("kill", None), ("kill", None)]
    assert backend.looks == []


def test_a_cow_kill_succeeds_through_the_simulator(tmp_path):
    for seed in range(500):
        world = NaturalWorld(seed, difficulty="peaceful")
        if "cow" in world.observe().entities:
            break
    episode, outcome = kill_in(tmp_path, world)
    assert outcome.ok and outcome.detail["killed"] is True and outcome.drops.get("beef", 0) >= 1
