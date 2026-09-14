# Home Assistant add-on: Stationeers dedicated server
# Thin game layer over the shared game-server-base supervisor.
# Game identity lives in games/game.yaml; Unity/Linux binary (no Java).
#
# Stationeers currently needs a recent glibc (2.40+). Debian Trixie provides it;
# Bookworm does not.

FROM debian:trixie-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    GAME_PLUGIN=/opt/games/game.yaml \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt \
    INSTALL_DIR=/data/game \
    STEAM_HOME=/data/steam-home \
    HOME=/data/steam-home \
    APP_VERSION=${BUILD_VERSION} \
    STATUS_HTTP_PORT=8099

RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      lib32gcc-s1 \
      libatomic1 \
      libglu1-mesa \
      libsdl2-2.0-0 \
      libxcursor1 \
      libxi6 \
      libxinerama1 \
      libxrandr2 \
      locales \
      python3 \
      python3-yaml \
      procps \
      tzdata \
      xz-utils \
 && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/steamcmd \
 && curl -fsSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
    | tar -xz -C /opt/steamcmd \
 && /opt/steamcmd/steamcmd.sh +quit || true

RUN useradd -m -u 1000 -s /bin/bash gameserver \
 && mkdir -p /data/game /data/world /data/logs /data/backups /data/supervisor /data/steam-home /opt/games \
 && chown -R gameserver:gameserver /opt/steamcmd /data /home/gameserver \
 && rm -f /etc/machine-id /var/lib/dbus/machine-id

# Shared supervisor already vendored into this add-on (run
# ../game-server-base/sync-into-addons.sh on the host after base edits; not a
# build-time fetch from ../game-server-base).
COPY game_server /opt/game_server
# Stationeers-only plugin config + HA defaults helper (not part of game_server)
COPY games/game.yaml /opt/games/game.yaml
COPY haos_defaults.py /opt/haos_defaults.py
COPY config.yaml /etc/hassio_app_config.yaml
COPY run.sh /run.sh
RUN chmod +x /run.sh \
 && printf '%s' "${BUILD_VERSION}" > /etc/hassio_app_version

WORKDIR /data/game

LABEL \
  io.hass.name="Stationeers Dedicated Server" \
  io.hass.description="Stationeers dedicated server with SteamCMD auto-updates, smart restarts, backups, and status page" \
  io.hass.type="app" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.source="https://github.com/esper256/ha-gameservers"

# 8099 = HA Ingress convention (not published on the host by default)
EXPOSE 27016/udp 27015/udp 8099
ENTRYPOINT ["/run.sh"]
