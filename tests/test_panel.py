import json
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
