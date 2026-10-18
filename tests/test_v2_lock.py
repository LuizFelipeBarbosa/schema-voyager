"""The instance lock: the Python context manager and the shell functions of v2-env.sh,
against a temporary SV2_HOME (no server, bot or life is started)."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from schema_voyager import instance_lock

ROOT = Path(__file__).resolve().parents[1]


def _dead_pid() -> int:
    child = subprocess.Popen(["true"])
    child.wait()
    return child.pid


def _write_lock(home: Path, pid: int, session: str = "other", purpose: str = "test") -> Path:
    path = home / "run" / "lock-a"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "session": session, "purpose": purpose, "started": "t"}) + "\n")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SV2_HOME", str(tmp_path))
    monkeypatch.delenv("SV2_LOCK_SESSION", raising=False)
    return tmp_path


# -- Python ---------------------------------------------------------------------------


def test_hold_writes_the_lock_and_removes_it(home):
    with instance_lock.hold("a", "life"):
        lock = json.loads((home / "run" / "lock-a").read_text())
        assert lock["pid"] == os.getpid() and lock["purpose"] == "life"
        assert set(lock) == {"pid", "session", "purpose", "started"}
    assert not (home / "run" / "lock-a").exists()


def test_hold_refuses_a_live_holder_of_another_session(home):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        path = _write_lock(home, holder.pid, session="theirs", purpose="demo")
        with pytest.raises(instance_lock.LockHeld, match=f"pid {holder.pid}.*demo"):
            with instance_lock.hold("a", "life"):
                pytest.fail("must not run")
        assert json.loads(path.read_text())["pid"] == holder.pid
    finally:
        holder.kill()


def test_hold_takes_over_a_stale_lock(home, capsys):
    _write_lock(home, _dead_pid())
    with instance_lock.hold("a", "life"):
        assert json.loads((home / "run" / "lock-a").read_text())["pid"] == os.getpid()
    assert "stale" in capsys.readouterr().err
    assert not (home / "run" / "lock-a").exists()


def test_hold_takes_over_from_the_same_session_and_hands_back(home, monkeypatch):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        path = _write_lock(home, holder.pid, session="s1", purpose="demo")
        monkeypatch.setenv("SV2_LOCK_SESSION", "s1")
        with instance_lock.hold("a", "life"):
            lock = json.loads(path.read_text())
            assert lock["pid"] == os.getpid() and lock["session"] == "s1" and lock["purpose"] == "life"
        back = json.loads(path.read_text())
        assert back["pid"] == holder.pid and back["purpose"] == "demo"
    finally:
        holder.kill()


def test_hand_back_removes_the_lock_when_the_previous_holder_died(home, monkeypatch):
    holder = subprocess.Popen(["sleep", "30"])
    path = _write_lock(home, holder.pid, session="s1")
    monkeypatch.setenv("SV2_LOCK_SESSION", "s1")
    with instance_lock.hold("a", "life"):
        holder.kill()
        holder.wait()
    assert not path.exists()


def test_hold_releases_on_an_exception(home):
    with pytest.raises(ValueError):
        with instance_lock.hold("a", "life"):
            raise ValueError("boom")
    assert not (home / "run" / "lock-a").exists()


def test_hold_releases_on_sigterm(home):
    script = (
        "import sys, time\n"
        "from schema_voyager import instance_lock\n"
        "with instance_lock.hold('a', 'life'):\n"
        "    print('held', flush=True); time.sleep(30)\n"
    )
    child = subprocess.Popen(
        ["python3", "-c", script], stdout=subprocess.PIPE, text=True,
        env={**os.environ, "SV2_HOME": str(home), "PYTHONPATH": str(ROOT / "src")},
    )
    assert child.stdout.readline().strip() == "held"
    assert (home / "run" / "lock-a").exists()
    child.send_signal(signal.SIGTERM)
    child.wait(timeout=10)
    assert not (home / "run" / "lock-a").exists()


# -- shell ----------------------------------------------------------------------------


def _bash(home: Path, script: str, **env) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith("SV2_")}
    return subprocess.run(
        ["bash", "-c", f"source {ROOT}/scripts/v2-env.sh; {script}"], cwd=ROOT, capture_output=True, text=True,
        env={**base, "SV2_HOME": str(home), **env}, timeout=30,
    )


def test_shell_acquire_and_release(home):
    result = _bash(home, 'sv2_lock_acquire test; cat "$SV2_LOCK_FILE"; sv2_lock_release; test ! -e "$SV2_LOCK_FILE"')
    assert result.returncode == 0, result.stderr
    lock = json.loads(result.stdout)
    assert lock["purpose"] == "test" and lock["session"].endswith("-test") and isinstance(lock["pid"], int)


def test_shell_exit_releases_the_lock(home):
    assert _bash(home, "sv2_lock_acquire test").returncode == 0
    assert not (home / "run" / "lock-a").exists()


def test_shell_refuses_a_second_session_naming_the_holder(home):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        _write_lock(home, holder.pid, session="theirs", purpose="demo")
        result = _bash(home, "sv2_lock_acquire test")
        assert result.returncode != 0
        assert f"pid {holder.pid}" in result.stderr and "theirs" in result.stderr and "demo" in result.stderr
        assert json.loads((home / "run" / "lock-a").read_text())["pid"] == holder.pid
    finally:
        holder.kill()


def test_shell_child_with_the_same_session_passes_through(home):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        path = _write_lock(home, holder.pid, session="s1", purpose="demo")
        result = _bash(home, "sv2_lock_acquire test; sv2_lock_release", SV2_LOCK_SESSION="s1")
        assert result.returncode == 0, result.stderr
        assert json.loads(path.read_text())["pid"] == holder.pid
    finally:
        holder.kill()


def test_shell_takes_over_a_stale_lock(home):
    _write_lock(home, _dead_pid())
    result = _bash(home, 'sv2_lock_acquire test; grep -c "$$" "$SV2_LOCK_FILE"')
    assert result.returncode == 0, result.stderr
    assert "stale" in result.stderr


def test_shell_release_only_by_the_holder(home):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        path = _write_lock(home, holder.pid, session="s1")
        assert _bash(home, "sv2_lock_release", SV2_LOCK_SESSION="s1").returncode == 0
        assert _bash(home, "sv2_lock_release").returncode == 0
        assert path.exists()
    finally:
        holder.kill()


def test_shell_releases_on_sigterm(home):
    child = subprocess.Popen(
        ["bash", "-c", f'source {ROOT}/scripts/v2-env.sh; sv2_lock_acquire test; echo held; sleep 30 & wait'],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "SV2_HOME": str(home)},
    )
    assert child.stdout.readline().strip() == "held"
    assert (home / "run" / "lock-a").exists()
    child.send_signal(signal.SIGTERM)
    child.wait(timeout=10)
    time.sleep(0.2)
    assert not (home / "run" / "lock-a").exists()


@pytest.mark.parametrize("script", sorted(p.name for p in (ROOT / "scripts").glob("*.sh")))
def test_scripts_parse(script):
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / script)]).returncode == 0
