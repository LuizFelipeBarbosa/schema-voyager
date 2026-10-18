#!/usr/bin/env bash
# Start the Minecraft server (Docker via Colima) and wait until RCON answers.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v colima >/dev/null && ! colima status >/dev/null 2>&1; then
  colima start
fi
docker compose -f mc/docker-compose.yml up -d

printf "Waiting for the server"
until docker exec schema-voyager-mc rcon-cli "list" >/dev/null 2>&1; do
  printf "."
  sleep 2
done
echo
echo "Minecraft 1.21.1 is up on localhost:25565 (offline mode, RCON on 25575)."
echo "Next: npm --prefix mc/bot start      # bot API on :3000, viewer on http://localhost:3007"
