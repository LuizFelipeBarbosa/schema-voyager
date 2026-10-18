"""Losing the bot (v3.0h): every way of losing it is a BackendError, and connected() never raises.

These run against real local sockets, because the risk is a bot that dies mid-request."""

import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schema_voyager.mineflayer_backend import BackendError, MineflayerBackend
from schema_voyager.models import Action

OBSERVATION = {"ok": True, "position": {"x": 0, "y": 64, "z": 0}, "inventory": {"oak_log": 1}}


def _serve(handler_class, server_class=ThreadingHTTPServer):
    server = server_class(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def serve():
    servers = []

    def start(handler_class, server_class=ThreadingHTTPServer):
        servers.append(_serve(handler_class, server_class))
        return MineflayerBackend(f"http://127.0.0.1:{servers[-1].server_address[1]}")

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _json_bot(routes):
    """A bot answering 200 with routes[path] (a dict, or raw bytes for a non-JSON body)."""

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = routes[self.path]
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = _answer

        def log_message(self, *args):
            pass

    return Handler


def _raw_bot(reply):
    """A bot that reads the request head, then writes `reply` (maybe nothing) and drops the connection."""

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            while self.rfile.readline() not in (b"\r\n", b""):
                pass
            if reply:
                self.wfile.write(reply)
                self.wfile.flush()
            if reply == b"RESET":
                self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")

    return Handler


class _ThreadingTCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


DROPPED_MID_REQUEST = [
    pytest.param(b"", id="closed-without-answering"),
    pytest.param(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{\"ok\": tr", id="truncated-body"),
    pytest.param(b"\x00garbage, not http\r\n\r\n", id="garbage"),
    pytest.param(b"RESET", id="connection-reset"),
]


@pytest.fixture
def refused_backend():
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    return MineflayerBackend(f"http://127.0.0.1:{port}")


# --- actions raise BackendError ------------------------------------------------


@pytest.mark.parametrize("reply", DROPPED_MID_REQUEST)
def test_a_bot_that_dies_mid_request_is_a_backend_error(serve, reply):
    backend = serve(_raw_bot(reply), _ThreadingTCPServer)
    with pytest.raises(BackendError, match="/observe"):
        backend.observe()
    with pytest.raises(BackendError, match="/mine"):
        backend.act(Action("mine", "oak_log"))


def test_a_refused_connection_is_a_backend_error(refused_backend):
    with pytest.raises(BackendError):
        refused_backend.observe()


def test_a_body_that_is_not_json_is_a_backend_error(serve):
    backend = serve(_json_bot({"/observe": b"<html>proxy error</html>"}))
    with pytest.raises(BackendError, match="/observe"):
        backend.observe()


def test_not_connected_is_a_backend_error_on_an_action_and_on_observe(serve):
    not_connected = {"ok": False, "error": "not_connected"}
    backend = serve(_json_bot({"/observe": not_connected, "/mine": not_connected}))
    with pytest.raises(BackendError, match=r"not connected.*/mine"):
        backend.act(Action("mine", "oak_log"))
    with pytest.raises(BackendError, match=r"not connected.*/observe"):
        backend.observe()


def test_an_ordinary_failure_is_still_an_outcome(serve):
    backend = serve(_json_bot({"/observe": OBSERVATION, "/mine": {"ok": False, "error": "unreachable", "inventory": {}}}))
    outcome = backend.act(Action("mine", "oak_log"))
    assert not outcome.ok and outcome.error == "unreachable"


# --- connected() ---------------------------------------------------------------


@pytest.mark.parametrize("health, expected", [
    ({"ok": True, "connected": True, "username": "bot", "version": "1.21"}, True),
    ({"ok": True, "connected": False}, False),
    ({"ok": True}, False),
])
def test_connected_reports_what_health_says(serve, health, expected):
    assert serve(_json_bot({"/health": health})).connected() is expected


def test_connected_is_false_for_a_body_that_is_not_json(serve):
    assert serve(_json_bot({"/health": b"garbage"})).connected() is False


def test_connected_is_false_when_the_connection_is_refused(refused_backend):
    assert refused_backend.connected() is False


@pytest.mark.parametrize("reply", DROPPED_MID_REQUEST)
def test_connected_is_false_when_the_bot_dies_mid_request(serve, reply):
    assert serve(_raw_bot(reply), _ThreadingTCPServer).connected() is False
