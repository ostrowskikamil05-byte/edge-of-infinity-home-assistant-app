# NVR browser regression tests

Requires Python 3.12+, FFmpeg/FFprobe, Node.js and Playwright with Chromium.
The fixture uses temporary configuration and generated seven-second MP4s, with
camera/network/runtime callbacks disabled. It never connects to Home Assistant.

Install browser test dependencies outside the repository:

```sh
npm install --prefix /tmp/edge-browser-tests playwright
NODE_PATH=/tmp/edge-browser-tests/node_modules /tmp/edge-browser-tests/node_modules/.bin/playwright install chromium
```

Start the isolated server in one terminal:

```sh
python tests/browser/fixture_server.py
```

In another terminal:

```sh
NODE_PATH=/tmp/edge-browser-tests/node_modules node tests/browser/nvr_playback.cjs
```

The suite checks 1440px desktop, 820px portrait tablet, 1180px landscape tablet,
390px phone and 320px small-phone viewports. It verifies a 21-second combined
player, automatic transitions through three actual seven-second MP4s, seeking,
date switches, visible image previews and their aspect ratios, no horizontal
overflow, and stable paused playback when the day grows during a status update.

Stop the fixture server with Ctrl+C. Optional environment variables:
`EDGE_NVR_TEST_PORT`, `EDGE_NVR_TEST_URL`, `EDGE_NVR_FIXTURE_INFO`,
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`, and `EDGE_NVR_SCREENSHOT_DIR`.
Create the screenshot directory before setting it. Use the fixture server only
with this test suite, not with a production URL (the tests add/remove a fixture MP4).
