# Edge of Infinity Home Assistant App

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="edge-of-infinity/logo.png">
  <img src="edge-of-infinity/logo-light.png" alt="Edge of Infinity — CCTV &amp; Live Stream" width="280">
</picture>

Home Assistant App repository for Edge of Infinity.

Version **0.17.2** names every add-on option in Polish and English and
tightens the dashboard bridge. Version **0.17.1** fixed dashboard cards that failed with "Importing a module
script failed" after an add-on update until Home Assistant Core was restarted;
the cards and Settings → Repairs now say when that restart is needed.

Version **0.17.9** restores Supervisor access through s6, verifies persisted
retired-option cleanup and keeps camera/detection settings in the Edge panel.
The app configuration defaults now contain only essential launcher/storage options.

Version **0.17.8** prepares HA Live/DVR startup in parallel and reuses up to eight
positive, exact-profile browser codec checks. Camera quality, audio, pacing and
recording files are preserved. Build-only compiler limits reduce competition
with Core/recorders on small hosts; see the host log guidance in the docs.

Version **0.17.7** fixes saved Supervisor options cleanup through its local Unix
socket and adds the original Edge icon to the restart notice. MediaMTX and Janus
artifacts are removed by name, with verified saves and bounded retries.

Version **0.17.6** restores the Home Assistant recordings gallery before a slow
network refresh, loads only the selected camera catalog and replaces repeated
preparing-video banners with a delayed in-video spinner. Adjacent decoded-frame
previews reuse the current player; catalog refresh preserves active playback. Original camera resolution, codec, 1x FPS, AAC and retention remain
unchanged. MP4 is retained for original-file downloads.

Live, DVR, events and dashboard cards can work entirely locally without Nabu
Casa. An optional HTTPS setup prepares a local certificate, a trusted-proxy
configuration snippet and a disabled-by-default gateway on port 8443.
See [local HTTPS setup](edge-of-infinity/DOCS.md#local-https-without-nabu-casa).
After updating the app, restart **Home Assistant Core** and reload the dashboard.

This is the correct packaging when Edge of Infinity should appear in Home Assistant under:

```text
Settings -> Apps / Add-ons
```

It provides:

- Supervisor-managed install.
- Logs visible in Home Assistant.
- Sidebar panel through Ingress.
- Watchdog health checks.
- Persistent app configuration.
- Optional host API port for integrations and local tools.

## Install Later

After publishing this repository on GitHub:

1. Open Home Assistant.
2. Go to Settings -> Apps / Add-ons -> App Store.
3. Open repositories.
4. Add the GitHub URL for this repository.
5. Install Edge of Infinity.

## Relation To The Integration

This app runs Edge Core.

The bundled Home Assistant integration exposes cameras and sensors, and automatically registers the Edge Live dashboard card. It supports 1–4 cameras, NVR/Main/Sub quality selection, audio controls and optional microphone talk. Live viewing shares the NVR recording feed by default. After an add-on update, restart Home Assistant Core to load the integration and refresh the frontend. See [setup and limitations](edge-of-infinity/DOCS.md#home-assistant-card-and-integration).

The app is the engine. The integration is the bridge.


## Native EdgeRTC

EdgeRTC uses our EdgeRTC/1 frame protocol over
WebSocket and browser WebCodecs, with no WebRTC or MSE on that path. One recording
profile is shared with native live, NVR and supported RTSP G711 microphone audio.
See [setup and current limits](edge-of-infinity/DOCS.md#live).

## Recording alerts (0.11.6)

In Edge settings, enable local detection, select the camera and choose person
and/or animal detection. Keep recording enabled. Intrusions add dots to the
video's existing seek bar: open a dot to see event thumbnails, then select a
thumbnail to watch that moment. Alerts also retain the picture and an original
quality clip. The clip becomes available after the post-event buffer and the
recording segment close; it includes whole boundary segments without re-encoding.

Detection reads the shared Edge relay; clips and preview thumbnails read saved
recordings. These features do not open additional physical camera streams.
Original resolution, codec and FPS are preserved. On HP t530, start with one
camera and the default total analysis rate of one frame per second.

Clips have a separate 256 MiB default budget; alert pictures have a 32 MiB / 2000
image budget. Both follow recording retention and evict their oldest files.
Original recordings and the existing 32 MiB preview cache retain their own rules.

For phone notifications, update the add-on and restart Home Assistant Core to
load the updated bundled integration. In Edge settings, expand phone
notifications, select your Companion `notify.mobile_app_…` service, enable and
save. Notifications use authenticated saved pictures and a link to the recorded
event. No extra automation is required; older manually created notification
automations can send duplicates if left enabled.

Version 0.11.7 keeps a persistent sampler on the existing Edge relay so local AI
can analyze frames between keyframes. It uses one low-priority decoder per
selected camera (maximum four) and one inference thread. Keep one camera and
one analyzed frame/second total on HP t530 initially. The recorder and live
stream retain the original resolution, codec and FPS. Long camera GOPs can
still delay the first decoded frame; the Events page now shows the last actual
analysis and a specific error code, also included in exported Local AI logs.

Recording tiles now prefer small cached JPEGs, and unchanged full-day file lists
are not repeatedly sent during polling. If the panel offers a refresh notice
after an update, reload that browser tab to load the current player code.

## Phone playback and decoder protection (0.11.8)

Update the app and refresh the panel tab. iPhone (including the Home Assistant
app) and Android request a normalized MP4 container for recordings and saved
alert clips. This copies encoded video without changing resolution, FPS or
pixels. A temporary 64 MiB / 12-segment cache avoids repeated work; it does not
copy whole days. HEVC playback requires phone/browser HEVC support.

In advanced local detection settings, leave the decoder on **Auto** initially.
Edge tries VAAPI when the host exposes a working GPU. If software decoding falls
behind under high CPU load, Auto can switch analysis to keyframes; Events shows
that mode and warns that short events between keyframes may be missed. Choose
**CPU — all frames** to force full decoding, or **CPU — keyframes** to reduce
load explicitly. Recording quality and FPS are independent of AI sampling. No
mode creates an extra physical camera stream. GPU behavior on HP t530 and native
phone playback must still be checked on the actual devices.

## Mobile playback and inline alerts (0.11.9)

The phone player releases hidden live/recording decoders when you change views
or cameras and starts muted; use the player's sound control to enable audio.
Alert points sit on the video's seek track, gray ahead of the cursor and green
behind it. Selecting a point still opens its intrusion pictures.

Existing files are prepared in the same bounded 64 MiB compatibility cache.
Video is copied without re-encoding, with track origins normalized to avoid a
black lead-in; AAC timing is repaired. These copies do not replace originals.
A failed AI VAAPI decoder is disabled for the rest of that app run, with CPU
fallback. Native phone tests now include WebKit 26.5 plus real saved HEVC/H264
and audio, rather than only Chromium with a phone user agent.

## Recording continuation and CPU diagnostics (0.11.10)

Same-day intrusion alerts reuse the current player. Opening a saved clip releases
inactive phone decoders. Playback keeps its Play intent across buffering and
can retry one transient network failure at the current position; explicit Pause
remains final. The next segment is prepared through a JSON request from saved
files, without starting another video decoder or camera connection.

From 0.11.11, compatible indexed originals are served directly; legacy files
use the bounded normalized-container cache. Healthy AAC packets
are copied, while irregular audio clocks are repaired; video pixels and FPS are
unchanged. H.265 can still take longer to decode/seek than a smaller H.264 source.
CPU diagnostics separate measured CPU utilization from I/O wait and load average,
and label a short startup load spike as a warning. Refresh diagnostics for the
first measured CPU sample. Disabled playback-copy cache logs are marked historical.

Version 0.11.11 adds **Recordings → ⋯ → Kodeki** for an explicit six-second
H.264/H.265 and AAC/silent/G.711 comparison using saved files. Conversions affect
only a bounded test copy, preserving original recordings and camera settings.
The playback log measures preparation, transfer, buffering and seek-to-play
time. Hardware/current-camera diagnostics and ONVIF/ISAPI profile discovery
help distinguish format support from hardware or network delays.

The Edge live card in EdgeRTC mode signs a Home Assistant WebSocket URL and
receives the shared Edge/NVR feed through `/api/edge/rtc/…`. It does not give the
browser a direct camera RTSP connection. The generated-camera integration test
checks this routing and one physical upstream with multiple viewers/microphone.

For individual phone/tablet reports in 0.11.11, restart Home Assistant Core,
then use **Diagnostics → Urządzenia i połączenia** to name/select a device and
download its session logs and connection map. Reports include browser-visible
resource timings, capabilities, video/decoder/buffer state and EdgeRTC stages;
unavailable phone CPU/RAM/radio/router data is identified explicitly. Logs are
bounded to 24 devices × 256 KiB, up to seven days, with redacted credentials.

## Archive loading and original recording (0.11.12)

Update the app and reopen the panel. The legacy recording-wide H.264 setting
migrates to original stream copy, with a configuration backup. Short codec tests
remain in **Recordings → ⋯ → Kodeki**; camera settings and existing files are
preserved. This avoids continuous 4K software encoding on small hosts.

Live loads only recorder state. Opening Recordings requests the archive
immediately, with gzip delivery and cached file metadata. Disk accounting runs
separately in Settings. Failed background polling retains the current player.
These changes preserve original resolution/FPS and the shared camera upstream.

Long camera GOPs still affect random seeks: a UNV stream with 24 seconds between
keyframes must decode from its preceding keyframe. An optional camera I-frame
interval of 1–2 seconds (20–40 frames at 20 FPS) can improve seeking, with a
possible bitrate/storage increase. Edge does not change that camera setting.

## AAC and archive readiness (0.11.13)

Update the app and reopen the panel. Unexpected native pauses retain Play
intent; explicit Pause cancels recovery. Rapid day changes ignore old player
callbacks and restore deferred phone sources before waiting for metadata.

G.711 voice recordings use AAC 16 kHz / 32 kbps mono with one audio thread;
native AAC packets are copied. Video keeps original dimensions, codec and FPS.
Valid short MP4 edits play directly; legacy audio clock/tail repairs stay in
the bounded compatibility cache. Existing recordings keep their original bytes.

A bounded shared catalog prepares history once, coalesces concurrent clients
and refreshes stale snapshots in the background. Visited days can open from the
browser cache immediately. Manual Refresh forces a current scan. Device logs
now expose audio timing/rate/clock state and unexpected pauses. See the
[catalog limits and playback details](edge-of-infinity/DOCS.md#01113-aac-pause-recovery-and-ready-archive-catalogs).
