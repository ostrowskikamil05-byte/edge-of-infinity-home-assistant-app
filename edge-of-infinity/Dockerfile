FROM bluenviron/mediamtx:1.19.2 AS mediamtx

FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=0.8.3
ARG BUILD_ARCH=amd64

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

COPY --from=mediamtx /mediamtx /usr/local/bin/mediamtx
COPY rootfs /

RUN apk add --no-cache curl ffmpeg jq python3 py3-pip janus-gateway && \
    chmod a+x /usr/bin/edge-app-run /usr/local/bin/mediamtx

CMD [ "/usr/bin/edge-app-run" ]
