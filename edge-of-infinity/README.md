# Edge of Infinity

Private low-latency live NVR core for Home Assistant.

This app runs Edge Core and exposes the Edge web UI through Home Assistant Ingress.

## Features

- Sidebar panel through Ingress.
- Supervisor logs.
- Watchdog health endpoint.
- Persistent config and media mounts.
- Optional public API port.

## First Configuration

Create an Edge Core config at:

```text
/addon_configs/<repo>_edge_of_infinity/edge.json
```

Inside the app container this file is mounted as:

```text
/config/edge.json
```

Recordings default to:

```text
/media/edge-of-infinity/recordings
```

## Notes

This package currently contains the Home Assistant app shell. The actual Edge Core binary will be bundled once the RTSP/WebRTC MVP is implemented.
