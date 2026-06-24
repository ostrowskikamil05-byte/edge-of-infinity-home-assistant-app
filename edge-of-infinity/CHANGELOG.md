# Changelog

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
