import json
import os
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

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
