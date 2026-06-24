# Edge of Infinity App Documentation

## Sidebar

The app enables Home Assistant Ingress and adds an Edge Infinity item to the sidebar for administrators. The sidebar panel can edit Hikvision camera settings, refresh RTSP status, and open an experimental MJPEG live preview.

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

Restart the app to refresh the current shell panel. The real video engine will use the same effective camera config once `edge-core` is bundled.

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

## Experimental Live Preview

Starting with version `0.4.6`, the panel uses refreshed JPEG live frames by default:

```text
/live-frame/<camera_id>.jpg
```

This avoids browser and Home Assistant Ingress problems with multipart MJPEG. When `Start live` is enabled, the panel keeps requesting fresh frames from the selected `sub` or `main` RTSP stream.

The older diagnostic MJPEG path is still available:

```text
/live/<camera_id>.mjpg
```

Both paths use FFmpeg and are stepping stones for testing the live pipeline from the Edge panel. The final low-latency engine should use WebRTC.

Starting with version `0.4.8`, live preview targeting uses the camera slot index in addition to the camera id. This prevents a duplicated or stale camera id from opening the wrong stream. Camera cards also show a small rounded status badge:

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
