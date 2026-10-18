#!/usr/bin/env bash
# One command for the demo: a fresh world, the datapacks installed, a life in a screen and
# the trace panel. Works on one instance (SV2_INSTANCE, default a; for b set the four port
# variables on the command line). It holds the instance lock (purpose demo) while it sets
# up, then hands it to the life, which keeps it until it ends.
#   v2-demo.sh [start] [--run <name>]   restore seed 1001 snapshot fresh, start the life + panel
#   v2-demo.sh stop [--all] [--panel]   stop the life (SIGINT, so it saves); --all: v2-down.sh too
#   v2-demo.sh --rehearse N             N fresh lives back to back, in the foreground
# Environment: SV2_DEMO_MINUTES (default 30), SV2_DEMO_CHANGE (default r2).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
export SV2_HOME SV2_INSTANCE SV2_MC_PORT SV2_RCON_PORT SV2_BOT_PORT SV2_VIEWER_PORT
export SV2_SEED="${SV2_SEED:-1001}"

LIFE_SCREEN="sv2-$SV2_INSTANCE-life"
LIFE_PID_FILE="$RUN_DIR/life-$SV2_INSTANCE.pid"
PANEL_SCREEN="sv2-panel"
PANEL_PORT=8766
PANEL_PAGE="http://127.0.0.1:$PANEL_PORT/site/dist/trace.html"
MINUTES="${SV2_DEMO_MINUTES:-30}"
CHANGE="${SV2_DEMO_CHANGE:-r2}"

usage() { die "usage: v2-demo.sh [start] [--run <name>] | stop [--all] [--panel] | --rehearse N"; }

# Stops this instance's life: SIGINT first so it saves its state, then SIGTERM, then kill.
# Leaves no stale lock behind.
stop_life() {
  local pid; pid=$(pid_from_file "$LIFE_PID_FILE")
  if pid_alive "$pid"; then
    kill -INT "$pid" 2>/dev/null || true
    sv2_wait_until 60 bash -c "! kill -0 $pid" || {
      kill -TERM "$pid" 2>/dev/null || true
      sv2_wait_until 10 bash -c "! kill -0 $pid" || kill -9 "$pid" 2>/dev/null || true
    }
  fi
  sv2_screen_exists "$LIFE_SCREEN" && screen -S "$LIFE_SCREEN" -X quit
  rm -f "$LIFE_PID_FILE"
  local holder; holder=$(_lock_field pid)
  if [ -n "$holder" ] && [ "$holder" != "$$" ] && ! pid_alive "$holder"; then rm -f "$SV2_LOCK_FILE"; fi
  return 0
}

# Fresh world from the snapshot, server and bot up, datapacks installed but disabled.
prepare_world() {
  "$SV2_HOME/scripts/world-restore.sh" fresh
  "$SV2_HOME/scripts/v2-up.sh"
  "$SV2_HOME/scripts/v2-change.sh" install >/dev/null
}

# Creates the run directory (with the world description, as v2-seeds.sh does) and prints
# the life command.
life_command() {
  local run="$1" world_json="$RUN_DIR/worlds/$SV2_INSTANCE-$SV2_SEED.json"
  mkdir -p "$SV2_HOME/runs/$run" "$LOG_DIR"
  [ -f "$world_json" ] && cp "$world_json" "$SV2_HOME/runs/$run/world.json"
  echo "cd '$SV2_HOME' && PYTHONPATH=src exec '$SV2_PYTHON' -m schema_voyager.demo life --backend mc --llm live" \
       "--minutes $MINUTES --seed $SV2_SEED --run-dir runs/$run --change '$CHANGE' --keep-going"
}

start_panel() {
  if curl -fsS -m 2 -o /dev/null "$PANEL_PAGE" 2>/dev/null; then return 0; fi
  sv2_screen_exists "$PANEL_SCREEN" && screen -S "$PANEL_SCREEN" -X quit
  screen -dmS "$PANEL_SCREEN" bash -c "cd '$SV2_HOME' && PYTHONPATH=src exec '$SV2_PYTHON' -m schema_voyager.demo panel --port $PANEL_PORT >> '$LOG_DIR/panel.log' 2>&1"
  sv2_wait_until 15 curl -fsS -m 2 -o /dev/null "$PANEL_PAGE" || echo "warning: the panel did not answer in 15 s; see $LOG_DIR/panel.log" >&2
}

start() {
  local run="demo-$(date +%Y%m%d-%H%M)" log_file
  while [ $# -gt 0 ]; do
    case "$1" in
      --run) run="${2:?--run needs a name}"; shift ;;
      *) usage ;;
    esac
    shift
  done
  log_file="$LOG_DIR/life-$run.log"

  sv2_require_port_free_or_ours "$SV2_MC_PORT" "$MC_PID_FILE"
  sv2_require_port_free_or_ours "$SV2_RCON_PORT" "$MC_PID_FILE"
  sv2_require_port_free_or_ours "$SV2_BOT_PORT" "$BOT_PID_FILE"
  sv2_require_port_free_or_ours "$SV2_VIEWER_PORT" "$BOT_PID_FILE"
  sv2_lock_acquire demo
  [ -e "$SV2_HOME/runs/$run" ] && die "run $run already exists; pick another with --run"

  prepare_world

  # The life gets the lock's session and takes the lock over (instance_lock.hold). The
  # session is spelled out because a screen window may not inherit the environment.
  : > "$log_file"
  screen -dmS "$LIFE_SCREEN" bash -c "echo \$\$ > '$LIFE_PID_FILE' && export SV2_LOCK_SESSION='$SV2_LOCK_SESSION' && $(life_command "$run") >> '$log_file' 2>&1"
  life_has_lock() {
    local pid; pid=$(pid_from_file "$LIFE_PID_FILE")
    [ -n "$pid" ] && [ "$(_lock_field pid)" = "$pid" ] && pid_alive "$pid"
  }
  sv2_wait_until 20 life_has_lock || { stop_life; die "the life did not take the lock in 20 s; see $log_file"; }

  start_panel
  echo "panel:   http://localhost:$PANEL_PORT/site/dist/trace.html?run=$run&viewer=http://localhost:$SV2_VIEWER_PORT"
  echo "viewer:  http://localhost:$SV2_VIEWER_PORT"
  echo "log:     $log_file"
  echo "stop:    SV2_INSTANCE=$SV2_INSTANCE $SV2_HOME/scripts/v2-demo.sh stop"
  echo "ready in $SECONDS s"
}

stop() {
  local all=false panel=false
  while [ $# -gt 0 ]; do
    case "$1" in
      --all) all=true ;;
      --panel) panel=true ;;
      *) usage ;;
    esac
    shift
  done
  stop_life
  if $panel && sv2_screen_exists "$PANEL_SCREEN"; then screen -S "$PANEL_SCREEN" -X quit; fi
  if $all; then "$SV2_HOME/scripts/v2-down.sh"; fi
  echo "life of instance $SV2_INSTANCE stopped"
}

# Runs N fresh lives in the foreground, holding the lock throughout; each life takes the
# lock over for its duration and hands it back.
rehearse() {
  local count="${1:?usage: v2-demo.sh --rehearse N}" stamp i run runs=()
  [[ "$count" =~ ^[1-9][0-9]*$ ]] || die "--rehearse needs a positive number, got '$count'"
  stamp=$(date +%Y%m%d-%H%M)
  sv2_lock_acquire demo
  cd "$SV2_HOME"
  for i in $(seq 1 "$count"); do
    run="demo-$stamp-r$i"
    echo "[$(date +%H:%M:%S)] rehearsal $i of $count: $run"
    prepare_world
    # A failed life must not stop the remaining rehearsals; its report shows how it ended.
    bash -c "$(life_command "$run")" 2>&1 | tee "$LOG_DIR/life-$run.log" || echo "rehearsal $i: the life exited with an error"
    runs+=("runs/$run")
  done
  for run in "${runs[@]}"; do
    echo "=== $run"
    PYTHONPATH=src "$SV2_PYTHON" -m schema_voyager.change_report "$run" || true
  done
  echo "run dirs:"
  printf '  %s\n' "${runs[@]}"
}

case "${1:-start}" in
  start) shift || true; start "$@" ;;
  --run) start "$@" ;;
  stop) shift; stop "$@" ;;
  --rehearse) shift; rehearse "$@" ;;
  *) usage ;;
esac
