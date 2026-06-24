# Publish Edge of Infinity Home Assistant App

This repository is the one Home Assistant should install as an App/Add-on repository.

## Recommended GitHub Repository

```text
edge-of-infinity-home-assistant-app
```

## Publish

Create an empty GitHub repository first, then run:

```bash
cd edge-of-infinity-home-assistant-app
git init
git add .
git commit -m "Initial Home Assistant app"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USER/edge-of-infinity-home-assistant-app.git
git push -u origin main
```

## Add To Home Assistant

In Home Assistant:

```text
Settings -> Apps / Add-ons -> App Store -> Repositories
```

Add:

```text
https://github.com/YOUR_GITHUB_USER/edge-of-infinity-home-assistant-app
```

Then install:

```text
Edge of Infinity
```

## Expected Home Assistant Features

- App/Add-on page.
- Logs tab.
- Sidebar panel through Ingress.
- Watchdog health check.
- Config options in the app page.

## Important

This app shell currently uses a placeholder health server when the real `edge-core` binary is not bundled yet.

Once Edge Core has a working RTSP/WebRTC MVP, the Dockerfile should build or copy the `edge-core` binary into the image.
