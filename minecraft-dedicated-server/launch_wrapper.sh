#!/usr/bin/env bash
# Minecraft launch wrapper (game layer). prepare = world_prepare hook.
set -euo pipefail
export PATH="/opt/java/bin:/opt/mc-image-helper/bin:${PATH:-}"
if [[ "${1:-}" == "prepare" ]]; then
  exec python3 /opt/haos_defaults.py prepare-world
fi
python3 /opt/haos_defaults.py prepare-world
exec python3 /opt/haos_defaults.py run
