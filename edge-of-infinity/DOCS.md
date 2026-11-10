# Edge of Infinity

## 0.14.1: EdgeRTC Live on variable networks

EdgeRTC is the sole streaming core. Update the app, restart Home Assistant Core
if the bundled integration is installed, and reopen Edge through the HA sidebar.
The version/build is `0.15.0` / `cellular-live-v141`.

The update removes Janus and MediaMTX binaries, packages, startup services,
configuration generators, engine selection, public media ports and obsolete
network settings. A bounded migration removes their saved options and exact
engine artifacts. It preserves each camera, credential, profile, recording
setting, database, alert, private EdgeRTC token and original recording file.
Existing configuration mirrors keep their own timestamps and camera contents;
migration cannot promote an old mirror over the authoritative configuration.

If a camera used custom shared RTSP/API ports, migration carries them to
`edge_rtc_rtsp_port` / `edge_rtc_api_port`. Native defaults remain 8556 / 9997.
The migration result is available in `/homeassistant/edge/native-migration.json`;
only counts, safe status codes and skipped filenames are recorded.

## Live

Each configured camera has one server-side source for the selected recording
profile. Live, recording, supported microphone and analysis share it. Main/sub
paths are aliases of that source; choosing another view does not open another
camera channel. Duplicate camera entries with the exact same URL share a source.
Sources stay connected with the panel closed. A viewer still needs to connect
and decode a keyframe; startup is not guaranteed to be instantaneous.

Original H.264/H.265, resolution and FPS are retained. No generated frames or
video re-encoding are added. Browser HEVC support depends on the device/profile.

Live measures delivery gaps and adds a bounded playout reserve, up to 400 ms,
when packets arrive in cellular-style bursts. Picture pacing and the continuous
PCM worklet adapt together. A steady connection keeps the existing short-queue
policy. Detection uses actual arrival/source timestamps and also works when
iOS does not expose a connection type. It never changes a camera encoder.
If decoding falls far behind, a newer complete keyframe can replace stale queued
references on the same socket. Arbitrary encoded delta frames are not skipped.

Device logs include `rtc_network_buffer`, `rtc_network_sample` and
`rtc_network_recovery`, with delivery rate, jitter estimate, application round-trip
time and bounded client/server queues. These are browser/stream observations,
not cellular signal strength, link capacity or a traceroute. Timing feedback uses
the same authenticated HA/Ingress socket; microphone permissions remain separate.
Buffering trades some latency for continuity. It cannot sustain original-quality
video when the server upload, relay or mobile downlink stays below the combined
bitrate of the selected camera views. Choose fewer simultaneous views in that case.

Open Home Assistant through **HTTPS**, including the Nabu Casa remote address.
Live uses authenticated HTTPS/WebSocket connections and browser WebCodecs.
No separate public streaming endpoint is configured. Microphone use requires
browser permission and a compatible G.711 ONVIF/RTSP backchannel.

UNV can advertise an invalid optional talk AAC format without its decoder
configuration. Edge filters that invalid optional alternative while preserving
forward H.265/AAC and valid PCMA/PCMU microphone formats. The event
`rtsp_optional_audio_filtered` and per-media SDP diagnostics identify this case.

## DVR and recordings

Open **Recordings**, select the camera/day, and use the in-picture seek bar.
Choose **EdgeRTC DVR** in the player's playback-mode menu; the saved device
choice is retained after updating.
EdgeRTC DVR reads indexed H.264/H.265/AAC samples from saved MP4 files over one
persistent authenticated socket. It seeks from the preceding keyframe, discards
outdated seek results and keeps bounded video/audio queues. It does not connect
to the camera, download a whole day or modify original files. Pause stops pulls;
leaving Recordings releases the viewer's decoder and socket.

The DVR offers frame steps, speed 0.5–2×, sound, full screen and event markers.
Gray event points turn green behind the playback position. Selecting an event
or its image seeks to that moment. Hover-thumbnail strips are not used.
Native MP4 playback remains available and is the automatic fallback for an
unsupported codec/profile or unavailable archive audio decoder.

DVR sound uses a separate bounded PCM policy: up to 600 ms, with encoded AAC
held until its picture time approaches. Decoder backpressure avoids silently
losing AAC packets. Live's separate policy adapts to delivery gaps. Neither mode
adds extra saved recordings. Diagnostics expose audio queue, packet loss,
underruns/overruns, worklet mode and buffer limit.

A short playback buffer is still needed for transfer and decoding. Seek time
depends on keyframe spacing, file layout, network and device decoder. Explicit
1 s / 2 s GOP camera actions use supported ONVIF/ISAPI profiles with readback;
they do not rewrite existing archives or change other encoder settings.

## Camera configuration

Camera settings are saved in `/homeassistant/edge/edge.json`. The app mirrors
this file to `/config/edge.json` and `panel-config.json` for visibility. An existing
invalid authoritative file is reported instead of restoring stale camera data.
An explicitly empty camera list remains empty.

Enter the vendor, camera IP, username and password; Edge builds standard vendor
RTSP/ONVIF/ISAPI endpoints. Explicit/custom stream paths remain supported.
Separate password fields expect the actual password; URL credentials are encoded
when building a URL. ONVIF discovery reads advertised profiles and capabilities.
Uniview `/media/videoN` and `/unicast/cN/sN/live` aliases are recognized without
conflating camera channels or main/sub selectors.

Saving camera connection settings updates the shared source registry. Recorders
waiting for metadata retry independently of the UI; another physical RTSP probe
is not started. Unsupported ONVIF synchronization is optional and does not by
itself mean the video source failed.

## Storage and retention

Recordings default to `/media/edge-of-infinity/recordings`, with **14-day retention**.
Original video is packet-copied. Existing source AAC is copied; incompatible
G.711 is converted once when needed for saved playback. Camera-compressed video
cannot generally be made much smaller without sacrificing detail.

The UI groups physical segments into complete local calendar days, including
DST days. Short segments keep seeking and crash recovery practical. Existing
recording directories and camera IDs remain compatible after this update.
The database defaults to `/homeassistant/edge/edge.db`.

Thumbnail, playback-compatibility and alert caches have separate limits. Storage
cleanup removes disposable generated data; it does not delete unrelated media
or original recordings. Storage statistics are loaded independently of the
archive gallery. Local gallery metadata/JPEG storage does not include passwords
or whole video files.

## Home Assistant card and integration

Enable automatic integration installation, update the app and restart HA Core.
Use the dashboard section to copy the generated `custom:edge-live-card` YAML or
select a standard HA camera entity. The custom card supports 1–4 views, layout,
audio, microphone and full screen. Its native source is the EdgeRTC registry.
HA entity read permission and microphone control permission remain separate.
The private core token is never included in public camera metadata/card YAML.

### Recordings and events cards (0.15.0)

Two more cards are registered automatically with the integration. Update the
app and restart HA Core once so Home Assistant loads them.

```yaml
type: custom:edge-recordings-card   # days, 24 h / 6 h / 1 h timeline, playback
type: custom:edge-events-card       # person, vehicle and animal detections
```

Both show every Edge camera by default. Optional settings:

| Option | Card | Meaning |
| --- | --- | --- |
| `title` | both | Heading text |
| `cameras` | both | List of `camera.…` entities to show |
| `muted` | recordings | Start playback without sound |
| `kinds` | events | Any of `person`, `vehicle`, `animal`, `motion` (motion is off by default) |
| `layout` | events | `grid` (default, grouped by day) or `strip` (one swipeable row) |
| `limit` | events | Events per page, 4–60 (default 12) |
| `max_height` | events | Scroll the list inside this height in pixels |

The cards lay themselves out from their own width, so the same YAML works in a
narrow phone column and a wide desktop section. In a sections view, give the
recordings card `grid_options: {columns: full}`. "Show in recordings" in the
event viewer moves the recordings card on the same view to that moment.

Requests go through Home Assistant: each one names a camera entity, requires
that user's read permission and is mapped to a fixed add-on route. Video is
requested with short-lived signed paths; no add-on address, token or camera
credential reaches the browser. Recordings keep their original codec. A browser
without an H.265 decoder shows a message instead of a black picture; use the
Edge panel or a device with H.265 support for those cameras.

## Detection and alerts

Detection is optional and keeps its existing settings during migration.
Enable person/animal/vehicle classes, camera selection and zones in settings.
Camera events use supported ONVIF/vendor APIs; local detection samples the shared
source. Original recording is independent of analysis load.

For HP t530, start with one analyzed camera and one analyzed frame/s overall.
Auto decoding prefers supported acceleration and protects CPU load. Detection
quality depends on object size, light and model limitations; it does not identify
a person's identity. Alerts retain a picture and bounded original-recording clip.

Choose a Home Assistant Companion `notify.mobile_app_…` service for phone alerts.
New events use the configured service/cooldown; retained history is not replayed
as new notifications after restart.

## Diagnostics

Logs are grouped by device, camera, module, severity and time. Device reports
include connection/decoder/seek timing, queue sizes, media support and audio
continuity counters. Export only the relevant device when diagnosing a phone.

Source status includes connection phase, RTSP method/status, safe error code,
per-media SDP formats, packet count, upstream count and RTP recovery counters.
`recording_autostart_waiting` distinguishes source/metadata waiting from a real
FFmpeg failure; older FFmpeg tails are not attached to metadata waiting.

An expired HA Ingress session pauses requests and asks to reopen the sidebar
panel. Structured diagnostics are bounded and batched. Optional camera features
remain warnings; authentication, missing sources and genuine decoder errors are
reported explicitly. The watchdog checks the native core's actual availability.
