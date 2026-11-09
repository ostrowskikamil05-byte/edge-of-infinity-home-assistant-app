# EdgeRTC/1 — experimental native frame transport

EdgeRTC owns one persistent upstream RTSP client per camera source. RTSP readers
(recording, snapshots, probes, future analysis) and native live viewers share it.
An advertised RTSP G711 8 kHz mono backchannel is SETUP on that **same** session;
only one microphone lease may write to it. Unsupported backchannels leave video
working. No direct-camera microphone fallback is opened in this mode.

The native browser path uses **neither WebRTC nor MSE**. Complete H264 access units
are depacketized from RTP, prefixed with SPS/PPS on IDRs, and sent over WebSocket.
WebCodecs decodes them directly. Video is not re-encoded. WSS passes through the
normal authenticated HA connection; the Edge panel has a separate read-only
tunnel. There is no native UDP/QUIC/WebTransport implementation in this version.
TCP retransmissions can increase latency on a lossy network; benchmark before
claiming a performance advantage over WebRTC.

## Protocol

After an authenticated upgrade, the server sends a JSON `hello` containing
`protocol: "edgertc/1"`, video codec/Annex-B format, optional audio configuration,
and microphone capability/lease state. Error messages contain codes, not source
URLs, credentials or private tokens.

Server binary messages:

| Offset | Meaning |
| --- | --- |
| 0 | 1 = H264 access unit; 2 = encoded camera audio |
| 1 | Bit 0 marks an independently decodable video keyframe |
| 2–9 | Big-endian signed 64-bit media timestamp in microseconds |
| 10… | Annex-B H264, or encoded audio as described by `hello` |

Client microphone messages are exactly 321 bytes: byte 0 = 3, followed by
160 signed 16-bit little-endian mono PCM samples at 8 kHz (20 ms). The browser
AudioWorklet downsamples capture; the source converts PCM to the camera's G711
variant and writes RTP through the existing RTSP client. PCM rate/size and
microphone ownership are bounded. Read-only streams reject microphone frames.

## Backpressure and ownership

- One connection/reconnect loop per registry entry; consumers never dial cameras.
- Configuration edits close and join the previous source before replacing it.
- Identical configured RTSP URLs are merged by the private config generator.
- Main/Sub compatibility paths alias the selected recording profile. The HA card
  advertises only that actual profile and NVR, so it does not promise two qualities
  from a single original source.
- A cached GOP accelerates joining. It is bounded to 16 MiB / 2048 access units.
- Steady viewer queues allow 128 audio/video packets within an 8 MiB byte cap;
  cached startup allows 1024 queued packets. Slow viewers are closed, without
  blocking recorder delivery. At most 32 native viewers attach to one source.
- Browser decoder queues are bounded; stale decoded frames are closed.
- Core status distinguishes current upstream connections from connection attempts.
- Private HTTP API requires a token. The HA bridge requires entity read access,
  and entity control access for microphone capture. The token remains backend-only.
- WS compression is disabled for already compressed video.
- Browser camera audio uses one continuous PCM AudioWorklet with an 80 ms
  initial jitter delay and at most 240 ms of source samples. Resampling spans
  packet boundaries; microphone capture retains its separate processor in the
  same module. Recording AAC and original video are independent of this output.

## Build and test

```sh
go test -race ./...
CGO_ENABLED=0 GOARCH=amd64 go build -o edge-rtc .
CGO_ENABLED=0 GOARCH=arm64 go build -o edge-rtc-arm64 .
```

The Go tests use real RTSP/WebSocket sockets and a simulated camera. They verify
four viewers, three RTSP consumers and returned microphone RTP on one upstream,
authentication, and serial replacement of a source. The optional browser fixture
also sends valid generated H264, decodes it with Chromium WebCodecs, routes real
browser-generated PCM back into the simulated camera, exercises the actual HA
bridge code with HA stubs, and records MP4 through the production FFmpeg command.
See `tests/browser/README.md`.

Local fixture measurements on a **warm** 640×360 / 10 fps H264 source showed first
drawn frames around 30–300 ms after client start. These are first-image startup
measurements, not glass-to-glass latency, a WebRTC comparison or a guarantee for
real cameras/networks. Hardware decoding is selected by the browser when usable;
software fallback is allowed. Physical speakers and actual HA Core were not run.

## Current limits and next modules

This is the first transport/ownership stage of Edge, not the complete planned
NVR/AI platform. Native live requires H264, a secure browser context and WebCodecs.
Camera G711 audio has a WebAudio fallback; AAC/Opus playback additionally depends
on browser AudioDecoder support. Recording still copies the source video; H265
can remain recordable while native H264-only live reports unsupported. Native PiP
and proprietary vendor talk APIs are not implemented. Existing legacy engines
remain selectable.

Further stages: WAN loss/congestion benchmarks; WebTransport datagrams with
capability negotiation; ONVIF/vendor event adapters and PTZ; then hardware-backed
local detection, zones and tracking on the shared source. Those capabilities are
planned and are not advertised as working controls in this version.

Runtime third-party licenses are included in `THIRD_PARTY_NOTICES.md`.
