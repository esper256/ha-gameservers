#!/usr/bin/env bash
# Supervisor owns Copyparty (mod uploads on the live mods folder).
set -euo pipefail

VERSION="${APP_VERSION:-}"
if [[ -z "${VERSION}" || "${VERSION}" == "dev" ]]; then
  if [[ -f /etc/hassio_app_version ]]; then
    VERSION="$(tr -d '[:space:]' </etc/hassio_app_version)"
  fi
fi
VERSION="${VERSION:-unknown}"

echo "============================================================"
echo " Minecraft Dedicated Server"
echo " Home Assistant app version: ${VERSION}"
echo "============================================================"

export APP_VERSION="${VERSION}"
export OPTIONS_FILE="${OPTIONS_FILE:-/data/options.json}"
export GAME_PLUGIN="${GAME_PLUGIN:-/opt/games/game.yaml}"
export PYTHONPATH="${PYTHONPATH:-/opt}"
export INSTALL_DIR="${INSTALL_DIR:-/data/installs}"
export DATA_DIR="${DATA_DIR:-/data/worlds}"
export STATE_DIR="${STATE_DIR:-/data/supervisor}"
export STATUS_HTTP_PORT="${STATUS_HTTP_PORT:-8099}"
export SERVER_PORT=25565
export PUBLISHER_PORT="${PUBLISHER_PORT:-8765}"
export PATH="/opt/java/bin:/opt/mc-image-helper/bin:${PATH}"

export MOD_PUBLISHER_DIR="${MOD_PUBLISHER_DIR:-/data/mod-publisher}"
mkdir -p /data/worlds /data/logs /data/backups /data/supervisor /data/installs \
  "${MOD_PUBLISHER_DIR}/incoming" "${MOD_PUBLISHER_DIR}/history" \
  "${MOD_PUBLISHER_DIR}/quarantine"
export HOME="${STATE_DIR}"

if [ -f "${OPTIONS_FILE}" ]; then
  echo "Using Home Assistant options from ${OPTIONS_FILE}"
else
  echo "No options.json at ${OPTIONS_FILE}; using environment defaults"
fi

python3 /opt/haos_defaults.py write-copyparty-banner
echo "Copyparty file-drop on TCP ${PUBLISHER_PORT} (live mods folder, supervisor)"

exec python3 -m game_server --plugin "${GAME_PLUGIN}"
