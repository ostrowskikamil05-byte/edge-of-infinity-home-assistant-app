# NVR browser regression tests

Requires Python 3.12+, FFmpeg/FFprobe, Node.js and Playwright with Chromium.
The multi-camera fixture also supports `nvr_codec_test.cjs` with either
`EDGE_TEST_BROWSER=webkit` or `chromium`. It uses actual generated HEVC/H.264
and AAC media, checks short codec-copy/convert requests, G.711 results and
buffered-scrub/progress recovery. No video responses are mocked.
`device_diagnostics.cjs` checks separate phone/tablet IDs and sessions, resource
timings, redaction, device selection and downloading the report. The actual
EdgeRTC fixture additionally verifies authenticated card telemetry and rejects
uploads without camera read permission.
The fixture uses temporary configuration and generated seven-second MP4s, with
camera/network/runtime callbacks disabled. It never connects to Home Assistant.

`nvr_archive_start.cjs` additionally checks a cold 12,000-file archive on WebKit
and Chromium: lightweight Live boot, immediate compressed history loading,
actual video playback and uninterrupted playback after a failed background
refresh. Start a separate fixture with `EDGE_TEST_ARCHIVE_FILES=12000` and
`EDGE_NVR_TEST_PORT=18092`; the test defaults to that port. Files are disposable
hardlinks to generated media, not production recordings.

Install browser test dependencies outside the repository:

```sh
npm install --prefix /tmp/edge-browser-tests playwright
NODE_PATH=/tmp/edge-browser-tests/node_modules /tmp/edge-browser-tests/node_modules/.bin/playwright install chromium
```

Start the isolated server in one terminal:

```sh
python tests/browser/fixture_server.py
```

In another terminal:

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/nvr_playback.cjs
```

The suite checks 1440px desktop, 820px portrait tablet, 1180px landscape tablet,
390px phone and 320px small-phone viewports. It verifies a 21-second combined
player, automatic transitions through three actual seven-second MP4s, seeking,
date switches, visible image previews and their aspect ratios, no horizontal
overflow, and stable paused playback when the day grows during a status update.

Stop the fixture server with Ctrl+C. Optional environment variables:
`EDGE_NVR_TEST_PORT`, `EDGE_NVR_TEST_URL`, `EDGE_NVR_FIXTURE_INFO`,
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`, and `EDGE_NVR_SCREENSHOT_DIR`.
Create the screenshot directory before setting it. Use the fixture server only
with this test suite, not with a production URL (the tests add/remove a fixture MP4).

The playback suite also delays the next MP4 response and verifies that the last
video frame stays visible without a poster until the next decoded frame appears.

After the playback suite, run the camera-form test against the same disposable
fixture (it changes only the fixture camera configuration):

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/camera_settings.cjs
```

This verifies vendor selection, edited IP/password, URL encoding, persistence,
the options-sync status message and an ONVIF discovery response loaded into the
form. ONVIF responses and Supervisor success are mocked in these browser tests;
protocol and projection behavior have separate unit tests. No physical camera
or production Supervisor is contacted.

The redesigned settings and Home Assistant card builder have an additional fixture-only suite:

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/ui_dashboard.cjs
```

It checks camera/advanced group visibility, unsaved state, a single retention
setting and save round-trips, actual mapped entity IDs, YAML quoting, clipboard
contents, invalid/missing-entity handling and responsive layout on four viewports.
It writes only fixture configuration and a fixture entity-ID map. Do not point
this suite at a production installation. Native Home Assistant stream-source,
FFmpeg fallback, entity registration and metadata export behavior are tested in
`tests/test_ha_live_camera.py` using API stubs; no real Home Assistant Core is run.

The bundled HA custom card has a real browser WebRTC loopback suite:

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/edge_live_card.cjs
```

The test loads the actual card module into a disposable page. HA's authenticated
WebSocket message envelope is simulated; both ends use real RTCPeerConnections,
generated video and synthetic audio. It checks decoded live frames, returned
audio RTP packets, no microphone capture on autoplay, release on toggle/removal/
page hiding, cancellation of delayed permission, denied and rejected return
audio, player identity on state updates, controls, editor changes and four
responsive viewports. It does not connect to Home Assistant, go2rtc or a camera.

If managed browser policy blocks WebRTC transport (for example
`WebRtcIPHandling: disable_non_proxied_udp`), run with
`EDGE_WEBRTC_SIGNALING_ONLY=1`. This mode still negotiates real SDP and verifies
microphone directions, but injects local synthetic tracks into the receive
handler for UI rendering. It does **not** verify ICE connectivity or RTP packet
transfer. Report which mode was run; do not describe this fallback as a passed
media transport test.

The card suite also checks 1–4 cameras, actual NVR/Main/Sub entity selection,
focus/all views, closed sessions for hidden tiles, exclusive microphone capture
and return from the temporary direct Talk entity to NVR. The YAML builder suite
covers 2/3/4-camera custom and native grids, quality, actual IDs and duplicates.

A separate RTSP integration test requires MediaMTX 1.19.2 and FFmpeg/FFprobe:

```sh
MEDIAMTX_BINARY=/path/to/mediamtx python tests/rtsp_shared_stream.py
```

It launches two temporary local MediaMTX processes, publishes generated H264 as a
camera, and uses the real Edge runtime config for the NVR. Three simultaneous
readers plus a parameter probe must share one upstream camera RTSP session,
preserve the 640×360 source dimensions, and release the upstream on NVR shutdown.
It uses ephemeral loopback ports and temporary logs, and cleans up its processes.
It does not contact a physical camera or Home Assistant.

Native EdgeRTC uses a separate real-media suite, with no WebRTC/MSE fallback:

```sh
(cd edge-of-infinity/edge-rtc && go test -c -o /tmp/edge-rtc-fixture .)
EDGE_RTC_TEST_BINARY=/tmp/edge-rtc-fixture python tests/browser/edge_rtc_fixture.py
# In another terminal:
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/edge_rtc_native.cjs
```

Requires aiohttp, FFmpeg and a WebCodecs-capable Chromium. The fixture generates
valid baseline H264 and a simulated RTSP camera with G711 return audio. Four
browser players receive actual frames through the real HA bridge implementation
(with HA APIs stubbed), with RTCPeerConnection and MediaSource deliberately
replaced by throwing constructors. Browser-generated PCM reaches the RTSP
backchannel. The suite checks one upstream, denied microphone control, source
cleanup, native Edge panel viewing, and MP4 recording through the production
FFmpeg command. First-image timings use an already-running source and are not
camera-to-screen latency. No physical camera, speaker or actual HA Core is used.
Stop the Python fixture with Ctrl+C or SIGTERM; it cleans up its Go camera/core.


Browser capability and connection-requirement regression tests run without a
server or physical camera:

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/edge_rtc_support.cjs
```

Playwright intercepts all requests on disposable HTTP and HTTPS test origins.
The suite exercises the real browser secure-context restriction, missing
WebCodecs in a secure context, supported WebCodecs, Polish guidance, microphone
cleanup and permanent-error retry handling in the actual card. This does not
validate a deployed TLS certificate or the Home Assistant Cloud tunnel.


Remote Edge panel routing has a fixture-only suite:

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/remote_live.cjs
```

This loads the actual native/legacy panel pages on intercepted HTTPS test
origins, including a Nabu Casa hostname and HA ingress prefix. It verifies
remote notices, same-origin player URLs, live toggles, hidden legacy controls,
saved values and four viewport widths. Player content and API responses are
stubbed; no Nabu Casa tunnel, real HA installation or camera is contacted.


Detection settings/history/NVR seek use a disposable fixture with seeded history:

```sh
EDGE_TEST_DETECTION_FIXTURE=1 python tests/browser/fixture_server.py
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/detection_ui.cjs
```

The new suite checks per-camera/global settings, persistence, event filters and
opening the real generated recording at the selected event time on four widths.
ONVIF/capture callbacks are disabled in this UI fixture.

For real shared-source inference and HEVC transport tests, use the native fixture
with the verified NanoDet model path:

```sh
EDGE_RTC_TEST_CODEC=h265 EDGE_RTC_TEST_DETECTION=1 EDGE_DETECTION_MODEL=/path/to/nanodet.onnx EDGE_RTC_TEST_BINARY=/tmp/edge-rtc-fixture python tests/browser/edge_rtc_fixture.py
python tests/edge_rtc_hevc_wire.py
EDGE_RTC_TEST_CODEC=h265 NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/edge_rtc_native.cjs
```

The wire test decodes actual HEVC EdgeRTC bytes with FFmpeg, checks changing video
timestamps, original HEVC recording, G711 return packets, local-model processing
and one upstream. The browser suite validates HEVC frames when its decoder is
available; otherwise it explicitly checks the permanent HEVC-unavailable message.
Report which HEVC browser path ran. Physical speaker/camera firmware and deployed
HA/ONVIF devices are not covered by these fixtures.

The detection UI fixture seeds 212 events, including an old person event outside
the recent 200-event mirror. `detection_ui.cjs` verifies complete history paging,
date/type filters, preservation during metadata polling, cancelled/stale filter
and timeline responses, Companion notification YAML/clipboard, event markers,
missing-footage handling and actual MP4 playback seeks at four screen widths.
`nvr_playback.cjs` also runs against this fixture in CI to protect clip transitions,
paused playback, growing days, thumbnails and fullscreen controls.

`detection_zones.cjs` uses the isolated detection fixture's generated 416×234 JPEG
to verify explicit preview loading, mouse and real emulated-touch drawing,
cancelled gestures, percentage inputs, disk/reload persistence, rejected-save
preservation and camera/local/zone-only notification YAML at four widths. The
browser fixture never contacts a camera. The separate real HEVC pipeline test
calls the actual preview service, checks its JPEG dimensions/cropping and confirms
preview, analysis, recording and microphone still share one upstream connection.
