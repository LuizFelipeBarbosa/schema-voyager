#!/usr/bin/env bash
# Stops one instance: graceful server stop, bot, screen sessions, pid files.
# Usage: [SV2_HOME=...] [SV2_INSTANCE=b SV2_*_PORT=...] scripts/v2-down.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings

bot_pid=$(pid_from_file "$BOT_PID_FILE")
if pid_alive "$bot_pid"; then
  kill "$bot_pid"
  sv2_wait_until 10 bash -c "! kill -0 $bot_pid" || kill -9 "$bot_pid"
fi
sv2_screen_exists "$BOT_SCREEN" && screen -S "$BOT_SCREEN" -X quit
rm -f "$BOT_PID_FILE"

sv2_stop_server
echo "instance $SV2_INSTANCE down"
