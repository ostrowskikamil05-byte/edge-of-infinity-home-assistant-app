# Changelog

## 0.17.7

- Support the Supervisor's `SUPERVISOR_SOCKET` Unix transport for own-app
  options/info, retaining legacy TCP token support. Fix cleanup reporting
  `unavailable` when a modern Supervisor supplies a socket instead of a token;
  use the same helper for settings saves and internal app-host discovery.
- Replace and verify persisted retired options/network bindings through the
  Supervisor API. Retry transient failures in a bounded background worker under
  the settings-save lock, rereading current settings each time. Report safe
  phase/status codes and show cleanup state in diagnostics.
- Extend exact-file cleanup to saved app/runtime locations and generated Janus
  configurations. Do not recursively delete directories or follow their links;
  preserve unknown files, cameras, credentials, recording settings and all media.
- Show the original, locally served Edge icon in the restart warning in every
  supported language. Keep the warning until Core loads the installed version.
- Add real tokenless Unix-socket tests for persisted cleanup, temporary failures,
  concurrent settings changes, denied access and legacy token compatibility.

## 0.17.6

- Restore the HA recordings gallery and JPEGs from a bounded, per-user local
  metadata cache while the authenticated catalog refresh runs. Coalesce duplicate
  startup loads, keep the current player/socket/cursor during refresh and evict
  saved catalog views when access is denied. Cache only metadata and thumbnails.
- Request the selected camera's catalog rather than preparing every camera's
  day list. Reuse a warm full catalog without duplicating its file-list cache,
  and keep current recorder state separate from cached archive metadata.
- Replace preparing-video text with a small, accessible animated indicator on
  the image, shown only after a genuine 250 ms wait. Hide it on the first decoded
  frame, pause, error and close; normal gaps between frames no longer flash it.
  Apply the same behavior to the panel, HA card and saved alert playback.
- Preview an adjacent, already-decoded frame without a new seek request or a
  decoder restart. Keep at most two paused frames for 350 ms, then release them;
  larger jumps continue to use the indexed native archive on the same socket.
- Use two bounded HA thumbnail requests so a cold decode cannot block every
  cached image. Release detached image references and keep stable thumbnail
  target locks even before waiting callers acquire them. Retain 0.17.5's server
  worker, warmup and disk-cache improvements.
- Retain the original Edge logo in the repository/app and reuse those exact
  original PNG assets in the integration; do not introduce Claude's replacement.
- Add actual Chromium/WebKit cache-before-network, refresh-during-playback,
  seek-spinner, adjacent-frame and denied-access tests. Preserve original video
  codecs/resolution/FPS, AAC, camera connections, recordings and card settings.

## 0.17.5

- Recording dashboard: thumbnails load faster (6 parallel fetches, 16 eager
  tiles, 700px prefetch margin, 3 ffmpeg workers with one decode per file,
  keyframe-only seek, browser cache headers, warm-up every 2 minutes with 8
  previews per camera).
- DVR scrubbing: seek throttle 80->40 ms, supersede 160->100 ms and settle
  100->50 ms for faster timeline response. UI layout is unchanged.

## 0.17.4

- Re-release of 0.17.2 under a new version number so Home Assistant offers the
  update again. No code changes. Restart Home Assistant Core after updating.

## 0.17.2

- Name and describe every add-on option and port in English and Polish; 27
  detection, notification and camera options previously showed raw keys.
- Accept camera IDs containing a dot in the dashboard bridge, escape the camera
  filter of event queries and answer cleanly when a viewer disconnects before
  streaming starts. Declare the integration's `http` dependency.
- Point the add-on and repository links at this repository.
- Remove eleven browser tests for the MP4 player retired in 0.16.0; they could
  no longer pass and were not run. Run the thumbnail back-off and settings-save
  tests in CI again and update the Live card test for viewport release.
- Make the HEVC recording/alert CI check wait for a finished recording segment
  instead of a fixed 3.5 s, and print the fixture log when it fails.
- No change to the Edge panel, recorder, Live or DVR behaviour. Restart Home
  Assistant Core after updating.

## 0.17.1

- Fix "Importing a module script failed" / "Failed to fetch dynamically imported
  module" in the recordings and events cards after an add-on update. The add-on
  replaces the card files at once, while Home Assistant kept serving only the
  files its older, still-running integration had listed, so the new player
  module was missing until Core restarted.
- Serve the whole card folder from Home Assistant instead of a fixed file list.
  A module added by a later update no longer needs a restart to be reachable.
- Both cards compare their own version with the integration Home Assistant is
  running and show a restart notice with both versions. A missing player module
  is explained in the card's language instead of showing the browser's error,
  and playback recovers on the same page once Home Assistant has restarted.
- Add a "Restart Home Assistant to finish the Edge update" entry under
  Settings → Repairs while installed and running integration versions differ.
  It disappears by itself after the restart.
- The Edge panel, recorder, Live and DVR paths are unchanged.

## 0.17.0

- Coordinate HA Live, recordings and alert viewers on phones/tablets. Retain the
  last picture while yielding a decoder; resume visible Live after DVR pauses.
  Retry hardware reclamation once and cancel stale seek/failure/reconnect work.
  Camera sources and recording settings remain unchanged.
- Report transport, authorization, old integration and codec errors separately.
  Check integration compatibility only after connection failure, without adding
  an extra startup request. Restart HA Core after updating the bundled integration.
- Connect the authenticated HA archive bridge directly to the same private
  EdgeRTC core used by Live. Preserve entity permissions, exact alert ownership,
  signed URLs and the remote-installation bridge fallback.
- Keep visible DVR at the media frame deadline when WebKit throttles animation
  callbacks, preventing video-paced AAC starvation. Cancel both scheduling paths
  on seek/pause/close and report fallback/gap metrics. Preserve every 1x picture.
- Adapt bounded DVR read-ahead/batches to measured transport RTT. Keep original
  resolution, codec and 1x FPS; rebase presentation after a network gap rather
  than racing through delayed pictures. Support 48 kHz AAC at 4x playback speed.
- Add an optional local HTTPS gateway for the entire Home Assistant, including
  login, dashboard, WebSockets and Ingress. No Nabu Casa subscription is required.
  Setup creates a private local CA/server certificate, downloads only the public
  CA and prepares a narrow trusted-proxy YAML snippet. Users explicitly install
  and trust the CA, merge HA settings and enable/map port 8443. The gateway and
  host port are disabled by default. Camera/HA configuration is not overwritten.
- Test actual H264/H265 Live plus two DVR cards with one available decoder,
  Chromium/WebKit seeking/AAC/network delay, TLS authentication/WS forwarding,
  certificate trust/hostname checks and configuration isolation.

## 0.16.0

- Use indexed EdgeRTC DVR throughout the panel and HA recordings/events cards,
  including saved alert clips. Keep original MP4s as streamed downloads with
  Range support; no video Blob downloads, automatic remux or MP4 player fallback.
  Unsupported video profiles show an error/download link; unsupported audio
  leaves native video playing and records the limitation in device diagnostics.
- Keep the archive socket between fragments, cancel stale seeks, decode actual
  frames while scrubbing and negotiate smaller, bounded archive delivery batches.
  Stop idle DVR animation work when there are no frames ready to display.
- Release HA Live/DVR browser connections outside the viewport and keep the
  shared camera upstream running. Reduce Live UI updates and ignore unrelated
  Home Assistant state changes. Original video quality/FPS is preserved.
- Allow vertical touch scrolling over the timeline. Capture horizontal gestures
  only after detecting their direction; retain the existing card YAML and layout.
  Stop quiet metadata refreshes from scrolling/rebuilding the thumbnail gallery.
- Share thumbnail requests and an 8 MiB/96-image memory cache per HA user,
  reuse the existing bounded WebKit-safe JPEG store and coalesce recording-key
  lookups. Camera credentials and signed URLs are never stored in that cache.
- Authenticate archive/alert sockets through HA entity read permissions. Bind an
  alert socket to that exact alert file, reject microphone/unknown commands and
  enforce request/queue bounds. Add actual Chromium/WebKit playback, gesture,
  permissions, original-file integrity, AAC and 4K regression coverage.
- Update both the add-on and integration to 0.16.0. Restart Home Assistant Core
  and reopen the dashboard/panel. Recording files, retention, detection options,
  camera profiles and seven interface languages remain unchanged.

## 0.15.2

- Add bundled interface translations for Polish, English, German, French,
  Spanish, Italian and Ukrainian across the Edge panel and Live/recordings/events
  cards, plus Home Assistant integration setup translations.
- Detect HA profile/browser language automatically, normalize regional codes and
  fall back to English. Add Settings → Language with a remembered browser
  preference and optional per-card `language` selection.
- Change labels and accessible text without replacing forms or reconnecting
  media. Preserve user camera names, credentials, addresses, YAML and raw logs.
- Add browser coverage for locale detection, manual switching, persistence,
  same-origin preference updates, phone layout and continuous Live/DVR playback.
  Camera profiles, source sharing, audio, original recordings and retention are
  unchanged.

## 0.15.1

- Build on 0.15.0, retaining its recordings/events dashboard cards, authenticated
  bridge and theme styling.
- Retry temporarily busy dashboard thumbnails with bounded backoff and reuse
  simultaneous requests for the same image. Preserve busy status/Retry-After
  through the authenticated bridge instead of reporting a gateway failure.
- Reduce DVR seek/start round trips with opt-in initial media, batches up to 64
  access units and two bounded batches of encoded lookahead. Preserve legacy
  clients/cores, the 24-picture decoder budget and the 600 ms AAC policy.
- Receive seeks independently of archive delivery, coalesce queued obsolete
  requests and cancel old batches at access-unit boundaries. New seeks no longer
  wait for old decoder promises. Skip unnecessary paused-preview decoding and
  coalesce duplicate scrub commits.
- Preload DVR modules without opening a recording/camera connection. Add batch
  transfer/read timings, queue counts and coalesced-seek diagnostics.
- Stabilize cellular Live buffer adaptation with coarser headroom steps, avoiding
  repeated small refills while keeping the 400 ms reserve cap and steady Wi-Fi.
- Add transparent app/repository/card branding, light/dark wordmarks, cached
  asset delivery and the supplied camera-infinity icon.
- Compare legacy/fast archive delivery through a bounded TCP proxy at 200 ms RTT.
  Retain original-file, AAC, frame-step, fallback and source-sharing checks.
  No transcoding, retention or camera-profile changes.

## 0.15.0

- Add `custom:edge-recordings-card` for Home Assistant dashboards: camera and
  day selection, 24 h / 6 h / 1 h timeline with detection markers, preview
  moments, speed, sound, download and full screen. Consecutive fragments are
  preloaded and handed over without reloading the player.
- Add `custom:edge-events-card`: person, vehicle, animal and optional motion
  history with pictures, filters, paging, a clip viewer and a hand-over to the
  recordings card on the same view.
- Both cards size themselves from their own width for phones, tablets and
  desktops and use Polish or English from Home Assistant.
- All three cards, Edge Live included, leave the card frame (background, blur,
  radius, border, shadow) to the active theme. Inner controls are borderless
  tints of the theme's text and accent colors, so light, dark and translucent
  themes such as Frosted Glass look consistent.
- Add an authenticated read-only bridge in the integration. Each request is
  checked against the user's camera entity permission; file names and event IDs
  are validated; video uses short-lived signed paths with byte ranges.
- The Edge panel, recorder, Live path and settings are unchanged. Restart Home
  Assistant Core after the update to load the new cards.

## 0.14.1

- Stabilize Live on variable 4G/5G delivery with an observed-gap playout reserve
  capped at 400 ms, source-timestamp picture pacing and matching PCM protection.
  Retain the existing short queues on steady connections and the DVR policy.
- Recover a severely backlogged viewer at a newer complete IDR without reopening
  the camera or skipping references needed by encoded delta pictures.
- Add bounded read-only timing feedback through the existing authenticated
  socket. Log application RTT, delivery cadence/rate, queue sizes, adaptation and
  keyframe recovery. Preserve separate microphone access checks.
- Keep original H264/H265 resolution/FPS/audio and one physical camera source.
  Playback remains limited by sustained network capacity; no transcoding or
  camera encoder changes are introduced.
- Test actual RTSP/HA/WebCodecs delivery bursts, PCM continuity, safe recovery,
  stable-network pacing and the existing archive regression suite.

## 0.14.0

- Make EdgeRTC the sole core. Remove Janus/MediaMTX binaries, dependencies,
  services, generators, listener mappings, engine selectors and network options.
- Migrate retired options and exact generated artifacts. Preserve camera secrets,
  selected profiles, recording/database paths, original files, tokens and cache
  budgets. Carry custom shared RTSP/API ports to native option names. Keep mirror
  timestamps and camera contents so stale settings cannot become authoritative.
- Batch up to 16 interleaved archive packets within the existing 2 MiB limit
  to reduce pull round trips. Reserve picture capacity for submitted frames,
  including decoder reordering, so delayed output cannot stop DVR playback.
- Stabilize DVR AAC with a separate bounded 600 ms PCM policy, encoded audio
  lookahead limited against picture time, and decoder backpressure instead of
  silently dropping packets. Keep Live's existing short queue.
- Test sustained actual DVR AAC, native-only routing/settings on phone/tablet/PC,
  migration idempotence, secrets, symlink confinement and source sharing.
- Keep the UNV optional-talk AAC compatibility fix, native MP4 fallback, full-day
  archives, 14-day retention and original video resolution/FPS/audio settings.

## 0.13.3

- Fix RTSP DESCRIBE failure when a camera advertises optional backchannel AAC
  without its configuration. Discard only invalid AAC alternatives in explicitly
  sendonly audio; retain valid G711 microphone alternatives and source H265/AAC.
  Validate the complete description before applying the compatibility filter.
- Apply the filter before library SDP parsing on the existing camera connection.
  Do not guess AAC configuration, rewrite source profiles or add camera clients.
  Forward-media and unrelated SDP errors remain visible.
- Log `rtsp_optional_audio_filtered` with camera, media index and payload number.
  Keep AAC configuration diagnostics scoped to the media, so a duplicate PT104
  cannot borrow another track's config. Include direction and media index.
- Reproduce the reported media-3 missing-config error in regression tests; verify
  original HEVC/AAC RTP at the recorder, live frame delivery and G711 microphone
  on one physical upstream. Retain recorder metadata retries and existing NVR.

## 0.13.2

- Retain and report camera connection failures previously discarded by the
  source retry loop: phase, RTSP method/status, attempt, duration and safe code.
- Capture bounded video/audio SDP codec declarations before parsing them, so an
  unsupported AAC/profile description is visible even when no RTP arrives.
  SDP control URLs, authentication headers and camera passwords are excluded.
- Retry DESCRIBE without the optional microphone requirement when the camera
  rejects it with 400/501/551, on the same connection. Authentication failures
  remain errors; the microphone is reported unavailable when rejected.
- Show per-camera RTSP failures and missing configured recorders explicitly in
  diagnostics, using the same runtime snapshot as the exported engine status.
- Include connection evidence in deferred recorder reports. Keep camera settings,
  original video/FPS, source AAC copying and the single camera connection policy.

## 0.13.1

- Serialize cached thumbnail restoration with visible-image loading, preventing
  competing jobs from replacing/revoking the same pending image in WebKit.
- Retry recorder starts waiting for shared EdgeRTC metadata independently of UI
  polling, using one bounded worker. Skip disk retention scans and FFmpeg until
  codec parameters are ready; retain source AAC copy and original video/FPS.
- Report waiting phases, packet count, source readiness and retry time separately
  from real start failures. Never attach an older FFmpeg failure to metadata
  waiting. Persistent waiting becomes a warning after 30 seconds.
- Allow a bounded H264 input-analysis window as already used for H265, avoiding
  an immediate MP4 header failure when stream dimensions are not yet available.
- Match known Uniview `/media/videoN` and `/unicast/cN/sN/live` aliases without
  conflating camera channels, main/sub streams or explicit profile selectors.
  Continue past a profile whose URI cannot be read. Unsupported ONVIF remains
  optional; custom or ambiguous routes are not guessed or changed.
- Attribute unknown RTP payloads to the source and its advertised SDP formats;
  retain counters while limiting repeated logs. If unknown packets persist with
  no valid video, close the old connection and fetch SDP again, at most once per
  minute. Healthy video is not restarted and payload codecs are never guessed.

## 0.13.0

- Add optional **EdgeRTC DVR** playback: one authenticated WebSocket per archive
  viewer, MP4 sample-table indexing, preceding-keyframe seeks and bounded pull
  batches. Read original H264/H265 and AAC; do not re-encode video, download a
  full day or open a camera connection. Keep MP4 as the default compatibility mode.
- Include a single in-picture seek track, gray/green alert markers, frame steps,
  pause, sound, playback speed, full screen and same-socket file transitions.
  Obsolete seek responses are discarded by request epoch. Pause stops media pulls.
- Check actual browser video/audio support and fall back to native MP4 for an
  incompatible file/profile. Preserve its recording and codec; do not change the
  camera's format to suit an individual phone. Use AVC configuration records for
  reordered H264 pictures in WebKit.
- Fix false UNV offline/live failures caused by short RTSP probes. Native camera
  status now uses the already connected EdgeRTC registry, including its actual
  audio codec and SPS dimensions; warming metadata remains a connecting state.
- Record known source AAC with packet copy. Read its codec from the existing
  registry instead of probing another RTSP reader or guessing an encoder at boot.
  Convert G711 to voice-rate AAC once when needed. Keep original video/FPS.
- Publish in-band H264/H265 parameters in the shared RTSP description. Wait
  briefly for live metadata on the same socket; request keyframes over the
  existing session and log source metadata wait time.
- Add explicit, verified GOP 1 s / 2 s camera actions through Hikvision ISAPI or
  supported ONVIF encoder profiles. Preserve the other profile fields. Existing
  archives keep their original GOP; no background re-encoding is added.
- Separate FFmpeg startup command headers from runtime diagnostics. Retain real
  errors and timestamp warnings. Include safe codes/phases for optional ONVIF
  synchronization failures.
- Cancel detached/unloading gallery image resources before releasing their Blob
  URLs, preventing WebKit from retrying a revoked cached thumbnail.
- Test actual H264/H265 pictures, AAC PCM, rapid long-GOP seeks, natural file
  boundaries, 4K, native fallback, original-file hashes, one-camera ownership and
  HEVC sources with no initial SDP parameters. Sub-200 ms startup is not guaranteed.

## 0.12.4

- Remove speculative saved-video downloads, server prepare requests, the two-file
  RAM/Blob cache and its UI preference. Retain the persistent metadata/JPEG gallery,
  native MP4 ranges, on-demand phone compatibility and original recording files.
- Send panel UI events once through bounded device diagnostics: one request in
  flight, at least five seconds between reports, up to 32 events / 24 KB per
  report. Suppress sends on hidden/departed pages and pause on expired Ingress.
- Normalize the document base before creating preload links, avoiding doubled
  Ingress slashes even during speculative HTML scanning. Check the session before
  resuming Live and show a reload notice after 401/403; preserve HA authentication.
- Preserve source frame intervals for short Live bursts. Retain at most eight
  decoded frames within 250 ms instead of compressing four 20 FPS frames into a
  display-refresh burst. No generated frames, codec changes or extra upstream.
- Prefer a fresh detection heartbeat mirror over a stale shadow file, without
  changing camera configuration precedence. Keep genuine stale/invalid status
  failures and include the status age/source in stale errors.
- Add WebKit/Chromium session and request-volume checks, direct-recording
  transitions, heartbeat fallback and 1–6-frame pacing burst coverage. Retain
  continuous PCM audio, full-resolution recording display, FPS and retention.

## 0.12.3

- Replace per-packet live AudioBufferSources with one continuous PCM AudioWorklet.
  Retain G711/AAC/Opus decoding, resample across packet boundaries, use 80 ms
  startup buffering and bound retained source samples to 240 ms. Mute clears
  playback; microphone capture retains its processor and shared camera session.
- Add live audio sample-rate, queue, output-frame, underrun and overrun diagnostics.
  Worklet failures leave video running and can be retried with the sound control.
- Start next-file prefetch with 4–12 seconds of buffered headroom, based on the
  previous successful transfer, instead of always waiting for the current file
  to finish downloading. Retain the two-file / 16 MiB budget and 8 MiB file cap.
  Failed/time-limited warming backs off for 30 seconds; explicit user cancellation
  remains immediate. Live still transfers no saved-video bodies.
- Discover the matching ONVIF profile in bounded background workers at EdgeRTC
  startup. Discovery is read-only; a viewer joining during discovery coalesces
  into one synchronization request. Existing discovery cache/backoff is retained.
- Allow an initial 45-second detection publisher startup window, with no retained
  event replay. Staleness after a fresh response or after that window still fails.
- Add seek/keyframe timing, continuous PCM reference and actual browser output
  tests, buffer/backoff tests and current-version worklet asset checks. Preserve
  original recordings, camera settings, FPS, retention and the persistent gallery.

## 0.12.2

- Display native WebKit recording frames through an original-resolution canvas,
  including playback, paused scrubbing and fragment changes. Keep one native
  decoder and its AAC audio; release the canvas when leaving Recordings.
- Inline the native panel Live document to remove an extra authenticated iframe
  HTML request. Retain authenticated connections, diagnostics and shared assets.
- Apply decoder backpressure instead of resetting valid delta frames during
  mobile bursts. Allow 128 queued live packets within the existing 8 MiB cap.
- Give camera feedback up to 600 ms to replace a large cached GOP before sending
  it. Optionally request ONVIF synchronization on the matched recording profile,
  with bounded workers, cached discovery and failure backoff. No encoder writes
  or additional camera video connection; unsupported cameras retain cached startup.
- Prepare compatible saved copies through JSON while on Live; defer history
  prefetch until Live is ready and fetch the next video body only after the
  current file is fully buffered or the network is idle.
- Group keyframe control and canvas presentation diagnostics by module. Reject
  non-WebSocket requests before preparing the HA RTC bridge.
- Add composed-pixel checks in WebKit/Chromium, a 4K H.264/AAC case, jitter and
  bootstrap tests, and ONVIF profile/authentication regressions.

## 0.12.1

- Remove seek-hover thumbnail requests and their preview UI. Scrubbing continues in the one main native player; measured FPS sets the seek step and the existing previous/next-frame controls remain available.
- Load native recording sources without a time fragment, then apply the latest requested position after metadata. Sample decoded pictures from native seeked/loadeddata events before using a transition fallback; clock progress alone no longer releases a hold. Keep the pending overlay slightly transparent so WebKit can continue compositing the video underneath.
- After native Live confirms its first picture, optionally prepare the first selected saved file for up to two cameras. Use the existing two-file / 16 MiB RAM budget, one at-most-8-MiB transfer, 12-second timeout and reported Save-Data/2G guard. No recording decoder runs on Live. Returning to Live releases decoders while retaining unused bytes for up to two minutes; other pages/disabled warming release them. Entering NVR cancels pending startup warming and uses a ready Blob directly.
- Preload native Live scripts and privately cache only URLs carrying the current app version. Each camera iframe reuses the same script bytes. Unversioned and old-version URLs remain no-store. Explicitly close the panel's Live decoder before opening NVR, rather than waiting for asynchronous iframe unload. EdgeRTC camera sources continue running with zero viewers through the existing single upstream.
- Add real WebKit/Chromium tests for warming after Live, network-blocked first playback, absence of hover requests, main-picture dragging, frame stepping, callback-suppressed native seek readiness and decoder cleanup. Add cache-version/scope regression coverage and startup/native-picture diagnostic stages. Preserve recordings, source quality/FPS, AAC, retention and existing gallery/log behavior.

## 0.12.0

- Ask for a fresh video keyframe through RTCP PLI on the existing RTSP session when a joining viewer faces a long/empty GOP cache. Coalesce requests per camera for three seconds; expose request/write-error counts and the time until a subsequently observed keyframe. A camera may ignore feedback. The cached-GOP fallback stays available. Prioritize a newer IDR before the browser's first picture, wake blocked bootstrap work, reset obsolete references once and skip superseded queued access units. Normal live frames, codec, dimensions/FPS, microphone routing and one physical upstream remain unchanged.
- Let a newer same-file seek supersede an unfinished seek immediately on release. Preserve Play pressed while dragging from Pause; use the current requested position in late metadata callbacks. Keep a bounded snapshot of a confirmed picture while changing sources. Use source/seek generations and video-frame callbacks to release the hold, with a guarded paused/clock-progress fallback for occluded WebKit surfaces. A failed canvas readback preserves the previous valid hold. Release replaced decoders and memory URLs when a day/camera card changes. Record actual picture-ready/release stages alongside metadata and seek timing.
- Prepare the next saved segment in browser RAM after stable visible playback. Keep at most two files / 16 MiB, one download of at most 8 MiB with a 12-second deadline and two-minute idle lifetime. Never open another decoder or encode video/audio. Reuse the exact served bytes through a Blob URL; pause, seeking, waiting and page departure cancel pending downloads. Reported Save-Data/2G disables warming. Add a per-device player switch; leaving Recordings releases the cache and URLs. The existing JSON preparation remains independent and also works when WebKit reports readyState 2 during healthy playback.
- Add private MP4 cache validators, conditional 304 responses and correct If-Range fallback. Keep validators stable when an immutable compatibility copy's LRU timestamp is touched. Original and compatibility byte representations remain separate and unchanged.
- Keep displayed gallery JPEG Blob URLs valid until their image nodes are removed. WebKit can reread them after scrolling/layout; reclaim detached URLs through DOM observation and release the document's URLs on unload. Verify repeat image decoding and removal cleanup during an archive/thumbnail network outage.
- Merge playback, backend, thumbnail, legacy recording-stream/cache and discovery event histories into the module views. Add camera, current-startup/24-hour/all-history and server/device filters. Infer timestamps/categories for legacy cache entries; keep old failures in their historical period. Preserve full collapsed raw histories and redacted exports. Live diagnostics now separate WebSocket opening, decoder configuration, cached decoding and superseded startup work; recording diagnostics identify RAM/server playback and confirmed-picture state.
- Add actual native 24-second-GOP H.264/HEVC seek and RAM transition tests on WebKit/Chromium, including a pending seek replaced in the same task, Play while dragging, actual visible pixels and SHA-256 equality of the cached served bytes. Test actual RTSP feedback with one upstream, ignored/supported PLI camera fixtures, blocked old bootstrap work, HTTP validators/eviction and camera/time/source log filters. Synthetic first-image measurements do not establish physical UNV or Nabu Casa latency. A first uncached seek in an old 24-second GOP still requires reference decoding; no camera encoder setting or original recording is rewritten.

## 0.11.14

- Restore the recording gallery from IndexedDB after a reload, then refresh it in the background. Prefetch the archive metadata three seconds after Live boot without fetching video bodies or opening hidden decoders. Retain up to two metadata snapshots / 12 MiB for 24 hours and 96 JPEGs / 8 MiB for 48 hours; scope them to the HA origin, recording directory and camera/profile IDs. Store exact JPEG bytes as ArrayBuffers to avoid WebKit file-backed Blob transaction stalls; bound storage reads/writes and fall back to the network. Credentials, recorder flags and video bodies are excluded. Failed background refreshes leave an available gallery usable. A first visit, cleared/denied browser storage or an uncached day still needs the server.
- Keep long UNV GOPs ready for joining EdgeRTC viewers: replace the 180-frame cutoff with 2,048 frames and the existing 16 MiB ceiling. Expose cache readiness, span and eviction reasons. Give startup a bounded 1,024-packet / 8 MiB live queue while the cached GOP transfers, then return to the normal 16-packet slow-viewer protection. Browser startup has a 4,096-message / 24 MiB cap, feeds at most eight queued access units to WebCodecs and discards historical display frames while decoding their references. Normal live pacing, source resolution/FPS and one physical upstream remain unchanged.
- Organize logs into overview, Live/EdgeRTC, recordings/gallery, devices/network, hardware, camera configuration and detection. Add severity and camera/event search, time-ordered event rows and collapsed raw details. Classify structured errors rather than matching optional capability text or every historical line. Expected viewer closure is informational; a canceled HA subscription is a warning; real decoder/transport errors remain visible. Keep full redacted JSON export.
- Add native IndexedDB/WebKit/Chromium reload, JPEG, actual playback, scope, TTL and storage-budget checks; phone log filters/escaping/layout; 600-frame decoder backpressure and bounded Go cache/queue race tests. A generated 24-second-GOP RTSP source started through the actual HA proxy and WebCodecs from 240 cached frames in about 190 ms locally. This is a warm-source fixture measurement, not a physical UNV/Nabu Casa guarantee. Camera settings, recording bytes, AAC policy and retention remain unchanged.

## 0.11.13

- Preserve Play intent across an unexpected native browser pause. Recover a healthy paused decoder with bounded retries; explicit application Pause cancels queued recovery. Ignore late metadata/Play callbacks from detached players. Hydrate a deferred phone source and issue Play immediately on day selection, without waiting for metadata from an unloaded source; avoid repeating an already applied fragment seek.
- Encode G.711 voice as mono AAC at 16 kHz / 32 kbps per channel, with one audio thread and a sample-count clock after resampling. Copy native AAC packets during recording. Video remains original stream copy with source dimensions/FPS. This policy applies to new recordings; camera configuration and physical upstream count are unchanged.
- Parse valid MP4 edit lists and serve fast-start originals directly when video/audio starts are within 150 ms and AAC clocks/tails are healthy. Retain original short A/V offsets. Repair irregular AAC or an excessive audio tail in the bounded compatibility copy, trimming only audio to the video duration. Version the new representation as `aac-clock-v6`; older open tabs retain their previous remux/direct representation and cache keys. Originals are never rewritten.
- Share a bounded archive catalog across clients: one low-priority Live warmup, coalesced cold requests, up to four snapshots / 24,000 file entries, 10-second freshness and at most five-minute stale reuse while refreshing in the background. Current recorder flags are read independently. Manual refresh, recording-tail continuation and missing-alert lookups force a current index. The browser retains up to four days / 24,000 entries, opens a cached day immediately and ignores repeated selection of the current day.
- Add catalog hit/build/error logs, native pause events, per-file audio timing/rate/clock data and mute/volume in device reports. Add actual WebKit/Chromium AAC PCM-output tests, cached-day playback with a held server response, native pause/explicit Pause checks, concurrent/stale catalog tests and G.711/AAC/video-frame preservation regressions.

## 0.11.12

- Fix cold phone archive loading: Live boot requests only recorder state; opening Recordings immediately fetches history, retaining known indexes on return. Distinguish an unread index from an empty day or a disabled recorder. Superseded requests and failed background polling do not overlay a working player with false errors.
- Remove full storage walks, cache maintenance, disabled daily-cache signatures and external FFprobe calls from the playback-list request. Reuse stat metadata and movie durations, cache timezone-aware calendar bounds and filename timestamps, and keep damaged-file duration estimates bounded. Disabled daily caches do not inspect old timeline metadata. Full disk statistics run separately in one background scan from Settings, while NVR shows compact free-space data.
- Compress large indexes with low-level gzip when negotiated; retain plain JSON fallback and unchanged MP4 byte-range behavior. Log index build time, serialized/wire bytes and encoding time. A synthetic 14,800-file archive (12,240 selected-day entries) improved from 2.255 s to 0.689 s cold and 1.777 s to 0.548 s warm locally; its compact JSON was about 5.2 MB versus 0.37 MB gzip. These are fixture measurements, not promises for a particular phone/network.
- Stop the old `browser_playback: h264` preference from transcoding the continuous recorder. Migrate only that legacy field to `copy`, retain a backup and all camera/storage settings, and keep H.264/H.265/audio comparisons in the explicit six-second codec lab. Recordings keep original source video/FPS; existing files are not rewritten. User reports showed HEVC 4K encoding on a two-core t530 at 98% host CPU and repeated list timeouts; removing the encoder and blocking list work addresses these separate causes.
- Fix RTSP redaction swallowing multiline FFmpeg command/error logs when a local URL was followed by a later decoder `@` message. Preserve full readable commands while hiding credentials.
- Add real cold-entry WebKit/Chromium phone/desktop tests with 12,000 saved fixture files, negotiated gzip, native playback and failed-poll protection; regress copy-only migration, no-probe/no-scan indexing, timezone calendars and compression opt-out. A source GOP around 24 seconds still limits arbitrary HEVC seek/decode speed; camera encoder settings remain unchanged.

## 0.11.11

- Add separate device/session reports for phones, tablets and desktops, including the HA EdgeRTC card and panel/live iframes. Capture browser-reported capabilities, resource DNS/TCP/TLS/TTFB/body times when available, online/offline/errors, native recording state, WebSocket/signing/first-frame stages, decoder/message/render queues and frame drops. No camera-channel probes, video payload capture or per-frame network logging. Sample active sources every 20 seconds and batch events; stop idle heartbeats and back off failed uploads. Unsupported device/CPU/RAM/radio/route measurements remain explicitly unavailable.
- Diagnostics lets users name a device, choose its logs and download a redacted report with observed proxy addresses and configured browser → HA → Edge → camera routes. This is an observed/configured connection map, not a traceroute. Keep 24 devices × 256 KiB for at most seven days with cross-process locking and input/rate limits. The HA card upload requires authentication and read permission for its camera; close/failure reports survive card removal. Redact passwords, signed URLs, query credentials and ingress IDs, including historical logs in exports. Restart Home Assistant Core after updating to register the new card endpoint/assets.

- Resume a playing recording when scrubbing began during buffering. Issue Play on release instead of waiting for a paused WebKit decoder to produce its first frame. Keep loading intent during same-file seeks and expose Pause while loading. A bounded progress watchdog tries Play after 8 seconds without clock progress, then one reload after another 12 seconds; explicit Pause, hidden pages and active scrubbing are respected. Persistent failures stop with a visible message and diagnostics instead of an endless retry loop.
- Serve already compatible saved MP4s directly when they have a fast-start index, valid video timing, zero-based video/audio, H.264/HVC1 video and regular AAC (or no audio). This bypasses remuxing, disk copies and another worker's lock. Keep normalization for legacy HEV1, non-fast-start, delayed or irregular files. Original files are never rewritten; existing next-segment preparation and bounded compatibility cache remain.
- Add **Recordings → player settings → Kodeki**: compare up to six seconds of saved video using the original codec, H.264 or H.265 and AAC, no audio, G.711 A-law or μ-law. G.711 uses a test MOV and may be rejected by a browser; the real result is logged. Video conversion is lossy, explicit and limited to a test copy, with original dimensions/FPS; normal playback/recording remains stream-copy. Tests do not change the camera's encoder or prove its native codec capabilities. One low-priority worker, one encoder thread, 24 MiB/test and the shared 64 MiB/12-file/48-hour budget limit resource use. A 90-second timeout cleans partial output; no background test transcoding.
- Log compatibility mode, prepare/queue time, delivered bytes, transfer time, seeking/first-play time, buffered data, native errors and frame drops. Add a dedicated bounded playback log and codec-test reports with FFprobe's actual video/audio formats. Read CPU/product/GPU-driver inventory once per boot; a detected GPU does not imply a working hardware decoder. Export current camera stream codec/resolution/FPS/audio and log ONVIF profile video/audio settings on explicit discovery, distinguishing observed profiles from all supported options. Credentials are redacted.
- Keep the existing continuously connected EdgeRTC source and its bounded keyframe cache. Export keep-alive/cache occupancy in diagnostics; no extra upstream or recording-quality change is introduced. Original HEVC/GOP, hardware and network conditions still affect physical-device start/seek speed.


## 0.11.10

- Fix black second-camera alerts on phones by using the common source-restoration/Play path. Reuse a loaded same-day player instead of rebuilding it or awaiting a status request before every alert. Refresh only when the selected event is absent from the current file index. Make the chosen video visible before Play, and release inactive phone decoders before saved clips open.
- Preserve Play through native pauses during loading/buffering, clear buffering only on actual playback, respect explicit Pause and retry one transient network error per segment at the current position. Export buffered ranges and playback intent for diagnosing stalls. These changes cover desktop and phone playback.
- Prepare the next saved segment through a small JSON request while the current one plays. No speculative video body, extra decoder or physical camera read. Keep one client preparation request, one backend remux worker and the existing 64 MiB / 12-file / 48-hour compatibility budget. Cached byte-range reads bypass an unrelated remux; an already-open file response survives cache eviction.
- Normalize recording/alert containers for desktop as well as phones. Read AAC sample-clock regularity directly from MP4 metadata, copy healthy AAC packets unchanged, and repair only irregular clocks. Video remains stream-copy with original pixels/FPS. Limit recording/audio filter threads, defer hidden recording sources/posters on desktop and phones, and defer NVR thumbnail work while another page is visible, and release the temporary desktop preview decoder after a short idle period or page departure. H.265 decode/seek speed still depends on the original profile, GOP, device and connection.
- Add nonblocking measured CPU busy/I/O-wait sampling. A startup load average of 1.302/core is an elevated-load warning, not by itself a hardware failure; an error requires measured busy CPU, sustained queue pressure and time after boot. Keep genuine sustained overload visible. Label disabled playback-cache logs as historical in the log viewer; full history and originals are retained.
- Verify the live card connects through authenticated Home Assistant `/api/edge/rtc/…` WebSockets to the shared Edge core. Add actual WebKit/Chromium alert/clip/segment/buffering tests, bounded network-recovery checks, unchanged AAC-packet tests, CPU classification and cache-eviction streaming tests. Physical phone and WAN performance still require user-device verification.

## 0.11.9

- Release hidden live iframe decoders when navigating away from Live, and restore them when returning. On phones, defer inactive recording sources and retain only the selected recording decoder; start muted and release paused recording decoders when leaving NVR. Temporary held pictures use screen-sized buffers; original recording/live pixels and FPS are unchanged.
- Prevent a failed WebKit canvas readback from keeping a black/stale canvas over a working native video. Show actionable playback errors and allow an explicit retry. Detect browsers that silently play AAC while ignoring an unsupported HEVC video track, and show a codec notice instead of black audio-only playback. Keep camera/day selection, paused scrubbing, frame steps and the existing single seek bar.
- Put intrusion points directly on the seek track. Points are gray ahead of the cursor and green behind it, including backward seeks. Tapping still opens the saved intrusion pictures and recording moment.
- Repair backward AAC clocks when recording. For existing phone recordings, normalize track origins and audio timing in the bounded compatibility cache, keep video stream-copy with its original frame intervals, retain B-frame composition order, and write seekable hvc1/faststart MP4. Existing originals are untouched. Display recorder diagnostics from the latest start marker so old warnings are not presented as current failures; retain full logs on disk. Handle HTTP suffix ranges correctly and return 416 for unsatisfiable ranges.
- Quarantine a VAAPI device after decoder failure or timeout until the app restarts, preventing repeated submission to a failing kernel/UVD decoder. AI continues on CPU; the existing resource-saving keyframe fallback and its short-event warning remain available.
- Reduce the client render backlog bound from 250 to 120 ms, retain the current I-frame when recovering a full decoder queue, prefer available hardware decoding and reuse the canvas context. Avoid changing the native live label every frame. The 120 ms bound concerns the client render queue, not measured total camera/network latency; normal 20/25/30 FPS bursts retain every original frame.
- Add actual WebKit 26.5 HEVC/H264/AAC playback, camera switching, decoded-pixel and canvas-readback tests; audio clock regression, legacy track-offset normalization, suffix-range and GPU quarantine tests. Physical iPhone 15 Pro Max/Android performance still requires device verification.

## 0.11.8

- Fix phone recording playback on iPhone/Home Assistant WKWebView and Android: issue Play immediately within the user gesture, including an unfinished seek, instead of waiting for a decoded frame that the phone may only load after Play. Release held seek pictures independently of callbacks from an occluded video surface. Keep one seek bar, original frames and pause/resume intent.
- Preserve camera RTP timestamps when recording instead of replacing them with network arrival times. Write new HEVC recordings as hvc1 for mobile compatibility. Mobile playback of existing recordings and saved alert clips uses a faststart MP4 stream-copy remux: no video re-encoding, resolution/FPS reduction or new camera connections. Copies are limited to 64 MiB, 12 segments and 48 hours in app temporary data, with one low-priority worker and disk reserve; originals are untouched. HEVC still requires a device/browser HEVC decoder.
- Increase the bounded local RTSP write queue from 256 to 4096 packets to absorb large 4K I-frame bursts. Isolate and close only a failed local consumer, rate-limit its messages and expose overflow/failure counters and consumer role. Recording/live upstreams are not restarted by a slow AI reader.
- Enable standard Home Assistant video-device access and include Mesa VAAPI drivers. Auto AI decoding tries the local render device, falls back to CPU if the stream is unsupported and switches an overloaded, delayed CPU reader to keyframes. Expose Auto/CPU/keyframe settings and an explicit warning that short events between keyframes can be missed. Full recording quality and FPS remain unchanged; hardware acceleration depends on the device, driver and codec.
- Retry busy thumbnail generation with backoff instead of starting full-resolution browser video downloads. Keep one extraction worker, the existing preview budget and historical cache diagnostics without reporting obsolete disk errors as active failures.
- Add actual HEVC/H264 remux pixel/FPS tests, RTSP burst and slow-reader isolation race tests, busy-thumbnail tests and iPhone/Android loading-policy simulations. Browser phone simulations do not replace testing on physical devices; no GPU is available in CI.

## 0.11.7

- Index each recording list once and use binary search while scrubbing. Reuse the index across metadata refreshes when the files are unchanged. Let polling send only a revision token and omit the unchanged file list from responses, reducing repeated full-day transfers through Home Assistant ingress/Nabu Casa.
- Load small cached JPEG film/date thumbnails first, with browser decoding as a fallback. Reuse the same JPEG for zero-second hover/poster/tile previews and recheck the cache after acquiring the extraction worker, so simultaneous requests decode once. Keep the existing storage limits and one low-priority extraction worker.
- Exclude MP4s still held open by the tracked recorder from playback/thumbnail candidates, including cameras with long GOPs and infrequent disk flushes. Do not delete recordings or require header scans of all retained footage.
- Preserve Play intent across rapid and unfinished seeks. Play pressed during loading resumes the selected moment when ready; Pause cancels that intent. Ignore trailing change events and expected Chrome AbortError/new-load interruptions instead of displaying raw goo.gl error links. Coalesce cross-file dragging, prioritize the final target, and preload the selected video. Preserve paused seeks, frame steps and the last valid picture.
- Replace per-sample RTSP/FFmpeg restarts and the ten-second I-frame wait with a persistent low-priority decoder of the existing shared Edge relay. Analyze sampled frames between keyframes, retain one latest frame per selected camera, cap readers at four and stop them when disabled/removed. Original live/recording video and the physical camera connection count are unchanged. HP t530 should start with one selected analysis camera and the total one-FPS default.
- Report running only after successful inference. Include capture/model/inference error codes, last analysis time/object count, decoder state and model readiness in the Events page and exported Local AI diagnostics. Keep the publisher independent of decoder waits. Warn when an old browser tab is using a different panel build and provide an explicit refresh button.
- Read the typical video sample interval for frame stepping instead of an average distorted by a final sample/audio tail; preserve encoded media and timestamps. Test actual animal detection in P-frames after a black I-frame, decoder lifecycle/bounds, one HEVC upstream with live/recording/analysis/talk/alerts, 12000-file indexing, status-transfer suppression, small-JPEG loading and responsive Play/load/seek races.

## 0.11.6

- Decode recording thumbnails in one shared browser video decoder and keep 64 recent previews in memory. Reuse the recorded MP4 files for hover and film tiles, with the existing bounded JPEG fallback for unsupported codecs. Stop background thumbnail extraction on the server.
- Hold the last valid video frame during all seeks, including delayed segment changes, until the requested frame is decoded. Add paused previous/next frame controls using the actual MP4 video sample clock, plus one-second navigation. Preserve the single seek bar and original resolution, codec and FPS.
- Add simple person/animal/camera switches, a separate animal confidence threshold and animal HA occupancy sensors. Use the packaged local NanoDet model and the shared Edge recording relay, with the conservative total one-frame-per-second default for HP t530.
- Put exact person/animal intrusion dots on the existing video seek bar. A dot opens saved event thumbnails; selecting one seeks the original recording to that event. Keep thumbnails accessible inside fullscreen and in the Alerts history. Dense dots group nearby events without creating another timeline.
- Automatically save a cropped detection thumbnail and a short intrusion clip from closed recording segments before/after the event. Remux H264/H265 without re-encoding; the clip may include whole boundary segments. Wait for the post-event buffer and segment closure, and show pending/ready/expired status. Recording must be enabled to save clips.
- Bound clips to 256 MiB by default (configurable), alert pictures to 32 MiB / 2000 images, and both to recording retention. Evict oldest alert media; never remove or change original recordings to satisfy alert quotas. Show clip/picture usage alongside existing storage usage.
- Add opt-in Home Assistant Companion notifications with a saved event picture, a recording link, selected mobile_app service and cooldown. Require authenticated camera read permission for the image; suppress initial history, stale events and duplicates. Update/restart the bundled HA integration before enabling this feature.
- Test actual HEVC alert pixels/FPS, recording, native EdgeRTC, local analysis, preview and microphone together with exactly one camera upstream connection. Add responsive no-server-thumbnail, held-frame, frame-step, event-dot, fullscreen and settings tests, plus media limits, persistence and notification access tests.

## 0.11.5

- Use one full-day seek bar inside the video picture. Remove the separate calendar-day slider below the player; day selection and event jumps remain available. Overlay controls remain inside the picture in native and in-page fullscreen, with a compact settings menu for narrow players.
- Dragging the bar now seeks the actual recorded video during the gesture, including across segment boundaries. Preserve fractional positions, coalesce rapid updates and wait for an in-flight decoder/load instead of launching every requested seek. Hover still shows the bounded recorded-video thumbnail preview without changing playback.
- Pause while dragging and resume only if the video was playing before the gesture. Support pointer capture, real touch, held keyboard keys, Escape to cancel and stale/slow seek cancellation. Keep requested position visible while a frame loads, and prevent old pointer callbacks from finishing a new keyboard gesture.
- Release held transition frames after paused seeks even when a browser defers video-frame callbacks for occluded video. Guard release callbacks against superseded sources/positions. Show a bounded error on an unfinished seek and cancel pending gestures on day/page changes.
- Preserve original recorded video, storage budgets, retention, AI and EdgeRTC frame pacing. Add responsive real-decoder tests for in-picture controls, changing pixels before pointer release, segment transitions, pause/resume, keyboard cancel, touch, fullscreen and latest-target behavior under a delayed clip load.

## 0.11.4

- Stop generating full-day playback copies by default: the day player keeps one timeline and seeks original segments. Remove recognized legacy playback-cache files automatically at startup/maintenance when the copy budget is zero. An optional global copy budget is capped at 2 GB, with space reservation and refusal of oversized day builds; active cached players are protected from ordinary eviction.
- Bound the combined normal/scrub thumbnail cache to 32 MiB by default, 2048 images and 48 hours since access. Evict oldest images across cameras and remove thumbnails whose source recording disappeared. Preserve thumbnail dimensions/encoding quality. Caches regenerate from originals as needed.
- Preserve H264/H265 camera video by default, including legacy automatic configurations that previously converted HEVC to ultrafast H264. Keep explicit H264 compatibility conversion available with a quality/space warning. Never automatically lower camera bitrate, resolution or frame rate. Probe the shared recording feed instead of opening another camera source.
- Give archival recording to the Edge recorder only: disable duplicate MediaMTX recording in both startup and generated runtime configurations. Existing recordings, including legacy MediaMTX folders, remain subject to the configured recording retention.
- Show original-video/cache/thumbnail usage, both filesystem free-space readings and estimated continuous-recording storage for the selected retention. Add a cache-only cleanup button and editable cache budgets, preserving settings across saves/restarts.
- Suppress recorder progress spam and bound recognized application/recorder logs with in-place tail retention so open recorder descriptors remain valid. Never remove original footage as part of cache cleanup; the existing recording-retention policy remains separate.
- Add storage quota, cache bundle/TTL/LRU, symlink/foreign-file protection, codec preservation, inventory and configuration tests plus responsive cleanup/playback coverage. AI and live frame pacing remain unchanged.

## 0.11.3

- Fix recording-day navigation behind a stalled refresh: a new selection cancels the older request, stale replies cannot switch the day back, requests have a timeout and errors/loading are visible. Keep explicitly requested empty days instead of silently opening the latest day.
- Scan recording metadata once per camera/status request and measure clip durations only in the selected day. Other day summaries use cached/estimated durations, avoiding video-payload reads across the entire retention period.
- Add desktop hover, keyboard and touch scrub thumbnails from recorded footage, sampled every five seconds. Keep playback untouched during preview; coalesce requests, use one extraction worker and bound preview caches to 128 disk images per camera / 64 browser images.
- Pace decoded EdgeRTC frames using original camera timestamps instead of replacing every pending frame with the newest one. Decode the cached GOP for references and start display at its newest frame. Bound slow-device backlog, release frames on close, and reduce card-control DOM refreshes to four per second. Source resolution, codec and FPS remain unchanged; no frame interpolation or transcoding. Display measured source FPS in the card.
- Move camera configuration method beside IP/login/password, show credential-free address previews, refresh non-Hikvision ONVIF hosts while preserving custom HTTPS ports/paths and honor an explicit ONVIF/ISAPI discovery selection. Existing manual/discovered RTSP paths remain supported. RTSP uses vendor discovery; ONVIF/ISAPI are configuration/control protocols.
- Add real-media responsive navigation/scrub tests and deterministic 20/25/30 FPS burst-pacing tests. AI functionality is unchanged.

## 0.11.2

- Add an opt-in rectangular local-AI zone per camera, with explicit still-image preview, mouse/touch drawing and percentage inputs. Validate bounds before saving; reject invalid saves without changing the authoritative file and keep invalid startup zones closed to detections.
- Remove model-input letterboxing from detected boxes before testing the object’s bottom-center point against the zone. Crop preview JPEGs to the same unpadded scene, using explicit BGR padding for exact geometry. Recordings and live video retain the complete original frame.
- Fix detection-toggle label layout and hide the preview placeholder after the frame loads.
- Serve previews only from the shared recording-profile RTSP relay, reusing recent analysis frames. Bound preview/capture concurrency and cache at most four small frames in memory; do not store preview images on disk.
- Filter local objects before history/state publication, label zone events with zone_id=local_zone, forward that identifier to HA, expose zone metadata and add camera/local/zone-only notification source choices. Camera ONVIF events retain their own camera-side rules.
- Preserve running recorders and ONVIF PullPoint subscriptions when only the local zone changes. Add configuration, geometry, worker, preview-cache/privacy and HA-event tests, responsive mouse/touch editor tests, and real H265 pipeline coverage for the cropped preview with one upstream connection.

## 0.11.1

- Browse the complete retained event history with server-side camera/type/day filters and stable cursor pagination. Keep the selected history page during detection-status polling; cancel superseded filter requests and bound request timeouts.
- Keep the complete NVR player and controls in element/in-page fullscreen, including browsers offering only video fullscreen. Use dynamic viewport height for the in-page fallback.
- Show motion/person/vehicle markers on each NVR day, with previous/next navigation and a link to that day’s full event history. Five-minute groups summarize all retained events, including events outside the recent-state window. Event refreshes update metadata without replacing the video; missing footage is reported before seeking.
- Add a Companion notification YAML builder for person/vehicle events, camera selection, validated notify.mobile_app service, clipboard/manual copying and a configurable cooldown. Users save and enable the generated automation in HA.
- Handle recorder-local calendar boundaries across 23/25-hour DST days and validate event query dates/cursors. Add history/API/DST tests and responsive browser coverage for deep history, filters, stale responses, notification YAML, markers and actual recording seeks. Run the existing NVR playback regression suite in CI.

## 0.11.0

- Add native H.265/HEVC RTP depacketization, RFC 6381 Main/Main10 codec metadata, Annex B access units with VPS/SPS/PPS on random-access frames, and browser profile support checks. Fix timestamp initialization on fragmented H264/H265 frames. H265 recording stays in the original codec; live HEVC requires a decoder on the viewing device.
- Add opt-in ONVIF PullPoint events: service/topic discovery, WS-Addressing/reference parameters, subscription renewal/unsubscribe, bounded retries and normalized motion/person/vehicle events where the camera reports them. Accept standard XAddr capability attributes, block SOAP redirects and keep credentials on the configured camera host.
- Add opt-in local NanoDet person/vehicle detection from the shared Edge RTSP source. Package the 3.8 MB Apache-2.0 model with a pinned revision/checksum, one CPU inference worker, keyframe sampling, a configurable total FPS budget and lower-priority analysis on Linux. Intended conservative settings for HP t530 are one selected camera and at most one analyzed frame/second total.
- Add private SQLite event history with retention/cap, an Events page, per-camera/global detection settings, filtering and seeking to a recorded event. Superseded analysis results are discarded.
- Add HA motion/person/vehicle binary sensors and edge_event automation events through a separate two-second metadata coordinator, with duplicate/startup suppression and stale-state handling. Preserve existing camera entity IDs and group camera/detection entities together.
- Honor explicit false/zero add-on options instead of replacing them with defaults.
- Preserve read-only panel media access and separate HA talk permissions. Show negotiated codec and camera microphone support; RTSP G711 return audio is still model/firmware dependent. Discover optional ONVIF manufacturer/model/firmware, including Uniview devices.
- Validate generated H264/H265 media, original HEVC recording, shared-source analysis/microphone, real local-model inference, SQLite/ONVIF/HA behavior and responsive event-to-recording playback. No physical Uniview camera or speaker was available for acoustic/firmware testing.

## 0.10.48

- Route native EdgeRTC notices, preview URLs, live-toggle diagnostics and timeout guidance through the same Home Assistant ingress/WebSocket plan. Remove the incorrect MediaMTX/public-port warning when native EdgeRTC is running on Nabu Casa or another HTTPS host.
- Hide unused MediaMTX public URL/ICE/TURN and legacy playback-engine controls in native mode, while preserving their saved values for switching back. Show native HTTPS/WSS guidance in Network settings.
- Add browser regressions for native and legacy remote access, Nabu Casa ingress prefixes, other HTTPS hosts, saved hidden settings and four screen widths.

## 0.10.47

- Treat client resets, aborted connections and broken pipes during HTTP request handling as closed client connections, including interrupted recording-status response headers/bodies. Keep other I/O exceptions visible.
- Distinguish an insecure HTTP browser context from missing WebCodecs support. Show actionable HTTPS/Home Assistant Cloud guidance in Polish or English; release microphone tracks and stop automatic card retries for these browser requirements. Native EdgeRTC still requires HTTPS/localhost and WebCodecs.
- Add real-browser HTTP/HTTPS support and permanent-error tests, plus HTTP recording-status disconnect regressions. Measure RTSP replacement ordering against completion of the previous source/client, since a remote TCP-close callback can be delivered after the replacement connection is accepted.

## 0.10.46

- Release camera/settings save buttons before refreshing auxiliary status; bound save and verification requests and retain edited forms after a timeout or error.
- Add a separate streaming-engine selector at the top of Edge Settings. Persist only the chosen engine to the app’s Supervisor options, verify the value, show saved versus running engine and explain the required app/Core restart. Serialize local options updates so camera saves preserve the selected engine.
- Mark camera credentials/endpoints and unused remote-access strings optional in the app configuration schema.
- Add regression coverage for hung status refreshes, save/verification timeouts, failed writes, engine persistence and credential-free responses.

## 0.10.45

- Add opt-in native EdgeRTC beta: a Go source registry/RTSP relay and EdgeRTC/1 encoded-frame protocol over WebSocket, decoded with browser WebCodecs. The native path uses neither WebRTC nor MSE and does not re-encode video. Existing core remains the default.
- Enforce one source/reconnect owner per camera recording profile, merge duplicate exact URLs, share it with recorder/probes/viewers and supported G711 RTSP microphone backchannels, and close old sources before applying replacements. Bound viewer queues, cached GOP size and microphone ownership.
- Add an authenticated HA binary bridge with separate read/control permissions, backend-only token handling, native card rendering/audio/microphone, and a read-only Edge panel tunnel. Expose current source counts and attempts in diagnostics.
- Build static native binaries for amd64/ARM64; add Go race tests and CI builds, runtime dependency license notices, native configuration/privacy tests and real browser H264/PCM/NVR coverage.
- Native live currently requires HTTPS/localhost, WebCodecs and H264; microphone requires camera RTSP G711 backchannel support. Native PiP, proprietary talk APIs, QUIC/WebTransport, PTZ/event adapters and local AI are not implemented in this stage. Warm-source local first-image measurements around 30–300 ms are not glass-to-glass latency or a universal WebRTC performance comparison.

## 0.10.44

- Route the existing Home Assistant camera entity through Edge NVR/MediaMTX using the recording profile. Discover the add-on internal hostname and export credential-free backend restream metadata; keep the host RTSP port unpublished by default. Missing NVR metadata never falls back to a direct camera connection.
- Add Main/Sub profile entities and a temporary direct Talk entity, preserving existing base unique IDs and publishing actual registered entity IDs for quality switching. Default NVR viewing shares the recorder feed without downscaling.
- Support 1–4 cameras in Edge Live, per-camera NVR/Main/Sub selection, decoded resolution display, responsive columns, focus-one/all views, suspended hidden players and one active microphone across the grid. Keep single-camera YAML compatible.
- Extend the visual editor and panel YAML builder to select 1–4 cameras and quality; retain the standard HA card/grid alternative.
- Return from direct talk to NVR after microphone stop/rejection/disconnection. Guard stale playback promises during stream changes. MediaMTX does not carry camera backchannels; direct talk and separate main/sub profiles can add upstream sessions.
- Move RTSP status probes and FFmpeg still-frame capture onto the shared proxy. Explicitly disable unused MoQ listeners in both runtime templates.
- Validate with 107 unit tests, responsive card/builder browser suites and a real MediaMTX test showing 3 readers plus a probe share one upstream RTSP session with unchanged dimensions. Browser WebRTC transport and physical camera speakers remain untested in this environment.

## 0.10.43

- Bundle and automatically register the Edge Live custom Home Assistant card with a visual editor and authenticated native HA WebRTC signaling. Add a card-type selector and copyable custom-card YAML to the panel.
- Add live start/stop, mute/volume, explicit microphone capture, image fit, echo cancellation, reconnect, fullscreen, browser picture-in-picture and frame export controls. Keep the player intact during entity state updates.
- Negotiate two-way audio through the Home Assistant WebRTC provider, report unsupported return audio, and release microphone tracks on stop, removal, page hiding, failure and cancelled permission requests. HTTPS and compatible camera/provider backchannel support are required.
- Load the frontend module once, version its resource URL, retain stable camera IDs and private RTSP source updates, and keep the standard Home Assistant camera card available.
- Add browser WebRTC loopback/signaling-only coverage and integration registration tests; physical camera backchannels and a production Home Assistant installation are not exercised by the test fixture.

## 0.10.42

- Organize navigation into Live view, Recordings, Cameras, Settings, Home Assistant and Diagnostics, removing the unused account placeholder.
- Group camera setup into connection, discovery, live/recording and advanced fields. Preserve expanded camera sections, open newly added cameras, and show unsaved changes next to sticky save actions.
- Put recording preferences first, with one visible retention value. Move storage paths, network/ICE, performance and server options into named expandable sections.
- Add a Home Assistant dashboard-card builder with exact registered entity IDs, YAML preview, clipboard copy and setup instructions. Export only camera/entity metadata; generated cards contain no RTSP credentials.
- Enable native Home Assistant camera streaming through stream_source and CameraEntityFeature.STREAM, declare stream/ffmpeg dependencies and provide on-demand snapshot fallback. Refresh existing stream sources after camera settings change and add new camera entities dynamically.
- Add native-camera lifecycle/source tests, credential-free entity mapping tests and responsive UI/clipboard/save-roundtrip browser coverage.

## 0.10.41

- Keep the actual last video frame over a segment load and remove the poster during playback, preventing flashes back to a thumbnail between MP4 files.
- Rebuild camera RTSP credentials/hosts consistently while preserving explicit paths and ports; encode reserved characters in credentials.
- Restart only affected recorders after connection, stream or recording settings change. Scheduled recorder work reloads the latest saved config. Reload a managed Janus child when its mount configuration changes; MediaMTX reloads its written configuration.
- Mirror saved camera settings to the add-on's own Supervisor options, preserving unrelated options and verifying the result. Add a generic cameras list while retaining the first two legacy camera option fields. This uses the default Supervisor role and only self/info and self/options endpoints.
- Publish updated camera details immediately while refreshing connection probes in the background, and discard probe results from superseded settings.
- Add RTSP profiles for Uniview, Reolink, Axis, Hanwha, Amcrest, Foscam, TP-Link Tapo and VIGI, plus generic ONVIF/RTSP support for other compatible cameras. Discover ONVIF Media profiles and main/sub RTSP URIs using authenticated SOAP with PasswordDigest.
- Add regression coverage for vendor URLs, special passwords, recorder restarts, Supervisor projection/verification, ONVIF discovery, stale probe protection and browser transitions with delayed video responses.

## 0.10.40

- Show one playback counter and seek bar for all video recorded in the selected day, with play/pause, skip, mute and speed controls. Load the required segment and continue through the day on the same video element instead of showing a seven-second native clip timeline.
- Keep existing players, pause state, speed and position during timer/manual status refreshes and live-tail updates. Switching one camera's day leaves other players intact, and day selections made during an in-flight refresh are queued.
- Fix invisible day thumbnails, incorrectly quoted placeholder image URLs, lingering error overlays and collapsed preview sizing. Queue at most two thumbnail loads per client.
- Use wider NVR cards, fixed 16:9 previews, touch-sized controls and responsive grids. Fix horizontal overflow on landscape tablets and support small 320px screens.
- Read MP4 movie-header duration directly to synchronize the recorded-day length with actual footage instead of gaps between filename timestamps, while caching duration lookups and retaining FFprobe fallback.
- Add MP4 metadata regression tests and a reproducible five-viewport browser suite covering real playback, all segment transitions, refresh stability, growing days and thumbnails.

## 0.10.39

- Save both camera and Edge forms together, verify the full persisted response, and keep the form on request failures. Serialize configuration saves and mirror updates and use unique, flushed temporary files for atomic JSON writes.
- Keep an existing edge.json authoritative, including an empty camera list. Report damaged JSON instead of automatically restoring stale mirror/backup settings. Preserve explicit empty optional WebRTC settings.
- Browse recordings as calendar days with a full-day seek bar, day navigation and sampled video previews. Keep every segment of the selected day visible regardless of the optional playback-cache limit.
- Measure irregular legacy clip lengths and the final clip with cached FFprobe results, fix continuous-stream seeking, and reject stale day caches whose offsets may refer to only a portion of a day.
- Load day thumbnails with bounded concurrency, retry missing short-clip thumbnails at the first frame, and use independent temporary thumbnail files.
- Handle local calendar-day boundaries across daylight-saving changes. Retention remains unchanged at 14 days by default.

## 0.10.38

- Run recording retention at startup and every hour, even while FFmpeg records continuously or cameras are disabled/removed.
- Remove only expired recorder-named MP4 segments under the configured recordings directory; skip symlinks and report filesystem errors.
- Use the storage retention setting when the NVR override is absent (default: 14 days).

## 0.10.37

- Infer NVR segment duration from neighboring recording timestamps, so short 4-second camera-generated chunks still map into one continuous timeline instead of being treated as broken single clips.
- Force multi-file NVR playback through the continuous recorded-day stream, even when each individual MP4 segment is shorter than the configured segment length.
- Let recording thumbnail generation try any ready segment directly through FFmpeg instead of blocking on the legacy MP4 header probe.

## 0.10.36

- Restore NVR recording lists even when saved MP4 segments have unusual metadata placement. Playback readiness now uses only age and size, while MP4 header checks are limited to thumbnail generation.
- Keep thumbnail safety for incomplete/corrupt MP4 files without allowing thumbnail failures to hide the actual recordings.

## 0.10.35

- Relax MP4 readiness after the 0.10.34 safety check: Edge now scans both the beginning and end of each recording segment for MP4 metadata, and older `ftyp` segments are allowed after a short grace window. This restores NVR playback for valid files whose `moov` atom is not at the beginning.
- Expose the MP4 header grace window in diagnostics so future NVR OFF/empty-list issues are easier to confirm from logs.

## 0.10.34

- Stop treating stale active-day NVR cache files as playable. Today's view now falls back to the recorded-day stream whenever the daily cache is not current, preventing old cache timelines from mixing yesterday/today or starting at the wrong offset.
- Require closed browser-playable MP4 headers before a recording segment appears in NVR playback or thumbnail generation. This avoids `moov atom not found` segments and prevents corrupt/incomplete files from breaking the filmstrip.
- Improve the mobile NVR filmstrip placeholder so missing thumbnails render as a clear preview/loading state instead of a stack of thin empty lines.
- Expose MP4 readiness thresholds and active-day stale-cache state in diagnostics.

## 0.10.33

- Fix NVR day/playback offset mixing: selected-day status now recalculates playback offsets from that day instead of inheriting offsets from older days.
- Stop rebuilding the huge playback cache for the active recording day; today's NVR view uses the saved segment stream while closed days can be cached after midnight.
- Make the NVR player derive `timeline_start` and `playback_start` from the same selected target, preventing yesterday/today jumps and wrong seek starts.
- Reduce false red diagnostics for successful recording-cache builds and expose the active-day cache defer reason in runtime parameters.

## 0.10.32

- Restore full-day NVR playback fallback: when the daily playback cache is missing, stale, or too short, the panel now uses the recorded-day stream built from saved clips instead of dropping to a single 10-12 second MP4 segment.
- Keep NVR seek math explicit by sending both playback offset and wall-clock timeline offset to `/recordings-stream/...`, making logs easier to read and preventing day-offset/playback-offset confusion.
- Preserve compatibility with older `continuous_stream` browser state while new renders use the clearer `recorded_day_stream` playback mode.
- Add cache-busting thumbnail URLs and a low-impact background thumbnail warmup loop so recording lists can recover from stale or missing preview images without overloading the CPU.
- Expose thumbnail warmup limits in runtime diagnostics.

## 0.10.31

- Keep NVR playback on recorded media only: when the daily cache is not ready, the panel now falls back to saved MP4 segments instead of the continuous live-style stream.
- Use saved MP4 segments for the tail of a stale daily cache, so today's newest recorded clips remain playable while the full-day cache catches up.
- Preserve a user-selected recording day during timer refreshes so older days cannot be silently replaced by the newest day.
- Scale the recording cache FFmpeg timeout by file count and recorded duration, preventing multi-thousand-segment camera timelines from being killed too early.
- Expose cache timeout and one-second recording live-edge delay values in runtime diagnostics.

## 0.10.30

- Protect the NVR video element while the user is seeking, paused, or interacting with playback controls so timer refreshes do not rebuild the player and jump back to the original position.
- Slow the NVR status timer from 5 seconds to 15 seconds and throttle timer debug events to reduce UI, disk, and CPU load while playback is stable.
- Add seek/play/pause/ratechange diagnostics that preserve playback state and make accidental NVR rerenders visible in the Logs tab.
- Color every raw Logs tab block as OK, WARNING, or ERROR instead of only coloring the top diagnostics list.
- Expand runtime diagnostics with panel process status, thread/file descriptor counts, system and process uptime, active recorder details, cache backlog, runtime limits, and engine ports.

## 0.10.29

- Change the NVR timeline to a real wall-clock day view: selected days now expose a full `00:00 -> 24:00` timeline while still showing how many hours were actually recorded.
- Add separate `timeline_offset` and `playback_offset` metadata so the UI can seek by time of day while the player seeks correctly inside the fast cached MP4.
- Map playback progress from server cache and continuous streams back to the selected day's wall-clock timeline, avoiding jumps caused by gaps between recording clips.
- Add colored panel diagnostics: green OK, yellow warnings, and red errors for FFmpeg/FFprobe, CPU load, memory, disk, recording workers, cache work, UI errors, and log tails.
- Log global browser UI errors and unhandled promises into Edge debug diagnostics so mobile panel failures are visible from the Logs page.

## 0.10.28

- Reduce NVR CPU spikes by building only one daily playback cache at a time and running the FFmpeg cache build at low OS priority.
- Rebuild stale daily playback cache files in batches instead of after every new 10-second recording segment.
- Switch daily playback cache assembly to stream-copy remuxing because recorded segments are already browser-oriented MP4 files.
- Stop enabling MediaMTX's built-in recorder by default, avoiding duplicate recording work while the Edge NVR recorder remains the single source used by the panel.
- Avoid rewriting unchanged MediaMTX and Janus runtime configs so saves do not unnecessarily reload the live core and interrupt NVR/live paths.
- Serve saved MP4 recordings and cache timelines with immutable range-friendly cache headers for faster seeking and less repeated disk/network work.

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

