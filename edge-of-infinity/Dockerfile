FROM golang:1.25-alpine AS edge_rtc
ARG BUILD_ARCH=amd64
WORKDIR /src
# Build-time limits leave room for Core and the running recorder on small hosts.
# The final image starts a new stage, so these do not constrain EdgeRTC at runtime.
ENV GOMAXPROCS=1 GOMEMLIMIT=384MiB
COPY edge-rtc/go.mod edge-rtc/go.sum ./
RUN go mod download
COPY edge-rtc/ ./
RUN edge_goarch="${BUILD_ARCH}"; if [ "${edge_goarch}" = "aarch64" ]; then edge_goarch=arm64; fi; \
    CGO_ENABLED=0 GOOS=linux GOARCH="${edge_goarch}" go build -p 1 -trimpath -ldflags="-s -w" -o /edge-rtc .

FROM ghcr.io/home-assistant/base:latest

# Keep stable dependencies before versioned binaries/files/labels so ordinary
# app updates can reuse this layer instead of downloading/extracting it again.
RUN apk add --no-cache curl ffmpeg jq python3 py3-pip py3-opencv py3-numpy mesa-va-gallium libva && \
    mkdir -p /usr/share/edge-of-infinity/models && \
    curl --fail --location --retry 3 --output /usr/share/edge-of-infinity/models/nanodet.onnx \
      https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx && \
    echo '4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186  /usr/share/edge-of-infinity/models/nanodet.onnx' | sha256sum -c -

COPY --from=edge_rtc /edge-rtc /usr/local/bin/edge-rtc
COPY edge-rtc/THIRD_PARTY_NOTICES.md /usr/share/edge-of-infinity/THIRD_PARTY_NOTICES.md
COPY rootfs /
RUN chmod a+x /usr/bin/edge-app-run
ENV OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

ARG BUILD_VERSION=0.17.9
ARG BUILD_ARCH=amd64
LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

CMD [ "/usr/bin/edge-app-run" ]

