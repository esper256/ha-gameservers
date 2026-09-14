#!/usr/bin/env bash
# Fail if any game add-on's vendored game_server/ drifts from game-server-base.
#
# After editing game-server-base/game_server/, run:
#   ./game-server-base/sync-into-addons.sh
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$BASE/.." && pwd)"
SRC="$BASE/game_server"

if [[ ! -d "$SRC" ]]; then
  echo "Missing source tree: ${SRC}" >&2
  exit 1
fi

fail=0
found=0
for dest in "$ROOT"/*/; do
  if [[ "$(cd "$dest" && pwd)" == "$BASE" ]]; then
    continue
  fi
  if [[ -f "${dest}config.yaml" && -d "${dest}games" ]]; then
    found=$((found + 1))
    if [[ ! -d "${dest}game_server" ]]; then
      echo "MISSING: ${dest}game_server (run ./game-server-base/sync-into-addons.sh)" >&2
      fail=1
      continue
    fi
    if ! diff -qr -x '__pycache__' -x '*.pyc' "$SRC" "${dest}game_server" >/dev/null; then
      echo "OUT OF SYNC: ${dest}game_server" >&2
      diff -qr -x '__pycache__' -x '*.pyc' "$SRC" "${dest}game_server" >&2 || true
      echo "Run: ./game-server-base/sync-into-addons.sh" >&2
      fail=1
    else
      echo "OK ${dest}"
    fi
  fi
done

if [[ "$found" -eq 0 ]]; then
  echo "No game add-ons found under ${ROOT}" >&2
  exit 1
fi

exit "$fail"
