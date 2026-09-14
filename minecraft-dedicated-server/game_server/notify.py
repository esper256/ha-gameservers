"""Home Assistant notifications without MQTT.

Uses the Supervisor Core API proxy when homeassistant_api is enabled, and always
writes machine-readable status to /data/supervisor/status.json for REST sensors
or Ingress consumers.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOG = logging.getLogger("game_server.notify")


class Notifier:
    def __init__(
        self,
        state_dir: str | Path,
        *,
        enabled: bool = True,
        notification_id_prefix: str = "game_server",
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = self.state_dir / "status.json"
        self.enabled = enabled
        self.notification_id_prefix = notification_id_prefix
        self._last_sent: dict[str, float] = {}
        self._min_interval_seconds = 300

    def write_status(self, status: dict[str, Any]) -> None:
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.status_path)

    def notify(
        self,
        key: str,
        title: str,
        message: str,
        *,
        force: bool = False,
    ) -> bool:
        """Create/update a Home Assistant persistent notification (deduped)."""

        if not self.enabled:
            return False
        now = time.time()
        last = self._last_sent.get(key, 0)
        if not force and now - last < self._min_interval_seconds:
            return False

        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            LOG.debug("SUPERVISOR_TOKEN unavailable; skipped HA notification: %s", title)
            return False

        payload = {
            "notification_id": f"{self.notification_id_prefix}_{key}",
            "title": title,
            "message": message,
        }
        req = urllib.request.Request(
            "http://supervisor/core/api/services/persistent_notification/create",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                if 200 <= resp.status < 300:
                    self._last_sent[key] = now
                    LOG.info("HA notification sent: %s", title)
                    return True
                LOG.warning("HA notification HTTP %s", resp.status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # HA Core / Supervisor network path — expected to fail sometimes.
            LOG.warning("HA notification failed: %s", exc)
        return False
