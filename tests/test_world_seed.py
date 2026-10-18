"""The seed helpers of v2-env.sh that world-snapshot.sh and world-restore.sh use,
run in bash against a temporary SV2_HOME (no server, RCON or snapshot script is run)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _bash(home: Path, script: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{ROOT}/scripts/v2-env.sh"; {script}'],
        input=stdin, capture_output=True, text=True, timeout=30,
        env={**os.environ, "SV2_HOME": str(home)},
    )


@pytest.mark.parametrize("reply, seed", [
    ("Seed: [2024]\n", "2024"),
    ("Seed: [-4172144997902289642]\n", "-4172144997902289642"),
])
def test_parse_seed(tmp_path, reply, seed):
    result = _bash(tmp_path, "sv2_parse_seed", reply)
    assert (result.returncode, result.stdout.strip()) == (0, seed)


@pytest.mark.parametrize("reply", ["", "Unknown command\n", "Seed: [abc]\n", "Seed: []\n"])
def test_parse_seed_fails_on_garbage(tmp_path, reply):
    result = _bash(tmp_path, "sv2_parse_seed", reply)
    assert result.returncode != 0 and result.stdout == ""


@pytest.mark.parametrize("content, seed", [("2024\n", "2024"), ("-77\n", "-77")])
def test_snapshot_seed_present(tmp_path, content, seed):
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "sv2-seed").write_text(content)
    result = _bash(tmp_path, f'sv2_snapshot_seed "{tmp_path}/snap"')
    assert (result.returncode, result.stdout.strip()) == (0, seed)


def test_snapshot_seed_absent_or_malformed(tmp_path):
    (tmp_path / "snap").mkdir()
    assert _bash(tmp_path, f'sv2_snapshot_seed "{tmp_path}/snap"').returncode != 0
    (tmp_path / "snap" / "sv2-seed").write_text("not a seed\n")
    assert _bash(tmp_path, f'sv2_snapshot_seed "{tmp_path}/snap"').returncode != 0
