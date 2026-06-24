# Edge of Infinity

Private low-latency live NVR core for Home Assistant.

This app runs Edge Core and exposes the Edge web UI through Home Assistant Ingress.

## Features

- Sidebar panel through Ingress.
- Supervisor logs.
- Watchdog health endpoint.
- Persistent config and media mounts.
- Optional public API port.
- Multi-camera shell with two Hikvision slots.
- RTSP reachability checks for enabled cameras.

## First Configuration

Use the Edge of Infinity add-on options page as the preferred camera configuration UI. The add-on exposes two Hikvision camera slots with host, credentials, RTSP main/sub, ONVIF/ISAPI, recording, low-latency, and snapshot stream settings.

If RTSP main/sub are left empty, Edge builds the standard Hikvision paths from host, username, and password.

The legacy fallback config is:

```text
/homeassistant/edge/edge.json
```

In Home Assistant File Editor this is shown as:

```text
/config/edge/edge.json
```

The database defaults to:

```text
/homeassistant/edge/edge.db
```

Recordings default to:

```text
/media/edge-of-infinity/recordings
```

## Notes

This package currently contains the Home Assistant app shell. The actual Edge Core binary will be bundled once the RTSP/WebRTC MVP is implemented.

The shell creates `/homeassistant/edge/edge.json` with two Hikvision camera slots so the app can be configured for multiple cameras before the real engine is bundled.

Enabled cameras are probed through `rtsp_main` once when the add-on starts. The shell also captures one JPEG snapshot through the per-camera `snapshot_stream` setting, either `sub` or `main`. This verifies camera connectivity before WebRTC live is implemented without refreshing the sidebar page.
