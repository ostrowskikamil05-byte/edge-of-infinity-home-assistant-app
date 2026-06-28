# Edge of Infinity

Private low-latency live NVR core for Home Assistant.

This app runs Edge Core and exposes the Edge web UI through Home Assistant Ingress.

## Features

- Sidebar panel through Ingress.
- Supervisor logs.
- Watchdog health endpoint.
- Persistent config and media mounts.
- Optional public API port.
- MediaMTX + Janus WebRTC live core.
- Multi-camera configuration with Hikvision-first defaults.
- RTSP reachability checks for enabled cameras.

## First Configuration

Use the Edge of Infinity sidebar panel as the preferred camera configuration UI. The panel exposes Hikvision camera slots with host, credentials, RTSP main/sub, ONVIF/ISAPI, recording, low-latency, and snapshot stream settings.

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

This package now runs the Edge panel as the controller and MediaMTX + Janus as the live core. MediaMTX rebroadcasts configured RTSP streams inside the add-on; Janus is prepared for WebRTC gateway workflows.

The add-on creates `/homeassistant/edge/edge.json` with starter Hikvision camera slots. Camera Settings in the panel is the preferred editor and preserves saved connection fields when a form submits blank technical values.

Enabled cameras are probed through the configured stream. Browser live preview should use MediaMTX WebRTC on port `8889`; the old MJPEG/JPEG preview paths are no longer the panel's live route.
