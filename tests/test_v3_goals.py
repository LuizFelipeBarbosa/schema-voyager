"""The goal queue (v3.1): goals.jsonl as the panel and the life share it, and goal_label."""

from __future__ import annotations

import json

import pytest

from schema_voyager.goals import GoalEntry, GoalQueue, goal_label
from schema_voyager.models import GoalSpec

STATUSES = ["submitted", "queued", "running", "done", "failed", "cancelled", "unsupported", "awaiting_approval"]

ALLOWED = {
    "submitted": {"queued", "unsupported", "cancelled"},
    "queued": {"running", "cancelled", "awaiting_approval"},
    "awaiting_approval": {"queued", "cancelled"},
    "running": {"done", "failed", "cancelled", "queued"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
    "unsupported": set(),
}


@pytest.fixture
def queue(tmp_path):
    return GoalQueue(tmp_path / "runs" / "life" / "goals.jsonl")


def torch_spec(goal_id: str, source: str = "user", count: int = 10) -> GoalSpec:
    return GoalSpec(goal_id, "obtain", source, "get me 10 torches", item="torch", count=count)


def goal_in_status(queue: GoalQueue, status: str) -> str:
    """A fresh user goal walked to `status` (awaiting_approval goes through queued)."""
    goal_id = queue.submit("get me 10 torches")
    path = {
        "submitted": [],
        "queued": ["queued"],
        "running": ["queued", "running"],
        "done": ["queued", "running", "done"],
        "failed": ["queued", "running", "failed"],
        "cancelled": ["cancelled"],
        "unsupported": ["unsupported"],
        "awaiting_approval": ["queued", "awaiting_approval"],
    }[status]
    for step in path:
        if step == "queued":
            assert queue.accept(torch_spec(goal_id))
        else:
            assert queue.mark(goal_id, step)
    return goal_id


def lines(queue: GoalQueue) -> list[str]:
    return queue.path.read_text().splitlines()


def test_round_trip(queue):
    goal_id = queue.submit("get me 10 torches")
    assert goal_id == "u001"
    assert [g.id for g in queue.pending()] == ["u001"]
    assert queue.next() is None

    spec = torch_spec(goal_id)
    assert queue.accept(spec)
    assert queue.pending() == []
    assert queue.next() == spec
    assert queue.get(goal_id).status == "queued"

    assert queue.mark(goal_id, "running", target_count=12)
    assert queue.next() is None
    assert queue.get(goal_id).target_count == 12

    assert queue.mark(goal_id, "done", deaths=1)
    entry = queue.get(goal_id)
    assert (entry.status, entry.target_count, entry.deaths, entry.spec) == ("done", 12, 1, spec)
    assert len(lines(queue)) == 4


def test_accept_keeps_entry_source_and_text(queue):
    goal_id = queue.submit("get me 10 torches")
    queue.accept(GoalSpec(goal_id, "obtain", "system", "other words", item="torch", count=10))
    entry = queue.get(goal_id)
    assert (entry.source, entry.text) == ("user", "get me 10 torches")
    assert entry.spec == GoalSpec(goal_id, "obtain", "user", "get me 10 torches", item="torch", count=10)


def test_accept_refuses_unknown_and_not_submitted(queue):
    assert not queue.accept(torch_spec("u009"))
    goal_id = goal_in_status(queue, "queued")
    before = lines(queue)
    assert not queue.accept(torch_spec(goal_id))
    assert lines(queue) == before


def test_mark_keeps_earlier_fields(queue):
    goal_id = goal_in_status(queue, "queued")
    queue.mark(goal_id, "running", target_count=7)
    queue.mark(goal_id, "failed", reason="no tree", deaths=2)
    entry = queue.get(goal_id)
    assert (entry.target_count, entry.reason, entry.deaths) == (7, "no tree", 2)


def test_two_queues_on_one_path_see_each_other(queue):
    panel, life = queue, GoalQueue(queue.path)
    goal_id = panel.submit("get me 10 torches")
    assert [g.id for g in life.pending()] == [goal_id]
    life.accept(torch_spec(goal_id))
    assert panel.next() == torch_spec(goal_id)
    assert panel.cancel(goal_id)
    assert life.get(goal_id).status == "cancelled"
    assert not life.mark(goal_id, "running")
    assert [g.id for g in life.goals()] == [goal_id]


def test_missing_file_is_empty_and_readers_do_not_create_it(queue):
    assert queue.goals() == []
    assert queue.pending() == []
    assert queue.next() is None
    assert queue.get("u001") is None
    assert not queue.path.exists()


def test_corrupt_last_line_is_skipped_and_next_write_is_valid(queue):
    goal_id = queue.submit("get me 10 torches")
    with queue.path.open("ab") as f:
        f.write(b'{"id": "u001", "status": "que')  # a crash mid-write, no newline
    assert queue.get(goal_id).status == "submitted"

    assert queue.accept(torch_spec(goal_id))
    assert queue.get(goal_id).status == "queued"
    assert queue.submit("another") == "u002"
    bad = []
    for line in lines(queue):
        try:
            json.loads(line)
        except json.JSONDecodeError:
            bad.append(line)
    assert bad == ['{"id": "u001", "status": "que']


def test_rebuild_skips_junk_lines(queue):
    queue.path.parent.mkdir(parents=True)
    good = {"id": "u001", "t": 1.0, "status": "submitted", "source": "user", "text": "hi"}
    queue.path.write_text(
        "\n".join(["not json", "[1, 2]", json.dumps({"status": "queued"}), json.dumps({"id": "u005"}), json.dumps(good)])
        + "\n"
    )
    assert [g.id for g in queue.goals()] == ["u001"]
    assert queue.submit("next") == "u002"


def test_latest_line_wins_and_order_is_first_line(queue):
    first, second = queue.submit("a"), queue.submit("b")
    queue.accept(torch_spec(first))
    assert [g.id for g in queue.goals()] == [first, second]
    assert [g.id for g in queue.pending()] == [second]


def test_cancel_every_non_final_status(queue):
    for status in ["submitted", "queued", "running", "awaiting_approval"]:
        goal_id = goal_in_status(queue, status)
        assert queue.cancel(goal_id)
        assert queue.get(goal_id).status == "cancelled"


def test_cancel_refused_for_final_and_unknown(queue):
    ids = [goal_in_status(queue, s) for s in ["done", "failed", "cancelled", "unsupported"]]
    before = lines(queue)
    assert not any(queue.cancel(i) for i in [*ids, "u099"])
    assert lines(queue) == before


def test_cancelled_goal_stays_cancelled(queue):
    goal_id = goal_in_status(queue, "running")
    queue.cancel(goal_id)
    before = lines(queue)
    assert queue.mark(goal_id, "done") is False
    assert queue.get(goal_id).status == "cancelled"
    assert lines(queue) == before


def test_requeue_running(queue):
    a, b, c = (goal_in_status(queue, s) for s in ["running", "queued", "running"])
    assert queue.requeue_running() == [a, c]
    assert [queue.get(i).status for i in (a, b, c)] == ["queued"] * 3
    assert queue.requeue_running() == []


@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("target", STATUSES)
def test_transition_table(queue, current, target):
    goal_id = goal_in_status(queue, current)
    before = lines(queue)
    result = queue.mark(goal_id, target)
    if target in ALLOWED[current]:
        assert result is True
        assert queue.get(goal_id).status == target
        assert len(lines(queue)) == len(before) + 1
    else:
        assert result is False
        assert queue.get(goal_id).status == current
        assert lines(queue) == before


def test_mark_unknown_id_writes_nothing(queue):
    queue.submit("x")
    before = lines(queue)
    assert not queue.mark("u099", "queued")
    assert lines(queue) == before


def test_mark_unknown_field_raises_before_touching_the_file(queue):
    with pytest.raises(ValueError):
        queue.mark("u001", "queued", colour="red")
    assert not queue.path.exists()
    goal_id = queue.submit("x")
    before = lines(queue)
    with pytest.raises(ValueError):
        queue.mark(goal_id, "queued", text="y")
    assert lines(queue) == before


def test_ids_per_prefix(queue):
    assert [queue.submit("a"), queue.submit("b", "system"), queue.submit("c"), queue.submit("d", "system")] == [
        "u001",
        "s001",
        "u002",
        "s002",
    ]
    assert GoalQueue(queue.path).submit("e") == "u003"


def test_curriculum_goals_do_not_go_in_the_file(queue):
    with pytest.raises(ValueError):
        queue.submit("get coal", "curriculum")
    assert not queue.path.exists()


def test_next_orders_system_before_user_then_oldest(queue):
    u1 = goal_in_status(queue, "queued")
    u2 = goal_in_status(queue, "queued")
    s1 = queue.submit("hunger", "system")
    queue.accept(GoalSpec(s1, "obtain", "system", "hunger", item="bread", count=4))
    s2 = queue.submit("hunger again", "system")
    queue.accept(GoalSpec(s2, "obtain", "system", "hunger again", item="bread", count=4))

    order = []
    while (spec := queue.next()) is not None:
        order.append(spec.id)
        queue.mark(spec.id, "running")
    assert order == [s1, s2, u1, u2]


def test_to_json_keys_and_values():
    keys = ["id", "t", "status", "source", "text", "kind", "item", "count", "target", "blueprint", "reason", "target_count", "deaths"]
    bare = GoalEntry("u001", "submitted", "user", "hi", 5.0).to_json()
    assert list(bare) == keys
    assert (bare["kind"], bare["item"], bare["count"], bare["target"], bare["blueprint"]) == (None,) * 5
    assert (bare["reason"], bare["target_count"], bare["deaths"]) == ("", None, 0)

    spec = GoalSpec("u001", "obtain", "user", "hi", item="torch", count=10)
    full = GoalEntry("u001", "queued", "user", "hi", 5.0, spec=spec, target_count=12, deaths=1).to_json()
    assert set(full) == set(keys)
    assert (full["kind"], full["item"], full["count"], full["target"], full["blueprint"]) == ("obtain", "torch", 10, None, None)
    assert (full["target_count"], full["deaths"], full["t"]) == (12, 1, 5.0)


def test_file_lines_carry_every_key(queue):
    queue.submit("hi")
    record = json.loads(lines(queue)[0])
    assert set(record) == {"id", "t", "status", "source", "text", "kind", "item", "count", "target", "blueprint", "reason", "target_count", "deaths"}


@pytest.mark.parametrize(
    "item, count, label",
    [
        ("torch", 10, "10 torches"),
        ("stick", 4, "4 sticks"),
        ("iron_ingot", 1, "1 iron ingot"),
        ("iron_ingot", 2, "2 iron ingots"),
        ("#planks", 4, "4 planks"),
        ("#planks", 1, "1 plank"),
        ("#log", 3, "3 logs"),
        ("coal", 3, "3 coal"),
        ("glass", 2, "2 glass"),
        ("shears", 1, "1 shears"),
        ("shears", 2, "2 shears"),
        ("crafting_table", 1, "1 crafting table"),
        ("furnace", 2, "2 furnaces"),
        ("wooden_pickaxe", 2, "2 wooden pickaxes"),
    ],
)
def test_goal_label_obtain(item, count, label):
    assert goal_label(GoalSpec("u001", "obtain", "user", "text", item=item, count=count)) == label
    assert "#" not in label and "_" not in label


def test_goal_label_other_kinds_and_no_spec():
    assert goal_label(GoalSpec("u002", "build", "user", "build a hut")) == "build a hut"
    assert goal_label(GoalSpec("u002", "build", "user", "build a hut"), "typed text") == "typed text"
    assert goal_label(None, "get me a castle") == "get me a castle"
    assert goal_label(None) == ""
