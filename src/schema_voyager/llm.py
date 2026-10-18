"""Claude calls for SchemaVoyager: prior rules, ranked revisions, and the
skill-only baseline's planner. Every call is metered from API usage and
recorded so a run can be replayed without spending tokens.

Modes: "live" calls the API and records; "replay" only reads recorded
responses (and reports their original usage); "fake" is a deterministic
offline stand-in for tests and dry runs.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import blueprint, normalize
from .models import Action, GoalKind, GoalSpec, Rule, Unsupported

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
DATA_DIR = Path(__file__).parent / "data"

WORLD_MODEL_SYSTEM = """You are the world-model module of an agent playing Minecraft Java Edition 1.21 in survival mode.
You write rules as STRIPS-style operators that predict the exact, server-decided outcome of one action.

Rule format:
- action_type "mine": subject is the block id. tool_any lists every tool with which the block drops the items in yields; use an empty list if a bare hand works. yields is the item entities dropped by mining one block. consumes is empty and station is "".
- action_type "craft": subject is the item id. consumes is what one recipe run uses up, yields is what one run produces, station is "crafting_table" when the recipe needs the 3x3 grid, otherwise "".
- Use item and block ids without the "minecraft:" prefix. Wood is always oak (oak_log, oak_planks).
- rationale is one short sentence."""

# Every system gets this after its first surprise (D) or failed plan (B, B+),
# so the comparison is between architectures, not prompts.
WORLD_MAY_DIFFER = "This world's rules may differ from vanilla Minecraft, and nobody will tell the agent how."

REVISION_INSTRUCTIONS = f"""A rule the agent relied on just made a wrong prediction in a world it has not seen before. {WORLD_MAY_DIFFER}

Propose up to 3 alternative rules for the same action and subject, ranked from most to least likely, each of which explains the counterexample. Each must be a complete rule in the same format. Prefer alternatives the agent can test with items it knows how to obtain."""

BASELINE_SYSTEM = """You control a Minecraft Java Edition 1.21 bot in survival mode through three actions:
- mine(target, tool): walk to the nearest visible block of type `target`, mine it holding `tool` ("" for a bare hand), and pick up what drops.
- craft(target, times): run the recipe for item `target` `times` times. A crafting table from the inventory is placed and picked back up automatically when the recipe needs one.
- explore(): walk 10 blocks outward to reveal more of the area.

Write a complete action sequence that reaches the goal from the current state, and a one-sentence summary of the plan. Use item and block ids without the "minecraft:" prefix. Wood is oak (oak_log, oak_planks). Set unused fields to "" or 1."""

_COUNT = {
    "type": "object",
    "properties": {"item": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["item", "count"],
    "additionalProperties": False,
}
_RULE = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": ["mine", "craft"]},
        "subject": {"type": "string"},
        "tool_any": {"type": "array", "items": {"type": "string"}},
        "consumes": {"type": "array", "items": _COUNT},
        "station": {"type": "string"},
        "yields": {"type": "array", "items": _COUNT},
        "rationale": {"type": "string"},
    },
    "required": ["action_type", "subject", "tool_any", "consumes", "station", "yields", "rationale"],
    "additionalProperties": False,
}
RULES_SCHEMA = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": _RULE}},
    "required": ["rules"],
    "additionalProperties": False,
}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["mine", "craft", "explore"]},
                    "target": {"type": "string"},
                    "tool": {"type": "string"},
                    "times": {"type": "integer"},
                },
                "required": ["kind", "target", "tool", "times"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "actions"],
    "additionalProperties": False,
}

# -- v2 (a continuous life in a natural world) ---------------------------------
# New constants: the v1 ones above stay byte-identical so v1's recorded
# responses still replay.

# Sonnet 5.5 pricing, per million tokens.
INPUT_USD_PER_MTOK = 2.0
OUTPUT_USD_PER_MTOK = 10.0
MAX_OUTPUT_TOKENS = 16000

WORLD_MODEL_SYSTEM_V2 = """You are the world-model module of an agent playing Minecraft Java Edition 1.21.1 in survival mode, in a naturally generated world.
You write rules as STRIPS-style operators that predict the exact, server-decided outcome of one action. Assume vanilla rules, with no Fortune and no Silk Touch.

Names. Use item and block ids without the "minecraft:" prefix. Several ids share one name:
- Groups start with "#" and stand for every species of one kind of item or block: #log (every log and wood block), #planks, #leaves, #sapling, #cobblestone (cobblestone and the things that stand in for it in stone tools and furnaces). A log of any species crafts 4 planks of that species: write consumes #log 1, yields #planks 4. Never name one species.
- A variant family is written by its base id and covers its deepslate or other variants: coal_ore also means deepslate_coal_ore, iron_ore also means deepslate_iron_ore. Write coal_ore, not the variant.
- stone and deepslate are different blocks with different drops.

Rule format:
- action_type "mine": subject is the block, group or family. tool_any lists every tool with which the block drops the items in yields; use an empty list if a bare hand works. yields is the item entities dropped by mining one block. consumes is empty, station is "" and fuel_any is empty.
- action_type "craft": subject is the item id (or group). consumes is what one recipe run uses up, yields is what one run produces, station is "crafting_table" when the recipe needs the 3x3 grid, otherwise "". fuel_any is empty.
- action_type "smelt": subject is the item produced. consumes is the one input item per item smelted, station is "furnace", fuel_any lists every item that works as fuel, and yields is what one smelted item produces.
- check is "exact" when the drops or yields never vary. Use "stochastic" only for a mine rule whose drop count is random (gravel, leaves, grass, redstone ore); then allowed lists the [min, max] count of each item one block can drop. For exact rules, allowed is empty.
- rationale is one short sentence."""

REVISION_INSTRUCTIONS_V2 = f"""A rule the agent relied on just made a wrong prediction in this world. {WORLD_MAY_DIFFER}

Propose up to 3 alternative rules for the same action and subject, ranked from most to least likely, each of which explains the counterexample. Each must be a complete rule in the same format, using the same group and family names as the failed rule and the counterexample. Prefer alternatives the agent can test with items it knows how to obtain."""

CURRICULUM_SYSTEM = """You choose what an agent playing Minecraft Java Edition 1.21.1 should try to obtain next, to learn how the world's crafting, mining and smelting rules work. Pick only from the candidates you are given, using their exact names, ordered from most to least useful next."""

_COUNT_V2 = {
    "type": "object",
    "properties": {"item": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["item", "count"],
    "additionalProperties": False,
}
_RANGE_V2 = {
    "type": "object",
    "properties": {"item": {"type": "string"}, "min": {"type": "integer"}, "max": {"type": "integer"}},
    "required": ["item", "min", "max"],
    "additionalProperties": False,
}
_RULE_V2 = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": ["mine", "craft", "smelt"]},
        "subject": {"type": "string"},
        "tool_any": {"type": "array", "items": {"type": "string"}},
        "consumes": {"type": "array", "items": _COUNT_V2},
        "station": {"type": "string"},
        "fuel_any": {"type": "array", "items": {"type": "string"}},
        "yields": {"type": "array", "items": _COUNT_V2},
        "check": {"type": "string", "enum": ["exact", "stochastic"]},
        "allowed": {"type": "array", "items": _RANGE_V2},
        "rationale": {"type": "string"},
    },
    "required": [
        "action_type", "subject", "tool_any", "consumes", "station", "fuel_any", "yields", "check", "allowed",
        "rationale",
    ],
    "additionalProperties": False,
}
RULES_SCHEMA_V2 = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": _RULE_V2}},
    "required": ["rules"],
    "additionalProperties": False,
}
ITEMS_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    "required": ["items"],
    "additionalProperties": False,
}
FAKE_EXPANSION_SIZE = 3

# v3.1e: parsing what the user typed.
SUPPORTED_GOAL_KINDS: frozenset[GoalKind] = frozenset({"obtain", "build"})  # M3 adds kill and guard
MAX_GOAL_COUNT = 256

PARSE_GOAL_SYSTEM = """You turn a request typed to a Minecraft Java Edition 1.21.1 bot into one goal.
- kind "obtain": get items. item is a vanilla item id without the "minecraft:" prefix, or "#planks" / "#log" for any kind of wood. count is how many MORE to get, whatever the inventory holds: "get me 20 torches" while holding 8 means count 20.
- kind "build": build something. kind "kill": kill mobs (target is the mob id, count how many). kind "guard": guard a player (target is the player name).
- kind "unsupported": anything else. reply is one short, friendly sentence saying what you can do. Never use "#" in a reply.
Set unused fields to "" or 1."""

PARSE_GOAL_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["obtain", "build", "kill", "guard", "unsupported"]},
        "item": {"type": "string"},  # obtain: a vanilla item id, or "#planks" / "#log" for any wood; else ""
        "count": {"type": "integer"},  # obtain: how many more (D5); kill: how many; else 1
        "target": {"type": "string"},  # kill: mob id; guard: player name; else ""
        "reply": {"type": "string"},  # unsupported: a short, friendly reply saying what it can do; else ""
    },
    "required": ["kind", "item", "count", "target", "reply"],
    "additionalProperties": False,
}

# v3.4b: Claude designs a blueprint (v3.4a "Claude's form"), and the errors go back to it.
DESIGN_RETRIES = 2

DESIGN_BLUEPRINT_SYSTEM = """You design a small building for a Minecraft Java Edition 1.21.1 bot to build, as a blueprint.
- layers[y][z][x]: layers go bottom-up, rows go north to south, characters go west to east. size is [width (x), height (y), depth (z)]; every layer has `depth` rows of `width` characters.
- Layer 0 stands on the ground.
- palette lists one-character keys and their blocks. Use "." for every cell that must be air, the interior included: a space keeps whatever grass or flowers were there. Use a space only where anything goes, such as roof corners.
- Use "#planks" and "#log" for wood: the bot places whichever species it holds.
- A door is two cells of the same door block in one column, with a full block (or the ground, in layer 0) below it.
- A torch has a full block below it or beside it. Glass, panes, doors and torches are not full blocks.
- Every other block connects to the ground face to face, through other blocks: nothing floats.
- Use glass only if windows are asked for, and torches only if light is asked for.
- Keep within the limits the request gives. Give a short name.
Example, a 5 x 4 x 5 hut with log corners, a door in the south wall and a flat roof whose corners are left as they are:
{"name": "small hut", "size": [5, 4, 5], "palette": [{"key": "L", "block": "#log"}, {"key": "P", "block": "#planks"}, {"key": "D", "block": "oak_door"}], "layers": [["LPPPL", "P...P", "P...P", "P...P", "LPDPL"], ["LPPPL", "P...P", "P...P", "P...P", "LPDPL"], ["LPPPL", "P...P", "P...P", "P...P", "LPPPL"], [" PPP ", "PPPPP", "PPPPP", "PPPPP", " PPP "]]}"""

_BLOCK_ENUM = sorted(blueprint.PLACEABLE) + list(blueprint.GROUPS) + ["air", "any"]

BLUEPRINT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "size": {"type": "array", "items": {"type": "integer"}},  # [width, height, depth]
        "palette": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "block": {"type": "string", "enum": _BLOCK_ENUM}},
                "required": ["key", "block"],
                "additionalProperties": False,
            },
        },
        "layers": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
    },
    "required": ["name", "size", "palette", "layers"],
    "additionalProperties": False,
}

FAKE_HUT = {
    "name": "small hut",
    "size": [5, 4, 5],
    "palette": {"L": "#log", "P": "#planks", "D": "oak_door", "T": "torch"},
    "layers": [
        ["LPPPL", "P...P", "P...P", "P...P", "LPDPL"],
        ["LPPPL", "PT..P", "P...P", "P...P", "LPDPL"],
        ["LPPPL", "P...P", "P...P", "P...P", "LPPPL"],
        [" PPP ", "PPPPP", "PPPPP", "PPPPP", " PPP "],
    ],
}


@dataclass(frozen=True)
class BlueprintLimits:
    max_size: tuple[int, int, int] = (7, 4, 7)  # a roof 4 up is one the bot can reach and drop from
    max_placed: int = 120  # placed cells: every non-air, non-any cell, both door cells counted


class BlueprintDesignError(Exception):
    """Claude's blueprint still had errors after the retries (not an LLMError: the parse turns it into Unsupported)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors[0] if errors else "no blueprint")
        self.errors = errors


_TRY_FETCH = 'I can fetch items: try "get me 10 torches".'
_DEFAULT_UNSUPPORTED_REPLY = 'I can only fetch items for now: try "get me 10 torches".'
_UNSUPPORTED_KIND_REPLIES = {
    "kill": f"I can't hunt yet. {_TRY_FETCH}",
    "guard": f"I can't guard anyone yet. {_TRY_FETCH}",
}
_MAX_REPLY_CHARS = 240


class LLMError(RuntimeError):
    pass


class SpendCapReached(LLMError):
    """A live call was refused because its worst case would pass the spend cap."""


@dataclass
class CallRecord:
    purpose: str
    input_tokens: int
    output_tokens: int
    replayed: bool = False
    live: bool = False  # a real API call: the only kind that costs money

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        if not self.live:
            return 0.0
        return (self.input_tokens * INPUT_USD_PER_MTOK + self.output_tokens * OUTPUT_USD_PER_MTOK) / 1e6


@dataclass
class LLM:
    mode: str = "live"
    model: str = field(default_factory=lambda: os.environ.get("SCHEMA_VOYAGER_MODEL", DEFAULT_MODEL))
    effort: str = field(default_factory=lambda: os.environ.get("SCHEMA_VOYAGER_EFFORT", DEFAULT_EFFORT))
    cache_path: Path = Path("runs/llm_cache.jsonl")
    calls: list[CallRecord] = field(default_factory=list)
    # v2: live calls are refused once their worst case would pass the cap (None: no cap).
    # `carried_usd` is spend from earlier sessions of a resumed life.
    spend_cap_usd: float | None = None
    carried_usd: float = 0.0
    spend_cap_hit: bool = False

    def __post_init__(self) -> None:
        self._client = None
        self._cache = _load_cache(self.cache_path) if self.mode != "fake" else {}

    # -- metering ---------------------------------------------------------

    @property
    def total_tokens(self) -> int:
        return sum(call.tokens for call in self.calls)

    @property
    def spent_usd(self) -> float:
        return self.carried_usd + sum(call.cost_usd for call in self.calls)

    # -- world model ------------------------------------------------------

    def propose_prior_rules(self, blocks: list[str], items: list[str]) -> list[Rule]:
        if self.mode == "fake":
            payload = _fixture_payload()
            self._meter_fake("prior_rules", json.dumps(payload))
        else:
            user = (
                f"The agent can mine these blocks: {', '.join(blocks)}.\n"
                f"It can craft these items: {', '.join(items)}.\n"
                "Write exactly one rule for each of those actions from your knowledge of vanilla Minecraft."
            )
            payload = self._json_call("prior_rules", WORLD_MODEL_SYSTEM, user, RULES_SCHEMA)
        return [_to_rule(raw, source="prior", scope="train") for raw in payload["rules"]]

    def revise_rule(
        self,
        rule: Rule,
        evidence_summary: str,
        counterexample: dict[str, Any],
        known_items: list[str],
        world_id: str,
    ) -> list[Rule]:
        if self.mode == "fake":
            payload = {"rules": _fake_revisions(rule, counterexample)}
            self._meter_fake("revise_rule", json.dumps(payload))
        else:
            user = (
                f"{REVISION_INSTRUCTIONS}\n\n"
                f"Rule that failed:\n{json.dumps(_from_rule(rule), indent=2)}\n\n"
                f"Evidence for it from training worlds:\n{evidence_summary}\n\n"
                f"Counterexample in the new world:\n{json.dumps(counterexample, indent=2)}\n\n"
                f"Items the agent knows how to obtain: {', '.join(known_items)}."
            )
            payload = self._json_call("revise_rule", WORLD_MODEL_SYSTEM, user, RULES_SCHEMA)

        revisions = []
        for rank, raw in enumerate(payload["rules"][:3]):
            if raw["action_type"] != rule.kind or raw["subject"] != rule.subject:
                continue
            revision = _to_rule(raw, source="revision", scope=world_id)
            revision.id = f"{rule.id}.rev{rank + 1}@{world_id}"
            revision.rank = rank
            revision.revision_of = rule.id
            revisions.append(revision)
        return revisions

    # -- v2: a continuous life in a natural world -------------------------

    def propose_prior_rules_v2(self, families: list[str], known_blocks: list[str], world_id: str) -> list[Rule]:
        """Prior rules for a batch of families (blocks to mine, items to craft or smelt),
        normalized, with `scope=world_id`. `known_blocks` are the block families the
        agent has seen so far; an empty list is shown as "none observed yet"."""
        user = (
            f"Block families the agent has seen near it so far: {', '.join(known_blocks) or 'none observed yet'}.\n"
            f"Write the vanilla rules for these names: {', '.join(families)}.\n"
            "For a block, write the rule that mines it. For an item, write the rule that crafts or smelts it. "
            "Write exactly one rule per name, and also one rule for each raw ingredient an item needs "
            "that is not a name above, so the agent can plan from blocks it can mine."
        )
        if self.mode == "fake":
            payload = _fake_priors_v2(families)
            self._meter_fake("prior_rules", user + json.dumps(payload))
        else:
            payload = self._json_call("prior_rules", WORLD_MODEL_SYSTEM_V2, user, RULES_SCHEMA_V2)
        return [_to_rule_v2(raw, source="prior", scope=world_id) for raw in payload["rules"]]

    def revise_rule_v2(
        self,
        rule: Rule,
        evidence_summary: str,
        counterexample: dict[str, Any],
        known_items: list[str],
        world_id: str,
    ) -> list[Rule]:
        """Up to 3 ranked alternatives for a contradicted rule, in normalized names."""
        if self.mode == "fake":
            payload = {"rules": _fake_revisions_v2(rule, counterexample)}
            self._meter_fake("revise_rule", json.dumps(payload))
        else:
            user = (
                f"{REVISION_INSTRUCTIONS_V2}\n\n"
                f"Rule that failed:\n{json.dumps(_from_rule_v2(rule), indent=2)}\n\n"
                f"Evidence for it so far in this world:\n{evidence_summary}\n\n"
                f"Counterexample:\n{json.dumps(counterexample, indent=2)}\n\n"
                f"Items the agent knows how to obtain: {', '.join(known_items)}."
            )
            payload = self._json_call("revise_rule", WORLD_MODEL_SYSTEM_V2, user, RULES_SCHEMA_V2)

        revisions = []
        for rank, raw in enumerate(payload["rules"][:3]):
            if raw["action_type"] != rule.kind or normalize.family(raw["subject"].removeprefix("minecraft:")) != rule.subject:
                continue
            revision = _to_rule_v2(raw, source="revision", scope=world_id)
            revision.id = f"{rule.id}.rev{rank + 1}@{world_id}"
            revision.rank = rank
            revision.revision_of = rule.id
            revisions.append(revision)
        return revisions

    def repair_rule_v2(self, rule: Rule, dead_items: list[str], obtainable: list[str], world_id: str) -> list[Rule]:
        """Up to 3 ranked alternatives for a rule that needs items nothing the agent knows
        produces (so it can never be executed, and never contradicted)."""
        if self.mode == "fake":
            payload = {"rules": _fake_repairs_v2(rule)}
            self._meter_fake("repair_rule", json.dumps(payload))
        else:
            user = (
                f"This rule needs items the agent cannot obtain by any rule it knows:\n"
                f"{json.dumps(_from_rule_v2(rule), indent=2)}\n\n"
                f"Items it cannot obtain: {', '.join(dead_items)}.\n"
                f"Items the agent can obtain: {', '.join(obtainable) or 'none yet'}.\n\n"
                f"Write up to 3 alternative rules for the same action ({rule.kind}) and the same subject ({rule.subject}), "
                "best first. Each must be a complete rule that only uses obtainable items, or items obtainable by "
                "mining, crafting or smelting from them, and gets a one-sentence rationale."
            )
            payload = self._json_call("repair_rule", WORLD_MODEL_SYSTEM_V2, user, RULES_SCHEMA_V2)

        repairs = []
        for rank, raw in enumerate(payload["rules"][:3]):
            if raw["action_type"] != rule.kind or normalize.family(raw["subject"].removeprefix("minecraft:")) != rule.subject:
                continue
            repair = _to_rule_v2(raw, source="revision", scope=world_id)
            repair.id = f"{rule.id}.fix{rank + 1}@{world_id}"
            repair.rank = rank
            repair.revision_of = rule.id
            repairs.append(repair)
        return repairs

    def expand_frontier(self, frontier: list[str], candidates: list[str], certified: list[str]) -> list[str]:
        """Up to 3 items to add to the curriculum's frontier. `candidates` is the part
        of the whitelist not yet in it, and nothing outside `candidates` is returned."""
        if self.mode == "fake":
            chosen = candidates[:FAKE_EXPANSION_SIZE]
            self._meter_fake("expand_frontier", json.dumps([frontier, candidates, chosen]))
            return chosen
        user = (
            f"The agent is already working on: {', '.join(frontier)}.\n"
            f"It has certified rules for: {', '.join(certified) or 'nothing yet'}.\n"
            f"Candidates: {', '.join(candidates)}.\n"
            f"Choose up to {FAKE_EXPANSION_SIZE} candidates to try next."
        )
        payload = self._json_call("expand_frontier", CURRICULUM_SYSTEM, user, ITEMS_SCHEMA)
        return [item for item in dict.fromkeys(payload["items"]) if item in candidates][:FAKE_EXPANSION_SIZE]

    # -- skill-only baseline ---------------------------------------------

    def baseline_plan(self, prompt: str, fake_plan: list[Action] | None = None) -> tuple[str, list[Action]]:
        if self.mode == "fake":
            actions = fake_plan or []
            self._meter_fake("baseline_plan", prompt + json.dumps([a.to_dict() for a in actions]))
            return "fake planner", actions
        payload = self._json_call("baseline_plan", BASELINE_SYSTEM, prompt, PLAN_SCHEMA)
        actions = [
            Action(
                kind=raw["kind"],
                target=raw["target"] or None,
                tool=(raw["tool"] or None) if raw["kind"] == "mine" else None,
                times=max(1, raw["times"]) if raw["kind"] == "craft" else 1,
            )
            for raw in payload["actions"]
        ]
        return payload["summary"], actions

    # -- goals (v3.1e) -----------------------------------------------------

    def parse_goal(self, text: str, inventory: dict[str, int], known_items: frozenset[str], *,
                   goal_id: str) -> GoalSpec | Unsupported:
        """Turn typed text into an obtain or build goal, or say why the bot can't do it. `known_items` is
        what an obtain item may be (besides the groups); LLMError from the call propagates."""
        if self.mode == "fake":
            self._meter_fake("parse_goal", text)
            kind, item, count = _fake_parse_goal(text, known_items)
            reply = ""
        else:
            user = f"Request: {text}\nInventory: {json.dumps(inventory, sort_keys=True)}"
            payload = self._json_call("parse_goal", PARSE_GOAL_SYSTEM, user, PARSE_GOAL_SCHEMA)
            kind, item, count, reply = payload["kind"], payload["item"], payload["count"], payload["reply"]
        if kind == "build":
            try:
                blueprint = self.design_blueprint(text)
            except BlueprintDesignError:
                return Unsupported("I couldn't design that. Try \"build a small wooden house with a door\".")
            return GoalSpec(id=goal_id, kind="build", source="user", text=text, blueprint=blueprint)
        return _checked_goal(goal_id, text, kind, item, count, reply, known_items)

    def design_blueprint(self, text: str, limits: BlueprintLimits = BlueprintLimits()) -> dict[str, Any]:
        """Turn a build request into a valid blueprint within `limits`, giving Claude the errors of a bad answer
        up to DESIGN_RETRIES times; BlueprintDesignError after that. LLMError from a call propagates."""
        if self.mode == "fake":
            self._meter_fake("design_blueprint", text)
            return copy.deepcopy(FAKE_HUT)

        width, height, depth = limits.max_size
        request = (f"Request: {text}\nLimits: at most {width} wide (x), {height} high (y) and {depth} deep (z); "
                   f"at most {limits.max_placed} placed cells (every cell that is not \".\" or a space).")
        user = request
        for attempt in range(DESIGN_RETRIES + 1):
            answer = self._json_call("design_blueprint", DESIGN_BLUEPRINT_SYSTEM, user, BLUEPRINT_SCHEMA)
            bp, errors = _blueprint_from_answer(answer, limits)
            if not errors:
                return bp
            user = (f"{request}\nYour previous answer:\n{json.dumps(answer)}\n"
                    "It has these errors, fix them and answer again:\n" + "\n".join(f"- {error}" for error in errors))
        raise BlueprintDesignError(errors)

    # -- transport --------------------------------------------------------

    def _json_call(self, purpose: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = _cache_key(self.model, self.effort, system, user, schema)
        cached = self._cache.get(key)
        if self.mode == "replay":
            if cached is None:
                raise LLMError(f"no recorded response for this {purpose} call; run with --llm live first")
            self.calls.append(CallRecord(purpose, cached["input_tokens"], cached["output_tokens"], replayed=True))
            return cached["payload"]

        self._check_spend_cap(purpose, system + user + json.dumps(schema))
        response = self._create(system, user, schema)
        if response.stop_reason == "refusal":
            raise LLMError(f"{purpose} call was refused: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{purpose} call hit max_tokens")
        text = next(block.text for block in response.content if block.type == "text")
        payload = json.loads(text)

        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0)
        )
        self.calls.append(CallRecord(purpose, input_tokens, usage.output_tokens, live=True))
        record = {"key": key, "purpose": purpose, "payload": payload,
                  "input_tokens": input_tokens, "output_tokens": usage.output_tokens}
        self._cache[key] = record
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return payload

    def _check_spend_cap(self, purpose: str, prompt: str) -> None:
        """Refuse a live call whose worst case (a generous input estimate, a full
        max_tokens reply) would take total spend past the cap."""
        if self.spend_cap_usd is None:
            return
        worst_case = (len(prompt) / 3 * INPUT_USD_PER_MTOK + MAX_OUTPUT_TOKENS * OUTPUT_USD_PER_MTOK) / 1e6
        if self.spent_usd + worst_case > self.spend_cap_usd:
            self.spend_cap_hit = True
            raise SpendCapReached(
                f"{purpose} call could pass the ${self.spend_cap_usd:.2f} cap "
                f"(spent ${self.spent_usd:.2f}, this call up to ${worst_case:.2f})"
            )

    def _create(self, system: str, user: str, schema: dict[str, Any]):
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        try:
            return self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except anthropic.AuthenticationError as error:
            raise LLMError("Anthropic API key missing or invalid; set ANTHROPIC_API_KEY in .env") from error
        except anthropic.APIStatusError as error:
            raise LLMError(f"Anthropic API error {error.status_code}: {error.message}") from error
        except anthropic.APIConnectionError as error:
            raise LLMError(f"could not reach the Anthropic API: {error}") from error

    def _meter_fake(self, purpose: str, text: str) -> None:
        estimate = max(1, len(text) // 4)
        self.calls.append(CallRecord(purpose, estimate, estimate // 4))


def _blueprint_from_answer(answer: dict[str, Any], limits: BlueprintLimits) -> tuple[dict[str, Any], list[str]]:
    """Claude's form (palette as a list) -> the blueprint dict, and every error of it, limits included."""
    palette: dict[str, str] = {}
    errors: list[str] = []
    for entry in answer["palette"]:
        if entry["key"] in palette:
            errors.append(f"palette: key '{entry['key']}' is listed twice")
        palette[entry["key"]] = entry["block"]
    bp = {**answer, "palette": palette}
    if errors:
        return bp, errors

    errors = blueprint.validate(bp)
    if errors:
        return bp, errors
    if any(extent > limit for extent, limit in zip(bp["size"], limits.max_size)):
        width, height, depth = limits.max_size
        return bp, [f"size: {bp['size']} is over this build's limit {width} x {height} x {depth}"]
    placed = sum(1 for _, value in blueprint.cells(bp, (0, 0, 0)) if value != "air")
    if placed > limits.max_placed:
        return bp, [f"layers: {placed} placed cells, this build's limit is {limits.max_placed}"]
    return bp, []


def fixture_prior_rules() -> list[Rule]:
    """Vanilla rules written by hand, for fake mode and the fake baseline planner."""
    return [_to_rule(raw, source="prior", scope="train") for raw in _fixture_payload()["rules"]]


def _fixture_payload() -> dict[str, Any]:
    return json.loads((DATA_DIR / "prior_rules.json").read_text(encoding="utf-8"))


def _to_rule(raw: dict[str, Any], source: str, scope: str) -> Rule:
    counts = lambda pairs: {p["item"]: p["count"] for p in pairs if p["count"] > 0}  # noqa: E731
    kind, subject = raw["action_type"], raw["subject"].removeprefix("minecraft:")
    if kind == "mine":
        action = {"type": "mine", "block": subject}
        preconditions: dict[str, Any] = {"tool_any": [t.removeprefix("minecraft:") for t in raw["tool_any"]]}
        effects: dict[str, Any] = {"block_removed": True, "drops": counts(raw["yields"])}
    else:
        action = {"type": "craft", "item": subject}
        preconditions = {"consumes": counts(raw["consumes"]), "station": raw["station"] or None}
        effects = {"gains": counts(raw["yields"])}
    return Rule(
        id=f"{kind}_{subject}",
        action=action,
        preconditions=preconditions,
        effects=effects,
        source=source,  # type: ignore[arg-type]
        scope=scope,
        rationale=raw.get("rationale", ""),
    )


def _from_rule(rule: Rule) -> dict[str, Any]:
    pairs = lambda counts: [{"item": k, "count": v} for k, v in counts.items()]  # noqa: E731
    return {
        "action_type": rule.kind,
        "subject": rule.subject,
        "tool_any": rule.preconditions.get("tool_any", []),
        "consumes": pairs(rule.preconditions.get("consumes", {})),
        "station": rule.preconditions.get("station") or "",
        "yields": pairs(rule.yields()),
        "rationale": rule.rationale,
    }


def _fake_revisions(rule: Rule, counterexample: dict[str, Any]) -> list[dict[str, Any]]:
    """Offline stand-in: for a mine rule, try stronger tool tiers, then 'drops nothing'."""
    base = _from_rule(rule)
    if rule.kind == "craft":
        delta = counterexample["observed"].get("inventory_delta", {})
        gains = [{"item": k, "count": v} for k, v in delta.items() if v > 0]
        return [{**base, "yields": gains, "rationale": "the recipe yields what was observed"}]

    tiers = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"]
    used = counterexample["action"].get("tool")
    above = tiers[tiers.index(used) + 1:] if used in tiers else tiers
    return [
        {**base, "tool_any": above, "rationale": "needs a higher-tier pickaxe than the one used"},
        {**base, "tool_any": tiers[2:], "rationale": "needs an iron-tier pickaxe"},
        {**base, "tool_any": [], "yields": [], "rationale": "drops nothing in this world"},
    ]


def _fake_priors_v2(families: list[str]) -> dict[str, Any]:
    """The whitelist's vanilla rules whose subject family was asked for, or that yield an asked item."""
    asked = {normalize.family(name) for name in families}
    rules = [
        raw
        for raw in json.loads((DATA_DIR / "prior_rules_v2.json").read_text(encoding="utf-8"))["rules"]
        if normalize.family(raw["subject"]) in asked or any(normalize.family(y["item"]) in asked for y in raw["yields"])
    ]
    return {"rules": rules}


def _to_rule_v2(raw: dict[str, Any], source: str, scope: str) -> Rule:
    """A rule in normalized names: every subject, tool, fuel and count goes through normalize."""

    def names(raw_names: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize.family(n.removeprefix("minecraft:")) for n in raw_names))

    kind, subject = raw["action_type"], normalize.family(raw["subject"].removeprefix("minecraft:"))
    check = raw.get("check", "exact")
    if kind == "mine":
        action = {"type": "mine", "block": subject}
        preconditions: dict[str, Any] = {"tool_any": names(raw["tool_any"])}
        drops = _sum_counts(raw["yields"])
        effects: dict[str, Any] = {"block_removed": True, "drops": drops}
        if check == "stochastic":
            allowed = {normalize.family(a["item"]): [a["min"], a["max"]] for a in raw.get("allowed", [])}
            effects["allowed"] = allowed or {item: [0, n] for item, n in drops.items()}
    else:
        action = {"type": kind, "item": subject}
        station = raw["station"] or ("furnace" if kind == "smelt" else None)
        preconditions = {"consumes": _sum_counts(raw["consumes"]), "station": station}
        if kind == "smelt":
            preconditions["fuel_any"] = names(raw.get("fuel_any", []))
        effects = {"gains": _sum_counts(raw["yields"])}
    return Rule(
        id=f"{kind}_{subject}",
        action=action,
        preconditions=preconditions,
        effects=effects,
        source=source,  # type: ignore[arg-type]
        scope=scope,
        rationale=raw.get("rationale", ""),
        check=check,  # type: ignore[arg-type]
    )


def _sum_counts(pairs: list[dict[str, Any]]) -> dict[str, int]:
    return normalize.counts({p["item"].removeprefix("minecraft:"): p["count"] for p in pairs if p["count"] > 0})


def _from_rule_v2(rule: Rule) -> dict[str, Any]:
    pairs = lambda counts: [{"item": k, "count": v} for k, v in counts.items()]  # noqa: E731
    allowed = [{"item": k, "min": lo, "max": hi} for k, (lo, hi) in rule.effects.get("allowed", {}).items()]
    return {
        "action_type": rule.kind,
        "subject": rule.subject,
        "tool_any": rule.preconditions.get("tool_any", []),
        "consumes": pairs(rule.preconditions.get("consumes", {})),
        "station": rule.preconditions.get("station") or "",
        "fuel_any": rule.preconditions.get("fuel_any", []),
        "yields": pairs(rule.yields()),
        "check": rule.check,
        "allowed": allowed,
        "rationale": rule.rationale,
    }


def _fake_revisions_v2(rule: Rule, counterexample: dict[str, Any]) -> list[dict[str, Any]]:
    """Offline stand-in, in normalized names: a craft or smelt yields what was observed;
    a mine rule tries stronger tool tiers, then 'drops nothing'."""
    base = _from_rule_v2(rule)
    if rule.kind != "mine":
        delta = counterexample["observed"].get("inventory_delta", {})
        gains = [{"item": k, "count": v} for k, v in delta.items() if v > 0]
        return [{**base, "yields": gains, "rationale": "the recipe yields what was observed"}]
    tiers = ["wooden_pickaxe", "stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe"]
    used = counterexample["action"].get("tool")
    above = tiers[tiers.index(used) + 1:] if used in tiers else tiers
    return [
        {**base, "tool_any": above, "rationale": "needs a higher-tier pickaxe than the one used"},
        {**base, "tool_any": tiers[2:], "rationale": "needs an iron-tier pickaxe"},
        {**base, "tool_any": [], "yields": [], "rationale": "drops nothing in this world"},
    ]


def _fake_repairs_v2(rule: Rule) -> list[dict[str, Any]]:
    """Offline stand-in: the whitelist's vanilla rule for the same action and subject."""
    vanilla = json.loads((DATA_DIR / "prior_rules_v2.json").read_text(encoding="utf-8"))["rules"]
    return [
        raw for raw in vanilla
        if raw["action_type"] == rule.kind and normalize.family(raw["subject"]) == rule.subject
    ]


def _cache_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {record["key"]: record for record in records}


@functools.cache
def _items_file() -> dict[str, Any]:
    return json.loads((DATA_DIR / "items_1_21_1.json").read_text(encoding="utf-8"))


def vanilla_items() -> frozenset[str]:
    """Every vanilla 1.21.1 item id."""
    return frozenset(_items_file()["items"])


def vanilla_foods() -> dict[str, int]:
    """Food item -> food points."""
    return dict(_items_file()["foods"])


def _is_group(name: str) -> bool:
    return name.startswith("#") and normalize.members(name) != [name]


def _fake_parse_goal(text: str, known_items: frozenset[str]) -> tuple[str, str, int]:
    """Offline stand-in for the model: (kind, item, count), by the rule in v3.1e."""
    text = text.lower()
    words = text.split()
    if words and words[0] in ("build", "kill", "guard"):
        return words[0], "", 1
    match = re.search(r"(\d+)?\s*([a-z_]+)\s*$", text)
    if match is None:
        return "obtain", "", 1
    count, word = int(match.group(1) or 1), match.group(2)
    candidates = [word, word[:-1], word[:-2], "#" + word, "#" + word[:-1]]
    item = next((name for name in candidates if name in known_items or _is_group(name)), word)
    return "obtain", item, count


def _checked_goal(goal_id: str, text: str, kind: str, item: str, count: int, reply: str,
                  known_items: frozenset[str]) -> GoalSpec | Unsupported:
    """The checks every mode shares, in the order of v3.1e; replies lose "#" and are cut."""
    def unsupported(message: str) -> Unsupported:
        return Unsupported(message.replace("#", "")[:_MAX_REPLY_CHARS])

    if kind == "unsupported":
        return unsupported(reply or _DEFAULT_UNSUPPORTED_REPLY)
    if kind not in SUPPORTED_GOAL_KINDS:
        return unsupported(_UNSUPPORTED_KIND_REPLIES[kind])

    item = item.strip().removeprefix("minecraft:").strip()
    if item not in known_items and not _is_group(item):
        return unsupported(f"I don't know an item called {item.replace('#', '')}.")
    if count > MAX_GOAL_COUNT:
        return unsupported(f"That's more than I can carry. Ask for {MAX_GOAL_COUNT} or fewer.")
    return GoalSpec(id=goal_id, kind="obtain", source="user", text=text,
                    item=normalize.family(item), count=max(1, count))
