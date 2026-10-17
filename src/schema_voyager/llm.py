"""Claude calls for SchemaVoyager: prior rules, ranked revisions, and the
skill-only baseline's planner. Every call is metered from API usage and
recorded so a run can be replayed without spending tokens.

Modes: "live" calls the API and records; "replay" only reads recorded
responses (and reports their original usage); "fake" is a deterministic
offline stand-in for tests and dry runs.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Action, Rule

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

REVISION_INSTRUCTIONS = """A rule the agent relied on just made a wrong prediction in a world it has not seen before. This world's rules may differ from vanilla Minecraft, and nobody will tell the agent how.

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


class LLMError(RuntimeError):
    pass


@dataclass
class CallRecord:
    purpose: str
    input_tokens: int
    output_tokens: int
    replayed: bool = False

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLM:
    mode: str = "live"
    model: str = field(default_factory=lambda: os.environ.get("SCHEMA_VOYAGER_MODEL", DEFAULT_MODEL))
    effort: str = field(default_factory=lambda: os.environ.get("SCHEMA_VOYAGER_EFFORT", DEFAULT_EFFORT))
    cache_path: Path = Path("runs/llm_cache.jsonl")
    calls: list[CallRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._client = None
        self._cache = _load_cache(self.cache_path) if self.mode != "fake" else {}

    # -- metering ---------------------------------------------------------

    @property
    def total_tokens(self) -> int:
        return sum(call.tokens for call in self.calls)

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

    # -- transport --------------------------------------------------------

    def _json_call(self, purpose: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        key = _cache_key(self.model, self.effort, system, user, schema)
        cached = self._cache.get(key)
        if self.mode == "replay":
            if cached is None:
                raise LLMError(f"no recorded response for this {purpose} call; run with --llm live first")
            self.calls.append(CallRecord(purpose, cached["input_tokens"], cached["output_tokens"], replayed=True))
            return cached["payload"]

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
        self.calls.append(CallRecord(purpose, input_tokens, usage.output_tokens))
        record = {"key": key, "purpose": purpose, "payload": payload,
                  "input_tokens": input_tokens, "output_tokens": usage.output_tokens}
        self._cache[key] = record
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return payload

    def _create(self, system: str, user: str, schema: dict[str, Any]):
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        try:
            return self._client.messages.create(
                model=self.model,
                max_tokens=16000,
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


def _cache_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {record["key"]: record for record in records}
