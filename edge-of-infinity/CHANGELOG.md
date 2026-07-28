# Changelog

## 0.10.27

- Change the NVR recording list into a sampled full-day filmstrip so the list covers the selected day without loading hundreds of thumbnails at once.
- Load recording-list thumbnails through the existing lazy hydration path instead of assigning all JPEG URLs immediately.
- Validate cached recording thumbnails as real JPEG files and regenerate corrupt or stale thumbnail files.
- Throttle thumbnail generation to one FFmpeg process at a time to reduce CPU spikes while browsing dense recording days.
- Add `/homeassistant/edge/recording-thumbnail.log` to the panel Logs view with thumbnail cache, generation, timeout, and request diagnostics.

## 0.10.26

- Switch NVR playback cache from a rolling window to calendar-day timelines, so each camera can build a `00:00 -> 23:59:59` playback file per day.
- Add a day strip in the NVR panel with date thumbnails, Day back/Day forward controls, and horizontal swipe support for moving to yesterday or the next day.
- Increase the default playback cache segment budget to 10000 so a full day of 10-second clips fits in one timeline.
- Serve day-specific cache files from `/recording-cache/<camera>/<YYYY-MM-DD>/timeline.mp4`.
- Load recording thumbnails directly and generate them with a more explicit MJPEG ffmpeg command, with UI diagnostics when a thumbnail fails.

## 0.10.25

- Use the server-side NVR playback cache as the primary playback path on desktop and mobile when it is ready, so rewind/forward uses one longer range-enabled MP4 timeline instead of a short live pipe.
- Increase the default playback cache window to 1000 recording segments and expose `Playback cache segments` in Edge Settings.
- Hide stale one-segment cache files when many newer recording segments exist, preventing the NVR player from being limited to a few seconds of rewind.
- Remux cache audio to AAC with timestamp resampling while keeping video stream-copy, reducing DTS/audio timeline issues in browser playback.
- Show the recording-cache build log in the Logs tab.

## 0.10.24

- Add a server-side NVR playback cache under `/homeassistant/edge/recording-cache`, built from closed MP4 recording segments into a single range-enabled `timeline.mp4`.
- Prefer the cached MP4 timeline on mobile NVR playback so phones can seek through one longer buffered file instead of stopping at each 8-10 second recording segment.
- Start a background recording-cache refresh loop with the panel server, and expose cache build/request diagnostics in the Logs page.

## 0.10.23

- Use direct saved MP4 files for mobile NVR playback through the existing range-enabled `/recordings/...` route instead of the ffmpeg concat pipe, improving Home Assistant mobile WebView compatibility.
- Keep desktop on the continuous NVR stream while phones use a `server_file_sequence` mode that switches to the next saved segment without rebuilding the whole NVR card.
- Make thumbnail clicks and Back/Forward update the active mobile video element directly, reducing reloads caused by full panel renders.

## 0.10.22

- Change always-on live warming to default to the tile preview stream instead of forcing every main/sub path warm, reducing 4K background load that could cause stutter after `0.10.21`.
- Add `always_on_stream_scope` / `mediamtx_always_on_stream_scope` with `tile`, `live`, `tile_live`, and `all` choices for controlled warm-up.
- Make mobile NVR use the continuous recording stream instead of opening single 10-second MP4 segments, so playback can continue across clips.
- Start NVR playback from the beginning of the available timeline by default instead of the newest closed segment.
- Load the first recording thumbnails immediately and show a readable placeholder if a thumbnail cannot be generated.

## 0.10.21

- Keep every enabled low-latency camera stream warm through MediaMTX when `always_on_enabled` is active, so live starts without waiting for a viewer to open the panel.
- Add the Edge Settings `Keep all enabled camera streams always on` control and persist it in `/homeassistant/edge/edge.json`.
- Add the Home Assistant add-on option `mediamtx_always_on_live` and startup logging so the generated MediaMTX config stays aligned after restarts.
- Keep NVR autostart behavior intact while warming both main/sub live paths for faster phone reconnects.

## 0.10.20

- Fix mobile fullscreen by falling back to an in-panel fullscreen mode when the Home Assistant mobile WebView blocks the browser Fullscreen API.
- Switch NVR playback toward native video controls instead of the custom range slider, reducing UI-triggered reloads while seeking.
- Defer recording thumbnail loading through lazy/idle loading so snapshot generation does not compete with the video player during startup.
- Add media-fragment seeks for mobile MP4 segment playback to make opening a selected moment faster.

## 0.10.19

- Add recording thumbnails for every listed MP4 segment, so the NVR list shows timestamped preview frames instead of using the player as a snapshot tool.
- Make mobile NVR playback use direct MP4 segment files with inline playback metadata, while desktop keeps the continuous joined timeline stream.
- Seek inside the currently loaded mobile segment without rebuilding the player, reducing reload delays when moving the timeline by a few seconds.
- Add `HEAD` and richer byte-range/CORS headers for recorded MP4 files to improve playback in mobile Home Assistant browsers.
- Log HTML5 recording-player diagnostics for errors, stalls, and metadata readiness so phone playback failures show codec/source details in Edge logs.

## 0.10.18

- Add a continuous NVR playback endpoint that streams a joined MP4 timeline from recorded segments, so playback no longer reloads at each 10-12 second segment boundary.
- Show exact recording date/time with seconds next to the timeline slider.
- Add playback snapshots below the NVR video with timestamped miniatures.
- Limit the visible recording segment list to a scrollable panel instead of letting the NVR card grow indefinitely.
- Route NVR recording through the MediaMTX rebroadcast path when the core is enabled, keeping the panel camera config as the single source of truth.

## 0.10.17

- Stop the NVR status timer from rebuilding the video player while a recording is playing or the timeline scrubber is active.
- Make timeline scrubbing inside the current MP4 segment seek the existing `<video>` element directly instead of reloading the source.
- Keep the timeline slider synchronized with playback time and auto-continue into the next recording segment when possible.

## 0.10.16

- Sync MediaMTX and Janus runtime config immediately after panel saves, so changed camera IPs, stream choices, WebRTC public hosts, ICE mode, and NVR segment length stop waiting for an add-on restart.
- Add runtime sync diagnostics to the Logs page, including the redacted generated MediaMTX and Janus streaming config.
- Expose NVR recordings as continuous video timeline metadata and add a second-level playback scrubber across recent MP4 segments.
- Add fullscreen controls for Home live tiles and NVR playback.

## 0.10.15

- Restore live audio by embedding MediaMTX player pages with audio unmuted and visible controls instead of forcing `muted=true` with hidden controls.
- Honor the selected Home tile stream exactly; choosing `main` no longer automatically falls back to `sub` because of 4K resolution.
- Enable audio in generated Janus RTSP streaming mounts so the Janus path is not configured as video-only.

## 0.10.14

- Keep Home camera tiles on the low-bandwidth substream when the configured tile stream is the 4K main stream and a substream exists, preventing MediaMTX/WebRTC player errors in small live tiles.
- Embed MediaMTX WebRTC player pages with muted autoplay, inline mobile playback, and hidden controls so Home shows video instead of player chrome.
- Mirror every successful panel save to `/config/edge.json` as well as `/homeassistant/edge/edge.json` and `panel-config.json`, making the app configuration file visible in the add-on config mount.
- Export `EDGE_ADDON_CONFIG` to the panel and refresh the `/config/edge.json` mirror during add-on startup.

## 0.10.13

- Fix save requests behind Home Assistant Ingress by reading JSON request bodies sent with `Transfer-Encoding: chunked`.
- Refuse empty `/api/config` saves instead of silently re-saving the old `edge.json`, which made the panel appear to bounce back to previous values.
- Add request-body diagnostics to save and UI debug events so future save issues show whether the browser payload reached the add-on.
- Replace the legacy `addon_config` map with `app_config` to satisfy current Supervisor app validation.

## 0.10.12

- Expose the active panel/server build in the UI, health payload, headers, `/api/version`, and Runtime summary.
- Add query-backed UI debug metadata so save-click diagnostics still identify the browser event and build even if the debug body is stripped or cached oddly by ingress.
- Keep save debugging focused on proving whether the browser is sending current DOM values or an older cached panel payload.

## 0.10.11

- Make Camera Settings save read the current DOM form values directly, refresh generated Hikvision RTSP fields only at save time, and keep an explicit local draft for diagnostics.
- Prevent accidental form submit/reload from discarding unsaved camera edits when Enter is pressed inside an input.
- Fix browser UI debug events so save logs include the submitted camera payload plus before/after form snapshots.

## 0.10.10

- Make `/homeassistant/edge/edge.json` the single authoritative camera config source; `panel-config.json` is now only a diagnostics mirror and legacy migration fallback.
- Update add-on startup to always prefer `edge.json` when it exists, preventing stale `panel-config.json` from restoring old camera values on restart.
- Verify panel saves against `edge.json` and make the Home Assistant integration read `edge.json` before the mirror.
- Add regression coverage for stale panel config no longer overriding saved camera fields.

## 0.10.9

- Make Hikvision RTSP main/sub URLs generated from Host/IP, username, password, and channel fields whenever those base fields are available.
- Overlay saved camera config onto Home Assistant camera status rows so stale `cameras.json` status cannot make entities show old IP/stream settings.
- Add stronger no-cache headers for panel/API responses behind Home Assistant ingress.
- Add integration regression coverage for stale status using old IP while saved config has the new IP.

## 0.10.8

- Adopt `/homeassistant/edge/edge.json` when it is newer than `panel-config.json`, so manual File Editor changes are not overwritten by an older panel copy.
- Refresh generated Hikvision RTSP, ONVIF, and ISAPI fields live in the Camera Settings form whenever camera connection fields change.
- Rebuild invalid Hikvision ONVIF/ISAPI values that point to stream endpoints instead of the camera base service.
- Add regression coverage for newer runtime config adoption and Hikvision URL rebuilding after IP changes.

## 0.10.7

- Rebuild generated Hikvision RTSP URLs when the saved form still contains an old RTSP URL but the camera host, username, password, or channel fields were changed.
- Update the Camera Settings form submit path to refresh Hikvision RTSP main/sub URLs before sending the save request, so edited IP/login values are visible immediately after saving.
- Add regression coverage for changing a camera IP/password while old RTSP URLs remain in the form.

## 0.10.6

- Fix camera settings save bounce by making manual Hikvision channel fields authoritative; `camera_number` now only generates default channels when channel fields are empty.
- Simplify the server save pipeline so the response is the verified `panel-config.json` payload, not a second re-normalized load that could reintroduce old values.
- Clear legacy `panel-camera-overrides.json` and `stream-overrides.json` on panel boot as well as after saves, removing stale override files from the active config path.
- Add regression coverage for saving stream roles and manual `201/202` channels without reverting to the existing `101/102` config.

## 0.10.5

- Add an explicit remote access mode for live preview: local-only, direct public DDNS, VPS relay, or TURN relay.
- Detect Nabu Casa remote UI separately and explain that it opens the Home Assistant panel but does not expose MediaMTX WebRTC ports.
- Prevent the panel from showing `ui.nabu.casa:8889` as if it were a valid MediaMTX endpoint when no WebRTC public URL is configured.
- Persist remote access mode through panel config, add-on options config, stream capabilities, and startup diagnostics.

## 0.10.4

- Detect remote WebRTC paths that cannot work on LTE, including missing public URL, LAN-only public URL, invalid URL scheme, and HTTPS pages trying to embed HTTP MediaMTX.
- Replace silent black live tiles with a clear per-camera reason so remote failures are visible in the panel instead of looking like a camera/auth problem.
- Add detailed UI live diagnostics for iframe render, load, timeout, blocked state, browser host/protocol, MediaMTX URL, ICE transport, and viewport.
- Expand UI debug events with timestamp and browser context so the Logs page can separate camera/RTSP problems from WebRTC/ICE reachability problems.

## 0.10.3

- Start configured NVR recordings automatically on add-on boot and after saving camera settings when a camera is enabled and `record` is true.
- Add NVR status states for `recording`, `scheduled`, `blocked`, and `off` so the panel no longer hides preflight errors behind a generic stopped state.
- Log autostart failures with camera id, error type, and FFmpeg log tail to make broken recording setup visible in the Logs page.
- Add regression tests for scheduled recording status and NVR autostart.

## 0.10.2

- Add WebRTC ICE transport selection: auto UDP+TCP, UDP only, or TCP only for LTE, strict Wi-Fi, and public relay testing.
- Add `mediamtx_webrtc_public_url` support to the panel path selection, so remote viewers can use a reachable MediaMTX WHEP address instead of the LAN-only Home Assistant host.
- Make Hikvision `camera_number` authoritative when building channels, so camera number `2` generates `201/202` and cannot be silently normalized back to `101/102`.
- Preserve camera number, access protocol, RTSP transport, and stream role values in save diagnostics and presets for easier debugging.
- Keep backward compatibility with the old `tcp_only` setting while moving the UI to the clearer transport selector.

## 0.10.1

- Fix Camera Settings persistence by making the saved panel config the only active source of stream roles, so stale override files can no longer force `tile`, `live`, `record`, or `snapshot` back to old values.
- Add explicit Hikvision main/sub channel fields and build RTSP URLs from those saved channels instead of hard-coded defaults.
- Harden NVR recording startup with preflight checks for missing host, username, password, RTSP, FFmpeg, and recording directory write access.
- Add per-camera FFmpeg recording logs and NVR card diagnostics so failed recordings show the real reason instead of silently doing nothing.
- Refresh NVR recording status while the NVR page is open, so new MP4 segments appear without manual page refresh.

## 0.10.0

- Add mobile WebRTC controls for public ICE hosts, STUN, and optional TURN fallback so LTE/5G failures can be fixed without editing generated runtime files.
- Let Edge Settings control MediaMTX warm-stream prebuffer timing and Janus keyframe buffering, following the same startup principle as Scrypted prebuffer without copying its implementation.
- Make `/homeassistant/edge/panel-config.json` the single authoritative save target and clear legacy override files after every successful save, preventing stream role selections from bouncing back to old values.
- Add Scrypted-style Hikvision autoconfig recommendations for H264/HEVC, Smart Codec/H264+, keyframe interval, LTE substream bitrate, and browser audio compatibility.
- Expose mobile ICE diagnostics through `/api/stream/capabilities`.

## 0.9.0

- Rework the NVR page into camera recording cards with one playback video window, back/forward controls, and recording tiles instead of technical RTSP/path details.
- Store recordings in per-camera folders under the configured recordings directory and expose them through range-aware MP4 playback.
- Add automatic NVR codec mode: H264 recordings use stream-copy video for low CPU, while HEVC/H265 recordings transcode to browser-friendly H264/AAC in auto mode.
- Add an Edge Settings NVR playback policy selector: auto H264 for HEVC, copy original, or always H264.
- Add regression tests for the recording FFmpeg command generation.

## 0.8.9

- Keep low-latency MediaMTX paths warm for enabled cameras when a stream is used by tile, live, or recording, reducing cold-start delay on phones.
- Add MediaMTX WebRTC gather/handshake timeouts tuned for LTE/5G connections without failing too early.
- Add `mediamtx_hls_always_remux` so LL-HLS can be kept ready for mobile tests without editing generated MediaMTX files.
- Increase Janus RTSP keyframe buffer and enable playout-delay RTP extension negotiation for faster viewer startup.
- Expose a codec policy in `/api/stream/capabilities`, making HEVC/H265 support explicit for RTSP proxying, LL-HLS, SRT, recording, and experimental browser WebRTC.

## 0.8.8

- Add `/homeassistant/edge/panel-config.json` as the authoritative panel-owned camera configuration, then mirror it to runtime `edge.json`.
- Make add-on startup prefer `panel-config.json` when it exists so MediaMTX and Janus are generated from the same state the panel saved.
- Stop stale override files from changing values stored in the authoritative panel config.
- Add a Logs page in the Edge panel with save diagnostics, runtime config summaries, debug tail, and ffmpeg recording log tails.
- Add regression coverage proving panel config wins over stale runtime config and stale override files.

## 0.8.7

- Persist the full camera form payload in `/homeassistant/edge/panel-camera-overrides.json`, not only stream roles.
- Apply panel camera overrides before normalization on every config load so host, credentials, RTSP, ISAPI/ONVIF, enable flags, and stream selections cannot bounce back after another source rewrites `edge.json`.
- Extend regression tests to prove all camera fields survive an external config rewrite.

## 0.8.6

- Make the panel-owned `/homeassistant/edge/edge.json` authoritative once it exists; add-on options are no longer allowed to rewrite it on restart.
- Persist stream role selections in `/homeassistant/edge/stream-overrides.json` and apply them on every config load so `tile`, `live`, `record`, and `snapshot` choices cannot bounce back after refresh or restart.
- Expand regression tests to cover stream overrides after an external config rewrite.

## 0.8.5

- Fix Camera Settings save verification so explicit `tile`, `live`, `record`, and `snapshot` stream choices from the panel are enforced after backend normalization and after the persisted config is read back.
- Stop automatic snapshot capture during status refresh so old JPEG fallback work cannot interfere with the MediaMTX/Janus live core.
- Tighten MediaMTX WebRTC ICE candidates: do not advertise Docker interface IPs, advertise configured LAN/public hosts, and enable TCP fallback on the ICE port.
- Remove tracked Python bytecode from the add-on payload and ignore future `__pycache__` files.

## 0.8.4

- Add explicit MediaMTX WebRTC public ICE hosts so browser WHEP sessions can reach the add-on over `homeassistant.local` or the LAN IP instead of timing out during ICE setup.
- Keep MediaMTX RTSP/SRT/API internal while preserving direct browser WebRTC on `8889` and UDP ICE on `8189`.
- Include configured WebRTC public hosts in core diagnostics.

## 0.8.3

- Stop exposing MediaMTX RTSP port `8554` on the Home Assistant host by default so the add-on can start when another RTSP service already uses that port.
- Keep MediaMTX RTSP available inside the add-on for Janus and internal rebroadcasting.
- Disable default host mappings for SRT `8890` and MediaMTX API `9997` to reduce startup port conflicts.
- Harden Camera Settings save: selected `main`/`sub` values now win, while empty technical fields no longer wipe saved RTSP, credentials, ONVIF, or ISAPI values.
- Remove the old unreachable shell placeholder page and MJPEG status generator from the add-on runner so MediaMTX + Janus is the only live core path.
- Point live tiles at direct MediaMTX WebRTC pages without legacy MJPEG path suffixes.
- Hide raw stream URLs from Home Assistant entity attributes to keep the integration focused on status and useful camera metadata.

## 0.8.0

- Add MediaMTX as the live core and RTSP rebroadcast layer for every configured camera stream.
- Add Janus WebRTC Gateway configuration generated from MediaMTX RTSP proxy paths so browsers use Janus instead of direct camera pulls.
- Expose MediaMTX RTSP, WHEP/WebRTC, low-latency HLS, SRT, API, and Janus HTTP/WebSocket ports through the Home Assistant add-on.
- Switch the default live engine to `janus_webrtc`; MJPEG remains only as a hidden diagnostic endpoint.
- Generate `/tmp/edge-runtime/mediamtx.yml` and `/tmp/edge-runtime/janus/janus.plugin.streaming.jcfg` on every add-on start from `/homeassistant/edge/edge.json`.
- Add `/api/core/status`, `/api/core/mediamtx.yml`, and `/api/core/janus-streaming.jcfg` diagnostics with RTSP credentials redacted.
- Preserve H.265/HEVC for MediaMTX proxying, SRT, LL-HLS, and recordings while keeping browser WebRTC codec limitations explicit.

## 0.7.2

- Fix camera settings being overwritten by background NVR refreshes while editing.
- Verify camera saves by reading `/homeassistant/edge/edge.json` back after write and returning the persisted config to the UI.
- Make Edge settings save use the current camera form values instead of stale in-memory config.

## 0.6.1

- Improve LL-HLS startup diagnostics so `hls_not_ready` returns ffmpeg status, generated files, working directory, and stderr tail.
- Wait longer for the first HLS playlist and stop waiting early if ffmpeg exits.

## 0.6.0

- Split camera stream roles into `tile_stream`, `live_stream`, `record_stream`, and `snapshot_stream` so Home tiles no longer overwrite live or recording choices.
- Add stream capability manifests with MJPEG, experimental LL-HLS, and planned MSE/WebRTC engine URLs per camera.
- Add experimental `/hls/<camera>/index.m3u8` fMP4 HLS generation using ffmpeg stream copy.
- Improve NVR recording with configurable short MP4 segments, stream-copy recording, retention cleanup, and richer recording debug logs.
- Add NVR segment settings to Edge Settings.

## 0.5.15

- Load Home tile MJPEG images through the direct Edge port URL first, matching working manual links like `http://<host>:8088/live/hikvision_1.mjpg`.
- Keep the Home Assistant Ingress live URL as an image fallback if direct access is unavailable.
- Allow `server.public_url` to override the direct live base URL for custom network setups.

## 0.5.14

- Add a lightweight Home tile live mode that prefers the sub stream for camera cards while leaving direct `/live/<camera>.mjpg` and recording settings untouched.
- Cap tile MJPEG previews to 5 FPS and 960 px width to reduce Ingress and mobile browser disconnects.
- Include tile mode details in live debug logs.

## 0.5.13

- Capture Hikvision snapshots through ISAPI JPEG endpoints before falling back to ffmpeg.
- Speed up RTSP probing with low-latency ffprobe analyze settings.
- Add lower-latency MJPEG ffmpeg flags for corrupt packet discard, keyframe output, no PTS sync buffering, and larger input queue.
- Auto-set Hikvision keyframe interval to roughly `fps * 4` when saving FPS through the stream editor.

## 0.5.12

- Normalize Home Assistant Ingress paths so doubled slashes like `//live/hikvision_1.mjpg` route to `/live/hikvision_1.mjpg`.
- Share the same route normalization for GET and POST panel requests.

## 0.5.11

- Make Home live tiles call canonical camera IDs, for example `/live/hikvision_1.mjpg`, instead of generated keys like `/live/hikvision_1_0.mjpg`.
- Make the live endpoint use the saved camera `live_stream` by default and ignore stale `stream=` query parameters unless explicit debug override is requested.
- Improve live debug classification so ffmpeg command options do not create false RTSP timeout hints.
- Send UI debug events with JSON fetch requests so `/homeassistant/edge/edge-debug.log` keeps useful payloads.

## 0.5.8

- **Fix**: Snapshot (`async_camera_image`) zawsze zwracal `None` — `capture_snapshot()` nie byla wywolywana w `refresh_status()`. Naprawione.
- **Fix**: MJPEG live stream nie dzialal przez Home Assistant Ingress — sciezki z prefixem `/api/hassio_ingress/` nie pasowaly do routera. Dodano automatyczne usuwanie prefixu w `do_GET` i `do_POST`.
- **Fix**: Ustawienia `live_stream` / `snapshot_stream` / `record_stream` wracaly do wartosci domyslnych po zapisie — `preserve_submitted_stream_choices()` dopasowywala po `camera.id`, ktore nie istnieje dla nowych kamer. Naprawione: dopasowanie po indeksie jako priorytet.
- **Fix**: Brakujacy naglowek `Access-Control-Allow-Origin` w odpowiedziach HTTP i strumieniu MJPEG — przeglądarka blokowała odpowiedzi w środowisku Ingress.
- **Improvement**: Dockerfile — przypięta wersja base image (`3.20` zamiast `latest`), dodano `py3-pip`.
## 0.5.7

- Move local Home Assistant file reads in the custom integration to the executor to avoid blocking the HA event loop.
- Fix local `health.json`, `cameras.json`, and camera image reads that triggered `homeassistant.util.loop` warnings.

## 0.5.6

- Add tunable MJPEG preview shaping: FPS, JPEG quality, and max preview width.
- Default browser MJPEG preview to 5 FPS, quality 8, and max width 1280 so 4K main streams do not create multi-megabyte JPEG frames.
- Add debug hints for oversized MJPEG frames that cause browser broken-pipe disconnects.

## 0.5.5

- Remove unsupported live ffmpeg `-rw_timeout` option that stopped MJPEG before opening RTSP.
- Add `/homeassistant/edge/edge-debug.log` as a central JSONL diagnostic log for boot, UI events, saves, refreshes, live sessions, ffmpeg, codec probes, NVR actions, and errors.
- Add diagnostic hints for common live failures such as unsupported ffmpeg options, auth failures, RTSP timeouts, decoder errors, missing frames, and browser disconnects.

## 0.5.4

- Preserve camera stream selectors by camera ID and index when saving from the panel.
- Restrict panel camera collection to explicit camera form sections only.
- Write `/homeassistant/edge/last-save-debug.json` with raw, normalized, and final save summaries for diagnosing panel save mismatches.

## 0.5.3

- Expand MJPEG live logs with the redacted ffmpeg command, request context, first-frame timing, bytes sent, cleanup reason, and ffmpeg stderr.
- Distinguish browser disconnects from ffmpeg failures so `exit=-9` is no longer treated as the whole diagnosis.
- Add RTSP read timeout logging support for live ffmpeg sessions.

## 0.5.2

- Rebuild Home as a clean live camera wall with compact video tiles and no RTSP/debug clutter.
- Start MJPEG live previews automatically for online cameras and toggle live by clicking the video tile.
- Remove snapshot fallback from the Home wall so live failures are visible instead of being hidden by still images.
- Stop capturing status snapshots during RTSP refreshes to avoid competing with the live RTSP session.
- Add a Home plus tile that opens Camera Settings and creates the next camera slot.
- Preserve submitted `live_stream`, `record_stream`, and `snapshot_stream` values after server normalization.

## 0.5.1

- Read Camera Settings values directly from each camera card before saving so `main`/`sub` selectors cannot fall back to stale form values.
- Preserve unsaved camera edits when NVR status refreshes while the settings form is dirty.
- Show both the submitted stream mapping and the saved server mapping when the server normalizes a camera payload.

## 0.5.0

- Make `/homeassistant/edge/edge.json` the panel-owned source of truth after first setup so add-on options no longer overwrite camera changes on restart unless `sync_addon_options` is enabled.
- Add add-on options for the live stream selector and preserve manually entered Hikvision RTSP URLs from both the panel and add-on options.
- Add Scrypted-inspired effective stream profiles for `main`, `sub`, `live`, `record`, and `snapshot` with channel diagnostics and redacted RTSP output.
- Read Hikvision ISAPI autoconfig from the channels in the saved RTSP URLs instead of assuming fixed `101` and `102` paths.
- Show detected Hikvision streaming channels, codec, resolution, bitrate mode, and bitrate in Autoconfig.
- Add CBR/VBR bitrate-mode editing in the stream settings panel while keeping changes manual and explicit.
- Normalize the Home Assistant integration around the same live/record/effective-stream mapping used by the panel.

## 0.4.27

- Preserve manually entered Hikvision RTSP URLs during panel saves and add-on option config generation.
- Stop rewriting explicit camera URLs to fixed `101`/`102` channels; `main` and `sub` now select the saved main/sub URLs.
- Report Hikvision channel diagnostics in Home Assistant from the actual saved RTSP URLs.

## 0.4.26

- Apply changed Home Assistant add-on camera options to `/homeassistant/edge/edge.json` instead of ignoring them when the file already exists.
- Track a hash of add-on camera options so Edge panel saves are preserved until the add-on options actually change.
- Keep a backup at `/homeassistant/edge/edge.before-addon-options.json` before applying changed add-on options.

## 0.4.25

- Bump the add-on and Home Assistant integration versions so Home Assistant detects the stream-mapping update.

## 0.4.24

- Add a separate `Recording stream` setting so NVR can use either `main` or `sub`.
- Show `Record stream` and redacted `Record RTSP` in the NVR panel.
- Restore strict Hikvision stream mapping: `main` rewrites RTSP to channel `101`, and `sub` rewrites RTSP to channel `102`.
- Remove the manual Hikvision sub-channel option so stream selection stays controlled by `main` or `sub`.
- Normalize Hikvision stream diagnostics inside the Home Assistant integration before exposing entity attributes.
- Keep add-on options, first-run config, panel config, and Home Assistant camera attributes aligned with the new stream fields.

## 0.4.23

- Keep Hikvision `rtsp_sub_channel` authoritative even if a stale RTSP sub URL is submitted.
- Stop orphaned FFmpeg recording processes when cameras are removed from the config.
- Fix Home Assistant config-flow updates to store the validated Edge URL.

## 0.4.22

- Add a Hikvision sub-channel selector so manual tests can switch between `102` and `202`.
- Preserve the chosen Hikvision sub-channel when saving camera settings and building RTSP URLs.
- Show live-stream probe details separately from the main RTSP status.

## 0.4.21

- Add live MJPEG diagnostics showing selected camera, stream, and redacted RTSP URL.
- Keep the MJPEG live pipeline unchanged while diagnosing HEVC input issues.

## 0.4.20

- Add a dedicated Live stream selector per camera.
- Keep Snapshot stream and MJPEG Live stream separate.
- Default MJPEG live to the sub stream so it does not accidentally use a HEVC main stream.

## 0.4.19

- Rework MJPEG live output to use Edge's own multipart stream writer.
- Keep JPEG only for snapshots while active live uses MJPEG.
- Write MJPEG FFmpeg errors to `/homeassistant/edge/live-*.log` for diagnostics.

## 0.4.18

- Add Dahua RTSP URL generation for main and sub streams.
- Add a Build RTSP action in Camera Settings for Hikvision and Dahua cameras.
- Keep ONVIF and generic RTSP cameras manual until vendor-specific discovery is added.

## 0.4.17

- Add Camera Settings controls for adding and removing camera slots.
- Add vendor selection for Hikvision, Dahua, ONVIF, and generic RTSP cameras.
- Render preset target slots dynamically for any configured camera count.
- Preserve existing cameras when saving settings payloads that do not include camera data.

## 0.4.16

- Prefer continuous MJPEG for active live preview instead of repeated JPEG frame reloads.
- Stop the grid refresh timer when MJPEG live is active.
- Keep JPEG snapshots separate from the active live path.

## 0.4.15

- Turn Edge Settings into an editable panel form.
- Save server, storage, retention, and live preview settings through the UI.
- Use the configured live frame interval for active previews.

## 0.4.14

- Auto-select the newest NVR segment for playback.
- Enable Rewind and Forward controls between recorded segments.
- Highlight the active segment in the NVR list.

## 0.4.13

- Add recent recording segment listing to the NVR page.
- Serve recorded MP4 files safely through the panel with browser range support.
- Add an in-panel video player for recorded segments.
- Add a Refresh NVR action without requiring a Home Assistant restart.

## 0.4.12

- Park the visible Autoconfig UI so Camera Settings returns to the main camera connection workflow.
- Add first NVR recording controls backed by FFmpeg segment recording.
- Add recording start, stop, and status APIs.
- Show recording PID, segment count, and output directory in the NVR panel.

## 0.4.11

- Add a hamburger navigation toggle for the left panel.
- Collapse the desktop sidebar to icon-only navigation.
- Hide and show the navigation menu cleanly on mobile.
- Improve responsive layout for camera cards, forms, toolbars, and narrow screens.

## 0.4.10

- Fix Autoconfig fallback when the panel request does not include `host` or `isapi_base_url`.
- Merge camera settings from the saved `/homeassistant/edge/edge.json` slot before calling Hikvision ISAPI.
- Send the camera slot index for Autoconfig and stream writes.

## 0.4.9

- Use curl `--anyauth` for Hikvision ISAPI so the camera can negotiate Basic or Digest authentication.
- Return detailed Autoconfig diagnostics per ISAPI endpoint instead of a generic failure.
- Keep the Autoconfig panel visible even when all ISAPI reads fail, so the exact camera-side problem is shown.

## 0.4.8

- Fix Start Live camera targeting by using the camera list index instead of only the camera id.
- Add visible rounded connection badges on every camera preview.
- Track `online`, `offline`, and `lost connection` states.
- Show video bitrate when FFprobe reports it.

## 0.4.7

- Add Hikvision ISAPI Autoconfig in Camera Settings.
- Read device info, stream 101, stream 102, time, video input, network, and image sections when available.
- Add safe stream editors for main/sub video and audio fields.
- Save stream changes back to the camera through Digest-authenticated ISAPI PUT requests.

## 0.4.6

- Replace the default Start Live view with Ingress-safe refreshed JPEG live frames.
- Add `/live-frame/<camera_id>.jpg` for browser-compatible live preview frames.
- Keep MJPEG stream code available for diagnostics, but avoid using it as the default UI path.

## 0.4.5

- Add saved camera presets at `/homeassistant/edge/camera-presets.json`.
- Automatically remember camera connection settings after a successful save.
- Add preset selection in Camera Settings so saved cameras can be loaded into a slot without retyping.

## 0.4.4

- Prevent empty UI saves from wiping camera configuration.
- Create `/homeassistant/edge/edge.backup.json` before saving camera changes.
- Improve UI save error handling.
- Add audio/video codec probing through `ffprobe`.
- Improve experimental MJPEG live preview FFmpeg flags for live camera viewing.

## 0.4.3

- Fix Home Assistant Ingress JSON loading by using relative panel API paths.
- Add a left sidebar with Home, NVR, Camera Settings, Edge Settings, and Account sections.
- Add navigation icons for each sidebar section.
- Move camera previews to the Home section.
- Add an NVR section shell with recording toggles and a timeline placeholder.
- Add Edge settings and account security placeholders for the next implementation steps.

## 0.4.2

- Replace the static sidebar page with a lightweight Python panel server.
- Add camera editing directly inside the Edge of Infinity panel.
- Save panel camera changes to `/homeassistant/edge/edge.json`.
- Add manual status refresh from the panel.
- Add an experimental MJPEG live preview endpoint per camera as the next step after snapshots.
- Prefer the panel config file on startup; add-on camera options now only initialize the file when it does not exist.

## 0.4.1

- Add Home Assistant add-on options for two Hikvision cameras.
- Let add-on options configure host, credentials, RTSP URLs, ONVIF/ISAPI URLs, recording, low-latency mode, and `snapshot_stream`.
- Auto-build standard Hikvision RTSP URLs from host, username, and password when RTSP fields are left empty.
- Prefer add-on camera options over `/homeassistant/edge/edge.json`, while keeping the JSON file as a fallback.
- Fix option parsing so explicit `false` values stay false.

## 0.4.0

- Capture one RTSP snapshot per online camera when the add-on starts.
- Show camera snapshots in the Edge sidebar shell when available.
- Expose snapshot paths in `cameras.json` for the Home Assistant camera entities.
- Add per-camera `snapshot_stream` selection: `sub` by default or `main` for full-quality snapshots.

## 0.3.7

- Stop auto-refreshing the sidebar page every 30 seconds.
- Run the temporary RTSP probe only once on add-on start, until the real live engine is bundled.

## 0.3.6

- Move the default database path to `/homeassistant/edge/edge.db` so user-facing files live under the Home Assistant config tree.
- Pass the selected `/homeassistant/edge/edge.json` camera config to the future `edge-core` binary.
- Clarify in the sidebar that File Editor shows this folder as `/config/edge`.

## 0.3.5

- Prefer `/homeassistant/edge/edge.json` as the editable camera config visible in Home Assistant File Editor.
- Keep `/config/edge.json` only as a fallback for older installs.
- Write the example template to both `/homeassistant/edge/edge.example.json` and `/config/edge.example.json`.

## 0.3.4

- Never overwrite an existing `/config/edge.json`.
- Always write the default template to `/config/edge.example.json` instead.
- Log whether the app created a first-run config or kept the existing camera config.

## 0.3.3

- Mirror health and camera probe status to `/homeassistant/edge/*.json`.
- Prepare the bundled custom component for local file mode, avoiding add-on hostname and port issues.

## 0.3.2

- Bundle the `edge` Home Assistant custom component inside the add-on image.
- Add `homeassistant_config` mapping so the add-on can install or update `/homeassistant/custom_components/edge`.
- Add `install_custom_component` option, enabled by default.

## 0.3.1

- Expose port `8088` by default so the Home Assistant custom component can connect to the add-on through `http://HOME_ASSISTANT_IP:8088`.

## 0.3.0

- Add `ffprobe` based RTSP reachability checks for enabled cameras.
- Refresh camera status every 30 seconds in the Home Assistant sidebar shell.
- Show online/offline/disabled status plus codec, resolution, and FPS when the RTSP stream is reachable.

## 0.2.0

- Add multi-camera shell UI for the Home Assistant sidebar.
- Create an example `/config/edge.json` with two Hikvision camera slots.
- Generate static camera metadata for the app shell while the real `edge-core` engine is not bundled yet.

## 0.1.4

- Use `darkhttpd` for the placeholder web server because the Home Assistant base image does not include `httpd`.

## 0.1.3

- Read app options directly from `/data/options.json` with `jq` to avoid Supervisor API permission errors.
- Replace the placeholder health server with BusyBox `httpd` for simpler Home Assistant compatibility.

## 0.1.2

- Replace BusyBox `nc -q` placeholder server with `socat` for Home Assistant base image compatibility.

## 0.1.1

- Fix startup option loading by reading `/data/options.json` through Bashio.
- Add safe defaults so the app does not crash when an option is missing.

## 0.1.0

- Initial Home Assistant app shell.
- Ingress sidebar support.
- Watchdog health check.
- Persistent config and media mounts.
