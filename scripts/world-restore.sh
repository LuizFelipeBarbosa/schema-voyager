#!/usr/bin/env bash
# Replaces the world with a snapshot and restarts the server (the bot reconnects by itself).
# Usage: scripts/world-restore.sh <name>
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
sv2_lock_acquire world-restore
name="${1:?usage: world-restore.sh <name>}"

snapshot="$SERVER_DIR/snapshots/$name"
[ -d "$snapshot" ] || die "no snapshot at $snapshot"
if seed=$(sv2_snapshot_seed "$snapshot"); then
  # sv2_start_server picks the world folder and seed from SV2_SEED, so it must name the snapshot's.
  level_name="world-$seed"
  if [ -n "${SV2_SEED:-}" ] && [ "$SV2_SEED" != "$seed" ]; then
    echo "using the snapshot's seed $seed (overriding SV2_SEED=$SV2_SEED)"
  else
    echo "using the snapshot's seed $seed"
  fi
  export SV2_SEED="$seed"
else
  level_name=$(sv2_level_name)
  [ -n "$level_name" ] || die "no level-name in $SERVER_DIR/server.properties"
  echo "warning: snapshot '$name' has no sv2-seed; the server starts on the current settings (set SV2_SEED=<seed> if they are not its seed)" >&2
fi

started=$SECONDS
sv2_stop_server
rm -rf "$SERVER_DIR/$level_name"
cp -cR "$snapshot" "$SERVER_DIR/$level_name"
sv2_start_server
sv2_apply_gamerules
echo "restored '$name' in $((SECONDS - started)) s"
