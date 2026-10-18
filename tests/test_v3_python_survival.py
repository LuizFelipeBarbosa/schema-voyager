"""v3.2: the Python side of survival: nearby_entities, 409 busy retries, interrupted actions and their retry budget."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager import mineflayer_backend
from schema_voyager.episode import MAX_INTERRUPTS, Episode
from schema_voyager.llm import LLM
from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend
from schema_voyager.models import Action, Outcome, Task
from schema_voyager.trace import EpisodeLog


class FakeBot:
    """Serves canned JSON per path (a list is answered one entry per request, the last repeating),
    optionally with an HTTP status, and records every request path."""

    def __init__(self):
        self.responses: dict[str, dict | list[tuple[int, dict]]] = {}
        self.requests: list[str] = []
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                bot.requests.append(self.path)
                canned = bot.responses.get(self.path, {"ok": True})
                if isinstance(canned, list):
                    status, body = canned[min(bot.requests.count(self.path), len(canned)) - 1]
                else:
                    status, body = 200, canned
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


@pytest.fixture
def bot():
    fake = FakeBot()
    fake.responses["/observe"] = {"ok": True, "inventory": {"dirt": 1}}
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def backend(bot):
    return MineflayerBackend(api_url=bot.url, natural=True)


@pytest.fixture
def sleeps(monkeypatch):
    """Patch the backend's sleep and clock: sleeping advances the clock instead of waiting."""
    slept: list[float] = []
    now = [0.0]

    def fake_sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(mineflayer_backend, "_sleep", fake_sleep)
    monkeypatch.setattr(mineflayer_backend, "_monotonic", lambda: now[0])
    return slept


BUSY = {"ok": False, "error": "busy", "detail": "guard"}


def test_request_timeout_is_180():
    assert mineflayer_backend.REQUEST_TIMEOUT_S == 180


# --- nearby_entities -----------------------------------------------------------


def test_nearby_entities_are_parsed_and_malformed_entries_skipped(bot, backend):
    bot.responses["/observe"] = {
        "ok": True,
        "nearby_entities": {
            "zombie": {"count": 2, "nearest": 6.4, "kind": "hostile"},
            "cow": {"count": 3, "nearest": 11, "kind": "animal"},
            "no_kind": {"count": 1, "nearest": 2.0},
            "bad_count": {"count": "2", "nearest": 2.0, "kind": "hostile"},
            "bad_nearest": {"count": 1, "nearest": None, "kind": "hostile"},
            "not_a_dict": 4,
        },
    }
    assert backend.observe().entities == {
        "zombie": {"count": 2, "nearest": 6.4, "kind": "hostile"},
        "cow": {"count": 3, "nearest": 11.0, "kind": "animal"},
    }


def test_missing_nearby_entities_gives_empty(bot, backend):
    assert backend.observe().entities == {}


# --- 409 busy ------------------------------------------------------------------


def test_busy_twice_then_ok_succeeds_after_waiting_025_then_05(bot, backend, sleeps):
    bot.responses["/observe"] = [(409, BUSY), (409, BUSY), (200, {"ok": True, "inventory": {"dirt": 2}})]
    assert backend.observe().inventory == {"dirt": 2}
    assert bot.requests == ["/observe"] * 3
    assert sleeps == [0.25, 0.5]


def test_busy_past_60_seconds_raises_naming_409_and_the_detail(bot, backend, sleeps):
    bot.responses["/observe"] = [(409, {"ok": False, "error": "busy", "detail": "orphan"})]
    with pytest.raises(BackendError, match=r"409.*orphan"):
        backend.observe()
    assert sum(sleeps) >= 60
    assert sum(sleeps[:-1]) < 60


@pytest.mark.parametrize("status, body", [(409, {"ok": False, "error": "not_connected"}), (500, {"ok": False, "error": "busy"})])
def test_other_http_errors_raise_at_once(bot, backend, sleeps, status, body):
    bot.responses["/observe"] = [(status, body)]
    with pytest.raises(BackendError, match=str(status)):
        backend.observe()
    assert bot.requests == ["/observe"]
    assert sleeps == []


def test_say_does_not_retry_a_busy_bot(bot, backend, sleeps):
    bot.responses["/chat"] = [(409, BUSY)]
    backend.say("hello")
    assert bot.requests == ["/chat"]
    assert sleeps == []


# --- interrupted ---------------------------------------------------------------

INTERRUPTED = {
    "ok": False,
    "error": "interrupted",
    "detail": {"threat": "zombie", "outcome": "killed"},
    "blocks_walked": 3.0,
    "position": {"x": 1, "y": 64, "z": 2},
    "events": [{"seq": 1, "type": "guard_fight"}],
}


@pytest.mark.parametrize(
    "action",
    [
        Action("mine", target="#log", tool="hand"),
        Action("craft", target="stick", times=1),
        Action("craft", target="#planks", times=2),
        Action("smelt", target="iron_ingot", ingredient="raw_iron", fuel="coal", times=1),
        Action("explore", direction="N"),
    ],
    ids=["mine", "craft", "craft-group", "smelt", "explore"],
)
def test_interrupted_body_becomes_an_interrupted_outcome(bot, backend, action):
    bot.responses["/observe"] = {"ok": True, "inventory": {"oak_log": 2, "raw_iron": 1, "coal": 1}}
    for path in ("/mine", "/craft", "/smelt", "/explore"):
        bot.responses[path] = INTERRUPTED

    outcome = backend.act(action)

    assert outcome.ok is False
    assert outcome.error == "interrupted"
    assert outcome.detail["threat"] == "zombie"
    assert outcome.detail["outcome"] == "killed"
    assert "detail" not in outcome.detail
    assert outcome.inventory == {"oak_log": 2, "raw_iron": 1, "coal": 1}


# --- Episode.act ---------------------------------------------------------------


class ScriptedBackend:
    """Answers each action with the next scripted error (None is ok); records the actions."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.actions: list[Action] = []

    def act(self, action):
        self.actions.append(action)
        if action.kind == "explore":
            return Outcome(ok=True)
        error = self.errors.pop(0) if self.errors else None
        return Outcome(ok=error is None, error=error)


def run_act(tmp_path, errors):
    backend = ScriptedBackend(errors)
    task = Task("g001", "train", "#log", 1, {}, (), 60, "test")
    episode = Episode(
        episode_id="D/life/g001-test", system="D", task=task, layout_seed=0, world_id="w1", backend=backend,
        llm=LLM(mode="fake", cache_path=tmp_path / "cache.jsonl"), log=EpisodeLog(None, "test"),
    )
    outcome = episode.act(Action("mine", target="oak_log"))
    return outcome, [action.kind for action in backend.actions]


def test_interrupted_then_ok_retries_the_same_action_without_an_explore(tmp_path):
    outcome, kinds = run_act(tmp_path, ["interrupted", None])
    assert outcome.ok
    assert kinds == ["mine", "mine"]


def test_interrupts_have_a_budget_of_max_interrupts(tmp_path):
    outcome, kinds = run_act(tmp_path, ["interrupted"] * 5 + [None])
    assert MAX_INTERRUPTS == 4
    assert outcome.error == "interrupted"
    assert kinds == ["mine"] * 5


def test_interrupts_do_not_use_up_the_explore_retries(tmp_path):
    outcome, kinds = run_act(tmp_path, ["interrupted", "interrupted", "unreachable", "unreachable", "unreachable"])
    assert outcome.error == "unreachable"
    assert kinds == ["mine", "mine", "mine", "explore", "mine", "explore", "mine"]
