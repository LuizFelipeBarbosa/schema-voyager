"""Local web server for the trace panel (site/dist/trace.html).

Serves the site, the episode traces, each run's life.jsonl, changes.jsonl and
store.json, and results/ read-only, plus /episodes.json (every trace, newest
first) and /runs.json (every run directory, newest first) so the panel can
follow a run while it is being written. Run it from the repository root.

It also steers the live life through that run's goals.jsonl (GoalQueue, v3.1f):
POST /goals {"text", "run"?}, GET /goals.json?run=, POST /goals/<id>/cancel {"run"?}, and for a build
goal waiting for approval POST /goals/<id>/approve and /discard {"run"?}; GET /goals.json adds a `preview`
to goals with a blueprint (v3.4b).
POST accepts only application/json from the panel's own origin. It binds 127.0.0.1.
"""

from __future__ import annotations

import json
import posixpath
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from . import blueprint
from .goals import GoalEntry, GoalQueue

PANEL_PATH = "/site/dist/trace.html"
_SERVED = re.compile(
    r"^(site/.+|results/.+|runs/[^/]+/episodes/[^/]+\.jsonl|runs/[^/]+/(life\.jsonl|changes\.jsonl|store\.json))$"
)
_RUN_NAME = re.compile(r"[A-Za-z0-9._-]+")
_GOAL_ACTION_PATH = re.compile(r"^goals/([^/]+)/(cancel|approve|discard)$")
_FINAL_STATUSES = {"done", "failed", "cancelled", "unsupported"}
_MAX_BODY = 4096
_MAX_GOAL_TEXT = 200


class _ApiError(Exception):
    def __init__(self, http_status: int, error: str, **fields: Any) -> None:
        super().__init__(error)
        self.status = http_status
        self.payload = {"ok": False, "error": error, **fields}


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


def _find_run(root: Path, requested: Any) -> str | None:
    """The run to steer: `requested`, else the run whose goals.jsonl changed last (None: there is none).

    Raises no_live_run for a name that isn't a run directory under runs/.
    """
    if requested in (None, ""):
        newest = max(root.glob("runs/*/goals.jsonl"), key=lambda path: path.stat().st_mtime, default=None)
        return newest.parent.name if newest else None
    valid = isinstance(requested, str) and _RUN_NAME.fullmatch(requested) and requested not in (".", "..")
    if not valid or not (root / "runs" / requested).is_dir():
        raise _ApiError(404, "no_live_run")
    return requested


def _goal_json(goal: GoalEntry) -> dict[str, Any]:
    """The goals.jsonl line, plus the build preview for a goal with a blueprint (None if it isn't valid)."""
    entry = goal.to_json()
    bp = entry["blueprint"]
    if bp is not None:
        entry["preview"] = None if blueprint.validate(bp) else {
            "name": bp["name"], "size": bp["size"], "layers": blueprint.render(bp),
            "palette": dict(bp["palette"]), "materials": blueprint.materials(bp),
        }
    return entry


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
        elif path == "goals.json":
            self._get_goals(parse_qs(urlsplit(self.path).query).get("run", [None])[0])
        elif _SERVED.match(path):
            super().do_GET()
        else:
            self.send_error(404)

    def _get_goals(self, requested_run: str | None) -> None:
        try:
            run = _find_run(Path(self.directory), requested_run)
        except _ApiError as error:
            self._send_json(error.payload, error.status)
            return
        goals = GoalQueue(Path(self.directory) / "runs" / run / "goals.jsonl").goals() if run else []
        self._send_json({"run": run, "goals": [_goal_json(goal) for goal in goals]})

    def do_POST(self) -> None:
        path = posixpath.normpath(unquote(urlsplit(self.path).path)).lstrip("/")
        action_match = _GOAL_ACTION_PATH.match(path)
        try:
            if path != "goals" and not action_match:
                raise _ApiError(404, "not_found")
            body = self._read_json_object()
            if action_match:
                goal_id, action = action_match.groups()
                if action == "cancel":
                    payload = self._cancel_goal(goal_id, body.get("run"))
                else:
                    payload = self._answer_approval(goal_id, action, body.get("run"))
                status = 200
            else:
                payload, status = self._submit_goal(body), 201
        except _ApiError as error:
            payload, status = error.payload, error.status
        self._send_json(payload, status)

    def _read_json_object(self) -> dict[str, Any]:
        """The request body, after the origin, media type and size checks."""
        own_port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (f"http://127.0.0.1:{own_port}", f"http://localhost:{own_port}"):
            raise _ApiError(403, "forbidden_origin")
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise _ApiError(415, "unsupported_media_type")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise _ApiError(400, "bad_request", detail="invalid Content-Length") from None
        if not 0 <= length <= _MAX_BODY:
            raise _ApiError(400, "bad_request", detail="body too large")
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            raise _ApiError(400, "bad_request", detail="body is not valid JSON") from None
        if not isinstance(body, dict):
            raise _ApiError(400, "bad_request", detail="body must be a JSON object")
        return body

    def _submit_goal(self, body: dict[str, Any]) -> dict[str, Any]:
        text = body.get("text")
        if not isinstance(text, str):
            raise _ApiError(400, "bad_request", detail="text must be a string")
        text = text.strip()
        if not 1 <= len(text) <= _MAX_GOAL_TEXT:
            raise _ApiError(400, "bad_request", detail=f"text must be 1 to {_MAX_GOAL_TEXT} characters")
        queue, run = self._live_queue(body.get("run"))
        goal_id = queue.submit(text, "user")
        return {"ok": True, "id": goal_id, "run": run, "status": "submitted"}

    def _cancel_goal(self, goal_id: str, requested_run: Any) -> dict[str, Any]:
        queue, _ = self._live_queue(requested_run)
        goal = queue.get(goal_id)
        if goal is None:
            raise _ApiError(404, "unknown_goal")
        if goal.status in _FINAL_STATUSES:
            raise _ApiError(409, "not_cancellable", status=goal.status)
        if not queue.cancel(goal_id):  # the life finished it meanwhile
            raise _ApiError(409, "not_cancellable", status=queue.get(goal_id).status)
        return {"ok": True, "id": goal_id, "status": "cancelled"}

    def _answer_approval(self, goal_id: str, action: str, requested_run: Any) -> dict[str, Any]:
        """Approve (queued) or discard (cancelled) a build goal that is awaiting_approval."""
        queue, _ = self._live_queue(requested_run)
        goal = queue.get(goal_id)
        if goal is None:
            raise _ApiError(404, "unknown_goal")
        new_status, fields = ("queued", {}) if action == "approve" else ("cancelled", {"reason": "discarded"})
        if goal.status != "awaiting_approval" or not queue.mark(goal_id, new_status, **fields):
            raise _ApiError(409, "not_awaiting_approval", status=queue.get(goal_id).status)
        return {"ok": True, "id": goal_id, "status": new_status}

    def _live_queue(self, requested_run: Any) -> tuple[GoalQueue, str]:
        root = Path(self.directory)
        run = _find_run(root, requested_run)
        if run is None:
            raise _ApiError(404, "no_live_run")
        return GoalQueue(root / "runs" / run / "goals.jsonl"), run

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
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
