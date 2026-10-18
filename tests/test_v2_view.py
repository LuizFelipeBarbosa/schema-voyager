"""Contract v2.5: every action's `position` reaches the outcome detail (the
route minimap of the trace panel reads it)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager import normalize
from schema_voyager.mineflayer_backend import MineflayerBackend
from schema_voyager.models import Action

POSITION = {"x": 12.5, "y": -58.0, "z": -3.5}


@pytest.fixture(autouse=True)
def simple_normalize(monkeypatch):
    monkeypatch.setattr(normalize, "members", lambda name: [name])
    monkeypatch.setattr(normalize, "resolve", lambda name, inventory: name)


@pytest.fixture
def bot_url():
    """A bot API whose every response carries POSITION (and the inventory)."""
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps({"ok": True, "position": POSITION, "inventory": {"oak_log": 1}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


ACTIONS = [
    Action("mine", "oak_log"),
    Action("craft", "oak_planks", times=1),
    Action("smelt", "iron_ingot", ingredient="raw_iron", fuel="coal"),
    Action("explore", direction="north"),
    Action("explore", direction="up"),
]


@pytest.mark.parametrize("action", ACTIONS, ids=lambda action: f"{action.kind}-{action.direction}")
def test_natural_actions_pass_the_position_into_the_detail(bot_url, action):
    backend = MineflayerBackend(bot_url, natural=True)
    backend._latest_inventory = {"raw_iron": 1, "coal": 1, "oak_log": 1}
    assert backend.act(action).detail["position"] == POSITION


@pytest.mark.parametrize("action", [Action("mine", "oak_log"), Action("craft", "oak_planks", times=1), Action("explore")])
def test_v1_actions_pass_the_position_into_the_detail(bot_url, action):
    backend = MineflayerBackend(bot_url, arena=object())
    assert backend.act(action).detail["position"] == POSITION


def test_a_response_without_a_position_adds_none(bot_url):
    backend = MineflayerBackend(bot_url, natural=True)
    backend._post = lambda path, payload=None, timeout=0: {"ok": True, "inventory": {}}
    assert "position" not in backend.act(Action("mine", "oak_log")).detail
