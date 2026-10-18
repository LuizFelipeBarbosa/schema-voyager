"""v3.5f: composite goals in the queue (parents, ordered children) and in the parser (parse_goal, replan_children)."""

import json

import pytest

from schema_voyager.goals import GoalEntry, GoalQueue
from schema_voyager.llm import LLM, PARSE_GOAL_SCHEMA, REPLAN_CHILDREN_SCHEMA, vanilla_items
from schema_voyager.models import GoalSpec, Unsupported

ITEMS = vanilla_items()


@pytest.fixture
def queue(tmp_path):
    return GoalQueue(tmp_path / "goals.jsonl")


@pytest.fixture
def fake(tmp_path):
    return LLM(mode="fake", cache_path=tmp_path / "c.jsonl")


def obtain(item="torch", count=1):
    return GoalSpec("", "obtain", "user", f"{count} {item}", item=item, count=count)


def build():
    return GoalSpec("", "build", "user", "a hut", blueprint={"name": "hut"})


def parent_with(queue, *specs):
    """A submitted parent accepted with `specs`; returns (parent id, child ids)."""
    parent_id = queue.submit("get ready")
    children = queue.accept_children(parent_id, list(specs))
    return parent_id, [c.id for c in children]


def status(queue, goal_id):
    return queue.get(goal_id).status


# -- the record ---------------------------------------------------------------


def test_record_keys_and_old_lines_load(queue):
    keys = list(GoalEntry("u001", "submitted", "user", "hi", 5.0).to_json())
    assert keys[-3:] == ["parent", "children", "replanned"]
    entry = GoalEntry("u001", "queued", "user", "hi", 5.0, parent="u000", children=("u002", "u003"), replanned=True)
    assert entry.to_json()["parent"] == "u000" and entry.to_json()["children"] == ["u002", "u003"]

    old = {"id": "u009", "t": 1.0, "status": "queued", "source": "user", "text": "x", "kind": "obtain", "item": "torch",
           "count": 2, "target": None, "blueprint": None, "reason": "", "target_count": None, "deaths": 0}
    queue.path.write_text(json.dumps(old) + "\n")
    loaded = queue.get("u009")
    assert (loaded.parent, loaded.children, loaded.replanned) == (None, (), False)


def test_parent_record_is_composite_with_null_fields(queue):
    parent_id, ids = parent_with(queue, obtain(), obtain("stick"))
    record = json.loads(queue.path.read_text().splitlines()[-1])
    assert (record["id"], record["kind"], record["status"]) == (parent_id, "composite", "queued")
    assert (record["item"], record["count"], record["target"], record["blueprint"]) == (None,) * 4
    assert (record["parent"], record["children"], record["replanned"]) == (None, ids, False)
    assert queue.get(parent_id).spec.kind == "composite"


# -- accept_children ------------------------------------------------------------


def test_accept_children_ids_statuses_and_parent(queue):
    parent_id = queue.submit("get ready")
    assert parent_id == "u001"
    accepted = queue.accept_children(parent_id, [build(), obtain(), obtain("stick")])
    assert [c.id for c in accepted] == ["u002", "u003", "u004"]
    assert [status(queue, c.id) for c in accepted] == ["awaiting_approval", "queued", "queued"]
    assert all(queue.get(c.id).parent == parent_id for c in accepted)
    assert all(queue.get(c.id).spec.id == c.id for c in accepted)
    assert queue.get(parent_id).children == ("u002", "u003", "u004")
    assert status(queue, parent_id) == "queued"


def test_accept_children_after_other_submissions_and_a_replan(queue):
    parent_id, first = parent_with(queue, obtain(), obtain("stick"))
    other = queue.submit("something else")
    queue.mark(first[0], "running")
    assert status(queue, parent_id) == "running"
    more = queue.accept_children(parent_id, [obtain("coal")])
    assert [c.id for c in more] == ["u005"] and other == "u004"
    assert status(queue, parent_id) == "running"  # a replan keeps the status
    assert queue.get(parent_id).children == (*first, "u005")
    assert [c.id for c in queue.children_of(parent_id)] == [*first, "u005"]


def test_accept_children_refuses_a_missing_or_final_parent(queue):
    assert queue.accept_children("u099", [obtain()]) == []
    parent_id, _ = parent_with(queue, obtain())
    queue.cancel(parent_id)
    assert queue.accept_children(parent_id, [obtain()]) == []


def test_parent_of_and_children_of(queue):
    parent_id, ids = parent_with(queue, obtain(), obtain("stick"))
    assert queue.parent_of(ids[1]).id == parent_id
    assert queue.parent_of(parent_id) is None and queue.parent_of("u099") is None
    assert queue.children_of("u099") == []


# -- next / mark ----------------------------------------------------------------


def test_next_runs_children_in_order_and_never_a_parent(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    assert queue.next().id == a
    queue.mark(a, "running")
    assert queue.next() is None  # the second waits for the first, the parent never runs
    queue.mark(a, "done")
    assert queue.next().id == b
    assert queue.next().id != parent_id


def test_a_failed_or_cancelled_earlier_child_releases_the_next(queue):
    _, (a, b, c) = parent_with(queue, obtain(), obtain("stick"), obtain("coal"))
    queue.mark(a, "running")
    queue.mark(a, "failed", reason="x")
    assert queue.next().id == b
    queue.cancel(b)
    assert queue.next().id == c


def test_build_child_waiting_for_approval_holds_back_later_children(queue):
    _, (hut, torches) = parent_with(queue, build(), obtain())
    assert queue.next() is None
    queue.mark(hut, "queued")
    assert queue.next().id == hut
    queue.mark(hut, "running")
    queue.mark(hut, "done")
    assert queue.next().id == torches


def test_next_order_between_parents_and_loose_goals(queue):
    _, (a, _b) = parent_with(queue, obtain(), obtain("stick"))
    loose = queue.submit("plain")
    queue.accept(GoalSpec(loose, "obtain", "user", "plain", item="coal"))
    system = queue.submit("food", "system")
    queue.accept(GoalSpec(system, "obtain", "system", "food", item="bread"))
    assert queue.next().id == system
    queue.mark(system, "running")
    assert queue.next().id == a  # oldest user goal first


def test_mark_child_running_moves_the_parent(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    lines_before = len(queue.path.read_text().splitlines())
    assert queue.mark(a, "running")
    assert status(queue, parent_id) == "running"
    assert len(queue.path.read_text().splitlines()) == lines_before + 2
    queue.mark(a, "done")
    queue.mark(b, "running")
    assert status(queue, parent_id) == "running"


def test_requeue_running_leaves_the_parent_running(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    queue.mark(a, "running")
    assert queue.requeue_running() == [a]
    assert (status(queue, parent_id), status(queue, a)) == ("running", "queued")
    assert queue.next().id == a


def test_mark_replanned(queue):
    parent_id, _ = parent_with(queue, obtain(), obtain("stick"))
    assert queue.mark(parent_id, "running", replanned=True)
    entry = queue.get(parent_id)
    assert entry.replanned and entry.children  # the children survive the rewrite
    with pytest.raises(ValueError):
        queue.mark(parent_id, "failed", children=())


def test_mark_same_status_updates_fields_only(queue):
    parent_id, (a, _) = parent_with(queue, obtain(), obtain("stick"))
    queue.mark(a, "running")
    assert queue.mark(parent_id, "running", replanned=True)
    assert queue.get(parent_id).status == "running" and queue.get(parent_id).replanned
    assert not queue.mark(parent_id, "running")  # no fields: still a refused transition


# -- settle / cancel --------------------------------------------------------------


def test_settle_waits_for_pending_children(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    queue.mark(a, "running")
    queue.mark(a, "done")
    assert queue.settle(parent_id) is None  # b is queued
    queue.mark(b, "running")
    assert queue.settle(parent_id) is None  # b is running
    assert status(queue, parent_id) == "running"


def test_settle_done_when_any_child_is_done(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    queue.mark(a, "running")
    queue.mark(a, "done")
    queue.mark(b, "running")
    queue.mark(b, "failed", reason="no")
    assert queue.settle(parent_id) == "done"
    assert status(queue, parent_id) == "done"
    assert queue.settle(parent_id) is None  # already terminal


def test_settle_cancelled_when_nothing_is_done(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    queue.mark(a, "running")
    queue.mark(a, "failed", reason="no")
    queue.cancel(b)
    assert queue.settle(parent_id) == "cancelled"


def test_settle_awaiting_approval_is_pending_and_a_failed_parent_stays(queue):
    parent_id, (hut, a) = parent_with(queue, build(), obtain())
    assert queue.settle(parent_id) is None
    queue.mark(parent_id, "running")
    queue.mark(parent_id, "failed", reason="hut: no wood")
    queue.cancel(hut)
    queue.cancel(a)
    assert queue.settle(parent_id) is None
    assert status(queue, parent_id) == "failed"


def test_cancel_parent_cascades(queue):
    parent_id, (a, b, c) = parent_with(queue, obtain(), obtain("stick"), build())
    queue.mark(a, "running")
    queue.mark(a, "done")
    queue.mark(b, "running")
    assert queue.cancel(parent_id)
    assert [status(queue, g) for g in (parent_id, a, b, c)] == ["cancelled", "done", "cancelled", "cancelled"]
    assert queue.cancel(parent_id) is False
    assert queue.next() is None


def test_cancel_a_single_child_leaves_the_parent(queue):
    parent_id, (a, b) = parent_with(queue, obtain(), obtain("stick"))
    assert queue.cancel(a)
    assert (status(queue, parent_id), status(queue, b)) == ("queued", "queued")


# -- fake parsing -----------------------------------------------------------------


def parse(llm, text, home=False, goal_id="u001"):
    return llm.parse_goal(text, {}, ITEMS, goal_id=goal_id, home=home)


def summary(children):
    return [(c.kind, c.item, c.count, c.id, c.source) for c in children]


@pytest.mark.parametrize("text", ["get 4 torches and 2 sticks", "get 4 torches then 2 sticks", "4 torches, 2 sticks"])
def test_fake_splits_on_and_then_comma(fake, text):
    children = parse(fake, text)
    assert summary(children) == [("obtain", "torch", 4, "", "user"), ("obtain", "stick", 2, "", "user")]
    assert all(c.text and "and" not in c.text.split() for c in children)


def test_fake_three_parts_with_an_oxford_comma(fake):
    children = parse(fake, "4 torches, 2 sticks, and 3 coal")
    assert [c.item for c in children] == ["torch", "stick", "coal"]


def test_fake_ready_for_night(fake):
    children = parse(fake, "get ready for night")
    assert summary(children) == [("build", None, 1, "", "user"), ("obtain", "torch", 8, "", "user"),
                                 ("obtain", "stone_sword", 1, "", "user")]
    assert children[0].blueprint["name"] == "small hut"
    assert summary(parse(fake, "get ready for night", home=True)) == [("obtain", "torch", 8, "", "user"),
                                                                     ("obtain", "stone_sword", 1, "", "user")]


def test_fake_part_that_does_not_parse_falls_back_to_one_goal(fake):
    result = parse(fake, "get 4 torches and 2 unobtainium")
    assert result == Unsupported("I don't know an item called unobtainium.")


def test_fake_single_goals_are_unchanged(fake):
    assert parse(fake, "get me 4 torches") == GoalSpec("u001", "obtain", "user", "get me 4 torches", item="torch", count=4)
    assert parse(fake, "guard me").kind == "guard"


def test_fake_guard_child_is_dropped_and_one_left_is_a_plain_goal(fake):
    result = parse(fake, "guard me and get 4 torches", goal_id="u005")
    assert result == GoalSpec("u005", "obtain", "user", "get 4 torches", item="torch", count=4)


def test_fake_only_guards_gives_unsupported(fake):
    assert isinstance(parse(fake, "guard me and guard bob"), Unsupported)


def test_fake_extra_builds_are_dropped(fake):
    children = parse(fake, "get 4 torches and build a hut and build a house")
    assert [c.kind for c in children] == ["obtain", "build"]


def test_fake_build_text_is_never_split(fake):
    result = parse(fake, "build a small wooden house with a door and a torch", goal_id="u006")
    assert isinstance(result, GoalSpec) and result.kind == "build"


def test_fake_kill_child(fake):
    children = parse(fake, "kill 2 zombies then get 4 torches")
    assert [(c.kind, c.target, c.count) for c in children] == [("kill", "zombie", 2), ("obtain", None, 4)]


# -- live parsing -----------------------------------------------------------------


def live_parse(monkeypatch, payload, **kwargs):
    calls = []

    def fake_json_call(self, purpose, system, user, schema):
        calls.append((purpose, system, user, schema))
        if purpose == "design_blueprint":
            return {"name": "hut", "size": [3, 3, 3], "palette": [{"key": "P", "block": "#planks"}],
                    "layers": [["PPP", "PPP", "PPP"], ["PPP", "P.P", "PPP"], ["PPP", "PPP", "PPP"]]}
        return payload

    monkeypatch.setattr(LLM, "_json_call", fake_json_call)
    result = LLM(mode="live").parse_goal("get ready for night", {"stick": 2}, ITEMS, goal_id="u007", **kwargs)
    return result, calls


def child(kind="obtain", text="", item="", count=1, target=""):
    return {"kind": kind, "text": text, "item": item, "count": count, "target": target}


def composite(*children):
    return {"kind": "composite", "item": "", "count": 1, "target": "", "reply": "", "children": list(children)}


def test_schema_has_composite_and_children():
    assert "composite" in PARSE_GOAL_SCHEMA["properties"]["kind"]["enum"]
    assert PARSE_GOAL_SCHEMA["properties"]["children"]["type"] == "array"
    assert "children" not in PARSE_GOAL_SCHEMA["required"]
    assert "children" in REPLAN_CHILDREN_SCHEMA["required"]


def test_live_composite(monkeypatch):
    payload = composite(child("obtain", "8 torches", "torch", 8), child("kill", "2 zombies", target="zombies", count=2),
                        child("guard", "guard me", target="me"), child("build", "a shelter"),
                        child("build", "another"), child("obtain", "unobtainium", "unobtainium"))
    result, calls = live_parse(monkeypatch, payload)
    assert [(c.kind, c.item, c.target, c.count, c.id, c.text) for c in result] == [
        ("obtain", "torch", None, 8, "", "8 torches"), ("kill", None, "zombie", 2, "", "2 zombies"),
        ("build", None, None, 1, "", "a shelter")]
    assert all(c.source == "user" for c in result)
    assert "composite" in calls[0][1]
    assert "already has a home" not in calls[0][1]
    assert result[2].blueprint["name"] == "hut"


def test_live_composite_home_line_and_one_child_left(monkeypatch):
    payload = composite(child("obtain", "8 torches", "torch", 8), child("guard", "guard me", target="me"))
    result, calls = live_parse(monkeypatch, payload, home=True)
    assert result == GoalSpec("u007", "obtain", "user", "8 torches", item="torch", count=8)
    assert "already has a home" in calls[0][1]


def test_live_composite_with_nothing_left(monkeypatch):
    payload = {**composite(child("obtain", "x", "unobtainium")), "reply": "I can fetch items."}
    result, _ = live_parse(monkeypatch, payload)
    assert result == Unsupported("I can fetch items.")
    result, _ = live_parse(monkeypatch, {**composite(), "reply": ""})
    assert isinstance(result, Unsupported) and result.reply


def test_live_composite_is_capped_at_six(monkeypatch):
    result, _ = live_parse(monkeypatch, composite(*[child("obtain", f"{n} sticks", "stick", n) for n in range(1, 9)]))
    assert [c.count for c in result] == [1, 2, 3, 4, 5, 6]


# -- replan_children --------------------------------------------------------------


def test_replan_fake_returns_remaining_with_empty_ids(fake):
    remaining = [GoalSpec("u004", "obtain", "user", "2 sticks", item="stick", count=2)]
    result = fake.replan_children("get ready", [], obtain(), "no wood", remaining, {}, ITEMS)
    assert result == [GoalSpec("", "obtain", "user", "2 sticks", item="stick", count=2)]
    assert fake.calls[-1].purpose == "replan_children"


def test_replan_live(monkeypatch):
    calls = []

    def fake_json_call(self, purpose, system, user, schema):
        calls.append((purpose, system, user, schema))
        return {"children": [child("obtain", "4 torches", "torch", 4), child("guard", "", target="me"),
                             child("obtain", "bad", "unobtainium")]}

    monkeypatch.setattr(LLM, "_json_call", fake_json_call)
    failed = obtain("coal", 3)
    result = LLM(mode="live").replan_children("get ready", [obtain()], failed, "no coal", [obtain("stick")],
                                              {"stick": 1}, ITEMS, home=True)
    assert result == [GoalSpec("", "obtain", "user", "4 torches", item="torch", count=4)]
    purpose, system, user, schema = calls[0]
    assert purpose == "replan_children" and schema is REPLAN_CHILDREN_SCHEMA
    assert "already has a home" in system and "no coal" in user


def test_replan_live_empty_means_give_up(monkeypatch):
    monkeypatch.setattr(LLM, "_json_call", lambda self, *a: {"children": []})
    assert LLM(mode="live").replan_children("x", [], obtain(), "why", [], {}, ITEMS) == []
