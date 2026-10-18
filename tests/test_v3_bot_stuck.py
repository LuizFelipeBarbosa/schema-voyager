"""A bot that stays connected but keeps failing (busy) stops the life with "bot_stuck" (v3.0i): 3 losses within
300 s with no action answered in between."""

import pytest

from schema_voyager import life
from schema_voyager.mineflayer_backend import BackendError
from test_v3_life_robust import FlakyWorld, life_events, run


class BusyWorld(FlakyWorld):
    """Every act whose call number is in `answer_on` is answered; the others raise a busy error. connected() is True."""

    def __init__(self, answer_on=()):
        super().__init__()
        self.answer_on = set(answer_on)

    def act(self, action):
        if self.act_calls + 1 not in self.answer_on:
            self.act_calls += 1
            raise BackendError("HTTP 409 busy")
        return super().act(action)


def test_three_losses_with_the_bot_connected_stop_the_life(tmp_path):
    world = BusyWorld()

    summary, recs = run(tmp_path, world, max_goals=10)

    assert summary["stop_reason"] == "bot_stuck"
    assert world.act_calls == 3
    [stuck] = life_events(tmp_path, "bot_stuck")
    assert stuck["goal_id"] == "u001" and stuck["losses"] == 3 and "busy" in stuck["error"]
    assert len(life_events(tmp_path, "bot_down")) == 2 and not life_events(tmp_path, "bot_back")[2:]
    assert [r["interrupt"] for r in recs] == ["bot", "bot", "bot"]
    assert not any("Couldn't" in line for line in world.said[-1:])


def test_losses_spread_wider_than_the_window_do_not_stop_the_life(tmp_path, monkeypatch):
    monkeypatch.setattr(life, "BOT_STUCK_WINDOW_S", 0.0)  # three losses are never within it

    summary, _ = run(tmp_path, BusyWorld(), max_goals=10, wall_clock_s=1.0)

    assert summary["stop_reason"] != "bot_stuck"
    assert not life_events(tmp_path, "bot_stuck")


def test_an_answered_act_between_losses_clears_the_count(tmp_path):
    world = BusyWorld(answer_on=set(range(1, 200, 3)))  # answered, raise, raise, answered, ...

    summary, _ = run(tmp_path, world, max_goals=10, wall_clock_s=1.0)

    assert summary["stop_reason"] != "bot_stuck" and not life_events(tmp_path, "bot_stuck")
    assert len(life_events(tmp_path, "bot_down")) >= 3  # three losses, but an answered act came between them


def test_a_bot_that_is_really_down_still_ends_bot_down(tmp_path):
    world = FlakyWorld()
    world.fail_act_on = {1}
    world.broken_after_failure = True

    summary, _ = run(tmp_path, world, bot_wait_s=0.05)

    assert summary["stop_reason"] == "bot_down"
    assert not life_events(tmp_path, "bot_stuck")
