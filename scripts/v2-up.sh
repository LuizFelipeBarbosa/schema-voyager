#!/usr/bin/env bash
# Brings up the v2 server and bot of one instance in detached screen sessions.
# Usage: [SV2_HOME=...] [SV2_INSTANCE=b SV2_*_PORT=...] [SV2_SEED=...] scripts/v2-up.sh [--no-bot]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings

WORLD_WIDTH=512

download_jar_once() {
  [ -f "$JAR_CACHE" ] && return
  local manifest="https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
  local version_url server_url sha1
  version_url=$(curl -fsS "$manifest" | "$SV2_PYTHON" -I -c '
import json, sys
print(next(v["url"] for v in json.load(sys.stdin)["versions"] if v["id"] == "1.21.1"))')
  read -r server_url sha1 < <(curl -fsS "$version_url" | "$SV2_PYTHON" -I -c '
import json, sys
d = json.load(sys.stdin)["downloads"]["server"]
print(d["url"], d["sha1"])')
  mkdir -p "$(dirname "$JAR_CACHE")"
  curl -fsS -o "$JAR_CACHE.part" "$server_url"
  [ "$(shasum -a 1 < "$JAR_CACHE.part" | cut -d' ' -f1)" = "$sha1" ] || { rm -f "$JAR_CACHE.part"; die "server jar sha1 mismatch"; }
  mv "$JAR_CACHE.part" "$JAR_CACHE"
}

# Centres the border on the bot's position (the world spawn, with spawnRadius 0)
# unless the world already has one.
set_world_border() {
  sv2_rcon "worldborder get" | grep -q "59999968" || return 0
  local pos x z
  pos=$(curl -fsS -X POST "localhost:$SV2_BOT_PORT/observe" -H 'content-type: application/json' -d '{}' \
        | "$SV2_PYTHON" -I -c 'import json,sys; p=json.load(sys.stdin)["position"]; print(int(p["x"]//1), int(p["z"]//1))')
  read -r x z <<< "$pos"
  sv2_rcon "worldborder center $x $z" >/dev/null
  sv2_rcon "worldborder set $WORLD_WIDTH" >/dev/null
}

start_bot() {
  for port in "$SV2_BOT_PORT" "$SV2_VIEWER_PORT"; do
    sv2_require_port_free_or_ours "$port" "$BOT_PID_FILE"
  done
  if ! pid_alive "$(pid_from_file "$BOT_PID_FILE")"; then
    : >> "$LOG_DIR/bot-$SV2_INSTANCE.log"
    screen -dmS "$BOT_SCREEN" bash -c "cd '$SV2_HOME/mc/bot' && echo \$\$ > '$BOT_PID_FILE' && MC_HOST=127.0.0.1 MC_PORT=$SV2_MC_PORT BOT_API_PORT=$SV2_BOT_PORT VIEWER_PORT=$SV2_VIEWER_PORT BOT_USERNAME=VoyagerBot exec node server.js >> '$LOG_DIR/bot-$SV2_INSTANCE.log' 2>&1"
  fi
  sv2_wait_until 60 bash -c "curl -fsS localhost:$SV2_BOT_PORT/health | grep -q '\"connected\":true'" \
    || die "bot not connected in 60 s; see $LOG_DIR/bot-$SV2_INSTANCE.log"
}

download_jar_once
sv2_require_port_free_or_ours "$SV2_MC_PORT" "$MC_PID_FILE"
if pid_alive "$(pid_from_file "$MC_PID_FILE")"; then
  echo "server $SV2_INSTANCE already running"
else
  sv2_start_server
fi
sv2_apply_gamerules

if [ "${1:-}" != "--no-bot" ]; then
  start_bot
  set_world_border
fi
echo "instance $SV2_INSTANCE up: mc $SV2_MC_PORT, rcon $SV2_RCON_PORT, bot $SV2_BOT_PORT, viewer $SV2_VIEWER_PORT"
