"""Episode trace: one JSON line per event on disk, a readable line on the
terminal, and the same line in the game chat, so the audience can follow every
action and decision in game."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

_RESET, _DIM, _BOLD = "\033[0m", "\033[2m", "\033[1m"
_RED, _GREEN, _YELLOW, _CYAN, _MAGENTA = "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[35m"
CHAT_LIMIT = 240  # characters per chat message


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
        for chat_line in (self._format(kind, data, plain=True) or "").splitlines():
            for message in chat_messages(chat_line.strip()):
                self.say(message)

    def _format(self, kind: str, d: dict[str, Any], plain: bool = False) -> str | None:
        """The event as text: for the terminal, tagged and coloured; plain, for the chat."""
        def c(color: str, text: str) -> str:
            return text if plain or not self._color else f"{color}{text}{_RESET}"

        tag = "" if plain else c(_DIM, f"[{self.label}]")
        if kind == "episode_start":
            return f"\n{tag} {c(_BOLD, d['description'])} world={d['world_id']} budget={d['budget']}"
        if kind == "plan":
            steps = " -> ".join(
                (c(_YELLOW, s["action"] + "?") if s.get("experimental") else s["action"]) for s in d["steps"]
            )
            skills = f" skills={','.join(d['skills'])}" if d.get("skills") else ""
            return f"{tag} {c(_CYAN, 'plan')} ({len(d['steps'])} steps{skills}): {steps}"
        if kind == "step":
            rule = f" [{d['rule']}]" if d.get("rule") else ""
            return f"{tag}  #{d['index']:02d} {d['action']}{c(_DIM, rule)} -> {_step_result(d, c)}"
        if kind == "contradiction":
            flagged = f"; flagged {', '.join(d['flagged'])}" if d["flagged"] else ""
            return (f"{tag}  {c(_RED, 'contradiction')}: {d['detail']}. "
                    f"Demoted {d['rule']} in this world{flagged}")
        if kind == "revision":
            lines = [f"{tag}  {c(_MAGENTA, 'LLM revision')} ({d['tokens']} tokens):"]
            for h in d["hypotheses"]:
                state = "" if h["kept"] else c(_DIM, " (rejected: contradicts the counterexample)")
                lines.append(f"{tag}    {h['rank']}. {h['summary']}{state}")
            return "\n".join(lines)
        if kind == "llm_plan":
            return (f"{tag} {c(_MAGENTA, 'LLM plan')} attempt {d['attempt']} ({d['tokens']} tokens, "
                    f"{len(d['actions'])} actions): {d['summary'][:160]}")
        if kind == "feedback":
            return f"{tag}  {c(_YELLOW, 'feedback')}: {d['summary']}"
        if kind == "certified":
            return f"{tag} {c(_GREEN, 'certified')}: {', '.join(d['rules'])}" if d["rules"] else None
        if kind == "skill":
            return f"{tag} skill {d['skill']} cites {', '.join(d['rule_ids'])}"
        if kind == "learned":
            return "\n".join(f"{tag} {c(_GREEN, line)}" for line in d["lines"])
        if kind == "plan_failed":
            return f"{tag} {c(_RED, 'no plan')}: {d['reason']}"
        if kind == "episode_end":
            result = c(_GREEN, "SUCCESS") if d["success"] else c(_RED, "FAIL")
            return (f"{tag} {result} actions={d['actions']} llm_tokens={d['llm_tokens']} "
                    f"walked={d['blocks_walked']} repeated_failures={d['repeated_failures']}")
        return None


def chat_messages(text: str, limit: int = CHAT_LIMIT) -> list[str]:
    """Splits a line into chat messages of at most `limit` characters: between
    plan steps where possible, else between words."""
    for separator in (" -> ", " "):
        parts = text.split(separator)
        if all(len(part) <= limit for part in parts):
            return _pack(parts, separator, limit)
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _pack(parts: list[str], separator: str, limit: int) -> list[str]:
    messages: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{separator}{part}" if current else part
        if len(candidate) <= limit:
            current = candidate
        else:
            messages.append(current)
            current = part
    if current:
        messages.append(current)
    return messages


def _step_result(d: dict[str, Any], c: Callable[[str, str], str]) -> str:
    if not d["ok"]:
        return c(_YELLOW, f"failed: {d['error']}")
    if d.get("check") == "mismatch":
        return f"{c(_RED, 'MISMATCH')} {json.dumps(d['observed'])}"
    if d.get("check") == "match":
        return f"{c(_GREEN, 'ok')} {json.dumps(d['observed'])}"
    # Unchecked steps (baseline, explore) still show what the world did.
    kind = d["action_obj"]["kind"]
    if kind == "mine":
        return f"done {json.dumps({'block_removed': d['block_removed'], 'drops': d['drops']})}"
    if kind == "craft":
        return f"done {json.dumps({'inventory_delta': d['inventory_delta']})}"
    return "done"
