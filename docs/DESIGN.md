# Home Assistant App Design

## Goal

Run Edge of Infinity as a first-class Home Assistant app:

- visible in the app/add-on store,
- manageable by Supervisor,
- logs available in Home Assistant,
- sidebar panel through Ingress,
- automatic watchdog checks.

## Runtime Shape

```text
Home Assistant Supervisor
  -> Edge of Infinity App container
     -> Edge Core
     -> Edge Web UI
     -> recordings/config under persistent mounts
```

## Ingress

Ingress lets the user open Edge of Infinity from the Home Assistant sidebar without exposing a separate public port.

The app should serve the UI on internal port `8088`.

## Watchdog

Supervisor watchdog should call:

```text
http://[HOST]:[PORT:8088]/health
```

If Edge Core stops responding, Supervisor can mark the app unhealthy and restart it depending on user settings.

## Logs

All Edge Core logs should be written to stdout/stderr.

Home Assistant Supervisor captures those logs and shows them in the app log tab.

## Storage

Use:

- `/data` for app runtime state and Supervisor options.
- `/config` for app config files via `addon_config:rw`.
- `/media/edge-of-infinity` for recordings if the user chooses media storage.

## Security

Default should be private:

- Ingress enabled.
- Public port optional.
- No direct camera exposure.
- API token required once auth is implemented.
