"""MineflayerBackend.toss and the free_slots field, against a fake HTTP bot (contracts/bot_api.md v3.0)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager.mineflayer_backend import MineflayerBackend


class FakeBot:
    """Serves canned JSON per path and records every request."""

    def __init__(self):
        self.responses: dict[str, dict] = {}
        self.requests: list[tuple[str, dict]] = []
        bot = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
                bot.requests.append((self.path, body))
                payload = json.dumps(bot.responses.get(self.path, {"ok": True})).encode()
                self.send_response(200)
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
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def backend(bot):
    return MineflayerBackend(api_url=bot.url, natural=True)


def test_toss_posts_items_and_returns_tossed_without_zero_counts(bot, backend):
    bot.responses["/toss"] = {"ok": True, "tossed": {"dirt": 40, "granite": 0}, "inventory": {"dirt": 24}}

    assert backend.toss({"dirt": 40, "granite": 64}) == {"dirt": 40}
    assert bot.requests == [("/toss", {"items": {"dirt": 40, "granite": 64}})]


def test_empty_request_makes_no_request(bot, backend):
    assert backend.toss({}) == {}
    assert bot.requests == []


def test_failed_toss_without_tossed_returns_empty(bot, backend):
    bot.responses["/toss"] = {"ok": False, "error": "busy"}

    assert backend.toss({"dirt": 1}) == {}


def test_toss_refreshes_the_cached_inventory(bot, backend):
    bot.responses["/observe"] = {"ok": True, "inventory": {"dirt": 64}}
    backend.observe()
    bot.responses["/toss"] = {"ok": True, "tossed": {"dirt": 40}, "inventory": {"dirt": 24}}

    backend.toss({"dirt": 40})

    assert backend._latest_inventory == {"dirt": 24}


def test_observe_parses_free_slots(bot, backend):
    bot.responses["/observe"] = {"ok": True, "inventory": {}, "free_slots": 12}

    assert backend.observe().free_slots == 12


def test_a_body_without_free_slots_gives_none(bot, backend):
    bot.responses["/observe"] = {"ok": True, "inventory": {}}

    assert backend.observe().free_slots is None


def test_begin_episode_parses_free_slots(bot, backend):
    bot.responses["/reset_episode"] = {"ok": True, "inventory": {}, "free_slots": 36}

    assert backend.begin_episode().free_slots == 36
