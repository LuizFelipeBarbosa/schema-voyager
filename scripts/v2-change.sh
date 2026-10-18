#!/usr/bin/env bash
# Installs and switches the v2 M3 datapacks of one instance (SV2_INSTANCE).
#   v2-change.sh install            copy mc/datapacks/v2_* into the world, reload, leave all disabled
#   v2-change.sh enable r2|stick2   switch a rule change on
#   v2-change.sh disable r2|stick2  switch it off
#   v2-change.sh status             which v2 packs are enabled / available
# install, enable and disable take the instance lock; status does not.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/v2-env.sh"
sv2_check_settings
case "${1:-}" in install|enable|disable) sv2_lock_acquire "change-$1" ;; esac

pack_for() {
  case "$1" in
    r2) echo "file/v2_coal_tool" ;;
    stick2) echo "file/v2_stick2" ;;
    *) die "unknown change '$1'; use r2 or stick2" ;;
  esac
}

all_packs() { for dir in "$SV2_HOME"/mc/datapacks/v2_*/; do echo "file/$(basename "$dir")"; done; }

case "${1:-}" in
  install)
    level=$(sv2_level_name); [ -n "$level" ] || die "no level-name in $SERVER_DIR/server.properties"
    target="$SERVER_DIR/$level/datapacks"
    mkdir -p "$target"
    for dir in "$SV2_HOME"/mc/datapacks/v2_*/; do
      rm -rf "$target/$(basename "$dir")"
      cp -R "$dir" "$target/$(basename "$dir")"
    done
    sv2_rcon "reload" >/dev/null
    # A new pack is enabled automatically on reload: switch every v2 pack off again.
    for pack in $(all_packs); do sv2_rcon "datapack disable \"$pack\"" >/dev/null || true; done
    sv2_rcon "datapack list"
    ;;
  enable)  sv2_rcon "datapack enable \"$(pack_for "${2:-}")\"" ;;
  disable) sv2_rcon "datapack disable \"$(pack_for "${2:-}")\"" ;;
  status)
    echo "enabled:   $(sv2_rcon "datapack list enabled")"
    echo "available: $(sv2_rcon "datapack list available")"
    ;;
  *) die "usage: v2-change.sh install | enable <r2|stick2> | disable <r2|stick2> | status" ;;
esac
