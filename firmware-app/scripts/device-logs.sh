#!/usr/bin/env bash
# ABOUTME: Stream the device's debug UART (retarget-io) over the KitProg3 serial bridge.
# ABOUTME: `--reset` first resets the device so the boot log is captured from the start.
#
# Usage:  scripts/device-logs.sh [--reset]
#   PORT=/dev/cu.usbmodemXXXX   override the auto-detected serial port
#   BAUD=115200                 override the baud (firmware retarget-io default is 115200)
# Ctrl-C to stop.
set -euo pipefail

BAUD="${BAUD:-115200}"
PORT="${PORT:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)}"
if [ -z "${PORT:-}" ] || [ ! -e "$PORT" ]; then
    echo "No KitProg3 serial port found (/dev/cu.usbmodem*). Is the board connected?" >&2
    echo "Check: fw-loader --device-list ; or set PORT=/dev/cu.usbmodemXXXX" >&2
    exit 1
fi

RESET=0
[ "${1:-}" = "--reset" ] && RESET=1

echo "Streaming $PORT @ ${BAUD} 8N1  (Ctrl-C to stop)" >&2

# Open the port once and set termios on that same fd, then cat — opening a second time on
# macOS resets the baud, so a separate `stty -f` + `cat` reads garbage. (See device bring-up.)
( stty "$BAUD" cs8 -cstopb -parenb -ixon raw -echo; exec cat ) < "$PORT" &
READER=$!
cleanup() { kill "$READER" 2>/dev/null || true; exit 0; }
trap cleanup INT TERM

if [ "$RESET" = 1 ]; then
    PROGTOOLS="${PROGTOOLS:-/Applications/ModusToolboxProgtools-1.8}"
    APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    GS="$APP_DIR/bsps/TARGET_APP_KIT_PSE84_AI/config/GeneratedSource"
    sleep 0.5  # let the reader attach so we don't miss the boot banner
    "$PROGTOOLS/openocd/bin/openocd" -s "$PROGTOOLS/openocd/scripts" -s "$GS" \
        -c "set QSPI_FLASHLOADER $GS/PSE84_SMIF.FLM" \
        -c "source [find interface/kitprog3.cfg]" -c "transport select swd" \
        -c "set ENABLE_CM55 1" \
        -c "source [find target/infineon/pse84xgxs2.cfg]" \
        -c "init; reset run; shutdown" >/dev/null 2>&1 || \
        echo "(reset failed — is OpenOCD/KitProg3 available? streaming anyway)" >&2
fi

wait "$READER"
