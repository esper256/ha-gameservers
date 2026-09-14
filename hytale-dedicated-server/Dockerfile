# Home Assistant add-on: Hytale dedicated server
# Thin game layer over the shared game-server-base supervisor.
# Official downloader CLI + Java 25 (not SteamCMD).

FROM debian:bookworm-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    GAME_PLUGIN=/opt/games/game.yaml \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt \
    INSTALL_DIR=/data/game \
    HOME=/data/supervisor \
    APP_VERSION=${BUILD_VERSION} \
    STATUS_HTTP_PORT=8099 \
    PATH=/opt/java/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      locales \
      python3 \
      python3-yaml \
      procps \
      tzdata \
 && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

# Official Temurin 25 JRE (Hytale requires Java 25).
RUN curl -fsSL \
      "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jre/hotspot/normal/eclipse?project=jdk" \
      -o /tmp/jre.tar.gz \
 && mkdir -p /opt/java \
 && tar -xzf /tmp/jre.tar.gz -C /opt/java --strip-components=1 \
 && rm -f /tmp/jre.tar.gz \
 && java -version

# Official Hytale Downloader CLI (Linux amd64).
RUN curl -fsSL -o /tmp/hytale-downloader.zip \
      https://downloader.hytale.com/hytale-downloader.zip \
 && python3 -c "import zipfile; zipfile.ZipFile('/tmp/hytale-downloader.zip').extract('hytale-downloader-linux-amd64', '/opt')" \
 && mv /opt/hytale-downloader-linux-amd64 /opt/hytale-downloader \
 && chmod +x /opt/hytale-downloader \
 && rm -f /tmp/hytale-downloader.zip

RUN useradd -m -u 1000 -s /bin/bash gameserver \
 && mkdir -p /data/game /data/world /data/logs /data/backups /data/supervisor /opt/games \
 && chown -R gameserver:gameserver /data /home/gameserver \
 && rm -f /etc/machine-id /var/lib/dbus/machine-id

COPY game_server /opt/game_server
COPY games/game.yaml /opt/games/game.yaml
COPY haos_defaults.py /opt/haos_defaults.py
COPY launch_wrapper.sh /opt/launch_wrapper.sh
COPY config.yaml /etc/hassio_app_config.yaml
COPY run.sh /run.sh
RUN chmod +x /run.sh /opt/launch_wrapper.sh \
 && printf '%s' "${BUILD_VERSION}" > /etc/hassio_app_version

WORKDIR /data/world

LABEL \
  io.hass.name="Hytale Dedicated Server" \
  io.hass.description="Hytale dedicated server with auto-updates, smart restarts, backups, and status page" \
  io.hass.type="app" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.source="https://github.com/esper256/ha-gameservers"

EXPOSE 5520/udp 8099
ENTRYPOINT ["/run.sh"]
