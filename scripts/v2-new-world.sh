#!/usr/bin/env bash
# Switches one instance to a fresh world for <seed> and leaves it ready for a life run:
# the server restarts on world-<seed>, the bot (kept running) reconnects, gamerules and
# the world border are applied, the spawn biome is detected, snapshot fresh-<seed> is
# taken, and run/worlds/<inst>-<seed>.json is written.
# Usage: [SV2_HOME=...] [SV2_INSTANCE=b SV2_*_PORT=...] scripts/v2-new-world.sh <seed>
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
sv2_lock_acquire new-world

seed="${1:?usage: v2-new-world.sh <seed>}"
[[ "$seed" =~ ^-?[0-9]+$ ]] || die "seed must be an integer, got '$seed'"
WORLD_WIDTH=512
WORLD_JSON="$RUN_DIR/worlds/$SV2_INSTANCE-$seed.json"

# Overworld biomes of 1.21.1.
BIOMES="badlands bamboo_jungle beach birch_forest cherry_grove dark_forest deep_cold_ocean deep_dark
  deep_frozen_ocean deep_lukewarm_ocean deep_ocean desert dripstone_caves eroded_badlands flower_forest
  forest frozen_ocean frozen_peaks frozen_river grove ice_spikes jagged_peaks jungle lukewarm_ocean
  lush_caves mangrove_swamp meadow mushroom_fields old_growth_birch_forest old_growth_pine_taiga
  old_growth_spruce_taiga ocean plains river savanna savanna_plateau snowy_beach snowy_plains
  snowy_slopes snowy_taiga sparse_jungle stony_peaks stony_shore sunflower_forest swamp taiga
  warm_ocean windswept_forest windswept_gravelly_hills windswept_hills windswept_savanna
  wooded_badlands"

log() { echo "[$(date +%H:%M:%S)] v2-new-world $SV2_INSTANCE/$seed: $*"; }

bot_position() {
  curl -fsS -X POST "localhost:$SV2_BOT_PORT/observe" -H 'content-type: application/json' -d '{}' \
    | "$SV2_PYTHON" -I -c 'import json,sys; p=json.load(sys.stdin)["position"]; print(*(int(p[k]//1) for k in "xyz"))'
}

# Prints the biome id at the given block position.
detect_biome() {
  local biome
  for biome in $BIOMES; do
    if sv2_rcon "execute if biome $1 $2 $3 minecraft:$biome" | grep -q "Test passed"; then
      echo "$biome"; return
    fi
  done
  echo "unknown"
}

pid_alive "$(pid_from_file "$BOT_PID_FILE")" || die "bot of instance $SV2_INSTANCE is not running; run scripts/v2-up.sh first"

log "stopping the server (the bot stays up)"
sv2_stop_server
# A fresh world: drop any earlier world and snapshot for this seed.
rm -rf "$SERVER_DIR/world-$seed" "$SERVER_DIR/snapshots/fresh-$seed"

log "starting the server on world-$seed"
SV2_SEED="$seed" sv2_start_server
sv2_wait_until 90 bash -c "curl -fsS localhost:$SV2_BOT_PORT/health | grep -q '\"connected\":true'" \
  || die "bot did not reconnect in 90 s; see $LOG_DIR/bot-$SV2_INSTANCE.log"
sv2_wait_until 30 bot_position || die "bot has no position in 30 s"

log "applying gamerules and world border"
sv2_apply_gamerules
read -r x y z <<< "$(bot_position)"
sv2_rcon "worldborder center $x $z" >/dev/null
sv2_rcon "worldborder set $WORLD_WIDTH" >/dev/null

biome=$(detect_biome "$x" "$y" "$z")
log "spawn $x $y $z, biome $biome"

"$SV2_HOME/scripts/world-snapshot.sh" "fresh-$seed"

mkdir -p "$(dirname "$WORLD_JSON")"
"$SV2_PYTHON" -I -c '
import json, sys
seed, instance, x, y, z, biome, path = sys.argv[1:]
world = {"seed": int(seed), "instance": instance, "spawn": {"x": int(x), "y": int(y), "z": int(z)}, "biome": biome}
open(path, "w").write(json.dumps(world, indent=2) + "\n")
' "$seed" "$SV2_INSTANCE" "$x" "$y" "$z" "$biome" "$WORLD_JSON"
log "ready: $WORLD_JSON"
