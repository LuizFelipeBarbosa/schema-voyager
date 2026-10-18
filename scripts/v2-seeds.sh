#!/usr/bin/env bash
# M4 multi-seed runner on one instance: for each seed a fresh world, then a 20-minute life
# in runs/m4-<seed>; at the end the per-seed table. With --survey it only reports each
# seed's spawn biome and the log species visible from spawn. For a parallel run, start a
# second copy with instance b's settings (SV2_INSTANCE=b SV2_MC_PORT=25567 ...).
# Usage: scripts/v2-seeds.sh [--survey] [--minutes 20] [--change-on <seed>] <seed>...
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/v2-env.sh"
sv2_check_settings
sv2_lock_acquire seeds
export SV2_INSTANCE SV2_MC_PORT SV2_RCON_PORT SV2_BOT_PORT SV2_VIEWER_PORT

survey=false
minutes=20
change_seed=""
seeds=()
while [ $# -gt 0 ]; do
  case "$1" in
    --survey) survey=true ;;
    --minutes) minutes="${2:?--minutes needs a value}"; shift ;;
    --change-on) change_seed="${2:?--change-on needs a seed}"; shift ;;
    -*) die "unknown option $1" ;;
    *) seeds+=("$1") ;;
  esac
  shift
done
[ "${#seeds[@]}" -gt 0 ] || die "usage: v2-seeds.sh [--survey] [--minutes 20] [--change-on <seed>] <seed>..."

log() { echo "[$(date +%H:%M:%S)] v2-seeds $SV2_INSTANCE: $*"; }

# Prints the log species ids visible from spawn, comma-separated.
visible_logs() {
  curl -fsS -X POST "localhost:$SV2_BOT_PORT/observe" -d '{}' -H 'content-type: application/json' \
    | "$SV2_PYTHON" -I -c '
import json, sys
logs = sorted(k for k in json.load(sys.stdin)["nearby_blocks"] if k.endswith("_log"))
print(",".join(logs) or "none")'
}

world_biome() {
  "$SV2_PYTHON" -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["biome"])' "$1"
}

cd "$SV2_HOME"
run_dirs=()
for seed in "${seeds[@]}"; do
  world_json="$RUN_DIR/worlds/$SV2_INSTANCE-$seed.json"
  log "seed $seed: new world"
  "$SV2_HOME/scripts/v2-new-world.sh" "$seed"

  if $survey; then
    biome=$(world_biome "$world_json")
    logs=$(visible_logs)
    log "seed $seed: biome $biome, logs $logs"
    echo "survey: seed=$seed biome=$biome logs=$logs"
    continue
  fi

  run_dir="runs/m4-$seed"
  mkdir -p "$run_dir"
  cp "$world_json" "$run_dir/world.json"
  run_dirs+=("$run_dir")

  change_env=()
  [ "$seed" = "$change_seed" ] && change_env=(SV2_M3_CHANGE=r2)
  log "seed $seed: life for $minutes min in $run_dir ${change_env[*]:-}"
  # A failed life must not stop the remaining seeds; the report shows how it ended.
  env ${change_env[@]+"${change_env[@]}"} PYTHONPATH=src "$SV2_PYTHON" -m schema_voyager.demo life \
      --backend mc --llm live --minutes "$minutes" --seed "$seed" --run-dir "$run_dir" \
      2>&1 | tee "$run_dir/life.out" || log "seed $seed: life exited with an error"
done

if [ "${#run_dirs[@]}" -gt 0 ]; then
  log "report"
  PYTHONPATH=src "$SV2_PYTHON" -m schema_voyager.seed_report "${run_dirs[@]}" --json "runs/m4-report-$SV2_INSTANCE.json"
fi
