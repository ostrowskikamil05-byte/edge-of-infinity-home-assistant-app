# Test In Home Assistant

## 1. Publish The Repo

Publish this repository to GitHub.

Example URL:

```text
https://github.com/YOUR_GITHUB_USER/edge-of-infinity-home-assistant-app
```

## 2. Add Repository In Home Assistant

Go to:

```text
Settings -> Apps / Add-ons -> App Store
```

Open repositories and add:

```text
https://github.com/YOUR_GITHUB_USER/edge-of-infinity-home-assistant-app
```

## 3. Install The App

Find:

```text
Edge of Infinity
```

Install it.

## 4. Start

Start the app.

Check:

- Logs tab shows startup logs.
- Sidebar shows `Edge Infinity`.
- Watchdog checks `/health`.
- App configuration is visible.

## 5. Known Current Limitation

The real video engine is not bundled yet.

The app currently runs a placeholder health server if `edge-core` is missing. This is intentional for testing Home Assistant packaging first.
