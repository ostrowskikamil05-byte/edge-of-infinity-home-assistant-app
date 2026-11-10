# Edge of Infinity

Native EdgeRTC Live, recording and indexed DVR for Home Assistant.

- One shared camera source for Live, recording, compatible microphone and analysis.
- Original H.264/H.265 resolution/FPS, source AAC copy and 14-day retention.
- Daily DVR timeline, frame steps, sound, speed, full screen and event markers.
- Authenticated HA HTTPS/WebSocket access, including Nabu Casa.
- Optional camera/local detection, zones, alert clips and phone notifications.
- Native card with 1–4 views and separate microphone permissions.
- Dashboard cards for recordings and events that adapt to phone, tablet and desktop.

Version 0.15.0 adds the `custom:edge-recordings-card` and
`custom:edge-events-card` dashboard cards. The panel, recorder and Live path are
unchanged; restart Home Assistant Core after updating to load the cards.

See [configuration, migration and playback](DOCS.md).
