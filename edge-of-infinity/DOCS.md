# Edge of Infinity App Documentation

## Sidebar

The app enables Home Assistant Ingress and adds an Edge Infinity item to the sidebar for administrators. The sidebar panel can edit Hikvision camera settings, refresh RTSP status, and open MediaMTX/Janus live preview paths.

## Logs

Open the app page in Home Assistant and use the Logs tab. Edge Core should write all logs to stdout/stderr so Supervisor can capture them.

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

The JSON file below stores panel changes and is still useful for manual recovery.

On first start, the app creates:

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

`edge.example.json` may be refreshed by the app. Your real camera settings belong in `/homeassistant/edge/edge.json`, visible in File Editor as `/config/edge/edge.json`.

The app must not overwrite an existing `edge.json`.

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

Starting with version `0.8.6`, `/homeassistant/edge/edge.json` remains the source of truth after it exists. Add-on options are copied only to `/tmp/edge-runtime/edge.options.json` for diagnostics and no longer overwrite panel changes on restart. Stream role choices are also persisted in:

```text
/homeassistant/edge/stream-overrides.json
```

Those overrides are applied on every config load so `tile`, `live`, `record`, and `snapshot` selections cannot bounce back when another source rewrites `edge.json`.

Starting with version `0.8.3`, the old shell placeholder page and MJPEG status generator were removed from the runner. The Edge panel is the controller, while MediaMTX + Janus is the live path.

Camera cards show a small rounded status badge:

```text
online
offline
lost connection
```

The `lost connection` state appears when a camera was previously online and the next RTSP probe fails.

## Hikvision Autoconfig

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
