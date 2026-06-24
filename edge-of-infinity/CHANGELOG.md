# Changelog

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
