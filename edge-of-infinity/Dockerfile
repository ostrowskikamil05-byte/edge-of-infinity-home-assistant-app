FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=0.4.8
ARG BUILD_ARCH=amd64

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

COPY rootfs /

RUN apk add --no-cache curl ffmpeg jq python3
RUN chmod a+x /usr/bin/edge-app-run

CMD [ "/usr/bin/edge-app-run" ]
