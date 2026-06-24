#!/usr/bin/env bash
# ABOUTME: Read back the edge device's current firmware so a flash can be rolled back.
# ABOUTME: Captures the exact regions our image overwrites as Intel HEX (restored via write_image).
#
# Usage:  scripts/device-backup.sh [output-dir]
# Output: backups/<UTC-timestamp>/{app_ns.hex, secure.hex, manifest.txt, sha256.txt}
#
# Method (verified on the E84 AI Kit): read the external-QSPI flash regions via OpenOCD's
# flashloader-backed `flash read_bank` (NOT a raw XIP memory dump — that reads zeros because the
# SMIF isn't XIP-mapped in a bare debug session), then objcopy each region to a HEX at its real
# address. Restore re-programs those HEX files with `flash write_image erase` — the SAME path
# `make program` uses, which is why `write_bank` from offset 0 is avoided (it aborts on the
# protected pre-app/secure region). The GeneratedSource search path registers the SMIF bank.
#
# Regions = exactly what build/app_combined.hex overwrites (so restore is a precise rollback):
#   app_ns : phys 0x340000 +0x250000  -> addr 0x60340000 (non-secure CM33/CM55 application)
#   secure : phys 0x100000 +0x010000  -> addr 0x70100000 (secure CM33 / boot, secure alias)
set -euo pipefail

PROGTOOLS="${PROGTOOLS:-/Applications/ModusToolboxProgtools-1.8}"
OPENOCD="$PROGTOOLS/openocd/bin/openocd"
OCD_SCRIPTS="$PROGTOOLS/openocd/scripts"
OBJCOPY="${OBJCOPY:-/Applications/mtb-gcc-arm-eabi/14.2.1/gcc/bin/arm-none-eabi-objcopy}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GS="$APP_DIR/bsps/TARGET_APP_KIT_PSE84_AI/config/GeneratedSource"

# label : read-bank : read-offset(bytes) : size(bytes) : restore-address
REGIONS=(
  "app_ns:2:0x340000:0x250000:0x60340000"
  "secure:2:0x100000:0x010000:0x70100000"
)

ts="$(date -u +%Y%m%dT%H%M%SZ)"
outdir="${1:-$APP_DIR/backups/$ts}"
mkdir -p "$outdir"
echo "Backing up current device firmware -> $outdir"

read_cmds=""
manifest="$outdir/manifest.txt"; : > "$manifest"
for r in "${REGIONS[@]}"; do
  IFS=":" read -r label bank off size addr <<< "$r"
  read_cmds+="flash read_bank $bank \"$outdir/$label.bin\" $off $size; "
done

"$OPENOCD" -s "$OCD_SCRIPTS" -s "$GS" \
  -c "set QSPI_FLASHLOADER $GS/PSE84_SMIF.FLM" \
  -c "source [find interface/kitprog3.cfg]" \
  -c "transport select swd" \
  -c "set ENABLE_CM55 1" \
  -c "source [find target/infineon/pse84xgxs2.cfg]" \
  -c "init; reset init; adapter speed 12000" \
  -c "$read_cmds" \
  -c "shutdown"

# Convert each region to a HEX at its real flash address; restore uses write_image on these.
for r in "${REGIONS[@]}"; do
  IFS=":" read -r label bank off size addr <<< "$r"
  "$OBJCOPY" -I binary -O ihex --change-addresses "$addr" "$outdir/$label.bin" "$outdir/$label.hex"
  rm -f "$outdir/$label.bin"
  echo "$label hex=$label.hex addr=$addr size=$size" >> "$manifest"
done

( cd "$outdir" && shasum -a 256 *.hex > sha256.txt )
echo "Backup complete:"; ls -la "$outdir"
echo "Manifest:"; cat "$manifest"
echo "Restore with: scripts/device-restore.sh \"$outdir\""
