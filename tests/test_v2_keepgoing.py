"""--keep-going: when the frontier runs dry the life explores instead of stopping."""

import json
from pathlib import Path

import pytest

from schema_voyager.curriculum import Curriculum
from schema_voyager.environment import NaturalWorld
from schema_voyager.life import LifeConfig, run_life
from schema_voyager.llm import LLM


def life(run_dir: Path, *, keep_going: bool, max_goals: int | None = None, resume: bool = False) -> dict:
    config = LifeConfig(run_dir=run_dir, world_id="life-sim2", max_goals=max_goals, keep_going=keep_going)
    llm = LLM(mode="fake", cache_path=run_dir / "llm_cache.jsonl")
    return run_life(NaturalWorld(seed=2), llm, config, resume=resume)


def records(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "life.jsonl").read_text().splitlines()]


def explore_directions(trace: Path) -> list[str]:
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    return [e["action_obj"]["direction"] for e in events if e["kind"] == "step"]


@pytest.fixture
def no_goals(monkeypatch):
    """A curriculum with nothing to try, whatever the world looks like."""
    monkeypatch.setattr(Curriculum, "next_goal", lambda *args, **kwargs: None)


def test_without_keep_going_an_empty_frontier_stops_the_life(tmp_path, no_goals):
    summary = life(tmp_path, keep_going=False)

    assert summary["stop_reason"] == "empty_frontier"
    assert all(record["goal"] != "explore" for record in records(tmp_path))


def test_keep_going_explores_in_different_directions_until_max_goals(tmp_path, no_goals):
    summary = life(tmp_path, keep_going=True, max_goals=3)

    assert summary["stop_reason"] == "max_goals"
    rounds = records(tmp_path)
    assert [r["goal"] for r in rounds] == ["explore"] * 3
    assert all(r["outcome"] == "success" and r["actions"] == 3 for r in rounds)
    assert [r["llm_calls"] for r in rounds[1:]] == [0, 0]  # the first round carries the priors asked before it
    traces = [tmp_path / "episodes" / f"D_life_g{r['index']:03d}_explore.jsonl" for r in rounds]
    directions = [explore_directions(trace) for trace in traces]
    assert all(len(d) == 3 for d in directions)
    assert len({tuple(d) for d in directions}) == 3


def test_an_exploration_round_gives_skipped_goals_another_chance(tmp_path, no_goals):
    life(tmp_path, keep_going=True, max_goals=1)
    state_path = tmp_path / "life_state.json"
    state = json.loads(state_path.read_text())
    state["curriculum"]["skipped"] = {"torch": "failed 2 attempts"}
    state["curriculum"]["failures"] = {"torch": 2}
    state_path.write_text(json.dumps(state))

    life(tmp_path, keep_going=True, max_goals=2, resume=True)

    curriculum = json.loads(state_path.read_text())["curriculum"]
    assert curriculum["skipped"] == {} and curriculum["failures"] == {}


def test_resume_continues_the_direction_counter(tmp_path, no_goals):
    life(tmp_path, keep_going=True, max_goals=1)
    assert json.loads((tmp_path / "life_state.json").read_text())["explores_done"] == 3

    life(tmp_path, keep_going=True, max_goals=2, resume=True)

    assert json.loads((tmp_path / "life_state.json").read_text())["explores_done"] == 6
    first, second = (explore_directions(tmp_path / "episodes" / f"D_life_g{n:03d}_explore.jsonl") for n in (1, 2))
    assert first != second
