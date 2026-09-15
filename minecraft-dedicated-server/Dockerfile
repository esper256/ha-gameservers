# Minecraft Java dedicated server (Fabric or NeoForge per world) for Home Assistant OS.
# Thin game layer over game-server-base. Loader installs via itzg mc-image-helper.

FROM debian:bookworm-slim

ARG BUILD_VERSION=dev
ARG BUILD_ARCH=amd64
ARG MC_IMAGE_HELPER_VERSION=1.69.1

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    GAME_PLUGIN=/opt/games/game.yaml \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt \
    INSTALL_DIR=/data/installs \
    HOME=/data/supervisor \
    APP_VERSION=${BUILD_VERSION} \
    STATUS_HTTP_PORT=8099 \
    PATH=/opt/java/bin:/opt/mc-image-helper/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      locales \
      python3 \
      python3-yaml \
      python3-pip \
      procps \
      tzdata \
      unzip \
 && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
      "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jre/hotspot/normal/eclipse?project=jdk" \
      -o /tmp/jre.tar.gz \
 && mkdir -p /opt/java \
 && tar -xzf /tmp/jre.tar.gz -C /opt/java --strip-components=1 \
 && rm -f /tmp/jre.tar.gz \
 && java -version

RUN curl -fsSL -o /tmp/mc-image-helper.tgz \
      "https://github.com/itzg/mc-image-helper/releases/download/${MC_IMAGE_HELPER_VERSION}/mc-image-helper-${MC_IMAGE_HELPER_VERSION}.tgz" \
 && mkdir -p /opt/mc-image-helper \
 && tar -xzf /tmp/mc-image-helper.tgz -C /opt/mc-image-helper --strip-components=1 \
 && chmod +x /opt/mc-image-helper/bin/mc-image-helper \
 && rm -f /tmp/mc-image-helper.tgz \
 && mc-image-helper --help >/dev/null

RUN curl -fsSL -o /opt/server-starter.jar \
      "https://github.com/NeoForged/serverstarterjar/releases/latest/download/server.jar"

RUN pip3 install --break-system-packages --no-cache-dir 'copyparty>=1.16.0'

RUN useradd -m -u 1000 -s /bin/bash gameserver \
 && mkdir -p /data/installs /data/worlds /data/logs /data/backups /data/supervisor \
      /data/mod-publisher /opt/games \
 && chown -R gameserver:gameserver /data /home/gameserver \
 && rm -f /etc/machine-id /var/lib/dbus/machine-id

COPY game_server /opt/game_server
COPY games/game.yaml /opt/games/game.yaml
COPY haos_defaults.py /opt/haos_defaults.py
COPY golden_boot.py /opt/golden_boot.py
COPY publish_mod.py /opt/publish_mod.py
COPY launch_wrapper.sh /opt/launch_wrapper.sh
COPY config.yaml /etc/hassio_app_config.yaml
COPY run.sh /run.sh
RUN chmod +x /run.sh /opt/launch_wrapper.sh \
 && printf '%s' "${BUILD_VERSION}" > /etc/hassio_app_version

WORKDIR /data/worlds

LABEL \
  io.hass.name="Minecraft Dedicated Server" \
  io.hass.description="Minecraft Java server with Fabric/NeoForge worlds, AutoModpack, and a LAN mod-upload page" \
  io.hass.type="app" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.source="https://github.com/esper256/ha-gameservers"

EXPOSE 25565/tcp 8765/tcp 8099
ENTRYPOINT ["/run.sh"]
