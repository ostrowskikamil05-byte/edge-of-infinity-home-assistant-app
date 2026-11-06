FROM golang:1.25-alpine AS edge_rtc
ARG BUILD_ARCH=amd64
WORKDIR /src
COPY edge-rtc/go.mod edge-rtc/go.sum ./
RUN go mod download
COPY edge-rtc/ ./
RUN edge_goarch="${BUILD_ARCH}"; if [ "${edge_goarch}" = "aarch64" ]; then edge_goarch=arm64; fi; \
    GOMAXPROCS=2 CGO_ENABLED=0 GOOS=linux GOARCH="${edge_goarch}" go build -p 2 -trimpath -ldflags="-s -w" -o /edge-rtc .

FROM bluenviron/mediamtx:1.19.2 AS mediamtx

FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION=0.10.46
ARG BUILD_ARCH=amd64

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

COPY --from=mediamtx /mediamtx /usr/local/bin/mediamtx
COPY --from=edge_rtc /edge-rtc /usr/local/bin/edge-rtc
COPY edge-rtc/THIRD_PARTY_NOTICES.md /usr/share/edge-of-infinity/THIRD_PARTY_NOTICES.md
COPY rootfs /

RUN apk add --no-cache curl ffmpeg jq python3 py3-pip janus-gateway && \
    chmod a+x /usr/bin/edge-app-run /usr/local/bin/mediamtx

CMD [ "/usr/bin/edge-app-run" ]

