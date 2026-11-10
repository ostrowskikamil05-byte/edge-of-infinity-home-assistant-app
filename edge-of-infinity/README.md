# Edge of Infinity

Native EdgeRTC Live, recording and indexed DVR for Home Assistant.

- One shared camera source for Live, recording, compatible microphone and analysis.
- Original H.264/H.265 resolution/FPS, source AAC copy and 14-day retention.
- Daily DVR timeline, frame steps, sound, speed, full screen and event markers.
- Authenticated HA HTTPS/WebSocket access, including Nabu Casa.
- Optional camera/local detection, zones, alert clips and phone notifications.
- Native card with 1–4 views and separate microphone permissions.

Version 0.14.0 removes retired streaming engines and migrates their settings while
preserving cameras and saved recordings. DVR AAC uses bounded buffering and
backpressure without changing Live audio behavior.

See [configuration, migration and playback](DOCS.md).
