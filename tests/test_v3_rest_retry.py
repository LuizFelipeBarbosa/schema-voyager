"""v3.2b: Episode.act rests a badly hurt bot after a flight instead of retrying in place at once."""

import pytest

from schema_voyager import episode as episode_module
from schema_voyager.episode import MAX_INTERRUPTS, Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import BackendError
from schema_voyager.models import Action, Outcome, Status, Task
from schema_voyager.trace import EpisodeLog


class RestingBackend:
    """Interrupts the first attempts with the scripted outcomes, then succeeds; status answers the scripted healths."""

    def __init__(self, interrupts, healths=(), food=20):
        self.interrupts = list(interrupts)
        self.healths = list(healths)
        self.food = food
        self.actions: list[Action] = []
        self.status_calls = 0

    def act(self, action):
        self.actions.append(action)
        if not self.interrupts:
            return Outcome(ok=True)
        detail, events = self.interrupts.pop(0)
        return Outcome(ok=False, error="interrupted", detail=detail, events=events)

    def status(self):
        self.status_calls += 1
        health = self.healths.pop(0) if len(self.healths) > 1 else self.healths[0]
        if health is BackendError:
            raise BackendError("down")
        return Status(health=health, food=self.food, position=(0, 0, 0))


class NoStatusBackend(RestingBackend):
    status = None


def fled(health):
    return {"outcome": "fled"}, [{"type": "fled", "health": health}]


def killed(health):
    return {"outcome": "killed"}, [{"type": "damage", "health": health}]


@pytest.fixture
def clock(monkeypatch):
    """A fake clock: each sleep advances it."""
    now = [0.0]
    sleeps = []
    monkeypatch.setattr(episode_module, "_monotonic", lambda: now[0])
    monkeypatch.setattr(episode_module, "_sleep", lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds)))
    return sleeps


def run_act(tmp_path, backend):
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"),
    )
    outcome = episode.act(Action("mine", target="oak_log"))
    return outcome, episode


def test_fled_at_low_health_with_food_rests_until_healed_then_retries(tmp_path, clock):
    backend = RestingBackend([fled(4.8)], healths=[4.8, 8.0, 12.0, 14.0])
    outcome, episode = run_act(tmp_path, backend)
    assert outcome.ok
    assert len(backend.actions) == 2
    assert backend.status_calls == 4
    assert clock == [2, 2, 2]
    rest = [event for event in episode.log.events if event["kind"] == "rest"]
    assert len(rest) == 1
    assert (rest[0]["health_before"], rest[0]["health_after"], rest[0]["food"], rest[0]["waited_s"]) == (4.8, 14.0, 20, 6)


def test_the_last_damage_event_gives_the_health_when_there_is_no_fled_event(tmp_path, clock):
    backend = RestingBackend([({"outcome": "fled"}, [{"type": "damage", "health": 9}, {"type": "damage", "health": 5}])],
                             healths=[14.0])
    run_act(tmp_path, backend)
    assert backend.status_calls == 1


def test_fled_at_low_health_without_food_stops_interrupted(tmp_path, clock):
    backend = RestingBackend([fled(4.8)], healths=[4.8], food=10)
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.error == "interrupted"
    assert len(backend.actions) == 1
    assert clock == []


def test_fled_at_health_12_retries_at_once(tmp_path, clock):
    backend = RestingBackend([fled(12)], healths=[12.0])
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.ok
    assert len(backend.actions) == 2
    assert backend.status_calls == 0


def test_killed_at_low_health_retries_at_once(tmp_path, clock):
    backend = RestingBackend([killed(4)], healths=[4.0])
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.ok
    assert len(backend.actions) == 2
    assert backend.status_calls == 0


def test_health_that_never_recovers_is_retried_after_60_seconds(tmp_path, clock):
    backend = RestingBackend([fled(4.8)], healths=[5.0])
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.ok
    assert len(backend.actions) == 2
    assert sum(clock) == 60


def test_a_status_error_ends_the_rest(tmp_path, clock):
    backend = RestingBackend([fled(4.8)], healths=[5.0, BackendError])
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.ok
    assert len(backend.actions) == 2
    assert clock == [2]


def test_running_out_of_time_ends_the_rest(tmp_path, clock):
    backend = RestingBackend([fled(4.8)], healths=[5.0])
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"), deadline=0.0,
    )
    episode.act(Action("mine", target="oak_log"))
    assert clock == []


def test_a_backend_without_status_retries_at_once(tmp_path, clock):
    backend = NoStatusBackend([fled(4.8)])
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.ok
    assert len(backend.actions) == 2


def test_rests_still_count_against_max_interrupts(tmp_path, clock):
    backend = RestingBackend([fled(4.8)] * 10, healths=[14.0])
    outcome, _ = run_act(tmp_path, backend)
    assert outcome.error == "interrupted"
    assert len(backend.actions) == MAX_INTERRUPTS + 1
