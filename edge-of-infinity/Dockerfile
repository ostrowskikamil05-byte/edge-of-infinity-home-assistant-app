FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=0.3.2
ARG BUILD_ARCH=amd64

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

COPY rootfs /

RUN apk add --no-cache darkhttpd ffmpeg jq
RUN chmod a+x /usr/bin/edge-app-run

CMD [ "/usr/bin/edge-app-run" ]
