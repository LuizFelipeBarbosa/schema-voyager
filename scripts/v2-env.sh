# Shared helpers for the v2 scripts. Source it; don't run it.
#
# Settings come from the command line first, then from $SV2_HOME/.env (only the
# keys listed below, never echoed), then from the defaults.

SV2_HOME="${SV2_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Sets VAR from .env unless VAR is already set. Reads only the named key.
_load_env_key() {
  local key="$1" line
  [ -n "${!key:-}" ] && return
  [ -f "$SV2_HOME/.env" ] || return 0
  line=$(grep -E "^${key}=" "$SV2_HOME/.env" | tail -n 1) || return 0
  line="${line#*=}"
  line="${line%\"}"; line="${line#\"}"
  [ -n "$line" ] && export "$key=$line"
  return 0
}

for _key in SV2_INSTANCE SV2_MC_PORT SV2_RCON_PORT SV2_BOT_PORT SV2_VIEWER_PORT RCON_PASSWORD JAVA_BIN SV2_SEED; do
  _load_env_key "$_key"
done
unset _key

SV2_INSTANCE="${SV2_INSTANCE:-a}"
JAVA_BIN="${JAVA_BIN:-/opt/homebrew/opt/openjdk@21/bin/java}"
case "$SV2_INSTANCE" in
  a) : "${SV2_MC_PORT:=25566}" "${SV2_RCON_PORT:=25576}" "${SV2_BOT_PORT:=3200}" "${SV2_VIEWER_PORT:=3207}" ;;
  b) : "${SV2_MC_PORT:=25567}" "${SV2_RCON_PORT:=25577}" "${SV2_BOT_PORT:=3210}" "${SV2_VIEWER_PORT:=3217}" ;;
esac

SV2_PYTHON="$SV2_HOME/.venv/bin/python"
SERVER_DIR="$SV2_HOME/server-$SV2_INSTANCE"
JAR_CACHE="$SV2_HOME/server-cache/server-1.21.1.jar"
RUN_DIR="$SV2_HOME/run"
LOG_DIR="$SV2_HOME/logs"
MC_SCREEN="sv2-$SV2_INSTANCE-mc"
BOT_SCREEN="sv2-$SV2_INSTANCE-bot"
MC_PID_FILE="$RUN_DIR/mc-$SV2_INSTANCE.pid"
BOT_PID_FILE="$RUN_DIR/bot-$SV2_INSTANCE.pid"

die() { echo "error: $*" >&2; exit 1; }

# Refuses to continue unless the instance settings are complete and none of the
# ports belongs to the live demo.
sv2_check_settings() {
  local name port
  for name in SV2_MC_PORT SV2_RCON_PORT SV2_BOT_PORT SV2_VIEWER_PORT RCON_PASSWORD; do
    [ -n "${!name:-}" ] || die "$name is not set (instance '$SV2_INSTANCE')"
  done
  for port in "$SV2_MC_PORT" "$SV2_RCON_PORT" "$SV2_BOT_PORT" "$SV2_VIEWER_PORT"; do
    case "$port" in
      25565|25575|3000|3007) die "port $port belongs to the live demo; refusing to run" ;;
    esac
  done
  [ -x "$JAVA_BIN" ] || die "JAVA_BIN not executable: $JAVA_BIN"
  [ -x "$SV2_PYTHON" ] || die "missing $SV2_PYTHON"
  mkdir -p "$RUN_DIR" "$LOG_DIR" "$SERVER_DIR"
}

# The instance lock (docs/v2-interfaces.md, "v2.3: the instance lock"): one JSON line
# {pid, session, purpose, started} in run/lock-<instance>.
SV2_LOCK_FILE="$RUN_DIR/lock-$SV2_INSTANCE"

# Prints one field of the lock file ("" when there is no lock or no such field).
_lock_field() {
  local line; line=$(head -n 1 "$SV2_LOCK_FILE" 2>/dev/null) || return 0
  case "$1" in
    pid) echo "$line" | sed -n 's/.*"pid": *\([0-9][0-9]*\).*/\1/p' ;;
    *)   echo "$line" | sed -n "s/.*\"$1\": *\"\([^\"]*\)\".*/\1/p" ;;
  esac
}

# Usage: sv2_lock_acquire <purpose>. Takes the lock or dies naming the holder. A holder with
# a dead pid is stale and taken over; one with our SV2_LOCK_SESSION is a hand-over, and this
# script then passes through (it neither writes nor removes the lock).
sv2_lock_acquire() {
  local purpose="${1:?usage: sv2_lock_acquire <purpose>}" holder_pid holder_session
  holder_pid=$(_lock_field pid); holder_session=$(_lock_field session)
  if [ -n "$holder_pid" ] && [ "$holder_pid" != "$$" ] && pid_alive "$holder_pid"; then
    if [ -n "${SV2_LOCK_SESSION:-}" ] && [ "$holder_session" = "$SV2_LOCK_SESSION" ]; then
      return 0
    fi
    die "instance '$SV2_INSTANCE' is locked by pid $holder_pid (session $holder_session, purpose $(_lock_field purpose), since $(_lock_field started)); $SV2_LOCK_FILE"
  fi
  [ -n "$holder_pid" ] && [ "$holder_pid" != "$$" ] && echo "warning: taking over the stale lock of dead pid $holder_pid" >&2
  export SV2_LOCK_SESSION="${SV2_LOCK_SESSION:-$$-$purpose}"
  mkdir -p "$RUN_DIR"
  printf '{"pid": %s, "session": "%s", "purpose": "%s", "started": "%s"}\n' \
    "$$" "$SV2_LOCK_SESSION" "$purpose" "$(date +%Y-%m-%dT%H:%M:%S)" > "$SV2_LOCK_FILE.$$.tmp"
  mv "$SV2_LOCK_FILE.$$.tmp" "$SV2_LOCK_FILE"
  trap sv2_lock_release EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

# Removes the lock only if this shell's session holds it and its pid is this shell's.
sv2_lock_release() {
  [ "$(_lock_field pid)" = "$$" ] && [ "$(_lock_field session)" = "${SV2_LOCK_SESSION:-}" ] || return 0
  rm -f "$SV2_LOCK_FILE"
}

listener_pids() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u || true; }

pid_alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

pid_from_file() { [ -f "$1" ] && cat "$1" || true; }

# Succeeds when nothing listens on the port, or when the listener is the pid in
# the given pid file (this instance's own process). Otherwise stops the caller.
sv2_require_port_free_or_ours() {
  local port="$1" pid_file="$2" own listeners
  listeners=$(listener_pids "$port")
  [ -z "$listeners" ] && return 0
  own=$(pid_from_file "$pid_file")
  [ -n "$own" ] && [ "$listeners" = "$own" ] && return 0
  die "port $port is already taken by pid(s) $(echo $listeners) which are not instance '$SV2_INSTANCE'"
}

# Level name follows the seed so each scouted seed gets a fresh world folder.
sv2_level_name() {
  if [ -f "$SERVER_DIR/server.properties" ]; then
    sed -n 's/^level-name=//p' "$SERVER_DIR/server.properties"
  fi
}

# Usage: sv2_rcon "command"   (prints the reply)
sv2_rcon() {
  "$SV2_PYTHON" -I -c '
import sys
from rcon.source import Client
with Client("127.0.0.1", int(sys.argv[1]), passwd=sys.argv[2], timeout=float(sys.argv[4])) as c:
    print(c.run(sys.argv[3]))
' "$SV2_RCON_PORT" "$RCON_PASSWORD" "$1" "${SV2_RCON_TIMEOUT:-10}"
}

# Usage: sv2_wait_until <seconds> <command...>
sv2_wait_until() {
  local deadline=$((SECONDS + $1)); shift
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep 0.5
  done
}

sv2_rcon_ready() { sv2_rcon "list"; }

sv2_screen_exists() { screen -ls 2>/dev/null | grep -qE "[0-9]+\.$1[[:space:]]"; }

sv2_apply_gamerules() {
  local cmd
  for cmd in "gamerule keepInventory true" "gamerule doDaylightCycle false" \
             "gamerule doWeatherCycle false" "gamerule spawnRadius 0" \
             "gamerule announceAdvancements false" "time set day" "weather clear"; do
    sv2_rcon "$cmd" >/dev/null
  done
}

# Writes server.properties and starts the server in screen; waits for RCON.
sv2_start_server() {
  [ -f "$SERVER_DIR/eula.txt" ] || echo "eula=true" > "$SERVER_DIR/eula.txt"
  # Without SV2_SEED, keep the world the properties file already names.
  local level_name level_seed
  if [ -n "${SV2_SEED:-}" ]; then
    level_name="world-$SV2_SEED"; level_seed="$SV2_SEED"
  else
    level_name=$(sv2_level_name); level_name="${level_name:-world}"
    level_seed=$(sed -n 's/^level-seed=//p' "$SERVER_DIR/server.properties" 2>/dev/null || true)
  fi
  cat > "$SERVER_DIR/server.properties" <<PROPS
server-port=$SV2_MC_PORT
enable-rcon=true
rcon.port=$SV2_RCON_PORT
rcon.password=$RCON_PASSWORD
broadcast-rcon-to-ops=false
online-mode=false
difficulty=peaceful
spawn-protection=0
gamemode=survival
level-name=$level_name
level-seed=$level_seed
view-distance=8
simulation-distance=6
PROPS
  sv2_require_port_free_or_ours "$SV2_MC_PORT" "$MC_PID_FILE"
  sv2_require_port_free_or_ours "$SV2_RCON_PORT" "$MC_PID_FILE"
  : >> "$LOG_DIR/mc-$SV2_INSTANCE.log"
  # `exec` makes the recorded pid the java process itself.
  screen -dmS "$MC_SCREEN" bash -c "cd '$SERVER_DIR' && echo \$\$ > '$MC_PID_FILE' && exec '$JAVA_BIN' -Xms1G -Xmx2G -jar '$JAR_CACHE' nogui >> '$LOG_DIR/mc-$SV2_INSTANCE.log' 2>&1"
  sv2_wait_until 120 sv2_rcon_ready || die "server did not answer RCON in 120 s; see $LOG_DIR/mc-$SV2_INSTANCE.log"
}

# Stops the server gracefully over RCON and waits for the process to exit.
sv2_stop_server() {
  local pid; pid=$(pid_from_file "$MC_PID_FILE")
  if pid_alive "$pid"; then
    sv2_rcon stop >/dev/null 2>&1 || kill "$pid" 2>/dev/null || true
    sv2_wait_until 60 bash -c "! kill -0 $pid" || { kill -9 "$pid" 2>/dev/null; echo "warning: server needed kill -9" >&2; }
  fi
  sv2_screen_exists "$MC_SCREEN" && screen -S "$MC_SCREEN" -X quit
  rm -f "$MC_PID_FILE"
  return 0
}
