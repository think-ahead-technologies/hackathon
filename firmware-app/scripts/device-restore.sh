#!/usr/bin/env bash
# ABOUTME: Roll a flash back to a backup captured by device-backup.sh.
# ABOUTME: Re-programs each saved HEX via `flash write_image erase` + `verify_image`.
#
# Usage:  scripts/device-restore.sh <backup-dir>
#
# Mirrors `make program`: write_image erase on each region HEX, then verify. (Do NOT use
# write_bank from offset 0 — it aborts on the protected pre-app/secure region.)
set -euo pipefail

PROGTOOLS="${PROGTOOLS:-/Applications/ModusToolboxProgtools-1.8}"
OPENOCD="$PROGTOOLS/openocd/bin/openocd"
OCD_SCRIPTS="$PROGTOOLS/openocd/scripts"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GS="$APP_DIR/bsps/TARGET_APP_KIT_PSE84_AI/config/GeneratedSource"

backup_dir="${1:?usage: device-restore.sh <backup-dir>}"
[ -f "$backup_dir/manifest.txt" ] || { echo "no manifest.txt in $backup_dir" >&2; exit 1; }

prog_cmds=""
while read -r label hex_kv addr_kv size_kv; do
  hex="$backup_dir/${hex_kv#hex=}"
  [ -f "$hex" ] || { echo "missing $hex" >&2; exit 1; }
  echo "restore $label <- $(basename "$hex") @ ${addr_kv#addr=}"
  prog_cmds+="flash write_image erase \"$hex\"; verify_image \"$hex\"; "
done < "$backup_dir/manifest.txt"

"$OPENOCD" -s "$OCD_SCRIPTS" -s "$GS" \
  -c "set QSPI_FLASHLOADER $GS/PSE84_SMIF.FLM" \
  -c "source [find interface/kitprog3.cfg]" \
  -c "transport select swd" \
  -c "set ENABLE_CM55 1" \
  -c "source [find target/infineon/pse84xgxs2.cfg]" \
  -c "init; reset init; adapter speed 12000" \
  -c "$prog_cmds" \
  -c "reset run; shutdown"

echo "Restore complete from $backup_dir"
