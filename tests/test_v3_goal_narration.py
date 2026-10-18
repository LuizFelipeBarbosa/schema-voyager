"""Goals the user gave are narrated as "On it: ..." and closed once by the life (v3.1h), in the simulator."""

import json

from schema_voyager.trace import EpisodeLog
from test_v3_life_queue import ChatWorld, chat, events_of, run


def first_plan_steps(tmp_path, record):
    plan = next(e for e in events_of(tmp_path, record) if e["kind"] == "plan")
    return len(plan["steps"])


def test_a_user_goal_says_on_it_then_done_and_never_goal_reached(tmp_path):
    world = ChatWorld({2: chat("goal", "4 torches")})

    _, recs = run(tmp_path, world, max_goals=1)

    steps = first_plan_steps(tmp_path, recs[0])
    assert f"On it: 4 torches ({steps} steps)" in world.said
    assert world.said.index(f"On it: 4 torches ({steps} steps)") < world.said.index("Done: 4 torches")
    assert not [m for m in world.said if m.startswith(("Goal:", "Goal reached"))]


def test_a_curriculum_goal_keeps_v2_narration(tmp_path):
    world = ChatWorld()

    run(tmp_path, world, max_goals=1)

    assert any(m.startswith("Goal: ") for m in world.said)
    assert any(m.startswith("Goal reached: ") for m in world.said)
    assert not any(m.startswith("On it:") for m in world.said)


def test_a_hash_goal_has_no_hash_in_chat(tmp_path):
    world = ChatWorld({2: chat("goal", "4 planks")})

    run(tmp_path, world, max_goals=1)

    on_it = next(m for m in world.said if m.startswith("On it: "))
    assert on_it.startswith("On it: 4 planks (") and "#" not in on_it


def test_every_message_is_short_and_has_no_hash(tmp_path):
    world = ChatWorld({2: chat("goal", "4 planks")})

    run(tmp_path, world, max_goals=2)

    assert world.said
    assert all(len(m) <= 240 and "#" not in m for m in world.said)


def format_event(kind, **data):
    return EpisodeLog(None, "L")._format(kind, data)


def test_trace_lines_for_goal_events():
    base = {"index": 3, "item": "torch", "count": 4, "reason": "next", "budget": 30}
    curriculum = format_event("goal", **base)
    assert curriculum == "\n[L] goal g003: 4 torch (next) budget=30"
    assert format_event("goal", **base, source="curriculum", goal_id="g003") == curriculum

    user = format_event("goal", **base, source="user", goal_id="u003", text="4 torches")
    assert user.startswith(curriculum) and "u003" in user and "user" in user and "4 torches" in user

    assert format_event("goal_interrupted", goal_id="u003", reason="cancelled") == "[L] interrupted u003: cancelled"
