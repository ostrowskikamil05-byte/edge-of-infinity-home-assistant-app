# Native EdgeRTC browser and media regression tests

Use Node with Playwright (Chromium/WebKit), Python, Go and FFmpeg. Browser binary
paths can be supplied through `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` and
`PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH`. Synthetic fixtures never contact a real
camera or Home Assistant.

## Unit and protocol checks

```sh
python -m unittest discover -s tests
go -C edge-of-infinity/edge-rtc test -race ./...
node tests/edge_rtc_pacing.cjs
node tests/edge_rtc_audio.cjs
node tests/edge_log_view.cjs
node tests/browser/remote_live.cjs
node tests/browser/edge_rtc_support.cjs
```

Go tests cover one physical upstream shared by viewers/recorder/microphone,
slow-consumer isolation, keyframe bootstrap, RTP recovery, camera SDP errors,
optional talk AAC filtering, indexed archive seeks and path confinement.
Audio tests compare G711 with FFmpeg, exercise the unchanged Live PCM policy,
and reproduce DVR ahead-of-picture AAC bursts, decoder backpressure and
cancellation across seek epochs at 0.5/1/2×.

`remote_live.cjs` exports disposable panel HTML itself. It covers authenticated
HTTPS/Nabu Casa paths, Ingress prefixes, native-only settings and save/readback
on phone, tablet and desktop. `panel_session.cjs` checks expired/resumed HA sessions.

## Panel fixture

```sh
EDGE_TEST_DETECTION_FIXTURE=1 python tests/browser/fixture_server.py
# In another terminal:
node tests/browser/settings_save.cjs
node tests/browser/camera_settings.cjs
node tests/browser/ui_dashboard.cjs
node tests/browser/nvr_navigation.cjs
node tests/browser/nvr_scrubbing.cjs
node tests/browser/nvr_alerts.cjs
node tests/browser/nvr_mobile.cjs
```

The default fixture listens on 18088. Set `EDGE_NVR_TEST_PORT` and matching
`EDGE_NVR_TEST_URL` to use another port. Tests retain actual Save error handling,
responsive layouts, camera profiles, full-day calendars, alert seeks and storage
controls. The engine selector and retired public-media settings no longer exist.

## Real mobile media and DVR

The CI workflow shows the complete fixture commands and environment variables.
`EDGE_TEST_MULTI_CAMERA=1` generates original H265 and H264 with AAC.
`EDGE_TEST_RECORDING_LONG_GOP=1` exercises long random-access intervals, and
`EDGE_TEST_RECORDING_4K=1` checks original-size composed pictures.

For EdgeRTC DVR, build the Go binary and pass it as
`EDGE_TEST_ARCHIVE_RTC_BINARY`; publish fixture metadata with
`EDGE_NVR_FIXTURE_INFO`. Run `nvr_edge_dvr.cjs` in WebKit and Chromium. It checks
actual decoded pixels, sustained audible AAC without sample loss, latest seek,
frame steps, pause/download bounds, same-socket file transitions, original-file
hashes and native MP4 fallback. `nvr_dvr_fallback.cjs` covers unsupported profiles.
Archive playback must leave the core's physical camera-source list empty.

`nvr_phone_native.cjs`, `nvr_aac_resume.cjs`, `nvr_seek_direct.cjs` and
`nvr_ready_playback.cjs` cover the native MP4 compatibility path. The 12000-file
archive scenario uses `nvr_archive_start.cjs`, `nvr_gallery_cache.cjs` and
`logs_organized.cjs`; it verifies IndexedDB/JPEG ownership, storage limits,
non-disruptive network failure and log filters.

`edge_rtc_long_gop.cjs` and `edge_rtc_native.cjs` inspect actual Live frame
presentation and cached-keyframe startup. `edge_rtc_fixture.py` / the Go fixture
provide generated camera frames and return-audio checks without external engines.

## Container smoke test

```sh
docker build -t edge-native-smoke edge-of-infinity
bash tests/container_smoke.sh edge-native-smoke
```

This starts the actual app image with a disabled synthetic camera and retired
options, then verifies a healthy native core, absence of retired executables,
custom-port migration, preserved credentials/settings and unchanged saved media.
