# Edge of Infinity

## 0.17.9: Supervisor access and a smaller app configuration

The runner restores its container environment with `with-contenv` before Python
starts. Otherwise s6 can clear the Supervisor socket/token while media still
works, leaving cleanup marked `unavailable`. Diagnostics displays the current
cleanup phase, safe code and HTTP status. A real s6 container test verifies the
persisted options over a Supervisor Unix socket.

Configure cameras, passwords and detection in **Edge → Cameras / Settings**.
The HA app defaults retain logging, listener ports, storage/retention and the
integration installer. Older camera/detection fields remain optional for a safe
one-time import. The empty `cameras` list is a schema compatibility importer,
not a second copy of the camera configuration.

After the authoritative configuration loads successfully, background cleanup
removes duplicate camera/detection overrides. A missing/invalid import preserves
its old options; retries reread current settings under the save lock. Panel saves
never repopulate those duplicates. Original files and camera codec/FPS/audio,
retention, zones and local-detection choices remain unchanged. Warnings already
in Supervisor logs are historical; use the current cleanup result. Update the
app and restart Home Assistant Core for matching integration assets.

## 0.17.8: startup work and small-host update load

HA Live loads its client and checks browser support before any signing request,
then prepares its signed URL and diagnostics module concurrently.
DVR overlaps signing and diagnostics module loading. Cancellation, failed signing
and unavailable diagnostics are tested separately. The browser must still finish
the normal authenticated connection and decode an original keyframe. Startup
logs include preparation/signing durations; Live first-frame time includes that
preparation, rather than moving work outside the measurement window.

Live/DVR share a bounded in-memory cache of up to eight positive video-capability
checks for the exact codec, description bytes and decoder configuration. It does
not retain configuration keys longer than 32 KiB (those are queried normally).
It does not retain a decoder or video frame, and neither failures nor negative answers
are kept. A changed browser decoder constructor or decoder failure clears it. Normal decoder
allocation, audio, original video, FPS, source count, packet budgets and playout
buffers are unchanged. Actual improvements depend on the device and connection.

Only the Docker compiler stage uses GOMAXPROCS=1, one build worker and a 384 MiB
soft Go memory target. This leaves more resources for the running recorder and
Core during updates; the final image does not inherit those build-time limits.
The soft target is not a hard limit on the whole build or host memory usage.
Cold compilation can take longer with fewer workers, reducing contention with
the running media services. Package/model installation now precedes versioned
binaries/files/labels, so ordinary releases can reuse its existing Docker layer
while it remains in the host's build cache. The package set/model checksum are
unchanged. CI verifies identical filesystem layers for a version-label rebuild.

### Host logs during update builds

HAOS 18.4 includes Docker 29.8.2. Messages such as
`session healthcheck failed fatally ... only one connection allowed` and a reset
on `/run/docker.sock` concern BuildKit sessions/transport. They are separate from
Edge's HTTP health endpoint and its one-camera-source policy. The message is also
reported in [upstream BuildKit](https://github.com/moby/buildkit/issues/748).
The text alone does not establish the exact cause or a permanent CPU spin.

`Under memory pressure, flushing caches` during a build signals competition for
host memory. A container scope's reported memory peak covers its lifetime; it
is not the current memory use or a measurement of the compiler. Container
`veth`, bridge, shim cleanup and successful mount/service shutdown messages
normally accompany updates and Core restarts. Look separately for OOM kills,
filesystem/I/O errors and physical LAN link-down events.

For x86-64 installations, HAOS 18.5 is not a Docker fix: its
[release notes](https://github.com/home-assistant/operating-system/releases/tag/18.5)
describe a Raspberry Pi/Yellow-only kernel update; 18.4 already contains that
kernel fix for other boards. If BuildKit errors continue while no update/build
is running, or installation fails, collect the matching Supervisor build-job and
host logs plus Docker/CPU diagnostics. An Edge application update cannot patch
the host's Docker daemon. Keep those errors visible for that investigation.

## 0.17.7: Supervisor cleanup and original restart-notice icon

Modern Supervisor installations can expose `SUPERVISOR_SOCKET` instead of a
TCP bearer token. Edge uses that local Unix socket for its own options/info;
older token-based installations remain supported. No additional Supervisor role
or TCP API exposure is needed. This repairs the `unavailable` migration status
that left retired MediaMTX/Janus options persisted and caused repeated Supervisor
schema warnings even though those engines were already removed.

Startup replaces the saved options through the API and verifies the result.
Temporary failures are retried in the background after 5, 15, 30, 60 and 120
seconds, under the same lock as panel settings saves. Each attempt reads the
Supervisor's latest options so it cannot restore an old camera/password change.
`native-migration.json` and diagnostics show the status and transport; failures
use fixed phase/error codes and an HTTP status when available, without secrets.
Warnings already in the Supervisor log remain historical; subsequent checks
should no longer report the removed options after a successful save.

Only named old engine files and generated Janus configuration files are removed
from the app's known config/runtime locations. Unknown files and directory-link
targets are preserved. Camera profiles, recordings and retention are unchanged.
The restart warning displays the existing original Edge icon from the local
`/edge/edge-brand.png` asset, including offline access. Restart Home Assistant
Core after updating, then reopen the dashboard.

## 0.17.6: faster dashboard gallery and quieter DVR loading

HA recordings cards restore their last gallery and JPEGs for the same HA user
while refreshing the authenticated catalog. The cache holds up to eight metadata
views (12 MiB total, 15-minute lifetime); it contains no video or credentials.
A permission denial clears that camera's saved views. The current video, socket
and cursor stay active when fresh catalog data arrives. The server prepares only
the requested camera when a full warm catalog is unavailable.

A small spinner inside the video replaces preparing-video banners. It appears
only when a seek/start/stall lasts 250 ms and disappears when a decoded frame
arrives. Regular inter-frame waits do not show it. Adjacent paused frame previews
reuse up to two decoded frames for 350 ms; distant seeks still use the original
archive index and keyframe references. Original files, image quality/FPS and
camera/recording settings are unchanged. MP4 remains an original-file download.

This update includes 0.17.5's thumbnail improvements and keeps the original Edge
branding. Update the app, restart HA Core to load the integration, then reopen the
dashboard/Companion view to load the matching card modules.

## 0.17.0: shared mobile playback and local HTTPS

Update Edge, restart **Home Assistant Core** to load the bundled integration,
and fully reopen the dashboard/Companion app to load the new card modules.
Existing card YAML, cameras, recording paths, original codecs/FPS and retention
are preserved. Live and DVR share browser decoder resources on phones/tablets;
pausing DVR releases its decoder and lets visible Live resume. The camera
connection stays warm on the server throughout. A remote network still needs
sufficient throughput for the original camera bitrate; no fixed instant-start
or zero-buffering guarantee is made.

### Local HTTPS without Nabu Casa

**All Live, DVR, recording, seeking, alerts and HA dashboard functions remain
available locally.** EdgeRTC uses WebCodecs, which browsers require to run in a
secure context. A LAN HTTP address does not meet that requirement. If HA already
has a working trusted local HTTPS address, use it; no new gateway is needed.

For a standard HA OS installation with HA listening on HTTP port 8123:

1. Open **Edge panel → Settings → Network → Local HTTPS**. Enter the exact LAN
   IP/hostname clients will use; reserve that IP in the router if needed. Add any
   extra DNS names that resolve to this same HA machine. Do not include a scheme
   or port in the certificate fields. Select **Prepare local certificate**.
2. Download the **public CA certificate**, preferably from your existing trusted
   HA/Nabu Casa session. Compare its SHA-256 fingerprint with the setup page.
   Private keys remain in the persistent Edge directory and are never downloaded.
3. Install/trust that CA on each device. On iPhone/iPad, install the profile,
   then enable full trust under **Settings → General → About → Certificate Trust
   Settings**. Android/computers must trust it in the store used by the browser/app.
   Managed devices may restrict user CAs; use a trusted local certificate/proxy
   already accepted by that device in that case. Do not bypass certificate warnings.
4. Merge the generated `http:` snippet into Home Assistant's `configuration.yaml`.
   Keep existing HTTP settings and existing `trusted_proxies`; add the exact proxy
   IP shown by the wizard. Do not create a second `http:` block. Check configuration
   and restart **HA Core**. The wizard writes a separate
   `edge/local-https-home-assistant.yaml` helper and never edits HA YAML itself.
5. In **HA → Apps → Edge of Infinity → Configuration → Network**, show disabled
   ports and map **8443/tcp** to host port **8443** (or choose another free host
   port). Then enable/save **Local HTTPS gateway** in the Edge network setup.
   Use the mapped host port in your address; generated links assume 8443.
6. Open `https://YOUR-LAN-IP:8443`, sign in to HA normally and open Edge/dashboard.
   In Companion configure that address as the **internal connection URL** for
   your home Wi-Fi. Keep the existing Nabu Casa/external URL for access away
   from home, if used. No cloud account is needed for local operation.

The gateway proxies the whole HA to `http://homeassistant:8123`, including HA
WebSockets and Ingress; it does not expose the private EdgeRTC core or bypass HA
login/entity permissions. It is off and its host port is unmapped by default.
Enable it only after proxy/certificate setup. A failure of this optional listener
does not stop Live sources or recorders. Disabling it closes its own sockets;
access through your original HA address is retained. If an app reinstall changes
the proxy's internal IP, refresh the snippet and update that exact trusted proxy.
To change certificate addresses, disable the gateway first, prepare the new
certificate and enable it again. The existing CA is retained, so normally you do
not reinstall trust when adding a hostname. The server certificate lasts one
year; renew it using the same procedure before the displayed expiry date.

Reference: [HA HTTP and trusted proxies](https://www.home-assistant.io/integrations/http/),
[Companion internal/external URLs](https://companion.home-assistant.io/docs/troubleshooting/networking/),
[WebCodecs secure-context requirement](https://developer.mozilla.org/en-US/docs/Web/API/VideoDecoder).

## 0.15.2: automatic and manual interface languages

EdgeRTC is the sole streaming core. Update the app, restart Home Assistant Core
if the bundled integration is installed, and reopen Edge through the HA sidebar.
The current version/build is `0.17.6` / `dashboard-gallery-v176`.

The panel and Live/recordings/events cards support Polish, English, German,
French, Spanish, Italian and Ukrainian (`pl`, `en`, `de`, `fr`, `es`, `it`, `uk`).
Automatic mode uses the Home Assistant profile language when available;
standalone access uses the browser's preferred languages. Region variants such
as `pl-PL` are supported; unsupported languages fall back to English.

Select **Settings → Language → Interface language** to override automatic mode.
The choice stays in this browser and is shared with Edge cards on the same HA
origin. Return to Automatic to use the HA/browser setting again. A card can
override the preference with `language: de` (also available in its editor).
The integration's setup dialog follows HA's own profile language.

Translations are bundled locally. Changing languages updates text and accessible
labels without replacing forms, reconnecting cameras or restarting playback.
Camera names, credentials, URLs, YAML, raw diagnostic data and recording files
are preserved. Dates use the selected interface locale; recorder timezone and
recording positions are unchanged.

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
The panel and HA recordings/events cards use EdgeRTC DVR automatically.
EdgeRTC DVR reads indexed H.264/H.265/AAC samples from saved MP4 files over one
persistent authenticated socket. It seeks from the preceding keyframe, discards
outdated seek results and keeps bounded video/audio queues. It does not connect
to the camera, download a whole day or modify original files. Pause stops pulls;
leaving Recordings releases the viewer's decoder and socket.

New cores can deliver the first encoded batch with the seek response, removing
one request round trip. The client requests up to 32 access units/1 MiB for a first picture, then
16 access units/512 KiB, with at most two batches of encoded lookahead. The existing per-picture,
24-picture decoder and 24 MiB pending-message limits remain; a batch's 2 MiB
maximum is soft for a single larger access unit; pictures are never split. This does not download a whole
day. Legacy cores/clients keep their earlier explicit-pull behavior.

The latest seek cancels stale batches at access-unit boundaries and replaces old
decoder work. Duplicate scrub previews/commits are coalesced. Once a paused
preview is displayed, later pictures in its batch need no GPU decoding. DVR
scripts are preloaded without creating hidden recording viewers. Diagnostics
expose batch count, transfer/read time, pending pulls and queued batches.

The DVR offers frame steps, speed 0.5–2×, sound, full screen and event markers.
Gray event points turn green behind the playback position. Selecting an event
or its image seeks to that moment. Hover-thumbnail strips are not used.
MP4 is available as an original-file download. Unsupported video profiles
show an error and download link; unsupported audio leaves native video playing
with a diagnostic entry.

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

After every add-on update, restart Home Assistant Core (Settings → System →
Restart Home Assistant) and reload the dashboard. The add-on installs the new
integration files immediately, but Home Assistant runs the previous code until
it restarts. From 0.17.1 the cards show a restart notice with both versions,
and Settings → Repairs lists "Restart Home Assistant to finish the Edge update"
until the restart is done. Restarting only the add-on is not enough.

```yaml
type: custom:edge-recordings-card   # days, 24 h / 6 h / 1 h timeline, playback
type: custom:edge-events-card       # person, vehicle and animal detections
```

Both show every Edge camera by default. Optional settings:

| Option | Card | Meaning |
| --- | --- | --- |
| `title` | both | Heading text |
| `cameras` | both | List of `camera.…` entities to show |
| `language` | both | `auto` (default) or `en`, `pl`, `de`, `fr`, `es`, `it`, `uk` |
| `muted` | recordings | Start playback without sound |
| `kinds` | events | Any of `person`, `vehicle`, `animal`, `motion` (motion is off by default) |
| `layout` | events | `grid` (default, grouped by day) or `strip` (one swipeable row) |
| `limit` | events | Events per page, 4–60 (default 12) |
| `max_height` | events | Scroll the list inside this height in pixels |

The cards take their frame from the active Home Assistant theme and add no
borders of their own, so translucent themes such as Frosted Glass apply as they
do to built-in cards. The cards lay themselves out from their own width, so the same YAML works in a
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

### Playback in 0.16.0

The panel and HA recordings/events cards play through EdgeRTC DVR over the
authenticated HA connection. No separate MP4 player or playback transport selector
is needed. MP4 links download the original fragment; storage format and camera
encoding are unchanged. Use HTTPS (including Nabu Casa) and a browser with
WebCodecs support for the camera codec. A missing audio decoder mutes playback
with diagnostics; a missing video decoder offers the original download.

A vertical swipe over the HA timeline scrolls the dashboard; a horizontal drag
seeks and displays decoded recording frames. Offscreen HA views release their
client decoder/socket. The Edge server retains the single camera connection and
its bounded keyframe cache for the next viewer. This cannot remove physical
network latency or make an insufficient 4G/5G bitrate carry a full-quality source.

After updating the add-on, restart Home Assistant Core and reopen the dashboard
and panel so cached frontend modules and the authenticated integration agree.
Existing card YAML, themes and language settings do not need to be recreated.
