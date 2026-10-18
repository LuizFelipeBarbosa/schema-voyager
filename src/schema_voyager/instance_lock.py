"""One live owner per v2 instance: run/lock-<instance> under SV2_HOME holds one JSON line
{pid, session, purpose, started}. See docs/v2-interfaces.md, "v2.3: the instance lock".
The shell side is sv2_lock_acquire / sv2_lock_release in scripts/v2-env.sh."""

import json
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class LockHeld(RuntimeError):
    """Another live session owns the instance."""


def _lock_path(instance: str) -> Path:
    home = Path(os.environ.get("SV2_HOME") or Path(__file__).resolve().parents[2])
    return home / "run" / f"lock-{instance}"


def _read(path: Path) -> dict | None:
    try:
        holder = json.loads(path.read_text().strip())
        return holder if isinstance(holder.get("pid"), int) else None
    except (OSError, ValueError, AttributeError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(entry) + "\n")
    os.replace(temp, path)


@contextmanager
def hold(instance: str, purpose: str):
    """Owns the instance's lock for the block. Raises LockHeld when a live process of another
    session holds it; takes over a stale lock (dead pid) with a warning; takes over from a
    holder of the same session (SV2_LOCK_SESSION) and hands the lock back on exit if that
    holder is still alive, else removes it. Also releases on SIGTERM."""
    path = _lock_path(instance)
    session = os.environ.get("SV2_LOCK_SESSION") or f"{os.getpid()}-{purpose}"
    holder = _read(path)
    previous = None
    if holder and holder["pid"] != os.getpid() and _pid_alive(holder["pid"]):
        if holder.get("session") != session:
            raise LockHeld(
                f"instance '{instance}' is locked by pid {holder['pid']} (session {holder.get('session')}, "
                f"purpose {holder.get('purpose')}, since {holder.get('started')}); {path}"
            )
        previous = holder
    elif holder:
        print(f"warning: taking over the stale lock of dead pid {holder['pid']} ({path})", file=sys.stderr)
    entry = {"pid": os.getpid(), "session": session, "purpose": purpose,
             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _write(path, entry)

    on_main_thread = threading.current_thread() is threading.main_thread()
    old_handler = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143)) if on_main_thread else None
    try:
        yield
    finally:
        if on_main_thread:
            signal.signal(signal.SIGTERM, old_handler)
        current = _read(path)
        if current and current["pid"] == os.getpid() and current.get("session") == session:
            if previous and _pid_alive(previous["pid"]):
                _write(path, previous)
            else:
                path.unlink(missing_ok=True)
