"""Episode trace: one JSON line per event on disk, a readable line on the
terminal, and short narration in the game chat for the moments that matter."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

_RESET, _DIM, _BOLD = "\033[0m", "\033[2m", "\033[1m"
_RED, _GREEN, _YELLOW, _CYAN, _MAGENTA = "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[35m"


class EpisodeLog:
    def __init__(self, path: Path | None, label: str, say: Callable[[str], None] = lambda message: None):
        self.path = path
        self.label = label
        self.say = say
        self.events: list[dict[str, Any]] = []
        self._color = sys.stdout.isatty()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")

    def event(self, kind: str, **data: Any) -> None:
        record = {"t": round(time.time(), 3), "kind": kind, **data}
        self.events.append(record)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        line = self._format(kind, data)
        if line:
            print(line, flush=True)

    def _c(self, color: str, text: str) -> str:
        return f"{color}{text}{_RESET}" if self._color else text

    def _format(self, kind: str, d: dict[str, Any]) -> str | None:
        tag = self._c(_DIM, f"[{self.label}]")
        if kind == "episode_start":
            return f"\n{tag} {self._c(_BOLD, d['description'])} world={d['world_id']} budget={d['budget']}"
        if kind == "plan":
            steps = " -> ".join(
                (self._c(_YELLOW, s["action"] + "?") if s.get("experimental") else s["action"]) for s in d["steps"]
            )
            skills = f" skills={','.join(d['skills'])}" if d.get("skills") else ""
            return f"{tag} {self._c(_CYAN, 'plan')} ({len(d['steps'])} steps{skills}): {steps}"
        if kind == "step_start":
            if not d["experimental"]:
                return None
            progress = f"{d['confirmed']}/{d['needed']} confirmed"
            return f"{tag}  {self._c(_DIM, 'testing ' + d['predicts'] + ' (' + progress + ')')}"
        if kind == "goal":
            return (f"\n{tag} {self._c(_BOLD, 'goal')} g{d['index']:03d}: {d['count']} {d['item']} "
                    f"({d['reason']}) budget={d['budget']}")
        if kind == "travel":
            return f"{tag}  {self._c(_DIM, 'travel ' + self._travel_summary(d))}"
        if kind == "look":
            if d["direction"] is None:
                where = "every direction is dead, giving up"
            else:
                where = f"toward {d['direction']}" if d["seen"] else f"nothing in sight, trying {d['direction']}"
            dead = f" (dead: {', '.join(d['dead'])})" if d.get("dead") else ""
            return f"{tag}  {self._c(_DIM, 'look for ' + str(d['target']) + ': ' + where + dead)}"
        if kind == "quarantined":
            return f"{tag}  {self._c(_YELLOW, 'quarantined')}: {d['reason']}; replanning, not evidence"
        if kind == "step":
            rule = f" [{d['rule']}]" if d.get("rule") else ""
            return f"{tag}  #{d['index']:02d} {d['action']}{self._c(_DIM, rule)} -> {self._step_result(d)}"
        if kind == "contradiction":
            flagged = f"; flagged {', '.join(d['flagged'])}" if d["flagged"] else ""
            return (f"{tag}  {self._c(_RED, 'contradiction')}: {d['detail']}. "
                    f"Demoted {d['rule']} in this world{flagged}")
        if kind == "revision":
            lines = [f"{tag}  {self._c(_MAGENTA, 'LLM revision')} ({d['tokens']} tokens):"]
            for h in d["hypotheses"]:
                state = "" if h["kept"] else self._c(_DIM, " (rejected: contradicts the counterexample)")
                lines.append(f"{tag}    {h['rank']}. {h['summary']}{state}")
            return "\n".join(lines)
        if kind == "llm_plan":
            return (f"{tag} {self._c(_MAGENTA, 'LLM plan')} attempt {d['attempt']} ({d['tokens']} tokens, "
                    f"{len(d['actions'])} actions): {d['summary'][:160]}")
        if kind == "feedback":
            return f"{tag}  {self._c(_YELLOW, 'feedback')}: {d['summary']}"
        if kind == "certified":
            return f"{tag} {self._c(_GREEN, 'certified')}: {', '.join(d['rules'])}" if d["rules"] else None
        if kind == "skill":
            return f"{tag} skill {d['skill']} cites {', '.join(d['rule_ids'])}"
        if kind == "learned":
            return "\n".join(f"{tag} {self._c(_GREEN, line)}" for line in d["lines"])
        if kind == "plan_failed":
            return f"{tag} {self._c(_RED, 'no plan')}: {d['reason']}"
        if kind == "episode_end":
            result = self._c(_GREEN, "SUCCESS") if d["success"] else self._c(_RED, "FAIL")
            return (f"{tag} {result} actions={d['actions']} llm_tokens={d['llm_tokens']} "
                    f"walked={d['blocks_walked']} repeated_failures={d['repeated_failures']}")
        return None

    @staticmethod
    def _travel_summary(d: dict[str, Any]) -> str:
        parts = [f"{d.get('distance', 0):.0f} blocks"]
        for label, key in (("dug", "dug"), ("placed", "placed"), ("picked up", "picked_up")):
            if d.get(key):
                parts.append(f"{label} {sum(d[key].values())}")
        return ", ".join(parts)

    def _step_result(self, d: dict[str, Any]) -> str:
        if d.get("check") == "quarantined":
            return self._c(_YELLOW, "quarantined (not evidence)")
        if d.get("check") == "stochastic":
            return f"{self._c(_CYAN, 'stochastic')} {json.dumps(d['observed'])}"
        if not d["ok"]:
            return self._c(_YELLOW, f"failed: {d['error']}")
        if d.get("check") == "mismatch":
            return f"{self._c(_RED, 'MISMATCH')} {json.dumps(d['observed'])}"
        if d.get("check") == "match":
            return f"{self._c(_GREEN, 'ok')} {json.dumps(d['observed'])}"
        # Unchecked steps (baseline, explore) still show what the world did.
        kind = d["action_obj"]["kind"]
        if kind == "mine":
            return f"done {json.dumps({'block_removed': d['block_removed'], 'drops': d['drops']})}"
        if kind == "craft":
            return f"done {json.dumps({'inventory_delta': d['inventory_delta']})}"
        return "done"
