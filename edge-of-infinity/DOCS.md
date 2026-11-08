# Edge of Infinity App Documentation

## 0.11.11: seek recovery and codec comparison

After this update, **restart Home Assistant Core**, then reopen the panel/card.
This registers the authenticated card-diagnostics endpoint and new JavaScript.
In **Diagnostics → Urządzenia i połączenia**, name each phone/tablet, select its
device/session and download the JSON report plus connection map. Open Edge/the
card on that device first; reports are collected from open sessions, not phones
which have never loaded the app. The same browser/origin keeps its random local
ID; a new private session, cleared storage or another HA hostname can create
another ID. Names are user labels; a browser cannot reliably identify an exact
iPhone model. Sessions keep monotonic durations and client/server timestamps.

Reports capture resource times, playback/decoder state, queues, dropped frames,
WebSocket/auth/first-frame stages and online/offline/JavaScript errors. Active
sources are sampled every 20 seconds; error/loading events are batched. This is
not a video packet capture. Limits are **24 devices × 256 KiB / seven days**;
bounded queues report dropped entries and retry with backoff. The exported
map distinguishes configured camera/Edge addresses, observed HA proxy peers
and browser endpoints. It does not inspect routers or run traceroute. Safari
may omit network/heap/timing fields; phone CPU load, free RAM and Wi-Fi signal
remain unavailable instead of guessed. Zero DNS/TCP can mean connection reuse.
Only camera-readable authenticated HA users can submit card telemetry, and
passwords/signatures/ingress IDs are removed from exported URLs and logs.

Update the app, then reload the Edge panel. Open **Recordings → ⋯ → Kodeki**
to compare up to six seconds from the current saved segment. Choose original,
H.264 or H.265 video, and AAC, no audio, A-law or μ-law. Changing video codec
encodes only a short test copy; it can take time on a two-core t530. Normal
recording/playback keeps original pixels and FPS and does not change camera
settings. The G.711 tests use MOV; unsupported browsers display an error and
record it in logs. If a recording has no audio, choose **Bez dźwięku**.

The tests share one low-priority worker and the existing 64 MiB/12-file/48-hour
compatibility cache, with at most 24 MiB for a test and a 90-second timeout.
They use already saved media, never a second physical camera stream. A-law/μ-law
generated from an AAC recording do not establish whether the camera supports
these encoders natively. Closing the test window stops its player; an already
running server preparation remains bounded by its timeout.

**Logs → Playback performance / Playback / codec tests** shows direct delivery,
remuxing, queue/preparation time, transfer bytes/time, seek-to-play time, native
buffer/error state and dropped frames. Compare AAC with no audio to isolate an
audio-related delay; compare H.264/HEVC test results on the same device. Direct
delivery is used only for indexed compatible originals with normal clocks.

**Hardware diagnostics → inventory** lists the detected CPU/product and GPU
driver/render nodes. It does not imply hardware acceleration works. **Camera
media** describes the current stream. To collect camera-reported profiles,
run its existing ONVIF/ISAPI discovery in Settings; **Camera discovery** logs
the model, firmware and reported video/audio profile settings. Discovery reads
settings and does not change camera codecs. Missing profile data means unknown,
not unsupported. Logs are bounded and credentials are redacted.

EdgeRTC already keeps its upstream connected and caches a bounded keyframe
group on the server. Diagnostics now show keep-alive and cache occupancy.
There is still one upstream per shared source. Original HEVC bitrate/GOP and
the network/browser can limit startup speed even with a warm server.

## 0.11.2: local AI zones

After updating the app, restart Home Assistant Core so `zone_id` is forwarded
by the updated integration, then refresh the panel/card in your browser.

1. Enable **Settings → Detection → Local AI** and **Cameras → Detection → Local AI**
   for the selected camera. Zones apply to local person/vehicle detection.
2. Save camera connection or recording-profile changes before loading a preview.
3. In that camera's **Detection**, enable **Restrict local AI to zone** and open
   **Set zone on image / Local AI zone**. Click **Refresh frame**.
4. Click **Draw zone** and drag a rectangle with the mouse or a finger. Drawing
   enables the zone. A tap or cancelled gesture keeps the previous rectangle.
   Percentage inputs provide a keyboard alternative; **Full frame** resets bounds.
5. Save cameras. The region must be inside the frame, at least 0.5% wide and high.
   Bounds are restored after a restart. Zone edits apply without restarting the
   recorder, camera RTSP connection or existing ONVIF subscription.
6. For zone-based alerts, use **Events → Phone notifications → Source → Only Edge
   zone**, then copy/save the generated automation in HA. Existing generic
   automations continue using their saved filters; replace their YAML if you
   want them to use the new zone-only source.

There is one rectangular zone per camera. The **bottom-center point** of each
object's bounding box determines membership; overlapping the region with only
the top/side of the box does not trigger it. Filtering occurs before new local
history events and local detection states are published. Model padding is removed
before checking coordinates, and the editor preview shows that same unpadded
scene. New local `objects[].box` values use normalized original-scene coordinates
`[left, top, right, bottom]`. Existing history is preserved.

The zone filters **local AI**. ONVIF events use the analytics/regions configured
inside the camera; Edge does not program the Uniview's own regions. Camera and
local states still feed the existing combined HA person/vehicle sensors. For an
alert restricted to the Edge region, use the generated event filter:

```yaml
event_type: edge_event
event_data:
  camera_id: your_camera_id
  source: local
  zone_id: local_zone
```

`zone_id: local_zone` is attached only when the local region is enabled. A zone-only
automation therefore stops matching if the region is disabled. The generator
also supports camera-only, local-only and both sources. It does not modify existing
HA automations or send a notification itself.

Preview is an explicit single-frame request from the existing recording-profile
loopback relay. Edge reuses analysis frames captured within five seconds, keeps
at most four small frames in memory and serializes capture/preview work. JPEGs
are not written to disk. Disabled/unavailable cameras return a preview error;
the editor requires a saved, enabled camera and a working shared relay. It never
falls back to a direct camera URL. The region limits alerts; inference still
processes its normal 416×416 input, and recordings/live/audio keep their complete
original streams. HP t530 settings remain one selected camera and 1 total FPS as
a conservative starting point.

The camera config adds optional `detection_zone_enabled` and normalized
`detection_zone_left/top/right/bottom` fields. Supervisor options mirror those
fields. Invalid submissions fail before persistence; invalid enabled regions
found in an existing file suppress local detections until repaired.
`GET /api/detection/preview?camera=ID` is a read-only JPEG response with no-store
headers, frame dimensions and capture time; concurrent requests can return 429.
Credential-free detection metadata includes `local_zone` and HA binary sensors
expose `local_zone_enabled` as an attribute.

## 0.11.1: event history, recording markers and phone notifications

**Events** now browses the complete retained SQLite history, up to its existing
20,000-event/retention limit. Select a camera, event type and recorder-local date;
leave the date blank for all days. **Older/Newer** pages contain up to 100 events.
Detection-status polling preserves your selected page. **Refresh** returns to the
newest matching events. Camera/type/day filtering happens in the database, so an
older person event remains searchable even after hundreds of newer motion events.

Each **Recordings** day has a separate event track: green motion, blue person,
yellow vehicle. A marker groups five minutes. Select its time from the dropdown to open
the first event in that interval; this also works on narrow touchscreens. **Previous/Next events** steps through groups; **Full day history**
opens the individual events filtered to that camera and date. Mixed groups use
person/vehicle color priority and list all contained types in their tooltip.
A missing recording displays a message and keeps the current playback position.
Metadata refreshes preserve the existing player and pause state. NVR fullscreen
expands the complete player with its controls; browsers lacking element fullscreen
use an in-page view instead of hiding the controls in video-only fullscreen. The event
track refreshes at most every 15 seconds while NVR is open, with at most two
timeline metadata requests in flight; it opens no camera media sessions. Day boundaries
use the recorder's timezone, including daylight-saving changes.

In **Events → Phone notifications / Home Assistant**, select a camera, enter the
Companion app service (for example `notify.mobile_app_your_phone`) and choose a
cooldown. Copy the generated YAML into a new HA automation's YAML editor, save
and enable it in Home Assistant. Find the service name under **Developer tools →
Actions** after registering the phone in the HA Companion app. The automation
reacts to person/vehicle `edge_event` messages from camera ONVIF or local AI.
It sends one notification and waits for the configured cooldown; other matching
events for that camera during the wait are ignored. Edge only generates the
code and does not create an automation or send a test notification itself.

The compatible history endpoint accepts
`GET /api/events?camera=ID&kind=person&day=2026-07-27&limit=100&before=EVENT_ID`.
It returns `events`, `has_more` and `next_before`; pass the latter as `before`
for the next older page. Omit filters for all events. The response is capped at
1,000 rows. Invalid kinds/dates/cursors return HTTP 400. Timeline metadata is
`GET /api/events/timeline?camera=ID&day=2026-07-27`, with all event counts grouped
into five-minute intervals and timestamps of their first events. Neither API
adds media decoding or public ports. Existing retention, recordings, detection
settings and HA entity IDs are preserved.

## 0.11: native HEVC, camera events and local detection

After updating the app, restart Home Assistant Core to load the new integration
and refresh the frontend. Existing camera IDs, recordings and retention settings
are preserved. Select **EdgeRTC** in **Settings → Streaming engine** for native
H264/H265 live. Open HA through HTTPS, including your Nabu Casa address.

### Enable detection

1. In **Cameras**, enable **ONVIF events** and/or **Local AI** for selected cameras.
2. In **Settings → Detection**, enable the corresponding global module and save.
3. Open **Events** to inspect camera/module state and recent motion/person/vehicle
   events. Filter by camera/type; **Open recording** selects the event day and
   position. A recording must exist at that time.

Both modules are initially off. ONVIF analysis runs on the camera; Edge subscribes
to its PullPoint event service and only exposes types it advertises or reports.
Enable/configure the camera's own analytics and grant its ONVIF account the
required event permissions. A product's AI label does not guarantee that its
firmware publishes person/vehicle classification through ONVIF. Generic motion,
line-crossing/intrusion boolean topics are grouped under motion when recognized.
Unsupported topics are ignored rather than guessed.

Local AI uses a packaged **NanoDet-m-plus-1.5x_416** model from OpenCV Zoo. Its
revision/SHA-256 and Apache-2.0 license are in `models/NOTICE` and `models/LICENSE`.
The image build downloads/verifies the model; running inference does not send
camera images to an external service. Detected classes are person and vehicle
(car, bicycle, motorcycle, bus and truck). This is object detection, not identity
recognition, plate recognition or object tracking. Boxes/scores are stored with
events; no additional image copies are retained by this module.

### HP t530 starting settings

Start with **one selected camera**, **1 total analyzed FPS**, confidence **0.45**
and a **10-second hold**. The single CPU worker handles cameras sequentially,
reads keyframes through the existing local RTSP relay and uses 416×416 input.
The FPS value is an upper limit shared across cameras; actual sampling also
has to wait for camera keyframes/decoding. More selected cameras increase the
time between samples. Prefer camera events when the Uniview's analytics expose
the needed event types. Video encoding/decoding for analysis has a CPU cost,
especially with a 4K HEVC source. No GPU/TPU is required or promised in this release.
The recording and source-registry loops are independent of model inference.

A test of the built amd64 image, limited to one CPU/512 MB, detected objects in a
fixture image at about 172 MB peak RSS for that model process. This does not
include the whole HA system/recorder or measure an actual HP t530 camera workload.

### HEVC live and microphone

The native core relays the original H264 or H265 recording profile without
opening another camera RTSP session or transcoding the video. WebCodecs support
for the **exact HEVC profile** is checked on the laptop/phone/tablet that displays
the stream. If unsupported, Edge shows a codec-specific message and stops retrying.
A camera H264 profile remains the broad-compatibility option. There is no automatic
4K H265-to-H264 software transcoder on the t530. Original H265 recordings continue
to work independently of native browser HEVC support; NVR playback can use its
existing browser-compatibility setting.

Listening supports G711 and supported AAC/Opus WebCodecs audio. Talking uses a
negotiated RTSP G711 8 kHz mono backchannel on the existing camera session and
requires HTTPS, browser permission and HA control permission. The card disables
the microphone when the camera does not advertise a supported return channel.
Uniview **IPC3618SB-ADF28KMC-I0** is the reported target model; use the Uniview
profile and ONVIF discovery to read its actual model, firmware and RTSP profiles.
Its physical speaker/firmware has not been tested here. Proprietary Uniview talk,
siren/light controls and unsupported return-audio codecs are not implemented.

### Home Assistant entities and automations

Three detection binary sensors are grouped with each camera: motion, person and
vehicle. Types not advertised/reported by the camera and not supplied by enabled
local analysis remain unavailable. The integration polls event metadata every
two seconds without re-polling the camera's video. Stale metadata makes detection
entities unavailable. New rising events also fire `edge_event`; existing history
is not replayed when HA starts. A typical automation trigger is:

```yaml
triggers:
  - trigger: event
    event_type: edge_event
    event_data:
      camera_id: uniview_1  # use your actual Edge camera ID
      kind: person
```

Event payload fields include id, camera_id, kind, source (`camera` or `local`),
timestamp and optional score. For short motion pulses, use `edge_event` rather
than relying only on the latest polled binary-sensor state. Metadata transport
uses a bounded recent-event window; the full retained history is in
`/homeassistant/edge/events.sqlite` (max 20,000 events, pruned with retention).
`detection.json` is the credential-free state mirror consumed by local HA.

Read-only endpoints are `GET /api/detection` and `GET /api/events?camera=ID&limit=200`.
These add no public media ports and do not alter HA's read/control split for talk.

## Sidebar

The app enables Home Assistant Ingress and adds an Edge Infinity item to the sidebar for administrators. The sidebar panel can edit Hikvision camera settings, refresh RTSP status, and open MediaMTX/Janus live preview paths.

## Logs

Open the app page in Home Assistant and use the Logs tab. It shows the panel debug tail, last save diagnostics, active config summaries, and ffmpeg recording log tails. Edge Core should also write all logs to stdout/stderr so Supervisor can capture them.

## Watchdog

The watchdog checks:

```text
http://[HOST]:[PORT:8088]/health
```

If the app stops answering, Home Assistant Supervisor can mark it unhealthy.

## Public API Port

The `8088/tcp` port is disabled by default. Ingress is preferred.

Enable a public/local host port in the app network settings only if another service needs direct API access.

## Storage

Configuration:

```text
/homeassistant/edge
```

In Home Assistant File Editor this same folder appears as:

```text
/config/edge
```

The add-on keeps `/config` only as an internal/fallback mount for older installs.

Database:

```text
/homeassistant/edge/edge.db
```

Recordings:

```text
/media/edge-of-infinity/recordings
```

Recordings are excluded from normal app backup by default.

## Camera Configuration

The preferred configuration path is now the Edge of Infinity sidebar panel. Configure each Hikvision camera there:

- enabled,
- name,
- host,
- username,
- password,
- optional RTSP main/sub URLs,
- optional ONVIF/ISAPI URLs,
- recording and low-latency flags,
- `snapshot_stream` as `sub` or `main`.

When `host`, `username`, and `password` are set but RTSP fields are empty, Edge builds the standard Hikvision paths automatically:

```text
/Streaming/Channels/101
/Streaming/Channels/102
```

Starting with version `0.10.10`, the sidebar panel and add-on startup use one authoritative camera state file:

```text
/homeassistant/edge/edge.json
```

This is visible in File Editor as:

```text
/config/edge/edge.json
```

The app also writes a template:

```text
/homeassistant/edge/edge.example.json
```

`edge.example.json` may be refreshed by the app. Your real camera settings belong in the sidebar panel, which writes `/homeassistant/edge/edge.json`, visible in File Editor as `/config/edge/edge.json`.

`/homeassistant/edge/panel-config.json` is now only a diagnostics mirror and a one-time legacy migration fallback. When `edge.json` exists and contains cameras, add-on startup always uses `edge.json` before generating MediaMTX and Janus runtime config.

The initial file contains two Hikvision camera slots:

```text
hikvision_1
hikvision_2
```

Edit the IP address, username, password, RTSP URLs, ONVIF URL, and ISAPI base URL for your real cameras. After editing, set:

```json
"enabled": true
```

Restart the app to refresh the current panel. MediaMTX and Janus use the same effective camera config generated from these fields.

## RTSP Probe Shell

Starting with version `0.3.0`, the app shell probes enabled cameras with `ffprobe`.

For each camera:

```json
"enabled": true
```

The panel checks `rtsp_main` once when the add-on starts and shows:

- online/offline/disabled status,
- codec,
- resolution,
- FPS value reported by the stream.

Starting with version `0.4.0`, the shell also captures one JPEG snapshot for each online camera. Choose the snapshot source per camera:

```json
"snapshot_stream": "sub"
```

Use `sub` for a lighter panel or `main` for a full-quality snapshot. `rtsp_main` remains the quality probe.

This is not live video yet. It is the first real camera connectivity test before WebRTC live is added. Restart the add-on when you want to rerun the temporary probe.

## Live Preview Core

Starting with version `0.8.0`, the panel uses MediaMTX and Janus as the live core. Camera RTSP streams are rebroadcast inside the add-on, and the browser opens the MediaMTX WebRTC page directly:

```text
http://<home-assistant-host>:8889/<camera_id>_<main-or-sub>
```

The add-on keeps MediaMTX RTSP internal by default so it does not conflict with other Home Assistant add-ons. The internal RTSP port defaults to `8556`. External browser playback uses WebRTC/WHEP on port `8889`, LL-HLS on `8888`, UDP/TCP ICE on `8189`, and Janus HTTP/WebSocket on `8192`/`8193`.

Starting with version `0.8.4`, MediaMTX also receives explicit WebRTC public hosts through:

```text
mediamtx_webrtc_public_hosts: homeassistant.local,192.168.33.17
```

These hosts are advertised as ICE candidates so the browser can connect to the add-on instead of waiting until the WebRTC session times out.

Starting with version `0.8.5`, MediaMTX no longer advertises Docker bridge interface IPs as WebRTC ICE candidates. It uses the configured public hosts and exposes the ICE port over UDP and TCP so browsers on the LAN are not offered unreachable `172.30.x.x` candidates first.

Starting with version `0.8.9`, enabled cameras with `low_latency` keep MediaMTX paths warm when a stream is used by a tile, live view, or recording. This removes most cold-start delay where a phone opens the panel and MediaMTX would otherwise only then start pulling RTSP from the camera. WebRTC gather and handshake timeouts are also extended for LTE/5G clients.

Starting with version `0.10.0`, Edge Settings can save mobile WebRTC runtime values into `/homeassistant/edge/edge.json`:

```text
mobile_webrtc_public_hosts
mobile_webrtc_stun_url
mobile_webrtc_turn_url
mobile_webrtc_turn_username
mobile_webrtc_turn_password
mobile_webrtc_ice_transport
prebuffer_enabled
always_on_enabled
always_on_stream_scope
prebuffer_local_ms
prebuffer_remote_ms
```

On restart, the add-on generates MediaMTX and Janus configs from those values. The fastest LTE path is still direct ICE through a reachable public host/DDNS and open WebRTC/ICE ports. TURN is only the fallback when CGNAT or a carrier firewall prevents direct ICE. A VPS can be used as that public host, TURN server, or future relay bridge when the home network cannot expose ports.

Starting with version `0.10.21`, `always_on_enabled` keeps all enabled low-latency camera paths connected in MediaMTX after add-on boot, even when no phone or browser is watching. This trades extra steady CPU/network use for much faster first-frame loading when the panel is opened again. NVR recording still follows each camera's `record` and `record_stream` settings.

Starting with version `0.10.22`, always-on warming defaults to `always_on_stream_scope: tile` so Edge keeps lightweight camera tiles ready without forcing every 4K `main` stream to stay active. Use `all` only when the host has enough CPU/network headroom and you explicitly want every profile warm.

Starting with version `0.10.23`, mobile NVR playback opens saved MP4 segment files directly from the server instead of the ffmpeg-generated continuous pipe. This is more compatible with the Home Assistant mobile WebView because the `/recordings/...` route supports byte ranges and lets the phone start playback immediately from an already written file. Desktop keeps the continuous stream mode.

Starting with version `0.10.24`, the server builds a playback cache from closed NVR MP4 segments under `/homeassistant/edge/recording-cache/<camera>/timeline.mp4`. Mobile playback prefers this cached range-enabled MP4 timeline, so seeking forward/back uses one already prepared server file instead of reloading a new 8-10 second segment on every jump. Recording still writes short segments for crash-safe storage; the cache is the fast playback layer.

Starting with version `0.10.25`, desktop and mobile NVR playback both prefer the same server-side cache when it is ready. The default cache window is 1000 recording segments and can be changed in Edge Settings with `Playback cache segments`. If an old one-segment cache is found while many newer recordings exist, the panel hides it and lets the background builder create a fresh longer timeline instead of limiting rewind to a few seconds. Cache builds also remux audio to AAC with timestamp resampling while keeping the camera video stream-copy path.

Starting with version `0.10.26`, NVR playback is grouped by calendar day. For every camera, the panel builds a day-specific cache under `/homeassistant/edge/recording-cache/<camera>/<YYYY-MM-DD>/timeline.mp4`, so a day is treated as `00:00 -> 23:59:59` instead of a rolling last-N-segments window. The NVR page shows a horizontal day strip with a thumbnail and date; moving right selects the older day, moving left selects the newer day. `Playback cache segments` now defaults to 10000 so a full 10-second-segment day fits into one timeline. Recording thumbnails are loaded directly from `/recording-thumbs/...` and thumbnail failures are logged in the panel diagnostics.

Starting with version `0.10.27`, the NVR segment list is rendered as a sampled filmstrip across the selected day instead of a long first-N list. This keeps morning, midday, and evening visible while limiting thumbnail work in the browser. Cached thumbnails are accepted only when they are fresh JPEG files; corrupt cache files are regenerated. Thumbnail generation is limited to one FFmpeg worker at a time and detailed diagnostics are written to `/homeassistant/edge/recording-thumbnail.log`, which is also available in the panel Logs tab.

Starting with version `0.10.28`, the daily playback cache is lower CPU. Edge builds only one daily cache at a time, runs that FFmpeg process at low OS priority on Linux, and rebuilds an already playable but stale day cache in batches instead of after every new recording segment. The cache remux now uses stream copy because Edge's own NVR segments are already browser-oriented MP4 files. MediaMTX remains the live/rebroadcast core, but its built-in recorder is disabled by default so the panel NVR recorder is the single recording source. Unchanged MediaMTX and Janus runtime config files are no longer rewritten, which avoids unnecessary MediaMTX reloads and live/NVR interruptions. MP4 recording and cache routes use range-friendly immutable cache headers.

Starting with version `0.10.29`, the NVR day timeline is a wall-clock day instead of a compressed sum of clips. The selected day exposes `00:00 -> 24:00` while each recording file carries both `timeline_offset` for the real time of day and `playback_offset` for the cached MP4 position. The Logs tab also starts with colored diagnostics: green entries mean OK, yellow means a condition may affect playback or CPU, and red means a real error or conflict was found in runtime checks, UI events, or log tails.

Starting with version `0.10.30`, the NVR player is protected during manual seeking and playback control interaction. Timer status refreshes can update recording metadata in memory, but they do not rebuild the `<video>` element while the user is scrubbing, paused, or watching a loaded recording. The status timer also runs every 15 seconds and timer debug logs are throttled, reducing background UI work and disk writes. The Logs tab now colors every raw block as OK, WARNING, or ERROR and adds runtime parameters such as process status, thread/file descriptor counts, cache backlog, recorder process details, cache limits, and engine ports.

Starting with version `0.10.31`, NVR playback stays on recorded files. The panel no longer falls back to the continuous live-style `/recordings-stream/...` player when a daily cache is missing; it uses saved MP4 segments until the full-day cache is ready. If a daily cache is stale and the user seeks into the newest part of today, Edge plays the saved segment for that tail while the background cache catches up. Cache build timeout is now scaled by file count and duration, which prevents large camera days from failing simply because a multi-thousand-segment timeline needs longer than a small day.

Starting with version `0.10.32`, the NVR fallback is a full recorded-day stream again, not a single short segment. `/recordings-stream/...` is explicitly treated as recorded media assembled from saved MP4 clips, and the panel logs both the wall-clock day offset and the compressed playback offset so seek bugs are easier to diagnose. Recording thumbnails also use source-file cache-busting and a low-impact background warmup, so existing and future camera days can recover missing preview images without forcing a heavy thumbnail burst.

Starting with version `0.10.34`, active-day cache files are used only when they match the current set of recording segments. If today's `timeline.mp4` is stale, Edge hides it and uses the recorded-day stream assembled from verified MP4 segments instead. Recording segments must also contain a playable MP4 header before they appear in the NVR list or thumbnail worker, which avoids `moov atom not found` thumbnails and bad mobile playback.

Starting with version `0.10.35`, MP4 segment readiness accepts metadata at either the start or end of the file. Some valid FFmpeg segment files keep `moov` near the tail, so Edge no longer hides a whole day of recordings just because the metadata is not in the first read window.

Starting with version `0.10.36`, the NVR list no longer depends on MP4 header probing. Segment playback visibility is based on file age and size. This prevents a thumbnail/metadata edge case from making the whole NVR page look empty or OFF.

Starting with version `0.10.37`, the NVR timeline infers segment duration from the next recording timestamp. This keeps short 4-second chunks playable as one continuous day stream and allows thumbnails to be generated directly from any ready recording segment.

Starting with version `0.10.2`, `mobile_webrtc_ice_transport` can be `auto`, `udp`, or `tcp`. Use `auto` first because it exposes both UDP and TCP ICE on port `8189`. Use `udp` when everything is local and you want the simplest low-latency path. Use `tcp` when LTE, hotel Wi-Fi, or a friend's Wi-Fi blocks UDP, but remember that the public MediaMTX WebRTC address on `8889` and ICE port `8189` must still be reachable from that network. If the home network is behind CGNAT or cannot forward those ports, use a VPS or TURN relay instead of a LAN-only `192.168.x.x` address.

The `WebRTC public URL` field must point to the public MediaMTX WHEP/WebRTC endpoint, for example:

```text
https://edge.example.com
http://PUBLIC-IP:8889
```

Do not use the internal Docker `172.30.x.x` address or a LAN-only `192.168.x.x` address for LTE/mobile playback.

Starting with version `0.10.4`, the panel refuses to embed WebRTC when the browser context proves the URL cannot work: missing public WebRTC URL on a remote host, LAN-only public URL, missing `http://` or `https://`, or an HTTPS Home Assistant page trying to embed an HTTP MediaMTX URL. The tile shows the reason and the Logs page records `ui_live_plan`, `ui_live_blocked`, `ui_live_frame_rendered`, `ui_live_frame_load`, and `ui_live_frame_timeout` events with browser host/protocol, MediaMTX URL, ICE transport, and viewport. If RTSP recording continues while WebRTC fails, the problem is reachability/ICE rather than camera Digest authentication.

Starting with version `0.10.5`, Edge Settings has a `Remote access` mode: `local_only`, `direct_public`, `vps_relay`, or `turn_relay`. Nabu Casa is treated as `local_only / panel only`: it can open the Home Assistant UI, but it is not a MediaMTX WebRTC relay and must not be used as `WebRTC public URL`. For LTE without forwarded ports, choose `vps_relay` or `turn_relay` and set `WebRTC public URL` to that relay endpoint.

Starting with version `0.10.6`, manual Hikvision channel fields are authoritative. `camera_number` still generates defaults such as `101/102` or `201/202`, but it no longer rejects a manually entered channel pair.

For LL-HLS mobile tests, `mediamtx_hls_always_remux` can be enabled in the add-on options. Keep it off unless you are testing HLS startup time, because always-remux keeps HLS work active even without a viewer.

Starting with version `0.10.10`, `/homeassistant/edge/edge.json` is the single source of truth after it exists. Add-on options are copied only to `/tmp/edge-runtime/edge.options.json` for diagnostics and no longer overwrite panel changes on restart. Successful panel saves clear legacy `panel-camera-overrides.json` and `stream-overrides.json` files so stale values cannot force stream roles or connection fields back to previous settings.

Starting with version `0.10.2`, Hikvision setup can use `camera_number` as the simple source of truth for stream URLs. Camera number `1` maps to `101/102`, camera number `2` maps to `201/202`, and so on. This lets the panel rebuild RTSP URLs from host, username, password, and camera number without manually editing every RTSP path. The saved `access_protocol` and `rtsp_transport` fields are also preserved so the panel and generated runtime config stay aligned.

## HEVC / H265

MediaMTX can proxy and record H265/HEVC through RTSP, LL-HLS, SRT, and fMP4 recording when the camera sends it. Browser WebRTC is different: the phone/browser WebRTC stack must also support HEVC. Use H265 for recording and compatible LL-HLS/SRT clients, but keep H264 available for universal WebRTC playback unless your target phone confirms H265 WebRTC support.

Starting with version `0.9.0`, the NVR panel has a browser playback policy. In `auto_h264` mode, H264 camera streams are recorded with video stream-copy, while HEVC/H265 streams are transcoded to H264/AAC MP4 segments so the Home Assistant panel and mobile browsers can play them. Use `copy` only when you want the original codec and already know your player supports it.

Starting with version `0.8.3`, the old shell placeholder page and MJPEG status generator were removed from the runner. The Edge panel is the controller, while MediaMTX + Janus is the live path.

Camera cards show a small rounded status badge:

```text
online
offline
lost connection
```

The `lost connection` state appears when a camera was previously online and the next RTSP probe fails.

## Hikvision Autoconfig

Starting with version `0.10.0`, Autoconfig also returns camera recommendations inspired by Scrypted's camera preparation rules: H264 for universal browser/WebRTC live, main stream for recording, warmed substream for mobile start, keyframe interval close to `FPS * 4`, and moderate LTE substream bitrate. HEVC/H265 is still useful for recording or compatible LL-HLS/SRT clients, but browser WebRTC may need H264 or transcoding.

Starting with version `0.4.7`, Camera Settings includes an `Autoconfig` action per camera. It reads Hikvision ISAPI sections through Digest authentication:

```text
/ISAPI/System/deviceInfo
/ISAPI/Streaming/channels/101
/ISAPI/Streaming/channels/102
/ISAPI/System/time
/ISAPI/System/Video/inputs/channels
/ISAPI/System/Network/interfaces
/ISAPI/Image/channels/1
```

The panel exposes safe editors for the main and sub stream. Saving writes back only supported stream fields through:

```text
PUT /ISAPI/Streaming/channels/101
PUT /ISAPI/Streaming/channels/102
```

This is the first camera-control layer. Wider image, motion, OSD, and event settings can be added after the stream editor is stable.

Starting with version `0.4.9`, Autoconfig uses curl `--anyauth` and reports the exact result for each ISAPI endpoint. If every ISAPI read fails, check the camera web panel:

```text
Network -> Advanced Settings -> Integration Protocol
```

Enable ISAPI/Hikvision-CGI support if the camera firmware exposes that option, then confirm the HTTP port and camera user permissions.

Starting with version `0.4.10`, Autoconfig also merges the clicked camera slot with the saved `/homeassistant/edge/edge.json` camera entry. This prevents `isapi_base_url_missing` when the panel request does not include the host or ISAPI URL.

## Responsive Panel

Starting with version `0.4.11`, the left navigation includes a hamburger toggle. On desktop it collapses the sidebar to icon-only mode. On phone and tablet layouts it hides or reveals the navigation menu above the panel content.

## NVR Recording

Starting with version `0.4.12`, the NVR page can start and stop FFmpeg segment recording per camera. Segments are written under:

```text
/media/edge-of-infinity/recordings/<camera_id>
```

This first recorder copies the RTSP stream without transcoding and creates 60-second MP4 segments. Playback timeline controls will attach to these segments in the next NVR steps.

Starting with version `0.4.13`, the NVR page lists recent MP4 segments per camera and can play them directly in the panel. The segment endpoint supports browser range requests so video playback and seeking can work without exposing the raw media folder.

Starting with version `0.4.14`, the newest segment is selected automatically. Rewind moves to an older segment, Forward returns to a newer segment, and the active item is highlighted in the recording list.

Starting with version `0.4.15`, the Edge Settings page can edit server metadata, storage paths, retention days, and live preview settings directly from the panel. Settings are saved to `/homeassistant/edge/edge.json` with the same backup protection as camera changes.

Starting with version `0.4.16`, active live preview prefers the continuous MJPEG endpoint. This avoids repeatedly rebuilding the camera grid for every JPEG frame. The older JPEG frame mode remains available from Edge Settings as a fallback.

Starting with version `0.4.17`, Camera Settings can add and remove camera slots directly from the panel. Each camera can choose a vendor: Hikvision, Dahua, ONVIF, or generic RTSP. If a settings save request does not include camera data, the backend preserves the existing camera configuration instead of rejecting the save or overwriting cameras.

Starting with version `0.4.18`, Camera Settings includes a Build RTSP action. Hikvision uses `/Streaming/Channels/101` and `/Streaming/Channels/102`. Dahua uses `/cam/realmonitor?channel=1&subtype=0` and `/cam/realmonitor?channel=1&subtype=1`. ONVIF and generic RTSP cameras keep manual RTSP fields for now.

Starting with version `0.4.19`, active live preview uses Edge's own MJPEG multipart response. JPEG remains for snapshots, not active live. If MJPEG fails, FFmpeg errors are written under `/homeassistant/edge/live-*.log`.

Starting with version `0.4.20`, each camera has a separate Live stream selector. Snapshot stream controls still image snapshots, while Live stream controls the MJPEG endpoint. Live defaults to `sub` to avoid accidentally decoding a HEVC/H.265 main stream.

Starting with version `0.4.21`, each MJPEG live attempt writes the selected camera, stream name, and redacted RTSP URL to `/homeassistant/edge/live-*.log`. This helps verify whether Edge is using `sub`/`102` or accidentally receiving a HEVC stream.

Starting with version `0.4.22`, Hikvision camera settings include a Sub channel selector for `102` and `202`. Saving camera settings rewrites the Hikvision `RTSP sub` URL to the selected channel, and the Home camera cards show a separate Live probe so the active live stream can be compared against the main-stream status.

Starting with version `0.4.23`, the backend also treats `rtsp_sub_channel` as authoritative when normalizing camera config, stops orphaned recording processes after camera removal, and stores the validated URL when the Home Assistant config flow updates an existing entry.

Starting with version `0.4.24`, NVR recording has its own `record_stream` setting. For Hikvision, `main` always uses channel `101` and `sub` always uses channel `102`. The NVR page shows the exact redacted Record RTSP separately from the Live RTSP so live preview and recording cannot be confused.

## Custom Component Auto-Install

Starting with version `0.3.2`, the add-on can install or update the Home Assistant custom component automatically.

The add-on maps the Home Assistant config folder and writes:

```text
/homeassistant/custom_components/edge
```

The option is enabled by default:

```yaml
install_custom_component: true
```

After the add-on updates this folder, restart Home Assistant Core so the integration reloads.


### Automatic recording retention (0.10.38)

Recordings are checked at panel startup and every hour, without opening the UI or restarting recording. The default recordings directory is `/media/edge-of-infinity/recordings`. MP4 segments older than 14 days (by last modification time) are removed, including subdirectories of disabled or removed cameras. Files exactly 14 days old remain until a later check.

`nvr.retention_days` takes precedence over `storage.retention_days`; the default is 14 days. Changes are read at the next hourly check. Only FFmpeg/MediaMTX recording filenames are eligible; unrelated media and symlinks are skipped. Retention results and errors appear as `recording_retention_done` / `recording_retention_error` in the debug log.

### Settings persistence and full-day playback (0.10.39)

Both **Save cameras** and **Save Edge settings** save the current values from both forms. The buttons are disabled while saving. The panel confirms success after checking the complete saved configuration. Failed requests keep the form values.

`/homeassistant/edge/edge.json` is authoritative. `panel-config.json` and `/config/edge.json` are mirrors; backup and legacy override files cannot replace an existing authoritative file on load or restart. If edge.json contains invalid JSON, the add-on reports the error instead of silently restoring old settings. Repair that JSON file (or deliberately restore a chosen backup) before restarting. Existing backup files remain available for manual recovery. Missing authoritative files can still be migrated from valid legacy files.

In **NVR**, select a calendar day, then use the **Day timeline** slider or a sampled preview to start at a specific time. Previous/next-day buttons navigate dates. The main timeline spans midnight to midnight in the add-on's local timezone (normally 24 hours; 23 or 25 hours on daylight-saving transitions). Missing periods jump to an available recording; they are not fabricated video. A day in progress contains only footage recorded so far.

Recordings remain small MP4 segments on disk to limit data loss if power fails. The player joins them into one continuous day stream, so there is no need to open individual seven-minute files. Existing older clips are included; irregular clip durations are measured using FFprobe. The playback-cache segment setting limits optional cache generation, not how much of the selected day can be watched. Old or partial caches are bypassed until they match the selected day's source files. Retention still defaults to 14 days.

### NVR day player (0.10.40)

The video controls show **elapsed recorded video / total recorded video in the selected day**. For example, three seven-second clips produce a 21-second player, rather than restarting a seven-second counter after every clip. The player loads the necessary MP4 segment and advances automatically. Play/pause, ±10 seconds, mute, playback speed and fullscreen are available on desktop and touch layouts. The separate midnight-to-midnight timeline still selects a wall-clock time; missing footage is skipped, not counted as recorded video.

Status polling updates counters without replacing the video or resetting pause, speed or position. The available duration grows when new recordings arrive. Switching dates replaces only that camera's player. Historical days stop at the end of their available recordings; the latest day can wait for new footage.

Preview tiles keep a 16:9 shape and load in a small client-side queue. Desktop, portrait/landscape tablet, phone and 320px layouts are covered by the browser regression suite. This is responsive browser support; codec playback still depends on the device. Keep **Browser recording = Auto H264 for HEVC** for broadly compatible new recordings.

### Camera profiles and immediate configuration sync (0.10.41)

Choose a vendor in Camera Settings, enter the host/IP, local camera username and password, and use **Build RTSP** or **Read camera / ONVIF**. Verify the main/sub URLs in the form and save. Existing camera IDs stay unchanged, so changing a vendor does not detach recorded history.

| Vendor/profile | Connection support |
| --- | --- |
| Hikvision | Hikvision RTSP paths and existing ISAPI configuration |
| Dahua / Amcrest | cam/realmonitor RTSP profiles |
| Uniview | media/video1 and media/video2 profiles; ONVIF discovery |
| Reolink | h264Preview channel main/sub profiles; ONVIF discovery (default device-service port 8000) |
| Axis | axis-media RTSP profiles; ONVIF discovery |
| Hanwha / Samsung | profile1/profile2 RTSP profiles; ONVIF discovery |
| Foscam | videoMain/videoSub profiles; ONVIF discovery |
| TP-Link Tapo / VIGI | stream1/stream2 profiles; ONVIF discovery |
| Bosch / Vivotek / Sony / Other ONVIF | Discover the camera's own ONVIF Media profiles or enter explicit RTSP URLs |
| Custom RTSP | Use explicit main/sub stream URLs |

These profiles support local live video, snapshots from RTSP, recording and playback through the existing engine. Paths and available services vary by model and firmware. Cameras that only expose a proprietary/cloud stream are not made RTSP-compatible by selecting a brand. Enable RTSP/ONVIF on the device, create a local camera/ONVIF account where required (especially Tapo/Reolink), and synchronize its clock. The ONVIF URL is editable; explicit RTSP URLs preserve custom paths and ports. Advanced encoder writes remain Hikvision ISAPI functionality; ONVIF discovery reads profiles and stream addresses, not all vendor administration settings.

When camera IP/password/stream settings change, the panel writes edge.json and its mirrors, updates MediaMTX runtime configuration and restarts only changed recorders. When Janus mount configuration changes, the managed Janus process is reloaded. Camera status initially shows the new details as checking while background probes finish. Old probe results cannot overwrite newer settings.

The panel also synchronizes the add-on's own Home Assistant options through Supervisor. The new **cameras** list contains all configured cameras; the first two legacy hikvision_1/hikvision_2 fields are mirrored for compatibility. Other Supervisor options are preserved. The add-on uses hassio_api with the default role, not manager/admin access. The save message confirms verified synchronization or clearly states that Supervisor options could not be synchronized. Standalone/local runs without Supervisor still save edge.json normally. Reload an already-open Home Assistant configuration tab to see its latest option values.

The runner imports the generic cameras list on first initialization. Once edge.json exists, it remains authoritative. In NVR, segment transitions retain the actual previous video frame until the new frame is presented; they do not revert to a live-camera snapshot or the initial recording poster.

### Organized UI and native Home Assistant dashboard cards (0.10.42)

Navigation separates daily viewing (**Live view**, **Recordings**) from camera setup, app settings, Home Assistant cards and diagnostics. Each camera has an expandable card with **Connection**, **Discover camera streams**, **Live & recording** and **Advanced connection** sections. The current camera stays open during edits; adding a camera opens its form. The save bar marks unsaved changes and saves both camera/app forms together.

Settings show recording retention and browser compatibility first. Storage paths, remote access/ICE, performance and server settings are expandable. There is one visible retention setting; saving it synchronizes storage and NVR retention values.

To put live video on the main Home Assistant dashboard:

1. Update/start the add-on, leaving `install_custom_component: true` (the default).
2. **Restart Home Assistant Core once** after this update so the changed Edge integration and its stream/FFmpeg dependencies load. If the integration is not added yet, go to **Settings → Devices & services → Add integration → Edge of Infinity** and use `local` as the connection.
3. In the Edge panel, open **Home Assistant**. Select the camera. The integration publishes the actual registered entity ID, including IDs renamed by the user, through `/config/edge/ha-entities.json` (seen by the add-on under `/homeassistant/edge`). If detection is unavailable, enter the exact `camera.…` entity ID from Home Assistant; no ID is guessed.
4. Click **Copy card YAML**. In Home Assistant, open your dashboard → **Edit dashboard → Add card → Manual**, paste the code and save.

Example only — use the generator for your actual entity ID:

```yaml
type: picture-entity
entity: camera.front_door
camera_view: live
name: Front door
show_name: true
show_state: false
aspect_ratio: "16:9"
```

This is a built-in card; no HACS/custom card, browser RTSP URL, camera password or public Edge port is needed in the YAML. Home Assistant's authenticated camera/stream pipeline handles the video, including access through the normal Home Assistant URL. Home Assistant Core must be able to reach the camera's RTSP address. Use an H264 live stream for broad browser compatibility; latency and codec playback depend on Home Assistant and the camera.

The native integration reads the configured live stream, updates an existing stream worker after its source changes, and adds entities for newly configured cameras on coordinator refresh. Snapshot requests use the existing cached image when available and otherwise request a frame through Home Assistant's FFmpeg integration. Entity IDs are exported atomically, without stream credentials. If multiple non-local installations use the same camera ID, choose the correct entity manually. An entity-mapping export failure does not prevent camera setup.

The UI and stream-source behavior are covered by browser/unit tests with a disposable local fixture and Home Assistant API stubs. Actual playback on a running Home Assistant Core still depends on the installation's network and camera codecs.

### Edge Live WebRTC card and updated integration (0.10.43)

Update/start the add-on with `install_custom_component: true`, then **restart Home Assistant Core**. This installs the updated `edge` integration and its bundled card; reloading an already loaded Python integration is not a substitute for this restart. Existing camera entity IDs remain unchanged. Refresh the browser/app frontend after restarting Core.

The integration automatically serves and registers `/edge/edge-live-card.js?v=0.10.43`. No HACS download is needed. In the dashboard card picker select **Edge of Infinity Live**, or open **Home Assistant** in the Edge panel, select **Edge Live**, and copy the generated YAML using your actual camera entity:

```yaml
type: custom:edge-live-card
entity: camera.front_door
name: Front door
autoplay: true
muted: true
volume: 0.6
microphone: true
reconnect: true
fit: contain
show_header: true
```

The visual card editor exposes these options. `microphone: true` shows a control; it **does not start microphone capture**. The card has start/stop, mute, volume, microphone toggle, fullscreen and settings for image fit (`contain` or `cover`), echo cancellation, automatic reconnect, reconnect now, snapshot download and picture-in-picture where the browser supports it. Playback volume/fullscreen/PiP support can differ on iOS and in the Home Assistant companion webview.

Live signaling uses the existing authenticated Home Assistant camera WebSocket API. Camera credentials and RTSP addresses stay in the integration, not in the card YAML. Home Assistant must have a WebRTC provider compatible with the camera, such as its **go2rtc** integration. Edge orders setup after go2rtc if it is configured; it does not install another go2rtc server. If HA reports WebRTC unavailable, check its go2rtc setup and camera stream capabilities. The generator still offers the **Standard Home Assistant camera** card for HA's normal stream playback. Use an H264 live feed for broad browser support. Remote WebRTC media still requires working ICE connectivity; opening the HA page remotely alone does not guarantee a media route.

**Talking through a camera** requires HTTPS (or localhost), browser microphone permission, and a camera/RTSP source plus WebRTC provider that support return audio/backchannel. This depends on the model and configured source, not only the camera brand. Click the microphone to request capture; click again to stop and release it. The microphone indicator turns on only after the WebRTC answer negotiates sending audio. This confirms browser/provider negotiation, not that a physical camera speaker played the sound. The card reports a rejected audio direction rather than claiming two-way audio works. There is no automatic talk mode. Capture ends on card removal, page hiding, stop, connection failure, or a cancelled permission request. Local playback is temporarily muted while talking and its prior mute state is restored afterwards.

The default browser test uses real RTCPeerConnections and generated video/audio with a simulated HA signaling endpoint. It verifies received video, audio packets in the return direction, controls, state refresh stability, denied/unsupported microphone behavior and cleanup. Registration/entity behavior is checked with HA API stubs. In managed browsers that block WebRTC transport, `EDGE_WEBRTC_SIGNALING_ONLY=1` instead checks real SDP negotiation and injects local synthetic tracks for rendering; it does not verify media transport or returned RTP packets. This release was checked in that restricted mode. A running Home Assistant Core, physical camera speakers and outside-LAN ICE paths are not tested by this fixture.

### Shared NVR live streams and 1–4 cameras (0.10.44)

Update/start Edge and **restart Home Assistant Core**, then refresh the HA frontend. The existing camera entity IDs remain; their default source now uses the same **Edge NVR recording profile**. The integration additionally registers Main, Sub and Talk camera entities, and publishes their actual entity IDs (including user renames) to the card. These extra entities do not open camera connections merely by being registered. The custom card editor and Edge's **Home Assistant** page can select 1, 2, 3 or 4 different cameras.

The default route is `camera → Edge MediaMTX/NVR → Home Assistant WebRTC provider → dashboard`. Native stream workers, still-frame fallback and periodic RTSP status probes also read from MediaMTX. The NVR relay forwards the existing encoded video; it does not downscale it or generate higher resolution than the camera provides. The player shows the actual decoded dimensions. WebRTC still needs a browser-compatible codec; configure an H264 profile if the camera's H265 feed cannot play on your browser.

Each tile offers **NVR** (same profile as recording), **Main** and **Sub** (usually less bandwidth). Only registered profiles are selectable. Multiple viewers of the **same profile** share one upstream MediaMTX camera session. Main and sub are separate camera streams, so using both can require two upstream sessions. Existing Edge preview/prebuffer settings can also keep another profile open. For a single active camera profile overall, use that same profile for recording, Live, Tile and snapshot settings, and avoid the prebuffer `all` scope. This update does not silently change your camera's codec, bitrate or existing per-camera preview preferences.

Example — replace the entity IDs with actual IDs from your installation, or use the generator:

```yaml
type: custom:edge-live-card
columns: auto
cameras:
  - entity: camera.front_door
    quality: nvr
  - entity: camera.garden
    quality: nvr
  - entity: camera.garage
    quality: nvr
  - entity: camera.driveway
    quality: nvr
muted: true
microphone: true
```

The previous single-camera `entity: camera.front_door` configuration remains valid; add `quality: nvr`, `main` or `sub` as desired. The visual editor supports 1–4 camera slots with independent quality selection, and `columns: auto`, `1` or `2`. Auto uses two columns on wide cards and one on narrow cards. The **View** selector focuses one selected camera or returns to all. Invisible tiles close their media sessions; visible tiles keep their video elements during ordinary HA state updates. Each tile retains its own sound, volume and fullscreen controls. Only one microphone is captured at a time across the grid.

**Talk audio is an explicit exception to shared NVR viewing.** MediaMTX's RTSP pull path skips camera backchannels. Pressing the microphone therefore temporarily uses the dedicated Talk entity and a direct camera/provider session. The card returns to NVR when talking stops, return audio is rejected, or the connection drops. HTTPS, browser permission and compatible camera/provider two-way audio support remain required. Merely opening the card never requests the microphone.

The add-on discovers its internal hostname through Supervisor's own `/addons/self/info` endpoint and writes credential-free `restream.json` beside `edge.json`. HA reads this file locally, or fetches `/api/home-assistant/streams` for an API-based integration. RTSP port 8556 stays unpublished on the host by default; HA Core uses the add-on's internal network. Standalone/remote deployments must provide a hostname reachable by that HA instance using `EDGE_HA_RTSP_HOST` and the configured RTSP port. Missing restream metadata reports an unavailable NVR entity, rather than silently reconnecting directly to the camera. The card YAML contains entity IDs and display options, never camera passwords or RTSP URLs.

Validation includes 107 unit tests and browser tests for 1–4 camera layouts, focus/suspension, NVR/Main/Sub entity routing, temporary Talk sessions, editor and YAML builder on four screen sizes. The browser uses real SDP negotiation with synthetic local media because managed browser policy blocks WebRTC transport. A separate integration test runs **MediaMTX 1.19.2 twice**, with generated H264 video as an RTSP camera: three concurrent readers plus a parameter probe share one upstream camera reader and preserve 640×360. No physical camera, production HA installation or physical speaker was tested.

### Native EdgeRTC beta (0.10.45)

`edge_rtc` is an **opt-in experimental core**, shipped beside the existing engine.
From version 0.10.46, open **Edge panel → Settings → Silnik obrazu / Streaming engine**,
select **EdgeRTC (beta)** and press **Zapisz silnik / Save engine**. The panel verifies
the saved value and shows the engine currently running. Restart the Edge app,
then **restart Home Assistant Core** and refresh its frontend. Alternatively, set
`core_engine: edge_rtc` in the app’s Home Assistant **Configuration**. The Save
button there activates when the value differs from the existing configuration;
YAML mode also requires valid YAML. Engine selection does not restart the app
automatically. Existing Edge Live
cards automatically use native EdgeRTC when the integration publishes this mode.
Select the **NVR** quality option. Switch `core_engine` back to `mediamtx_janus` and
restart the add-on/Core to use the existing engine again. Existing camera settings,
recordings and retention paths are retained.

EdgeRTC/1 sends H264 (and H265 from 0.11) access units directly to browser WebCodecs over WS/WSS. This
path instantiates neither RTCPeerConnection nor MediaSource, and does not wait
for MP4 fragments. Browser support is checked at runtime. **HTTPS or localhost
and a WebCodecs-compatible browser are required for native live**, not just for
microphone capture. A local LAN address such as `http://homeassistant.local:8123`
is not a secure browser context. For Home Assistant Cloud, open **Settings →
Home Assistant Cloud → Remote access** and use the `https://…ui.nabu.casa` address
to open Home Assistant and its Edge panel/card. An existing trusted HTTPS
reverse-proxy address also works. The top-level HA page and the embedded player
must both have a secure context. This does not require exposing camera/Edge
media ports. Since 0.10.48, native EdgeRTC uses this same connection plan for
remote-access notices and live previews; the unrelated MediaMTX public URL/ICE/
TURN controls are hidden and their existing values are retained for switching
back to the legacy core. `localhost` refers to the computer running the browser, so it is
not a substitute address for a separate Home Assistant server.

If HTTPS is already in use but the browser has no WebCodecs video decoder, use
an up-to-date Chrome or Edge browser. Companion webviews may differ. Since
0.10.47, Edge displays a specific reason and stops automatic card retries for
these browser requirements. Peer resets while sending HTTP responses are handled
as client disconnects rather than unhandled request tracebacks.

Desktop/tablet/phone layouts were exercised in Chromium;
physical iOS/Android companion webviews and Safari/Firefox still need validation.
Native picture-in-picture is currently hidden.

There is one always-on RTSP session per enabled camera source. Live, the recorder,
probes, snapshots and a supported RTSP G711 8 kHz mono microphone share this
session. Duplicate exact source URLs are merged. The single source is the camera's
recording profile; independent Main/Sub quality choices are unavailable in this
mode. H264 has broad native-live support; from 0.11, H265 works on devices with a compatible HEVC decoder. The engine does not
silently re-encode H265 into H264 or open a second camera stream. Models that need
proprietary talk APIs are not supported by the native microphone yet. A camera
backchannel may remain reserved by Edge while that source is enabled.

The HA integration proxies native binary frames under HA authentication. Reading
an entity is required for viewing; control permission is also required to talk.
The private core token is stored in `/config/edge/edge-rtc-token`, seen by the
add-on in `/homeassistant/edge`, and is never put in the card YAML or browser.
For API-based integrations outside the shared config directory, the configured
API key must be this private core token and the advertised internal API must be
reachable. Host API port 9997 and RTSP 8556 stay unpublished by default. No media
ICE/TURN/public-port settings are needed when using the authenticated HA WSS
bridge. The Edge panel's tunnel permits viewing only.

Diagnostics → Runtime summary / **EdgeRTC source connections** shows active
upstreams, connection attempts, frame traffic, native viewers and microphone
packets. Configuration changes are watched once per second; old sessions close
before replacements start. Network failures use one bounded backoff loop per
source. Slow viewers are disconnected rather than blocking NVR delivery.

Validation: 112 Python tests; Go race tests; amd64/ARM64 builds; actual H264
WebCodecs playback and PCM microphone RTP on desktop, tablet and phone-sized
Chromium viewports; actual HA bridge code with HA API stubs; control-permission
rejection; production FFmpeg stream-copy recording and the Edge panel viewer.
A generated 640×360/10 fps warm source produced first drawn images around
30–300 ms locally. This is not a measured glass-to-glass latency or proof of
being faster than WebRTC on a lossy WAN. QUIC/WebTransport, camera event adapters,
PTZ and local AI detection are subsequent modules, not completed features here.
See [EdgeRTC internals](edge-rtc/README.md).
