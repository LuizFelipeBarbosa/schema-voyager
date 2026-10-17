#!/usr/bin/env bash
# Replaces the world with a snapshot and restarts the server (the bot reconnects by itself).
# Usage: scripts/world-restore.sh <name>
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
name="${1:?usage: world-restore.sh <name>}"

snapshot="$SERVER_DIR/snapshots/$name"
[ -d "$snapshot" ] || die "no snapshot at $snapshot"
level_name=$(sv2_level_name)
[ -n "$level_name" ] || die "no level-name in $SERVER_DIR/server.properties"

started=$SECONDS
sv2_stop_server
rm -rf "$SERVER_DIR/$level_name"
cp -cR "$snapshot" "$SERVER_DIR/$level_name"
sv2_start_server
sv2_apply_gamerules
echo "restored '$name' in $((SECONDS - started)) s"
