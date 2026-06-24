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

The optional Home Assistant custom integration can still be used later to expose cameras, sensors, and WebRTC streams as native Home Assistant entities.

The app is the engine. The integration is the bridge.
