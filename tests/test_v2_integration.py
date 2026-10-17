"""Wave 1 integration: the planner prefers producers the agent can see, and a smelt's
fuel and ingredient groups resolve to an item actually held. Real normalize, no fakes
of it."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager.agent import run_schemavoyager
from schema_voyager.curriculum import Curriculum
from schema_voyager.environment import NaturalWorld
from schema_voyager.episode import Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action, Observation, Outcome, Rule, Task
from schema_voyager.planner import make_plan
from schema_voyager.schema_store import SchemaStore
from schema_voyager.trace import EpisodeLog


def mine_rule(subject, drops, status="hypothesis", **fields) -> Rule:
    return Rule(
        id=f"mine_{subject}",
        action={"type": "mine", "block": subject},
        preconditions={"tool_any": []},
        effects={"block_removed": True, "drops": drops},
        status=status,
        **fields,
    )


def cobblestone_rules(stone_status="hypothesis") -> list[Rule]:
    """deepslate sorts before stone by id and is certified; stone is still a hypothesis."""
    return [
        mine_rule("deepslate", {"#cobblestone": 1}, status="certified"),
        mine_rule("stone", {"#cobblestone": 1}, status=stone_status),
    ]


def first_mined(rules, visible):
    plan = make_plan("#cobblestone", 1, {}, rules, visible=visible)
    return plan[0].action.target


# -- the planner --------------------------------------------------------------------


def test_a_visible_producer_beats_an_invisible_certified_one():
    assert first_mined(cobblestone_rules(), {"stone": 12}) == "stone"
    assert first_mined(cobblestone_rules(), {"stone": 12, "deepslate": 0}) == "stone"


def test_the_more_visible_of_two_visible_producers_goes_first():
    rules = cobblestone_rules(stone_status="certified")
    assert first_mined(rules, {"stone": 3, "deepslate": 40}) == "deepslate"
    assert first_mined(rules, {"stone": 40, "deepslate": 3}) == "stone"


def test_certified_goes_before_visibility_counts_among_visible_producers():
    assert first_mined(cobblestone_rules(), {"stone": 40, "deepslate": 3}) == "deepslate"


def test_nothing_visible_falls_back_to_the_usual_order():
    assert first_mined(cobblestone_rules(), {"dirt": 9}) == "deepslate"


def test_without_a_view_the_order_is_unchanged():
    assert first_mined(cobblestone_rules(), None) == "deepslate"
    assert first_mined(cobblestone_rules(), None) == first_mined(cobblestone_rules(), {})


def test_the_goals_skill_still_outranks_visibility():
    store = SchemaStore()
    rules = cobblestone_rules()
    for rule in rules:
        store.add_rule(rule)
    store.compile_skill("#cobblestone", 1, [(Action("mine", "deepslate"), "mine_deepslate")], "e1")
    plan = make_plan("#cobblestone", 1, {}, rules, store.skill_for, visible={"stone": 12})
    assert plan[0].action.target == "deepslate"


# -- the curriculum and the agent ----------------------------------------------------


def test_the_curriculum_plans_with_the_visible_counts():
    store = SchemaStore()
    for rule in [*cobblestone_rules(), mine_rule("#log", {"#log": 1})]:
        store.add_rule(rule)
    curriculum = Curriculum(frontier=["#cobblestone"])
    assert curriculum.next_goal({}, store, "w1", {"stone": 9}).uncertified == ("mine_stone",)
    assert curriculum.next_goal({}, store, "w1") is None  # certified deepslate, nothing to learn


class StoneOnlyBackend:
    """Shows stone but no deepslate; every action fails, so the agent keeps replanning."""

    name = "stone-only"

    def __init__(self):
        self.actions = []
        self.observations = 0

    def observe(self):
        self.observations += 1
        return Observation((0.0, 70.0, 0.0), {}, {"stone": 9, "grass_block": 30})

    def say(self, message):
        pass

    def look(self, blocks, radius=64):
        return {}

    def act(self, action):
        self.actions.append(action)
        return Outcome(ok=False, error="not_found", inventory={})


def run_cobblestone_goal(tmp_path, natural):
    backend = StoneOnlyBackend()
    store = SchemaStore()
    for rule in cobblestone_rules():
        store.add_rule(rule)
    llm = LLM(mode="fake", cache_path=tmp_path / "cache.jsonl")
    task = Task("g001", "train", "#cobblestone", 1, {}, (), 3, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=llm, log=EpisodeLog(None, "test", say=backend.say), inventory={}, natural=natural,
    )
    run_schemavoyager(episode, store)
    return backend


def test_the_natural_agent_plans_stone_when_only_stone_is_in_view(tmp_path):
    backend = run_cobblestone_goal(tmp_path, natural=True)
    mined = {action.target for action in backend.actions if action.kind == "mine"}
    assert mined == {"stone"}


def test_only_the_natural_agent_observes_to_plan(tmp_path):
    v1 = run_cobblestone_goal(tmp_path, natural=False).observations
    assert run_cobblestone_goal(tmp_path, natural=True).observations > v1


# -- smelt fuel and ingredient groups ------------------------------------------------


def test_the_simulated_smelt_burns_the_planks_held_not_those_of_the_logs():
    world = NaturalWorld(7)
    world.inventory.update(raw_iron=2, furnace=1, birch_planks=2, oak_log=3)
    outcome = world.act(Action("smelt", "iron_ingot", times=2, fuel="#planks", ingredient="raw_iron"))
    assert outcome.ok and outcome.detail["fuel"] == "birch_planks"
    assert outcome.inventory_delta == {"raw_iron": -2, "birch_planks": -2, "iron_ingot": 2}


def test_the_simulated_smelt_takes_the_most_held_member_and_fails_when_none_is_held():
    world = NaturalWorld(7)
    world.inventory.update(raw_iron=1, furnace=1, birch_planks=1, oak_planks=4)
    assert world.act(Action("smelt", "iron_ingot", times=1, fuel="#planks", ingredient="raw_iron")).detail["fuel"] == "oak_planks"
    empty = NaturalWorld(7)
    empty.inventory.update(raw_iron=1, furnace=1)
    outcome = empty.act(Action("smelt", "iron_ingot", times=1, fuel="#planks", ingredient="raw_iron"))
    assert not outcome.ok and outcome.error == "missing_ingredients"


class FakeBot:
    def __init__(self, inventory):
        self.inventory = inventory
        self.requests = []
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
                bot.requests.append((self.path, body))
                response = {"ok": True, "inventory": bot.inventory, "nearby_blocks": {}}
                payload = json.dumps(response).encode()
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
    fake = FakeBot({"raw_iron": 2, "furnace": 1, "birch_planks": 2, "oak_log": 3})
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def test_the_bot_smelt_request_names_the_planks_held(bot):
    backend = MineflayerBackend(bot.url, natural=True)
    backend.act(Action("smelt", "iron_ingot", times=2, fuel="#planks", ingredient="raw_iron"))
    path, request = bot.requests[-1]
    assert path == "/smelt"
    assert request["fuel"] == "birch_planks" and request["ingredient"] == "raw_iron" and request["item"] == "iron_ingot"


def test_the_bot_smelt_fails_without_a_request_when_no_group_member_is_held(bot):
    bot.inventory = {"raw_iron": 2, "furnace": 1, "oak_log": 3}
    backend = MineflayerBackend(bot.url, natural=True)
    outcome = backend.act(Action("smelt", "iron_ingot", times=2, fuel="#planks", ingredient="raw_iron"))
    assert not outcome.ok and outcome.error == "missing_ingredients"
    assert "/smelt" not in [path for path, _ in bot.requests]
