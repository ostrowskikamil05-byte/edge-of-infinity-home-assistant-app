#!/usr/bin/env python3
"""Edge of Infinity panel server."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


HOME_DIR = Path(os.environ.get("EDGE_HOME_DIR", "/homeassistant/edge"))
DATA_DIR = Path(os.environ.get("EDGE_DATA_DIR", "/tmp/edge-placeholder"))
CONFIG_PATH = Path(os.environ.get("EDGE_HOME_CONFIG", "/homeassistant/edge/edge.json"))
CONFIG_BACKUP_PATH = HOME_DIR / "edge.backup.json"
PRESETS_PATH = HOME_DIR / "camera-presets.json"
PORT = int(os.environ.get("API_PORT", "8088"))
SNAPSHOT_DIR = HOME_DIR / "snapshots"
DATA_SNAPSHOT_DIR = DATA_DIR / "snapshots"


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value or "camera")


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def backup_config() -> None:
    if CONFIG_PATH.exists():
        shutil.copyfile(CONFIG_PATH, CONFIG_BACKUP_PATH)


def build_rtsp(explicit: str, host: str, username: str, password: str, channel: str) -> str:
    if explicit:
        return explicit
    if host and username and password:
        return f"rtsp://{username}:{password}@{host}:554/Streaming/Channels/{channel}"
    return ""


def normalize_camera(raw: dict, index: int) -> dict:
    camera_id = raw.get("id") or f"hikvision_{index}"
    name = raw.get("name") or f"Hikvision {index}"
    host = raw.get("host") or ""
    username = raw.get("username") or "admin"
    password = raw.get("password") or ""
    rtsp_main = build_rtsp(raw.get("rtsp_main") or "", host, username, password, "101")
    rtsp_sub = build_rtsp(raw.get("rtsp_sub") or "", host, username, password, "102")
    snapshot_stream = raw.get("snapshot_stream") if raw.get("snapshot_stream") == "main" else "sub"

    onvif_url = raw.get("onvif_url") or (f"http://{host}:80/onvif/device_service" if host else "")
    isapi_base_url = raw.get("isapi_base_url") or (f"http://{host}" if host else "")

    return {
        "id": camera_id,
        "name": name,
        "vendor": raw.get("vendor") or "hikvision",
        "host": host,
        "username": username,
        "password": password,
        "rtsp_main": rtsp_main,
        "rtsp_sub": rtsp_sub,
        "onvif_url": onvif_url,
        "isapi_base_url": isapi_base_url,
        "enabled": bool(raw.get("enabled")),
        "record": bool(raw.get("record", True)),
        "low_latency": bool(raw.get("low_latency", True)),
        "snapshot_stream": snapshot_stream,
    }


def normalize_config(payload: dict) -> dict:
    cameras = payload.get("cameras") if isinstance(payload, dict) else []
    if not isinstance(cameras, list):
        cameras = []

    normalized = [normalize_camera(camera, index + 1) for index, camera in enumerate(cameras[:8])]
    return {
        "server": payload.get("server") or {"listen": "0.0.0.0:8088", "public_url": ""},
        "storage": payload.get("storage")
        or {
            "recordings_dir": "/media/edge-of-infinity/recordings",
            "database_path": "/homeassistant/edge/edge.db",
            "retention_days": 14,
        },
        "cameras": normalized,
        "future_vendors": payload.get("future_vendors") or ["dahua", "onvif", "rtsp"],
    }


def preset_key(camera: dict) -> str:
    return camera.get("host") or camera.get("rtsp_main") or camera.get("id") or camera.get("name") or ""


def preset_camera(camera: dict) -> dict:
    return {
        key: camera.get(key)
        for key in (
            "id",
            "name",
            "vendor",
            "host",
            "username",
            "password",
            "rtsp_main",
            "rtsp_sub",
            "onvif_url",
            "isapi_base_url",
            "enabled",
            "record",
            "low_latency",
            "snapshot_stream",
        )
    }


def load_presets() -> list[dict]:
    payload = read_json(PRESETS_PATH, [])
    if isinstance(payload, dict):
        payload = payload.get("presets", [])
    if not isinstance(payload, list):
        return []
    presets = []
    for index, camera in enumerate(payload):
        if isinstance(camera, dict) and (camera.get("host") or camera.get("rtsp_main")):
            presets.append(normalize_camera(camera, index + 1))
    return presets


def save_presets(presets: list[dict]) -> None:
    write_json(PRESETS_PATH, [preset_camera(camera) for camera in presets[:20]])


def remember_camera_presets(cameras: list[dict]) -> None:
    presets = load_presets()
    by_key = {preset_key(camera): camera for camera in presets if preset_key(camera)}

    for camera in cameras:
        if not isinstance(camera, dict) or not (camera.get("host") or camera.get("rtsp_main")):
            continue
        normalized = normalize_camera(camera, len(by_key) + 1)
        key = preset_key(normalized)
        if key:
            by_key[key] = normalized

    save_presets(list(by_key.values()))


def validate_config_for_save(payload: dict) -> None:
    cameras = payload.get("cameras") if isinstance(payload, dict) else None
    if not isinstance(cameras, list) or not cameras:
        raise ValueError("Refusing to save empty camera configuration.")
    meaningful = [
        camera
        for camera in cameras
        if isinstance(camera, dict) and (camera.get("host") or camera.get("rtsp_main"))
    ]
    if not meaningful:
        raise ValueError("Refusing to save cameras without host or RTSP.")


def load_config() -> dict:
    config = normalize_config(read_json(CONFIG_PATH, {"cameras": []}))
    if not config.get("cameras") and CONFIG_BACKUP_PATH.exists():
        backup = normalize_config(read_json(CONFIG_BACKUP_PATH, {"cameras": []}))
        if backup.get("cameras"):
            write_json(CONFIG_PATH, backup)
            return backup
    return config


def run_json(command: list[str], timeout: int) -> dict | None:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def capture_snapshot(camera: dict, target_id: str) -> tuple[str, str]:
    stream = camera_stream(camera, camera.get("snapshot_stream") or "sub")
    if not stream:
        return "", ""

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    home_path = SNAPSHOT_DIR / f"{target_id}.jpg"
    data_path = DATA_SNAPSHOT_DIR / f"{target_id}.jpg"
    command = [
        "ffmpeg",
        "-rtsp_transport",
        "tcp",
        "-y",
        "-i",
        stream,
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(data_path),
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "", ""
    if result.returncode != 0 or not data_path.exists():
        return "", ""
    shutil.copyfile(data_path, home_path)
    return f"snapshots/{target_id}.jpg", f"snapshots/{target_id}.jpg"


def camera_stream(camera: dict, stream_name: str) -> str:
    stream = camera.get("rtsp_main") if stream_name == "main" else camera.get("rtsp_sub")
    return stream or camera.get("rtsp_main") or ""


def capture_live_frame(camera: dict, stream_name: str) -> bytes:
    stream = camera_stream(camera, stream_name)
    if not stream:
        raise ValueError("rtsp_not_configured")

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-rtsp_transport",
        "tcp",
        "-i",
        stream,
        "-an",
        "-frames:v",
        "1",
        "-q:v",
        "5",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-",
    ]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("live_frame_timeout") from error
    except OSError as error:
        raise RuntimeError(str(error)) from error

    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or "live_frame_failed"
        raise RuntimeError(detail[-500:])
    return result.stdout


def refresh_status() -> dict:
    config = load_config()
    cameras = []

    for camera in config.get("cameras", []):
        camera_id = safe_id(camera.get("id", "camera"))
        status = "disabled"
        detail = "Camera is configured but disabled."
        video_codec = ""
        audio_codec = ""
        audio_sample_rate = ""
        audio_channels = ""
        width = ""
        height = ""
        fps = ""
        snapshot_url = ""
        snapshot_path = ""

        if camera.get("enabled"):
            rtsp_main = camera.get("rtsp_main") or ""
            if not rtsp_main:
                status = "missing_rtsp"
                detail = "Camera is enabled, but rtsp_main is empty."
            else:
                probe = run_json(
                    [
                        "ffprobe",
                        "-rtsp_transport",
                        "tcp",
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
                        "-of",
                        "json",
                        rtsp_main,
                    ],
                    timeout=8,
                )
                streams = (probe or {}).get("streams", []) if probe else []
                video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
                audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
                if video_stream:
                    status = "online"
                    detail = "RTSP main stream is reachable."
                    video_codec = str(video_stream.get("codec_name") or "")
                    width = str(video_stream.get("width") or "")
                    height = str(video_stream.get("height") or "")
                    fps = str(video_stream.get("r_frame_rate") or "")
                    audio_codec = str(audio_stream.get("codec_name") or "")
                    audio_sample_rate = str(audio_stream.get("sample_rate") or "")
                    audio_channels = str(audio_stream.get("channels") or "")
                    snapshot_url, snapshot_path = capture_snapshot(camera, camera_id)
                else:
                    status = "offline"
                    detail = "RTSP probe failed. Check IP, credentials, port 554, and camera stream path."

        camera_status = {
            "id": camera.get("id"),
            "name": camera.get("name"),
            "vendor": camera.get("vendor"),
            "host": camera.get("host"),
            "enabled": bool(camera.get("enabled")),
            "record": bool(camera.get("record")),
            "low_latency": bool(camera.get("low_latency")),
            "snapshot_stream": camera.get("snapshot_stream") or "sub",
            "status": status,
            "detail": detail,
            "codec": video_codec,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "audio_sample_rate": audio_sample_rate,
            "audio_channels": audio_channels,
            "width": width,
            "height": height,
            "fps": fps,
            "snapshot_url": snapshot_url,
            "snapshot_path": snapshot_path,
        }
        cameras.append(camera_status)

    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cameras": cameras}
    write_json(DATA_DIR / "cameras.json", payload)
    write_json(HOME_DIR / "cameras.json", payload)
    return payload


def health_payload() -> dict:
    return {"status": "ok", "product": "Edge of Infinity", "mode": "panel-live-mjpeg"}


INDEX_HTML = r"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Edge of Infinity</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #0b1014;
        --panel: #121a20;
        --panel-2: #17222a;
        --line: #273844;
        --text: #edf5f7;
        --muted: #9fb0ba;
        --accent: #56d6b5;
        --warn: #e4b45d;
        --danger: #e66b6b;
      }
      * { box-sizing: border-box; }
      body { margin: 0; background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; }
      .app { min-height: 100vh; display: grid; grid-template-columns: 220px 1fr; }
      .sidebar {
        border-right: 1px solid var(--line);
        background: #0f171c;
        padding: 18px 12px;
        position: sticky;
        top: 0;
        height: 100vh;
      }
      .brand { margin: 0 0 18px; font-size: 18px; font-weight: 800; }
      .nav { display: grid; gap: 7px; }
      .nav button {
        width: 100%;
        text-align: left;
        background: transparent;
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .nav-icon {
        width: 20px;
        height: 20px;
        flex: 0 0 auto;
        color: currentColor;
      }
      .nav-icon svg {
        width: 20px;
        height: 20px;
        display: block;
      }
      .nav button.active {
        border-color: rgba(86,214,181,.65);
        background: rgba(86,214,181,.08);
        color: var(--accent);
      }
      main { width: min(1180px, calc(100vw - 28px)); margin: 0 auto; padding: 24px 0 40px; }
      .page[hidden] { display: none; }
      header { display: flex; align-items: end; justify-content: space-between; gap: 14px; margin-bottom: 18px; }
      h1 { margin: 0 0 7px; font-size: clamp(28px, 5vw, 44px); line-height: 1; }
      h2 { margin: 0 0 12px; font-size: 20px; }
      p { margin: 0; color: var(--muted); line-height: 1.45; }
      button, input, select { font: inherit; }
      button {
        border: 1px solid var(--line);
        border-radius: 7px;
        background: var(--panel-2);
        color: var(--text);
        padding: 8px 11px;
        cursor: pointer;
      }
      button.primary { border-color: rgba(86,214,181,.65); color: var(--accent); }
      button.danger { color: var(--danger); }
      .toolbar { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(285px, 1fr)); gap: 14px; }
      .camera, .settings, .panel {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: linear-gradient(180deg, var(--panel-2), var(--panel));
        overflow: hidden;
      }
      .preview { aspect-ratio: 16 / 9; display: grid; place-items: center; background: #080d10; border-bottom: 1px solid var(--line); }
      .preview img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .preview span { color: var(--muted); font-size: 13px; padding: 12px; text-align: center; }
      .body, .settings { padding: 14px; }
      .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
      .name { font-weight: 750; overflow-wrap: anywhere; }
      .vendor { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; color: var(--accent); font-size: 12px; text-transform: uppercase; }
      .meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
      .metric { border: 1px solid var(--line); border-radius: 8px; padding: 9px; background: rgba(0,0,0,.14); }
      .metric b { display: block; margin-bottom: 3px; color: var(--muted); font-size: 11px; text-transform: uppercase; }
      .metric span { font-size: 14px; overflow-wrap: anywhere; }
      .state-online { color: var(--accent); }
      .state-offline, .state-disabled { color: var(--warn); }
      .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
      .settings, .panel { padding: 14px; }
      .panel + .panel { margin-top: 14px; }
      .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
      label { display: grid; gap: 5px; color: var(--muted); font-size: 12px; }
      input, select {
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 7px;
        background: rgba(0,0,0,.22);
        color: var(--text);
        padding: 8px 9px;
      }
      input[type="checkbox"] { width: auto; }
      .check-row { display: flex; align-items: center; gap: 8px; min-height: 36px; }
      .camera-form { border-top: 1px solid var(--line); padding-top: 14px; margin-top: 14px; }
      .notice { margin-top: 10px; color: var(--muted); font-size: 13px; }
      .preset-bar {
        display: grid;
        grid-template-columns: minmax(180px, 1fr) 120px auto;
        gap: 8px;
        align-items: end;
        margin-bottom: 14px;
      }
      .timeline {
        height: 88px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: repeating-linear-gradient(90deg, rgba(86,214,181,.18), rgba(86,214,181,.18) 3px, transparent 3px, transparent 54px), rgba(0,0,0,.16);
        margin-top: 12px;
      }
      code { color: var(--accent); overflow-wrap: anywhere; }
      @media (max-width: 720px) {
        .app { grid-template-columns: 1fr; }
        .sidebar { position: static; height: auto; }
        .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .preset-bar { grid-template-columns: 1fr; }
        header { align-items: start; flex-direction: column; }
        .toolbar { justify-content: flex-start; }
      }
    </style>
  </head>
  <body>
    <div class="app">
      <aside class="sidebar">
        <div class="brand">Edge of Infinity</div>
        <nav class="nav">
          <button class="active" data-page-target="home"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></svg></span><span>Home</span></button>
          <button data-page-target="nvr"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/></svg></span><span>NVR</span></button>
          <button data-page-target="camera-settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 8h11l5 4-5 4H4z"/><circle cx="9" cy="12" r="2"/></svg></span><span>Camera Settings</span></button>
          <button data-page-target="edge-settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a8 8 0 0 0 .1-6"/><path d="M4.5 9a8 8 0 0 0 .1 6"/><path d="M15 4.6a8 8 0 0 0-6 0"/><path d="M9 19.4a8 8 0 0 0 6 0"/></svg></span><span>Edge Settings</span></button>
          <button data-page-target="account"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.8-4 14.2-4 16 0"/></svg></span><span>Account</span></button>
        </nav>
      </aside>
      <main>
        <section class="page" data-page="home">
          <header>
            <div>
              <h1>Home</h1>
              <p>Live camera wall, RTSP status, and quick preview controls.</p>
            </div>
            <div class="toolbar">
              <button class="primary" id="refresh">Refresh status</button>
              <button data-page-target="camera-settings">Camera settings</button>
            </div>
          </header>
          <section class="grid" id="camera-grid"></section>
        </section>

        <section class="page" data-page="nvr" hidden>
          <header>
            <div>
              <h1>NVR</h1>
              <p>Recording control and playback timeline. Real segments come next.</p>
            </div>
          </header>
          <section class="panel">
            <h2>Recording</h2>
            <div id="nvr-grid" class="grid"></div>
          </section>
          <section class="panel">
            <h2>Timeline</h2>
            <p>Playback, rewind, and forward controls will attach here when recording segments are enabled.</p>
            <div class="timeline"></div>
          </section>
        </section>

        <section class="page" data-page="camera-settings" hidden>
          <header>
            <div>
              <h1>Camera Settings</h1>
              <p>Edit Hikvision camera connection, streams, and low-latency preferences.</p>
            </div>
          </header>
        <section class="settings" id="settings">
            <div class="preset-bar">
              <label>Saved camera preset<select id="preset-select"></select></label>
              <label>Slot<select id="preset-slot">
                <option value="0">Camera 1</option>
                <option value="1">Camera 2</option>
              </select></label>
              <button id="apply-preset" type="button">Load preset</button>
            </div>
            <form id="config-form"></form>
            <div class="actions">
              <button class="primary" id="save-config" type="button">Save cameras</button>
            </div>
            <p class="notice" id="save-state">Changes are saved to <code>/homeassistant/edge/edge.json</code>.</p>
          </section>
        </section>

        <section class="page" data-page="edge-settings" hidden>
          <header>
            <div>
              <h1>Edge Settings</h1>
              <p>Core paths, retention, future WebRTC settings, and diagnostics.</p>
            </div>
          </header>
          <section class="panel">
            <h2>Storage</h2>
            <p>Recordings: <code>/media/edge-of-infinity/recordings</code></p>
            <p>Config: <code>/homeassistant/edge/edge.json</code></p>
          </section>
          <section class="panel">
            <h2>Live Engine</h2>
            <p>Current preview is MJPEG. The next engine target is low-latency WebRTC.</p>
          </section>
        </section>

        <section class="page" data-page="account" hidden>
          <header>
            <div>
              <h1>Account</h1>
              <p>Additional Edge login and trusted-device options will live here.</p>
            </div>
          </header>
          <section class="panel">
            <h2>Security</h2>
            <div class="form-grid">
              <label>Username<input value="admin" disabled></label>
              <label>Password<input type="password" value="" placeholder="Coming next" disabled></label>
              <label class="check-row"><input type="checkbox" disabled> Remember this device</label>
              <label class="check-row"><input type="checkbox" checked disabled> Trust Home Assistant Ingress</label>
            </div>
            <p class="notice">This is a UI placeholder. Backend account protection will be added after the live/NVR path is stable.</p>
          </section>
        </section>
      </main>
    </div>
    <script>
      const grid = document.getElementById('camera-grid');
      const nvrGrid = document.getElementById('nvr-grid');
      const form = document.getElementById('config-form');
      const presetSelect = document.getElementById('preset-select');
      const presetSlot = document.getElementById('preset-slot');
      const saveState = document.getElementById('save-state');
      let config = { cameras: [] };
      let live = {};
      let presets = [];
      let liveTimer = null;

      const panelBase = window.location.pathname.endsWith('/')
        ? window.location.pathname
        : `${window.location.pathname}/`;

      function panelPath(path) {
        return `${panelBase}${String(path).replace(/^\/+/, '')}`;
      }

      function text(value, fallback = 'unknown') {
        return value === undefined || value === null || value === '' ? fallback : value;
      }

      function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[char]));
      }

      function cameraCard(camera) {
        const online = camera.status === 'online';
        const stateClass = online ? 'state-online' : `state-${text(camera.status)}`;
        const resolution = camera.width && camera.height ? `${camera.width}x${camera.height}` : 'unknown';
        const videoCodec = camera.video_codec || camera.codec;
        const audioCodec = camera.audio_codec
          ? `${camera.audio_codec}${camera.audio_sample_rate ? ` ${camera.audio_sample_rate}Hz` : ''}${camera.audio_channels ? ` ${camera.audio_channels}ch` : ''}`
          : 'none';
        const liveUrl = panelPath(`live-frame/${encodeURIComponent(camera.id)}.jpg?stream=${encodeURIComponent(camera.snapshot_stream || 'sub')}&t=${Date.now()}`);
        const preview = live[camera.id]
          ? `<img src="${liveUrl}" alt="${escapeHtml(text(camera.name, camera.id))} live" onerror="this.outerHTML='<span>Live frame failed. Check logs.</span>'">`
          : camera.snapshot_url
            ? `<img src="${panelPath(`${camera.snapshot_url}?t=${Date.now()}`)}" alt="${escapeHtml(text(camera.name, camera.id))} snapshot">`
            : `<span>${online ? 'RTSP reachable' : escapeHtml(text(camera.detail, 'Waiting for camera'))}</span>`;
        return `
          <article class="camera">
            <div class="preview">${preview}</div>
            <div class="body">
              <div class="row">
                <div class="name">${escapeHtml(text(camera.name, camera.id))}</div>
                <div class="vendor">${escapeHtml(text(camera.vendor))}</div>
              </div>
              <div class="meta">
                <div class="metric"><b>Host</b><span>${escapeHtml(text(camera.host))}</span></div>
                <div class="metric"><b>Status</b><span class="${stateClass}">${escapeHtml(text(camera.status))}</span></div>
                <div class="metric"><b>Video</b><span>${escapeHtml(text(videoCodec))} ${escapeHtml(resolution)}</span></div>
                <div class="metric"><b>Audio</b><span>${escapeHtml(audioCodec)}</span></div>
                <div class="metric"><b>FPS</b><span>${escapeHtml(text(camera.fps))}</span></div>
                <div class="metric"><b>Snapshot</b><span>${escapeHtml(text(camera.snapshot_stream))}</span></div>
              </div>
              <div class="actions">
                <button data-live="${escapeHtml(camera.id)}" ${online ? '' : 'disabled'}>${live[camera.id] ? 'Stop live' : 'Start live'}</button>
              </div>
            </div>
          </article>
        `;
      }

      function nvrCard(camera, index) {
        return `
          <article class="camera">
            <div class="body">
              <div class="row">
                <div class="name">${escapeHtml(text(camera.name, `Camera ${index + 1}`))}</div>
                <div class="vendor">${camera.record !== false ? 'RECORD' : 'OFF'}</div>
              </div>
              <div class="meta">
                <div class="metric"><b>Host</b><span>${escapeHtml(text(camera.host))}</span></div>
                <div class="metric"><b>Status</b><span>${camera.record !== false ? 'recording enabled' : 'recording disabled'}</span></div>
              </div>
              <div class="actions">
                <button data-record-toggle="${index}">${camera.record !== false ? 'Disable recording' : 'Enable recording'}</button>
                <button disabled>Rewind</button>
                <button disabled>Forward</button>
              </div>
            </div>
          </article>
        `;
      }

      function cameraForm(camera, index) {
        const prefix = `camera-${index}`;
        return `
          <div class="camera-form" data-index="${index}">
            <h2>${escapeHtml(text(camera.name, `Hikvision ${index + 1}`))}</h2>
            <div class="form-grid">
              <label>Name<input name="${prefix}-name" value="${escapeHtml(text(camera.name, `Hikvision ${index + 1}`))}"></label>
              <label>Host/IP<input name="${prefix}-host" value="${escapeHtml(camera.host || '')}"></label>
              <label>Username<input name="${prefix}-username" value="${escapeHtml(camera.username || 'admin')}"></label>
              <label>Password<input name="${prefix}-password" type="password" value="${escapeHtml(camera.password || '')}"></label>
              <label>RTSP main<input name="${prefix}-rtsp-main" value="${escapeHtml(camera.rtsp_main || '')}"></label>
              <label>RTSP sub<input name="${prefix}-rtsp-sub" value="${escapeHtml(camera.rtsp_sub || '')}"></label>
              <label>ONVIF URL<input name="${prefix}-onvif" value="${escapeHtml(camera.onvif_url || '')}"></label>
              <label>ISAPI URL<input name="${prefix}-isapi" value="${escapeHtml(camera.isapi_base_url || '')}"></label>
              <label>Snapshot stream<select name="${prefix}-snapshot-stream">
                <option value="sub" ${camera.snapshot_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.snapshot_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label>
              <label class="check-row"><input name="${prefix}-enabled" type="checkbox" ${camera.enabled ? 'checked' : ''}> Enabled</label>
              <label class="check-row"><input name="${prefix}-record" type="checkbox" ${camera.record !== false ? 'checked' : ''}> Record</label>
              <label class="check-row"><input name="${prefix}-low-latency" type="checkbox" ${camera.low_latency !== false ? 'checked' : ''}> Low latency</label>
            </div>
          </div>
        `;
      }

      function renderConfig() {
        const cameras = config.cameras && config.cameras.length ? config.cameras : [
          { id: 'hikvision_1', name: 'Hikvision 1', vendor: 'hikvision', username: 'admin', snapshot_stream: 'sub', record: true, low_latency: true },
          { id: 'hikvision_2', name: 'Hikvision 2', vendor: 'hikvision', username: 'admin', snapshot_stream: 'sub', record: true, low_latency: true }
        ];
        form.innerHTML = cameras.map(cameraForm).join('');
        nvrGrid.innerHTML = cameras.map(nvrCard).join('');
      }

      function renderPresets() {
        presetSelect.innerHTML = presets.length
          ? presets.map((camera, index) => {
              const label = `${text(camera.name, `Preset ${index + 1}`)} - ${text(camera.host, camera.rtsp_main || 'RTSP')}`;
              return `<option value="${index}">${escapeHtml(label)}</option>`;
            }).join('')
          : '<option value="">No saved presets yet</option>';
        document.getElementById('apply-preset').disabled = presets.length === 0;
      }

      async function loadPresets() {
        const response = await fetch(panelPath('api/presets'), { cache: 'no-store' });
        const data = await response.json();
        presets = Array.isArray(data.presets) ? data.presets : [];
        renderPresets();
      }

      function collectConfig() {
        const cameras = Array.from(form.querySelectorAll('.camera-form')).map((section, index) => {
          const prefix = `camera-${index}`;
          const get = (name) => form.elements[`${prefix}-${name}`];
          return {
            id: config.cameras[index]?.id || `hikvision_${index + 1}`,
            name: get('name').value,
            vendor: 'hikvision',
            host: get('host').value.trim(),
            username: get('username').value.trim(),
            password: get('password').value,
            rtsp_main: get('rtsp-main').value.trim(),
            rtsp_sub: get('rtsp-sub').value.trim(),
            onvif_url: get('onvif').value.trim(),
            isapi_base_url: get('isapi').value.trim(),
            enabled: get('enabled').checked,
            record: get('record').checked,
            low_latency: get('low-latency').checked,
            snapshot_stream: get('snapshot-stream').value
          };
        });
        return { ...config, cameras };
      }

      function applyPresetToSlot() {
        const preset = presets[Number(presetSelect.value)];
        const slot = Number(presetSlot.value);
        if (!preset || Number.isNaN(slot)) return;
        const current = config.cameras && config.cameras.length ? [...config.cameras] : [
          { id: 'hikvision_1', name: 'Hikvision 1', vendor: 'hikvision', username: 'admin', snapshot_stream: 'sub', record: true, low_latency: true },
          { id: 'hikvision_2', name: 'Hikvision 2', vendor: 'hikvision', username: 'admin', snapshot_stream: 'sub', record: true, low_latency: true }
        ];
        current[slot] = {
          ...preset,
          id: current[slot]?.id || `hikvision_${slot + 1}`,
          name: preset.name || current[slot]?.name || `Hikvision ${slot + 1}`
        };
        config = { ...config, cameras: current };
        renderConfig();
        saveState.textContent = 'Preset loaded into the form. Click Save cameras to write it.';
      }

      function hasMeaningfulCameras(payload) {
        return Array.isArray(payload.cameras) && payload.cameras.some((camera) =>
          camera && (camera.host || camera.rtsp_main)
        );
      }

      async function loadConfig() {
        const response = await fetch(panelPath('api/config'), { cache: 'no-store' });
        config = await response.json();
        renderConfig();
      }

      async function loadCameras() {
        const response = await fetch(panelPath('cameras.json'), { cache: 'no-store' });
        const data = await response.json();
        const cameras = Array.isArray(data.cameras) ? data.cameras : [];
        grid.innerHTML = cameras.length ? cameras.map(cameraCard).join('') : '<p>No cameras configured yet.</p>';
      }

      function showPage(pageName) {
        document.querySelectorAll('[data-page]').forEach((page) => {
          page.hidden = page.dataset.page !== pageName;
        });
        document.querySelectorAll('[data-page-target]').forEach((button) => {
          button.classList.toggle('active', button.dataset.pageTarget === pageName);
        });
      }

      function updateLiveTimer() {
        const hasLive = Object.values(live).some(Boolean);
        if (hasLive && !liveTimer) {
          liveTimer = window.setInterval(() => loadCameras(), 1200);
        }
        if (!hasLive && liveTimer) {
          window.clearInterval(liveTimer);
          liveTimer = null;
        }
      }

      document.querySelectorAll('[data-page-target]').forEach((button) => {
        button.addEventListener('click', () => showPage(button.dataset.pageTarget));
      });

      document.getElementById('refresh').addEventListener('click', async () => {
        await fetch(panelPath('api/refresh'), { method: 'POST' });
        await loadCameras();
      });

      document.getElementById('save-config').addEventListener('click', async () => {
        const payload = collectConfig();
        if (!hasMeaningfulCameras(payload)) {
          saveState.textContent = 'Save blocked: at least one camera needs host/IP or RTSP, so existing configuration was not overwritten.';
          return;
        }
        saveState.textContent = 'Saving cameras...';
        const response = await fetch(panelPath('api/config'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          saveState.textContent = data.error || 'Could not save configuration.';
          return;
        }
        config = data;
        renderConfig();
        await loadPresets();
        await loadCameras();
        saveState.textContent = 'Saved. Backup created and status refreshed.';
      });

      document.getElementById('apply-preset').addEventListener('click', applyPresetToSlot);

      grid.addEventListener('click', async (event) => {
        const id = event.target?.dataset?.live;
        if (!id) return;
        live[id] = !live[id];
        updateLiveTimer();
        await loadCameras();
      });

      nvrGrid.addEventListener('click', async (event) => {
        const index = event.target?.dataset?.recordToggle;
        if (index === undefined) return;
        const camera = config.cameras[Number(index)];
        if (!camera) return;
        camera.record = camera.record === false;
        renderConfig();
        const response = await fetch(panelPath('api/config'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config)
        });
        config = await response.json();
        renderConfig();
        await loadCameras();
      });

      Promise.all([loadConfig(), loadPresets(), loadCameras()]).catch((error) => {
        grid.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      });
    </script>
  </body>
</html>
"""


class EdgeHandler(BaseHTTPRequestHandler):
    server_version = "EdgePanel/0.4"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[edge-panel] {self.address_string()} {format % args}")

    def send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/health":
            self.send_json(health_payload())
            return
        if path == "/api/config":
            self.send_json(load_config())
            return
        if path == "/api/presets":
            self.send_json({"presets": load_presets()})
            return
        if path == "/cameras.json":
            payload = read_json(DATA_DIR / "cameras.json", {"cameras": []})
            self.send_json(payload)
            return
        if path.startswith("/snapshots/"):
            self.serve_snapshot(path.removeprefix("/snapshots/"))
            return
        if path.startswith("/live-frame/") and path.endswith(".jpg"):
            self.serve_live_frame(path, parse_qs(parsed.query))
            return
        if path.startswith("/live/") and path.endswith(".mjpg"):
            self.serve_live(path, parse_qs(parsed.query))
            return

        self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.save_config()
            return
        if parsed.path == "/api/refresh":
            self.send_json(refresh_status())
            return
        self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def read_body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8")) if body else {}

    def save_config(self) -> None:
        try:
            raw_payload = self.read_body_json()
            validate_config_for_save(raw_payload)
            payload = normalize_config(raw_payload)
            validate_config_for_save(payload)
            backup_config()
            write_json(CONFIG_PATH, payload)
            remember_camera_presets(payload.get("cameras", []))
            refresh_status()
            self.send_json(payload)
        except (json.JSONDecodeError, OSError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def serve_snapshot(self, filename: str) -> None:
        safe_name = Path(filename).name
        path = DATA_SNAPSHOT_DIR / safe_name
        if not path.exists():
            path = SNAPSHOT_DIR / safe_name
        if not path.exists():
            self.send_json({"error": "snapshot_not_found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(path.read_bytes(), "image/jpeg")

    def serve_live_frame(self, path: str, query: dict[str, list[str]]) -> None:
        camera_id = path.removeprefix("/live-frame/").removesuffix(".jpg")
        stream_name = (query.get("stream") or ["sub"])[0]
        config = load_config()
        camera = next((item for item in config.get("cameras", []) if item.get("id") == camera_id), None)
        if not camera:
            self.send_json({"error": "camera_not_found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            self.send_bytes(capture_live_frame(camera, stream_name), "image/jpeg")
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    def serve_live(self, path: str, query: dict[str, list[str]]) -> None:
        camera_id = path.removeprefix("/live/").removesuffix(".mjpg")
        stream_name = (query.get("stream") or ["sub"])[0]
        config = load_config()
        camera = next((item for item in config.get("cameras", []) if item.get("id") == camera_id), None)
        if not camera:
            self.send_json({"error": "camera_not_found"}, HTTPStatus.NOT_FOUND)
            return

        stream = camera_stream(camera, stream_name)
        if not stream:
            self.send_json({"error": "rtsp_not_configured"}, HTTPStatus.BAD_REQUEST)
            return

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-rtsp_transport",
            "tcp",
            "-i",
            stream,
            "-an",
            "-vf",
            "fps=10",
            "-c:v",
            "mjpeg",
            "-q:v",
            "5",
            "-flush_packets",
            "1",
            "-f",
            "mpjpeg",
            "-",
        ]
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=ffmpeg")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(DATA_DIR / "health", health_payload())
    write_json(HOME_DIR / "health.json", health_payload())
    refresh_status()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), EdgeHandler)
    print(f"[edge-panel] listening on 0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
