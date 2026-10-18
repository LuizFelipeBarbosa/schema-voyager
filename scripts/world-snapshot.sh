#!/usr/bin/env bash
# Copies the running world to server-<inst>/snapshots/<name> (APFS clone) and records its seed
# in <snapshot>/sv2-seed, so world-restore.sh brings back the same world.
# Usage: scripts/world-snapshot.sh <name>
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
name="${1:?usage: world-snapshot.sh <name>}"

world="$SERVER_DIR/$(sv2_level_name)"
target="$SERVER_DIR/snapshots/$name"
[ -d "$world" ] || die "no world at $world"
[ ! -e "$target" ] || die "snapshot '$name' already exists; remove $target first"

mkdir -p "$SERVER_DIR/snapshots"
sv2_rcon "save-off" >/dev/null
trap 'sv2_rcon "save-on" >/dev/null' EXIT
# Flushing a freshly generated world can take well over the default 10 s RCON timeout.
SV2_RCON_TIMEOUT=120 sv2_rcon "save-all flush" | grep -q "Saved the game" || die "save-all flush did not complete"
seed=$(sv2_rcon "seed" | sv2_parse_seed) || die "could not read the world seed over RCON"
cp -cR "$world" "$target"
echo "$seed" > "$target/sv2-seed"
echo "snapshot '$name' saved: $target"
