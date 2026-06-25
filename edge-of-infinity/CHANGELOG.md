# Changelog

## 0.4.24

- Add a separate `Recording stream` setting so NVR can use either `main` or `sub`.
- Show `Record stream` and redacted `Record RTSP` in the NVR panel.
- Restore strict Hikvision stream mapping: `main` rewrites RTSP to channel `101`, and `sub` rewrites RTSP to channel `102`.
- Remove the manual Hikvision sub-channel option so stream selection stays controlled by `main` or `sub`.
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
