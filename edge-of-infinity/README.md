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


### Recording storage

Recordings preserve the camera video codec, resolution and FPS by default. Legacy automatic playback settings migrate to original-video recording; an explicitly selected H264 compatibility conversion remains available and can increase file size or change image quality. Existing recordings are not recompressed. H265 playback depends on the viewing browser/device.

The day player reads the original segments as one timeline. Full-day playback copies are disabled by default (`nvr.playback_cache_mb: 0`), so viewing another day does not duplicate that day's video on disk. Optional copies have a global byte budget, free-space checks and a two-day idle lifetime. The combined thumbnail cache defaults to 32 MiB, at most 2048 images and 48 hours without access; images regenerate from retained footage. Its dimensions and encoding quality are unchanged.

In Recording settings, usage separates original footage, playback copies and thumbnails. **Posprzątaj cache i miniatury** removes recognized generated files and trims logs; it does not delete original recordings or change the recording-retention setting. Automatic maintenance also runs on startup and hourly. The deprecated MediaMTX recording switch no longer creates a second archive; legacy archive files are retained until the configured retention removes them.

A camera bitrate of 4 Mbit/s produces approximately 43.2 GB per day, or 604.8 GB over 14 days, before small container/audio overheads. Already-compressed camera footage has no guaranteed further size reduction without losing detail. Storage estimates in the panel use recent recorded segment sizes/durations and vary with bitrate, scene and recording continuity.

The NVR uses one seek bar over the video image. Hover to preview a recorded thumbnail; drag to change the actual video frame before releasing. Playback pauses during a drag and resumes only if it was playing beforehand. Arrow keys preview while held and Escape restores the position at the start of the gesture. The same controls remain available in fullscreen; narrow players place speed and skip controls in **Playback settings**.
