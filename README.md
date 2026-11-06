# Edge of Infinity Home Assistant App

Home Assistant App repository for Edge of Infinity.

This is the correct packaging when Edge of Infinity should appear in Home Assistant under:

```text
Settings -> Apps / Add-ons
```

It provides:

- Supervisor-managed install.
- Logs visible in Home Assistant.
- Sidebar panel through Ingress.
- Watchdog health checks.
- Persistent app configuration.
- Optional host API port for integrations and local tools.

## Install Later

After publishing this repository on GitHub:

1. Open Home Assistant.
2. Go to Settings -> Apps / Add-ons -> App Store.
3. Open repositories.
4. Add the GitHub URL for this repository.
5. Install Edge of Infinity.

## Relation To The Integration

This app runs Edge Core.

The bundled Home Assistant integration exposes cameras and sensors, and automatically registers the Edge Live dashboard card. It supports 1–4 cameras, NVR/Main/Sub quality selection, audio controls and optional microphone talk. Live viewing shares the NVR recording feed by default. After an add-on update, restart Home Assistant Core to load the integration and refresh the frontend. See [setup and limitations](edge-of-infinity/DOCS.md#shared-nvr-live-streams-and-14-cameras-01044).

The app is the engine. The integration is the bridge.

