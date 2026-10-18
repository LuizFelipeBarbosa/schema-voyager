import json
import os
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

from schema_voyager.goals import GoalQueue
from schema_voyager.panel import _PanelHandler


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, ""


def test_panel_serves_traces_and_site_but_nothing_else(tmp_path):
    (tmp_path / "site" / "dist").mkdir(parents=True)
    (tmp_path / "site" / "dist" / "trace.html").write_text("<html></html>")
    (tmp_path / "runs" / "r1" / "episodes").mkdir(parents=True)
    (tmp_path / "runs" / "r1" / "episodes" / "D_h3_L6.jsonl").write_text('{"kind": "episode_start"}\n')
    (tmp_path / "runs" / "llm_cache.jsonl").write_text("{}\n")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=secret\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_PanelHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, body = _get(port, "/episodes.json")
        assert status == 200
        assert [e["path"] for e in json.loads(body)] == ["runs/r1/episodes/D_h3_L6.jsonl"]
        assert _get(port, "/site/dist/trace.html")[0] == 200
        assert _get(port, "/runs/r1/episodes/D_h3_L6.jsonl")[0] == 200
        for path in ("/.env", "/site/../.env", "/site/%2e%2e/.env", "/runs/llm_cache.jsonl"):
            assert _get(port, path)[0] == 404, path
    finally:
        server.shutdown()
        server.server_close()


def test_panel_serves_life_files_and_lists_runs(tmp_path):
    (tmp_path / "runs" / "old" / "episodes").mkdir(parents=True)
    (tmp_path / "runs" / "life1").mkdir(parents=True)
    (tmp_path / "runs" / "life1" / "life.jsonl").write_text('{"index": 1}\n')
    (tmp_path / "runs" / "life1" / "changes.jsonl").write_text('{"change": "r2"}\n')
    (tmp_path / "runs" / "life1" / "store.json").write_text("{}")
    (tmp_path / "runs" / "life1" / "life_state.json").write_text("{}")
    (tmp_path / "runs" / "life1" / "llm_cache.jsonl").write_text("{}\n")
    (tmp_path / "secret.json").write_text("{}")
    os.utime(tmp_path / "runs" / "old", (1, 1))

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_PanelHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        for name in ("life.jsonl", "changes.jsonl", "store.json"):
            assert _get(port, f"/runs/life1/{name}")[0] == 200, name
        assert _get(port, "/runs/old/life.jsonl")[0] == 404
        for path in ("/runs/life1/life_state.json", "/runs/life1/llm_cache.jsonl", "/runs/../secret.json",
                     "/runs/life1/../../secret.json", "/runs/%2e%2e/secret.json", "/runs/life1/%2e%2e/life1/life.jsonl/x"):
            assert _get(port, path)[0] == 404, path
        status, body = _get(port, "/runs.json")
        assert status == 200
        assert [(r["run"], r["has_life"]) for r in json.loads(body)] == [("life1", True), ("old", False)]
    finally:
        server.shutdown()
        server.server_close()


def _start_panel(root):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_PanelHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _request(port, method, path, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def _goal_lines(root, run):
    return [json.loads(line) for line in (root / "runs" / run / "goals.jsonl").read_text().splitlines()]


def _two_runs(root):
    for run, mtime in (("old", 100), ("new", 200)):
        (root / "runs" / run).mkdir(parents=True)
        goals = root / "runs" / run / "goals.jsonl"
        goals.write_text("")
        os.utime(goals, (mtime, mtime))


def test_post_goal_uses_newest_goals_file_or_explicit_run(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        assert _request(port, "POST", "/goals", {"text": "  10 torches "}) == (
            201, {"ok": True, "id": "u001", "run": "new", "status": "submitted"})
        line = _goal_lines(tmp_path, "new")[-1]
        assert (line["id"], line["status"], line["source"], line["text"]) == ("u001", "submitted", "user", "10 torches")
        assert _request(port, "POST", "/goals", {"text": "a", "run": None})[1]["run"] == "new"
        assert _request(port, "POST", "/goals", {"text": "a", "run": ""})[1]["run"] == "new"
        assert _request(port, "POST", "/goals", {"text": "b", "run": "old"}) == (
            201, {"ok": True, "id": "u001", "run": "old", "status": "submitted"})
        assert [g["id"] for g in _goal_lines(tmp_path, "old")] == ["u001"]
    finally:
        server.shutdown()
        server.server_close()


def test_goal_routes_reject_bad_runs(tmp_path):
    _two_runs(tmp_path)
    (tmp_path / "runs" / "empty").mkdir()
    (tmp_path / "runs" / "file").write_text("")
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        for run in ("..", ".", "../old", "a/b", "nope", "bad name", "file", 5):
            assert _request(port, "POST", "/goals", {"text": "x", "run": run}) == (
                404, {"ok": False, "error": "no_live_run"}), run
            assert _request(port, "POST", "/goals/u001/cancel", {"run": run})[0] == 404, run
        assert _request(port, "GET", "/goals.json?run=..")[0] == 404
        assert _request(port, "GET", "/goals.json?run=nope") == (404, {"ok": False, "error": "no_live_run"})
        # a run directory without goals.jsonl is a valid explicit target
        assert _request(port, "POST", "/goals", {"text": "x", "run": "empty"})[0] == 201
    finally:
        server.shutdown()
        server.server_close()


def test_goal_routes_without_any_goals_file(tmp_path):
    (tmp_path / "runs" / "r1").mkdir(parents=True)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        assert _request(port, "POST", "/goals", {"text": "x"}) == (404, {"ok": False, "error": "no_live_run"})
        assert _request(port, "POST", "/goals/u001/cancel", {}) == (404, {"ok": False, "error": "no_live_run"})
        assert _request(port, "GET", "/goals.json") == (200, {"run": None, "goals": []})
    finally:
        server.shutdown()
        server.server_close()


def test_get_goals_json_with_and_without_run(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        _request(port, "POST", "/goals", {"text": "one"})
        _request(port, "POST", "/goals", {"text": "two"})
        status, body = _request(port, "GET", "/goals.json")
        assert (status, body["run"]) == (200, "new")
        assert [(g["id"], g["text"], g["status"]) for g in body["goals"]] == [
            ("u001", "one", "submitted"), ("u002", "two", "submitted")]
        _request(port, "POST", "/goals", {"text": "old one", "run": "old"})
        status, body = _request(port, "GET", "/goals.json?run=old")
        assert (body["run"], [g["text"] for g in body["goals"]]) == ("old", ["old one"])
    finally:
        server.shutdown()
        server.server_close()


def test_post_requires_json_media_type(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        assert _request(port, "POST", "/goals", {"text": "x"}, {"Content-Type": "text/plain"}) == (
            415, {"ok": False, "error": "unsupported_media_type"})
        assert _request(port, "POST", "/goals", {"text": "x"}, {"Content-Type": "application/json; charset=utf-8"})[0] == 201
        assert _request(port, "POST", "/goals", {"text": "x"}, {"Content-Type": " Application/JSON "})[0] == 201
        assert _request(port, "POST", "/goals/u001/cancel", {}, {"Content-Type": "text/plain"})[0] == 415
    finally:
        server.shutdown()
        server.server_close()


def test_post_checks_origin_before_media_type(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        evil = {"Origin": "http://evil.example"}
        assert _request(port, "POST", "/goals", {"text": "x"}, evil) == (403, {"ok": False, "error": "forbidden_origin"})
        assert _request(port, "POST", "/goals", {"text": "x"}, {**evil, "Content-Type": "text/plain"})[0] == 403
        assert _request(port, "POST", "/goals/u001/cancel", {}, evil)[0] == 403
        assert _request(port, "POST", "/goals", {"text": "x"}, {"Origin": f"http://127.0.0.1:{port + 1}"})[0] == 403
        assert _goal_lines(tmp_path, "new") == []
        assert _request(port, "POST", "/goals", {"text": "x"}, {"Origin": f"http://127.0.0.1:{port}"})[0] == 201
        assert _request(port, "POST", "/goals", {"text": "y"}, {"Origin": f"http://localhost:{port}"})[0] == 201
    finally:
        server.shutdown()
        server.server_close()


def test_post_goal_rejects_bad_bodies(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        bodies = [
            {"text": ""}, {"text": "   "}, {"text": "x" * 201}, {"text": 5}, {"text": None}, {}, {"run": "new"},
        ]
        for body in bodies:
            status, payload = _request(port, "POST", "/goals", body)
            assert (status, payload["error"]) == (400, "bad_request"), body
            assert payload["detail"], body
        assert _request(port, "POST", "/goals", raw=b"{not json")[1]["error"] == "bad_request"
        assert _request(port, "POST", "/goals", raw=b"")[0] == 400
        assert _request(port, "POST", "/goals", raw=b'["text"]') == (
            400, {"ok": False, "error": "bad_request", "detail": "body must be a JSON object"})
        assert _request(port, "POST", "/goals", raw=b"null")[0] == 400
        assert _request(port, "POST", "/goals", {"text": "x", "pad": "p" * 5000}) == (
            400, {"ok": False, "error": "bad_request", "detail": "body too large"})
        assert _goal_lines(tmp_path, "new") == []
        assert _request(port, "POST", "/goals", {"text": "x" * 200})[0] == 201
    finally:
        server.shutdown()
        server.server_close()


def test_cancel_goal(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        _request(port, "POST", "/goals", {"text": "one"})
        _request(port, "POST", "/goals", {"text": "two"})
        assert _request(port, "POST", "/goals/u001/cancel", {}) == (
            200, {"ok": True, "id": "u001", "status": "cancelled"})
        assert _goal_lines(tmp_path, "new")[-1]["status"] == "cancelled"
        assert _request(port, "POST", "/goals/u001/cancel", {"run": "new"}) == (
            409, {"ok": False, "error": "not_cancellable", "status": "cancelled"})
        assert _request(port, "POST", "/goals/u099/cancel", {}) == (404, {"ok": False, "error": "unknown_goal"})

        queue = GoalQueue(tmp_path / "runs" / "new" / "goals.jsonl")
        for status in ("queued", "running", "done"):
            assert queue.mark("u002", status)
        assert _request(port, "POST", "/goals/u002/cancel", {}) == (
            409, {"ok": False, "error": "not_cancellable", "status": "done"})
        assert queue.get("u002").status == "done"
    finally:
        server.shutdown()
        server.server_close()


def test_other_post_paths_are_404_and_head_stays_405(tmp_path):
    _two_runs(tmp_path)
    server = _start_panel(tmp_path)
    port = server.server_address[1]
    try:
        for path in ("/episodes.json", "/goals/u001", "/goals/u001/other", "/x"):
            assert _request(port, "POST", path, {}) == (404, {"ok": False, "error": "not_found"}), path
        assert _request(port, "HEAD", "/goals.json")[0] == 405
        assert _request(port, "HEAD", "/site/dist/trace.html")[0] == 405
    finally:
        server.shutdown()
        server.server_close()
