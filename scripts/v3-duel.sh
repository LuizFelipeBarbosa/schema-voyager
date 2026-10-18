#!/usr/bin/env bash
# Measures duel tactics on a live instance (docs/v3-interfaces.md "v3.6 Duels"): starts the second
# bot (VoyagerBot2), runs N duels between two tactic sets through POST /duel on both bots, and prints
# a win-rate table. Needs the server and bot A already up (scripts/v2-up.sh). Takes the instance lock.
#
# Usage: [SV2_INSTANCE=b SV2_MC_PORT=... SV2_RCON_PORT=... SV2_BOT_PORT=... SV2_VIEWER_PORT=...] \
#        scripts/v3-duel.sh --a <set> --b <set> [--n 40] [--timeout 60] [--out runs/<name>] [--keep-bot2]
#        scripts/v3-duel.sh --vs <player> --a <set> [--n 3] [--timeout 120] [--out runs/<name>]
# <set>: comma-separated tactic names; "baseline" or "" means [] (the baseline).
# Bot 2: SV2_BOT2_PORT (3218), SV2_BOT2_DIR (mc/bot of SV2_HOME), SV2_BOT2_OWNERS (Otopo).
# Exits with the status of scripts/v3-duel.js.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
sv2_lock_acquire duel

SV2_BOT2_PORT="${SV2_BOT2_PORT:-3218}"
case "$SV2_BOT2_PORT" in
  25565|25575|3000|3007|8765) die "port $SV2_BOT2_PORT belongs to the live demo; refusing to run" ;;
esac
[ "$SV2_BOT2_PORT" != "$SV2_BOT_PORT" ] || die "SV2_BOT2_PORT equals SV2_BOT_PORT ($SV2_BOT_PORT)"
BOT2_PID_FILE="$RUN_DIR/bot2-$SV2_INSTANCE.pid"
BOT2_SCREEN="sv2-$SV2_INSTANCE-bot2"
BOT2_DIR="${SV2_BOT2_DIR:-$SV2_HOME/mc/bot}"

node_args=()
vs_mode=0
keep_bot2=0
for arg in "$@"; do
  case "$arg" in
    --keep-bot2) keep_bot2=1 ;;
    --vs) vs_mode=1; node_args+=("$arg") ;;
    *) node_args+=("$arg") ;;
  esac
done

# Stops bot 2 by the pid in its pid file (never by name), then releases the lock. One trap does both:
# a second `trap ... EXIT` would replace the lock's.
cleanup() {
  if [ "$vs_mode" = 0 ] && [ "$keep_bot2" = 0 ]; then
    local pid; pid=$(pid_from_file "$BOT2_PID_FILE")
    if pid_alive "$pid"; then
      kill "$pid" 2>/dev/null || true
      sv2_wait_until 10 bash -c "! kill -0 $pid" || kill -9 "$pid" 2>/dev/null || true
    fi
    sv2_screen_exists "$BOT2_SCREEN" && screen -S "$BOT2_SCREEN" -X quit
    rm -f "$BOT2_PID_FILE"
  fi
  sv2_lock_release
}
trap cleanup EXIT

sv2_rcon list >/dev/null 2>&1 || die "the server does not answer RCON; bring the instance up first: scripts/v2-up.sh"
curl -fsS --max-time 5 "localhost:$SV2_BOT_PORT/health" 2>/dev/null | grep -q '"connected":true' \
  || die "bot A is not connected; bring the instance up first: scripts/v2-up.sh"

if [ "$vs_mode" = 0 ]; then
  sv2_require_port_free_or_ours "$SV2_BOT2_PORT" "$BOT2_PID_FILE"
  if ! pid_alive "$(pid_from_file "$BOT2_PID_FILE")"; then
    : >> "$LOG_DIR/bot2-$SV2_INSTANCE.log"
    screen -dmS "$BOT2_SCREEN" bash -c "cd '$BOT2_DIR' && echo \$\$ > '$BOT2_PID_FILE' && MC_HOST=127.0.0.1 MC_PORT=$SV2_MC_PORT BOT_API_PORT=$SV2_BOT2_PORT VIEWER_PORT=0 BOT_USERNAME=VoyagerBot2 BOT_OWNERS='${SV2_BOT2_OWNERS:-Otopo}' exec node server.js >> '$LOG_DIR/bot2-$SV2_INSTANCE.log' 2>&1"
  fi
  sv2_wait_until 60 bash -c "curl -fsS localhost:$SV2_BOT2_PORT/health | grep -q '\"connected\":true'" \
    || die "bot 2 not connected in 60 s; see $LOG_DIR/bot2-$SV2_INSTANCE.log"
fi

# RCON_PASSWORD is exported by v2-env.sh (from the command line or .env); node reads it from the environment.
status=0
SV2_HOME="$SV2_HOME" SV2_RCON_PORT="$SV2_RCON_PORT" SV2_BOT_PORT="$SV2_BOT_PORT" SV2_BOT2_PORT="$SV2_BOT2_PORT" \
  DUEL_BOT_NAME=VoyagerBot DUEL_BOT2_NAME=VoyagerBot2 \
  node "$SV2_HOME/scripts/v3-duel.js" ${node_args[@]+"${node_args[@]}"} || status=$?
exit "$status"
