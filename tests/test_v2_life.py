"""The v2 life: curriculum, life loop, step-loop dispatch, explore policy, spend cap,
smelt planning, the demo-port guard. The rules lane's normalize and check are
replaced by fakes that follow docs/v2-interfaces.md, and the world by a scripted
LifeBackend, so these tests need neither the real rules code nor a server."""

import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from schema_voyager import agent, curriculum, normalize
from schema_voyager.adapter import DIRECTIONS
from schema_voyager.agent import run_schemavoyager
from schema_voyager.curriculum import Curriculum, Goal
from schema_voyager.demo import check_instance_ports
from schema_voyager.episode import Episode
from schema_voyager.evaluator import failure_signature
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM, LLMError, SpendCapReached
from schema_voyager.models import Action, Observation, Outcome, Task
from schema_voyager.planner import make_plan
from schema_voyager.schema_store import SchemaStore
from schema_voyager.trace import EpisodeLog
from schema_voyager.verifier import Check, predict

ROOT = Path(__file__).resolve().parents[1]
SPECIES = ("oak", "birch")
PICKAXES = ("wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe")


# -- fakes of the rules lane's normalize and check ----------------------------------


def _fake_family(name):
    for species in SPECIES:
        if name in (f"{species}_log", f"{species}_wood", f"stripped_{species}_log"):
            return "#log"
        if name == f"{species}_planks":
            return "#planks"
    if name in ("cobblestone", "cobbled_deepslate", "blackstone"):
        return "#cobblestone"
    if name.startswith("deepslate_") and name.endswith("_ore"):
        return name.removeprefix("deepslate_")
    return name


def _fake_members(name):
    if name == "#log":
        return sorted(f"{s}_log" for s in SPECIES)
    if name == "#planks":
        return sorted(f"{s}_planks" for s in SPECIES)
    if name == "#cobblestone":
        return ["blackstone", "cobblestone", "cobbled_deepslate"]
    if name.endswith("_ore"):
        return [name, f"deepslate_{name}"]
    return [name]


def _fake_counts(raw):
    total = {}
    for name, count in raw.items():
        total[_fake_family(name)] = total.get(_fake_family(name), 0) + count
    return {name: total[name] for name in sorted(total) if total[name]}


def _fake_species(name):
    return next((s for s in SPECIES if name.startswith(f"{s}_")), None)


def _fake_resolve(name, inventory):
    if name not in ("#planks", "#log"):
        return name
    logs = {s: inventory.get(f"{s}_log", 0) for s in SPECIES}
    planks = {s: inventory.get(f"{s}_planks", 0) for s in SPECIES}
    held = logs if any(logs.values()) or name == "#log" else planks
    species = max(sorted(held), key=lambda s: held[s])
    return f"{species}_{'log' if name == '#log' else 'planks'}"


@pytest.fixture(autouse=True)
def fake_normalize(monkeypatch):
    monkeypatch.setattr(normalize, "SPECIES", SPECIES)
    for name, fake in (("family", _fake_family), ("members", _fake_members), ("counts", _fake_counts),
                       ("species", _fake_species), ("resolve", _fake_resolve),
                       ("species_mismatch", lambda raw: None)):
        monkeypatch.setattr(normalize, name, fake)


def fake_check(rule, action, outcome):
    """verifier.check as docs/v2-interfaces.md describes it, for what these tests exercise."""
    predicted = predict(rule, action)
    phases = [event.get("phase", "step") for event in outcome.events if event["type"] in ("damage", "death")]
    if outcome.error == "quarantined" or "step" in phases:
        return Check("quarantined", predicted, {}, "damage", kind="quarantined")
    if not outcome.ok:
        return Check("failed", predicted, {"error": outcome.error}, outcome.error or "")
    if rule.kind == "mine":
        observed = {"block_removed": bool(outcome.block_removed), "drops": _fake_counts(outcome.drops)}
    else:
        observed = {"inventory_delta": _fake_counts(outcome.inventory_delta)}
    if rule.check == "stochastic":
        return Check("stochastic", predicted, observed, "in range", kind="stochastic")
    status = "match" if observed == predicted else "mismatch"
    return Check(status, predicted, observed, "" if status == "match" else f"expected {predicted}, saw {observed}")


@pytest.fixture(autouse=True)
def use_fake_check(monkeypatch):
    monkeypatch.setattr(agent, "check", fake_check)


# -- a scripted world ---------------------------------------------------------------


class FakeLifeBackend:
    """A tiny natural world run from the whitelist's own rules table, on raw ids.
    `reset` is a failure: a life never resets."""

    name = "fake-life"

    def __init__(self, coal_needs_stone=False):
        table = json.loads((ROOT / "src/schema_voyager/data/prior_rules_v2.json").read_text())["rules"]
        self.table = {(r["action_type"], r["subject"]): r for r in table}
        self.coal_needs_stone = coal_needs_stone
        self.inventory = {}
        self.said = []
        self.episode_starts = []
        self.calls = []
        self.step_events = {}  # act call number -> events to report
        self.looks = []
        self.look_result = {}
        self.fail_with = []  # errors for the next mine calls

    def reset(self, task, layout):
        raise AssertionError("a life must never call backend.reset")

    def begin_episode(self):
        self.episode_starts.append(dict(self.inventory))
        return self.observe()

    def observe(self):
        nearby = {"oak_log": 5, "stone": 9, "coal_ore": 2, "iron_ore": 1, "grass_block": 30, "air": 99}
        return Observation((0.0, 70.0, 0.0), dict(self.inventory), nearby)

    def say(self, message):
        self.said.append(message)

    def look(self, blocks, radius=64):
        self.looks.append(blocks)
        return self.look_result

    def act(self, action):
        self.calls.append(action)
        events = self.step_events.get(len(self.calls), [])
        if action.kind == "explore":
            return Outcome(ok=True, inventory=dict(self.inventory))
        if action.kind == "mine" and self.fail_with:
            return Outcome(ok=False, error=self.fail_with.pop(0), inventory=dict(self.inventory))
        rule = self.table.get((action.kind, _fake_family(action.target)))
        if rule is None:
            return Outcome(ok=False, error="unknown", inventory=dict(self.inventory))
        if action.kind == "mine":
            return self._mine(action, rule, events)
        return self._craft(action, rule, events)

    def _mine(self, action, rule, events):
        tools = rule["tool_any"]
        if tools and action.tool not in tools:
            return Outcome(ok=False, error="missing_tool", inventory=dict(self.inventory))
        drops = {} if self.coal_needs_stone and rule["subject"] == "coal_ore" and action.tool == "wooden_pickaxe" else {
            self._raw(y["item"]): y["count"] for y in rule["yields"]
        }
        delta = dict(drops)
        self.inventory["dirt"] = self.inventory.get("dirt", 0) + 1  # picked up on the way
        for item, count in drops.items():
            self.inventory[item] = self.inventory.get(item, 0) + count
        return Outcome(
            ok=True, block_removed=True, drops=drops, picked_up=drops, inventory_delta=delta,
            inventory=dict(self.inventory), detail={"tool_used": action.tool}, events=events,
            travel={"distance": 6.0, "dug": {"dirt": 1}, "placed": {}, "picked_up": {"dirt": 1}},
        )

    def _craft(self, action, rule, events):
        delta = {}
        species = "oak"
        for needed in rule["consumes"]:
            remaining = needed["count"] * action.times
            for raw in sorted(_fake_members(needed["item"]), key=lambda r: -self.inventory.get(r, 0)):
                take = min(remaining, self.inventory.get(raw, 0))
                if take:
                    delta[raw] = delta.get(raw, 0) - take
                    species = _fake_species(raw) or species
                    remaining -= take
            if remaining:
                return Outcome(ok=False, error="missing_ingredients", inventory=dict(self.inventory))
        if rule["station"] and not self.inventory.get(rule["station"]):
            return Outcome(ok=False, error="missing_crafting_table", inventory=dict(self.inventory))
        if action.kind == "smelt":
            fuel = _fake_resolve(action.fuel, self.inventory)
            used = math.ceil(action.times / 8) if fuel in ("coal", "charcoal") else action.times
            delta[fuel] = delta.get(fuel, 0) - used
        for gained in rule["yields"]:
            raw = f"{species}_planks" if gained["item"] == "#planks" else gained["item"]
            delta[raw] = delta.get(raw, 0) + gained["count"] * action.times
        for item, change in delta.items():
            self.inventory[item] = self.inventory.get(item, 0) + change
        self.inventory = {item: n for item, n in self.inventory.items() if n}
        return Outcome(ok=True, inventory_delta=delta, inventory=dict(self.inventory), events=events)

    @staticmethod
    def _raw(item):
        return {"#log": "oak_log", "#cobblestone": "cobblestone"}.get(item, item)


def make_llm(tmp_path, **kwargs):
    return LLM(mode="fake", cache_path=tmp_path / "llm_cache.jsonl", **kwargs)


def make_config(tmp_path, **kwargs):
    return LifeConfig(run_dir=tmp_path / "run", world_id="life-7-20260101-0000", **kwargs)


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def prior_store(llm, *families, world_id="w1"):
    store = SchemaStore()
    for rule in llm.propose_prior_rules_v2(list(families), [], world_id):
        store.add_rule(rule)
    return store


def make_episode(backend, llm, goal_item, goal_count=1, budget=40, **kwargs):
    task = Task("g001", "train", goal_item, goal_count, {}, (), budget, "test")
    return Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=llm, log=EpisodeLog(None, "test", say=backend.say), inventory=dict(backend.inventory), natural=True,
        **kwargs,
    )


def events_of(episode, kind):
    return [e for e in episode.log.events if e["kind"] == kind]


# -- the curriculum -----------------------------------------------------------------


@pytest.fixture
def priors(tmp_path):
    names = ["#planks", "stick", "crafting_table", "wooden_pickaxe", "stone_pickaxe", "torch", "#log", "stone",
             "coal_ore", "#cobblestone", "coal", "iron_ingot", "raw_iron", "iron_pickaxe", "furnace"]
    return prior_store(make_llm(tmp_path), *names)


def test_next_goal_is_the_cheapest_with_an_uncertified_rule(priors):
    plan = Curriculum(["wooden_pickaxe", "stick", "#planks"]).next_goal({}, priors, "w1")
    assert (plan.item, plan.count, plan.plan_length) == ("#planks", 1, 2)
    assert "craft_#planks" in plan.uncertified


def test_ties_go_to_fewest_uncertified_rules_then_to_the_name(priors):
    inventory = {"oak_log": 1}
    assert Curriculum(["stick", "crafting_table"]).next_goal(inventory, priors, "w1").item == "crafting_table"
    priors.rules["craft_stick"].status = "certified"
    goal = Curriculum(["stick", "crafting_table"]).next_goal(inventory, priors, "w1")
    assert (goal.item, goal.uncertified) == ("stick", ("craft_#planks",))  # one uncertified rule beats two
    both_one_step = Curriculum(["stick", "crafting_table"]).next_goal({"oak_planks": 4}, priors, "w1")
    priors.rules["craft_stick"].status = "hypothesis"
    assert Curriculum(["stick", "crafting_table"]).next_goal({"oak_planks": 4}, priors, "w1").item == "crafting_table"
    assert both_one_step.item == "crafting_table" or both_one_step.item == "stick"


def test_certified_plans_teach_nothing(priors):
    for rule in priors.rules.values():
        rule.status = "certified"
    assert Curriculum(["stick"]).next_goal({}, priors, "w1") is None


def test_an_item_failed_twice_is_skipped_for_the_rest_of_the_life(priors):
    frontier = Curriculum(["#planks", "stick"])
    goal = frontier.next_goal({}, priors, "w1")
    frontier.record(goal, False)
    assert frontier.next_goal({}, priors, "w1").item == "#planks"
    frontier.record(goal, False)
    assert "#planks" in frontier.skipped
    assert frontier.next_goal({}, priors, "w1").item == "stick"
    assert Curriculum.from_dict(frontier.to_dict()).skipped == frontier.skipped


def test_items_that_cannot_be_planned_are_passed_over(tmp_path):
    store = prior_store(make_llm(tmp_path), "#planks", "#log")  # no iron rules at all
    assert Curriculum(["iron_pickaxe", "#planks"]).next_goal({}, store, "w1").item == "#planks"
    assert Curriculum(["iron_pickaxe"]).next_goal({}, store, "w1") is None


def test_expansion_takes_only_whitelist_items(tmp_path):
    frontier = Curriculum(["#planks"])
    assert frontier.extend(["diamond_pickaxe", "furnace", "#planks", "furnace"]) == ["furnace"]
    assert "diamond_pickaxe" not in frontier.frontier
    assert make_llm(tmp_path).expand_frontier(["a"], ["x", "y", "z", "w"], []) == ["x", "y", "z"]


def test_life_expands_from_the_whitelist_then_stops_on_an_empty_frontier(tmp_path, monkeypatch):
    class JunkLLM(LLM):
        def expand_frontier(self, frontier, candidates, certified):
            return ["bedrock_pickaxe", *candidates[:1]]

    monkeypatch.setattr(curriculum, "SEED_FRONTIER", ())
    monkeypatch.setattr(curriculum, "WHITELIST", ("#planks", "stick"))
    backend = FakeLifeBackend()
    summary = run_life(backend, JunkLLM(mode="fake", cache_path=tmp_path / "c.jsonl"), make_config(tmp_path, max_goals=40))
    state = json.loads((tmp_path / "run" / "life_state.json").read_text())
    assert state["curriculum"]["frontier"] == ["#planks", "stick"]
    assert summary["stop_reason"] in ("empty_frontier", "max_goals")


def test_an_empty_frontier_stops_the_life(tmp_path, monkeypatch):
    monkeypatch.setattr(curriculum, "SEED_FRONTIER", ())
    monkeypatch.setattr(curriculum, "WHITELIST", ())
    summary = run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path))
    assert (summary["goals"], summary["stop_reason"]) == (0, "empty_frontier")


# -- the life loop ------------------------------------------------------------------


def test_a_life_runs_goals_in_one_world_without_resets(tmp_path):
    backend = FakeLifeBackend()
    llm = make_llm(tmp_path)
    config = make_config(tmp_path, max_goals=6)
    summary = run_life(backend, llm, config)

    run_dir = config.run_dir
    lines = read_lines(run_dir / "life.jsonl")
    assert summary["stop_reason"] == "max_goals" and summary["goals"] == len(lines) == 6
    assert [line["index"] for line in lines] == [1, 2, 3, 4, 5, 6]
    assert all(line["outcome"] == "success" for line in lines)
    assert len(backend.episode_starts) == 6  # one episode per goal, reset never called
    assert backend.episode_starts[0] == {} and backend.episode_starts[1]  # the inventory carried over
    assert len(list((run_dir / "episodes").glob("D_life_g00?_*.jsonl"))) == 6
    assert not [p for p in (run_dir / "episodes").iterdir() if "#" in p.name or "/" in p.name]
    assert (run_dir / "store.json").exists() and (run_dir / "life_state.json").exists()
    store = SchemaStore.load(run_dir / "store.json")
    assert store.rules and {r.scope for r in store.rules.values()} == {config.world_id}
    assert {t.world_id for t in store.transitions} == {config.world_id}
    assert all(line["budget"] == max(30, 4 * line["plan_length"]) for line in lines)
    assert sum(1 for message in backend.said if message.startswith("Goal:")) == 6
    assert all(len(message) <= 240 for message in backend.said)


def test_the_goal_budget_is_four_times_the_plan_when_that_is_more_than_30(tmp_path, monkeypatch):
    monkeypatch.setattr(curriculum, "SEED_FRONTIER", ("stone_pickaxe",))
    config = make_config(tmp_path, max_goals=1)
    run_life(FakeLifeBackend(), make_llm(tmp_path), config)
    line = read_lines(config.run_dir / "life.jsonl")[0]
    assert line["plan_length"] > 7 and line["budget"] == 4 * line["plan_length"]
    start = next(e for e in read_lines(next((config.run_dir / "episodes").iterdir())) if e["kind"] == "episode_start")
    assert start["budget"] == line["budget"]


def test_the_goal_wall_clock_deadline_stops_a_goal_before_it_acts(tmp_path):
    backend = FakeLifeBackend()
    summary = run_life(backend, make_llm(tmp_path), make_config(tmp_path, max_goals=2, goal_wall_clock_s=0.0))
    lines = read_lines(tmp_path / "run" / "life.jsonl")
    assert [(line["outcome"], line["actions"]) for line in lines] == [("failed", 0)] * 2
    assert backend.calls == [] and summary["goals"] == 2


def test_the_life_wall_clock_stops_the_loop(tmp_path):
    summary = run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, wall_clock_s=0.0))
    assert (summary["goals"], summary["stop_reason"]) == (0, "wall_clock")


def test_resume_continues_the_same_world_and_goal_index(tmp_path):
    first = run_life(FakeLifeBackend(), make_llm(tmp_path), make_config(tmp_path, max_goals=2))
    asked_before = set(json.loads((tmp_path / "run" / "life_state.json").read_text())["asked"])

    asked_again = []

    class SpyLLM(LLM):
        def propose_prior_rules_v2(self, families, known_blocks, world_id):
            asked_again.extend(families)
            return super().propose_prior_rules_v2(families, known_blocks, world_id)

    config = LifeConfig(run_dir=tmp_path / "run", world_id="a-different-id", max_goals=4)
    second = run_life(FakeLifeBackend(), SpyLLM(mode="fake", cache_path=tmp_path / "c2.jsonl"), config, resume=True)

    assert second["world_id"] == first["world_id"] == "life-7-20260101-0000"
    assert [line["index"] for line in read_lines(tmp_path / "run" / "life.jsonl")] == [1, 2, 3, 4]
    assert not set(asked_again) & asked_before
    store = SchemaStore.load(tmp_path / "run" / "store.json")
    assert {t.world_id for t in store.transitions} == {"life-7-20260101-0000"}


def test_prior_calls_are_batched_and_never_repeat_a_family(tmp_path):
    batches = []

    class SpyLLM(LLM):
        def propose_prior_rules_v2(self, families, known_blocks, world_id):
            batches.append((list(families), list(known_blocks)))
            return super().propose_prior_rules_v2(families, known_blocks, world_id)

    run_life(FakeLifeBackend(), SpyLLM(mode="fake", cache_path=tmp_path / "c.jsonl"), make_config(tmp_path, max_goals=5))
    asked = [family for families, _ in batches for family in families]
    assert len(asked) == len(set(asked))
    assert set(curriculum.SEED_FRONTIER) <= set(batches[0][0])  # the first call: the seed frontier and the blocks
    assert {"#log", "stone", "coal_ore"} <= set(batches[0][0])
    assert len(batches) <= 4  # rounds, not one call per family


def test_the_prior_prompt_always_lists_the_known_blocks(tmp_path):
    prompts = []

    class SpyLLM(LLM):
        def _json_call(self, purpose, system, user, schema):
            prompts.append(user)
            return {"rules": []}

    llm = SpyLLM(mode="live", cache_path=tmp_path / "c.jsonl")
    llm.propose_prior_rules_v2(["stick"], [], "w1")
    llm.propose_prior_rules_v2(["stick"], ["#log", "stone"], "w1")
    assert "none observed yet" in prompts[0]
    assert "#log, stone" in prompts[1] and "none observed yet" not in prompts[1]
    assert all("oak" not in prompt for prompt in prompts)


def test_priors_are_chosen_by_family_and_scoped_to_the_world(tmp_path):
    rules = make_llm(tmp_path).propose_prior_rules_v2(["coal_ore", "iron_ingot"], [], "life-x")
    assert {rule.id for rule in rules} == {"mine_coal_ore", "smelt_iron_ingot"}  # yields iron_ingot, subject coal_ore
    smelt = next(rule for rule in rules if rule.kind == "smelt")
    assert smelt.scope == "life-x" and smelt.preconditions["station"] == "furnace"
    assert smelt.preconditions["fuel_any"] == ["coal", "charcoal", "#planks", "#log"]


def test_v2_prompts_never_assume_a_species():
    from schema_voyager.llm import REVISION_INSTRUCTIONS_V2, WORLD_MODEL_SYSTEM_V2

    for prompt in (WORLD_MODEL_SYSTEM_V2, REVISION_INSTRUCTIONS_V2):
        assert "oak" not in prompt.lower() and "wood is" not in prompt.lower()
    assert "A log of any species crafts 4 planks of that species" in WORLD_MODEL_SYSTEM_V2


# -- the spend cap ------------------------------------------------------------------


def _stub_response(input_tokens=1000, output_tokens=500):
    usage = SimpleNamespace(
        input_tokens=input_tokens, output_tokens=output_tokens, cache_creation_input_tokens=0, cache_read_input_tokens=0
    )
    return SimpleNamespace(
        stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=json.dumps({"rules": []}))]
    )


def test_a_live_call_is_refused_before_it_would_pass_the_cap(tmp_path, monkeypatch):
    llm = LLM(mode="live", cache_path=tmp_path / "c.jsonl", spend_cap_usd=0.165)
    created = []
    monkeypatch.setattr(llm, "_create", lambda *args: created.append(1) or _stub_response())

    llm.propose_prior_rules_v2(["stick"], [], "w1")
    assert llm.spent_usd == pytest.approx(1000 * 2e-6 + 500 * 10e-6)  # $2 and $10 per million tokens
    with pytest.raises(SpendCapReached):
        llm.propose_prior_rules_v2(["stone"], [], "w1")
    assert len(created) == 1 and llm.spend_cap_hit and issubclass(SpendCapReached, LLMError)


def test_fake_and_replayed_calls_cost_nothing(tmp_path):
    llm = make_llm(tmp_path, spend_cap_usd=0.0)
    llm.propose_prior_rules_v2(["stick"], [], "w1")
    assert llm.spent_usd == 0.0 and llm.total_tokens > 0


def test_the_life_stops_when_the_cap_would_be_passed(tmp_path, monkeypatch):
    llm = LLM(mode="live", cache_path=tmp_path / "c.jsonl")
    created = []
    monkeypatch.setattr(llm, "_create", lambda *args: created.append(1) or _stub_response())
    summary = run_life(FakeLifeBackend(), llm, make_config(tmp_path, spend_cap_usd=0.01))
    assert (summary["goals"], summary["stop_reason"], created) == (0, "spend_cap", [])


# -- the step loop: statuses --------------------------------------------------------


def scripted_check(statuses):
    """A check that answers with the next scripted status, then the fake real one."""
    queue = list(statuses)

    def check(rule, action, outcome):
        real = fake_check(rule, action, outcome)
        if not queue:
            return real
        status = queue.pop(0)
        return Check(status, real.predicted, real.observed, status, kind="quarantined" if status == "quarantined" else "exact")

    return check


def planks_run(tmp_path, monkeypatch, statuses, backend=None):
    backend = backend or FakeLifeBackend()
    llm = make_llm(tmp_path)
    store = prior_store(llm, "#planks", "#log")
    monkeypatch.setattr(agent, "check", scripted_check(statuses))
    episode = make_episode(backend, llm, "#planks", 4)
    result = run_schemavoyager(episode, store)
    return episode, store, llm, result


def test_a_quarantined_step_is_never_recorded_and_not_revised(tmp_path, monkeypatch):
    episode, store, llm, result = planks_run(tmp_path, monkeypatch, ["quarantined"])
    assert result.success
    # The mine happened but was quarantined, so only the craft is evidence.
    assert [t.action.kind for t in store.transitions] == ["craft"]
    assert len(events_of(episode, "quarantined")) == 1
    assert not [c for c in llm.calls if c.purpose == "revise_rule"]
    assert len(events_of(episode, "plan")) == 2  # replanned from the current state


def test_a_failed_step_is_not_recorded_and_replans(tmp_path, monkeypatch):
    episode, store, llm, result = planks_run(tmp_path, monkeypatch, ["failed"])
    assert result.success and [t.action.kind for t in store.transitions] == ["craft"]
    assert len(events_of(episode, "plan")) == 2 and not events_of(episode, "contradiction")


def test_a_stochastic_step_is_recorded_with_its_kind(tmp_path, monkeypatch):
    episode, store, llm, result = planks_run(tmp_path, monkeypatch, ["stochastic"])
    assert result.success
    assert [t.kind for t in store.transitions] == ["stochastic", "exact"]


def test_a_mismatch_is_recorded_and_makes_exactly_one_revision_call(tmp_path, monkeypatch):
    episode, store, llm, _ = planks_run(tmp_path, monkeypatch, ["mismatch"])
    assert [c.purpose for c in llm.calls].count("revise_rule") == 1
    assert store.transitions[0].action.kind == "mine"  # recorded
    assert len(events_of(episode, "contradiction")) == 1 and len(events_of(episode, "revision")) == 1
    assert store.demoted_in["w1"]  # demoted in this world
    assert any(rule.source == "revision" and rule.scope == "w1" for rule in store.rules.values())


def test_a_real_mismatch_in_a_changed_world_is_caught_and_revised_once(tmp_path):
    backend = FakeLifeBackend(coal_needs_stone=True)
    backend.inventory = {"wooden_pickaxe": 1}
    llm = make_llm(tmp_path)
    store = prior_store(llm, "coal", "coal_ore")
    episode = make_episode(backend, llm, "coal", 1)
    run_schemavoyager(episode, store)
    assert [c.purpose for c in llm.calls].count("revise_rule") == 1
    assert len(events_of(episode, "contradiction")) == 1


def test_the_before_step_hook_sees_every_step(tmp_path):
    backend, llm = FakeLifeBackend(), make_llm(tmp_path)
    seen = []
    episode = make_episode(backend, llm, "#planks", 4)
    run_schemavoyager(episode, prior_store(llm, "#planks", "#log"),
                      before_step=lambda ep, step, plan: seen.append((step.action.kind, len(plan))))
    assert seen == [("mine", 2), ("craft", 2)]


def test_the_goal_is_announced_with_the_first_plan_only(tmp_path, monkeypatch):
    backend = FakeLifeBackend()
    monkeypatch.setattr(agent, "check", scripted_check(["failed", "failed"]))
    llm = make_llm(tmp_path)
    run_schemavoyager(make_episode(backend, llm, "#planks", 4), prior_store(llm, "#planks", "#log"))
    assert sum(m.startswith("Goal:") for m in backend.said) == 1  # three plans, one narrated


def test_a_chat_burst_is_three_messages_of_240_characters(tmp_path):
    backend = FakeLifeBackend()
    episode = make_episode(backend, make_llm(tmp_path), "stick")
    agent._say(episode, ["x" * 500] * 5)
    assert len(backend.said) == 3 and all(len(m) == 240 for m in backend.said)


# -- the natural explore policy and travel ------------------------------------------


def test_travel_is_its_own_event_before_the_step_and_never_in_a_transition(tmp_path):
    backend, llm = FakeLifeBackend(), make_llm(tmp_path)
    store = prior_store(llm, "#planks", "#log")
    episode = make_episode(backend, llm, "#planks", 4)
    run_schemavoyager(episode, store)

    kinds = [e["kind"] for e in episode.log.events if e["kind"] in ("travel", "step")]
    assert kinds[:2] == ["travel", "step"]  # the mine walked, the craft did not
    assert kinds.count("travel") == 1
    mine_step = next(e for e in episode.log.events if e["kind"] == "step")
    assert mine_step["travel"]["distance"] == 6.0 and mine_step["events"] == []
    for transition in store.transitions:
        assert "dirt" not in json.dumps(transition.observed) and "distance" not in transition.observed
    assert episode.inventory.get("dirt")  # the pickup is real, and tolerated


def test_not_found_explores_toward_the_nearest_hit_up_to_four_times(tmp_path):
    backend = FakeLifeBackend()
    backend.fail_with = ["not_found"] * 9
    backend.look_result = {"N": {"oak_log": {"distance": 40.0}}, "SE": {"birch_log": {"distance": 20.0}}}
    episode = make_episode(backend, make_llm(tmp_path), "#log")
    outcome = episode.act(Action("mine", "#log"))
    explores = [call for call in backend.calls if call.kind == "explore"]
    assert not outcome.ok and len(explores) == 4
    assert {call.direction for call in explores} == {"SE"}
    assert backend.looks[0] == ["birch_log", "oak_log"]  # the target's raw members


def test_a_hazard_is_searched_like_not_found_and_blind_search_walks_the_compass(tmp_path):
    backend = FakeLifeBackend()
    backend.fail_with = ["hazard", "not_found", "hazard", "not_found", "hazard"]
    episode = make_episode(backend, make_llm(tmp_path), "#log")
    assert not episode.act(Action("mine", "#log")).ok
    explores = [call.direction for call in backend.calls if call.kind == "explore"]
    assert explores == ["up", *DIRECTIONS[:3]]  # v2.1: back to open sky first
    assert episode.log.events[-1]["kind"] == "step"


def test_v1_episodes_keep_their_three_spiral_explores(tmp_path):
    backend = FakeLifeBackend()
    backend.fail_with = ["not_found"] * 9
    task = Task("t", "train", "oak_log", 1, {}, (), 40, "v1")
    episode = Episode("e", "D", task, 1, "w", backend, make_llm(tmp_path), EpisodeLog(None, "t"))
    episode.act(Action("mine", "oak_log"))
    explores = [call for call in backend.calls if call.kind == "explore"]
    assert len(explores) == 3 and all(call.direction is None for call in explores) and not backend.looks


# -- the planner --------------------------------------------------------------------


def smelting_rules(tmp_path):
    store = prior_store(make_llm(tmp_path), "iron_ingot", "coal", "coal_ore", "#planks", "#log", "stone", "furnace")
    return store.usable_rules("w1")


def test_smelt_plans_name_the_ingredient_fuel_and_runs(tmp_path):
    rules = smelting_rules(tmp_path)
    plan = make_plan("iron_ingot", 2, {"raw_iron": 2, "furnace": 1, "coal": 1}, rules)
    assert [step.action for step in plan] == [
        Action("smelt", "iron_ingot", times=2, ingredient="raw_iron", fuel="coal")
    ]
    assert plan[0].expected_inventory == {"furnace": 1, "iron_ingot": 2}


def test_coal_covers_eight_items_and_planks_one_and_a_half(tmp_path):
    rules = smelting_rules(tmp_path)
    nine = make_plan("iron_ingot", 9, {"raw_iron": 9, "furnace": 1, "coal": 2}, rules)
    assert nine[-1].action.fuel == "coal" and nine[-1].action.times == 9 and len(nine) == 1
    assert nine[-1].expected_inventory.get("coal", 0) == 0  # 2 coal used: ceil(9 / 8)
    eight = make_plan("iron_ingot", 8, {"raw_iron": 8, "furnace": 1, "coal": 1}, rules)
    assert eight[-1].expected_inventory.get("coal", 0) == 0
    planks = make_plan("iron_ingot", 3, {"raw_iron": 3, "furnace": 1, "#planks": 3}, rules)
    assert (planks[-1].action.fuel, len(planks)) == ("#planks", 1)  # a fuel already held wins
    assert planks[-1].expected_inventory.get("#planks", 0) == 1  # ceil(3 / 1.5) = 2 planks used


def test_a_smelt_plan_gathers_its_furnace_and_fuel(tmp_path):
    rules = smelting_rules(tmp_path)
    plan = make_plan("iron_ingot", 1, {"raw_iron": 1, "#cobblestone": 8, "crafting_table": 1, "#log": 1}, rules)
    assert [step.action.kind for step in plan] == ["craft", "smelt"]
    assert plan[0].action.target == "furnace" and plan[1].action.fuel == "#log"


# -- trace and evaluator ------------------------------------------------------------


def test_trace_formats_the_new_events():
    log = EpisodeLog(None, "g001 stick")
    assert "travel 6 blocks, dug 1, picked up 1" in log._format("travel", {"distance": 6.0, "dug": {"dirt": 1}, "picked_up": {"dirt": 1}})
    assert "g003" in log._format("goal", {"index": 3, "item": "stick", "count": 1, "reason": "why", "budget": 30})
    assert "quarantined" in log._format("quarantined", {"reason": "damage"})
    step = {"index": 4, "action": "mine #log with hand", "ok": True, "error": None, "observed": {"drops": {}}}
    assert "stochastic" in log._format("step", {**step, "check": "stochastic"})
    assert "quarantined" in log._format("step", {**step, "check": "quarantined", "ok": False, "error": "quarantined"})


def test_stochastic_and_quarantined_steps_are_not_failures():
    step = {"action_obj": {"kind": "mine", "target": "short_grass", "tool": None}, "ok": True, "error": None,
            "block_removed": True, "drops": {}}
    assert failure_signature(step) is not None
    assert failure_signature({**step, "check": "stochastic"}) is None
    assert failure_signature({**step, "check": "quarantined", "ok": False, "error": "quarantined"}) is None


# -- the guard ----------------------------------------------------------------------


@pytest.mark.parametrize("env", [
    {}, {"SV2_BOT_PORT": "3200"}, {"SV2_RCON_PORT": "25576"},
    {"SV2_BOT_PORT": "3000", "SV2_RCON_PORT": "25576"},
    {"SV2_BOT_PORT": "3200", "SV2_RCON_PORT": "25575"},
])
def test_the_guard_refuses_missing_and_demo_ports(env):
    with pytest.raises(SystemExit) as refused:
        check_instance_ports(env)
    assert "SV2_" in str(refused.value)


def test_the_guard_accepts_an_instance():
    assert check_instance_ports({"SV2_BOT_PORT": "3200", "SV2_RCON_PORT": "25576"}) == (3200, 25576)
    assert check_instance_ports({"SV2_BOT_PORT": "3210", "SV2_RCON_PORT": "25577"}) == (3210, 25577)


# -- v1 stays intact ----------------------------------------------------------------


def test_the_v1_llm_constants_are_unchanged(tmp_path, monkeypatch):
    source = subprocess.run(
        ["git", "show", "main:src/schema_voyager/llm.py"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    old_file = tmp_path / "llm_v1.py"
    old_file.write_text(source)
    spec = importlib.util.spec_from_file_location("schema_voyager._llm_v1", old_file)
    old = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, old)  # dataclasses looks its module up here
    spec.loader.exec_module(old)

    from schema_voyager import llm

    for name in ("WORLD_MODEL_SYSTEM", "REVISION_INSTRUCTIONS", "WORLD_MAY_DIFFER", "BASELINE_SYSTEM", "RULES_SCHEMA",
                 "PLAN_SCHEMA"):
        assert getattr(llm, name) == getattr(old, name), name


def test_the_life_modules_hard_code_no_world_vocabulary():
    banned = ("oak_", "birch_", "spruce_", "GROUND_Y", "ARENA_RADIUS", "from .layouts import")
    for module in ("life.py", "curriculum.py", "agent.py", "planner.py"):
        text = (ROOT / "src" / "schema_voyager" / module).read_text()
        assert not [word for word in banned if word in text], module
