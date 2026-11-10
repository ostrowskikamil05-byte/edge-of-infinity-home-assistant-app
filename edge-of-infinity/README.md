# Edge of Infinity

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="logo.png">
  <img src="logo-light.png" alt="Edge of Infinity — CCTV &amp; Live Stream" width="280">
</picture>

Native EdgeRTC Live, recording and indexed DVR for Home Assistant.

- One shared camera source for Live, recording, compatible microphone and analysis.
- Original H.264/H.265 resolution/FPS, source AAC copy and 14-day retention.
- Daily DVR timeline, frame steps, sound, speed, full screen and event markers.
- Authenticated HA HTTPS/WebSocket access, including Nabu Casa.
- Optional camera/local detection, zones, alert clips and phone notifications.
- Native card with 1–4 views and separate microphone permissions.
- Dashboard cards for recordings and events that adapt to phone, tablet and desktop.

Version 0.16.0 moves panel and HA card playback to native EdgeRTC DVR, retains
original MP4 downloads and reduces competition between offscreen players,
thumbnail requests and visible streams. Vertical dashboard swipes no longer
seek the archive. Existing card YAML, themes and interface languages remain
available. Restart Home Assistant Core and reopen the panel/dashboard after
updating to load the integration and frontend together.

See [configuration, migration and playback](DOCS.md).
