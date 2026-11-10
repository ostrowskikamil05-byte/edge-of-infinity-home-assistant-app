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

Version 0.15.1 improves indexed DVR seeking and cellular Live and adds transparent
branding. It retains the recordings/events dashboard cards and theme support
from 0.15.0. Restart Home Assistant Core after updating to load the cards.

See [configuration, migration and playback](DOCS.md).
