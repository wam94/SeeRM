#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title SeeRM Greeting
# @raycast.mode compact
# @raycast.packageName Messaging
# @raycast.argument1 {"type":"text","placeholder":"Callsign (e.g. mercury)"}
# @raycast.argument2 {"type":"text","placeholder":"Emails comma-separated"}
# @raycast.argument3 {"type":"text","placeholder":"First names (e.g. “Alex and team”)"}
# @raycast.argument4 {"type":"text","placeholder":"Gift link","optional":true}
# @raycast.argument5 {"type":"text","placeholder":"Manual notes","optional":true}
# @raycast.argument6 {"type":"text","placeholder":"Unleash paste","optional":true}
# @raycast.description Draft a greeting via the SeeRM messaging app.

set -euo pipefail

REPO="/Users/wmitchell/Documents/project_rm_at_scale/SeeRM"
APP_DIR="$REPO/apps/messaging_consumer"

cd "$APP_DIR"

PYTHON="$APP_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Missing virtualenv interpreter at $PYTHON"
  exit 1
fi

# Load env vars if .env exists
if [ -f ".env.local" ]; then
  set -a
  source ./.env.local
  set +a
elif [ -f ".env" ]; then
  set -a
  source ./.env
  set +a
fi

ARGS=( "$PYTHON" -m messaging_consumer.cli greetings "$1" "$2" --first-names "$3" )

GIFT_LINK="${4:-https://mercury.com}"
ARGS+=( --gift-link "$GIFT_LINK" )

if [ -n "${5:-}" ]; then
  ARGS+=( --notes "$5" )
fi

if [ -n "${6:-}" ]; then
  ARGS+=( --kb-text "$6" )
fi

if [ "${GREETING_DRY_RUN:-}" = "1" ]; then
  ARGS+=( --dry-run )
fi

"${ARGS[@]}"
