#!/usr/bin/env bash
# ABOUTME: Safe flash: back up the device's current firmware, then program the new image.
# ABOUTME: Backup-before-flash is enforced — a failed/empty backup aborts before programming.
#
# Usage:  scripts/device-flash.sh [WIFI_SSID=...] [WIFI_PASSWORD=...]
#         (build first with `make build`, or pass nothing to program the last build)
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

echo "==> Step 1/2: backing up current device firmware (rollback safety)"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$APP_DIR/backups/$ts"
scripts/device-backup.sh "$backup_dir"

# Refuse to flash if the backup looks empty (board not connected / readback failed).
if ! ls "$backup_dir"/region_*.bin >/dev/null 2>&1; then
  echo "ERROR: backup produced no region files — aborting flash." >&2
  exit 1
fi
echo "Backup OK in $backup_dir (restore with: scripts/device-restore.sh '$backup_dir')"

echo "==> Step 2/2: programming new image (app_combined.hex)"
SHELL_BASH="${SHELL_BASH:-/Applications/ModusToolbox/tools_3.8/modus-shell/bin/bash}"
export CY_TOOLS_PATHS="${CY_TOOLS_PATHS:-/Applications/ModusToolbox/tools_3.8}"
"$SHELL_BASH" -lc "cd '$APP_DIR'; export CY_TOOLS_PATHS='$CY_TOOLS_PATHS'; make program $*"

echo "Done. If the new firmware misbehaves, roll back with:"
echo "  scripts/device-restore.sh '$backup_dir'"
