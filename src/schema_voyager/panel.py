"""Local web server for the trace panel (site/dist/trace.html).

Serves the site, the episode traces, each run's life.jsonl, changes.jsonl and
store.json, and results/ read-only, plus /episodes.json (every trace, newest
first) and /runs.json (every run directory, newest first) so the panel can
follow a run while it is being written. Run it from the repository root.
"""

from __future__ import annotations

import json
import posixpath
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

PANEL_PATH = "/site/dist/trace.html"
_SERVED = re.compile(
    r"^(site/.+|results/.+|runs/[^/]+/episodes/[^/]+\.jsonl|runs/[^/]+/(life\.jsonl|changes\.jsonl|store\.json))$"
)


def serve_panel(port: int, root: Path = Path(".")) -> None:
    handler = partial(_PanelHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Trace panel: http://127.0.0.1:{port}{PANEL_PATH}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def list_episodes(root: Path) -> list[dict[str, Any]]:
    paths = [*root.glob("runs/*/episodes/*.jsonl"), *root.glob("results/episodes/*.jsonl")]
    episodes = [
        {"path": path.relative_to(root).as_posix(), "run": path.parent.parent.name, "name": path.stem,
         "mtime": path.stat().st_mtime}
        for path in paths
    ]
    return sorted(episodes, key=lambda episode: -episode["mtime"])


def list_runs(root: Path) -> list[dict[str, Any]]:
    runs = []
    for run_dir in root.glob("runs/*/"):
        life = run_dir / "life.jsonl"
        mtime = max(run_dir.stat().st_mtime, life.stat().st_mtime if life.exists() else 0)
        runs.append({"run": run_dir.name, "has_life": life.exists(), "mtime": mtime})
    return sorted(runs, key=lambda run: -run["mtime"])


class _PanelHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".jsonl": "text/plain; charset=utf-8"}

    def do_GET(self) -> None:
        path = posixpath.normpath(unquote(urlsplit(self.path).path)).lstrip("/")
        if path in ("", "."):
            self.send_response(302)
            self.send_header("Location", PANEL_PATH)
            self.end_headers()
        elif path == "episodes.json":
            self._send_json(list_episodes(Path(self.directory)))
        elif path == "runs.json":
            self._send_json(list_runs(Path(self.directory)))
        elif _SERVED.match(path):
            super().do_GET()
        else:
            self.send_error(404)

    def _send_json(self, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.send_error(405)  # GET only, so every path goes through the filter above

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")  # traces grow while a run is live
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass  # the panel polls; one line per request would flood the terminal
