#!/bin/sh
# Stream Whiteboard — Mac/Linux quick start
# Usage: ./start.sh [tablet-host]
set -e
cd "$(dirname "$0")"
HOST="${1:-${TABLET_HOST:-172.16.10.175}}"
exec python3 bridge.py --tablet "$HOST" --serve --open
