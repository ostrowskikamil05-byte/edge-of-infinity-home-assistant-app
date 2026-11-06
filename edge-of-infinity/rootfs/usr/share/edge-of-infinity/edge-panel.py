#!/usr/bin/env python3
"""Edge of Infinity panel server."""

from __future__ import annotations

import json
import base64
import signal
import functools
import os
import platform
import re
import shutil
import subprocess
import sys
import hashlib
import threading
import tempfile
import time
from datetime import date, timedelta, datetime, timezone
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse, quote, unquote
from xml.sax.saxutils import escape as xml_escape


APP_VERSION = "0.10.45"
SERVER_VERSION = f"EdgePanel/{APP_VERSION}"
UI_BUILD = "native-edgertc-webcodecs-v7"
MAX_REQUEST_BODY_BYTES = 2_000_000
HOME_DIR = Path(os.environ.get("EDGE_HOME_DIR", "/homeassistant/edge"))
DATA_DIR = Path(os.environ.get("EDGE_DATA_DIR", "/tmp/edge-placeholder"))
CONFIG_PATH = Path(os.environ.get("EDGE_HOME_CONFIG", "/homeassistant/edge/edge.json"))
ADDON_CONFIG_PATH = Path(os.environ.get("EDGE_ADDON_CONFIG", "/config/edge.json"))
CONFIG_BACKUP_PATH = HOME_DIR / "edge.backup.json"
PRESETS_PATH = HOME_DIR / "camera-presets.json"
PANEL_CONFIG_PATH = HOME_DIR / "panel-config.json"
PANEL_CAMERA_OVERRIDES_PATH = HOME_DIR / "panel-camera-overrides.json"
STREAM_OVERRIDES_PATH = HOME_DIR / "stream-overrides.json"
DEBUG_LOG_PATH = HOME_DIR / "edge-debug.log"
PORT = int(os.environ.get("API_PORT", "8088"))
SNAPSHOT_DIR = HOME_DIR / "snapshots"
DATA_SNAPSHOT_DIR = DATA_DIR / "snapshots"
STREAM_LIST_DIR = HOME_DIR / "stream-lists"
RECORDING_THUMB_DIR = HOME_DIR / "recording-thumbs"
RECORDING_CACHE_DIR = HOME_DIR / "recording-cache"
RECORDING_STREAM_LOG_PATH = HOME_DIR / "recording-stream.log"
RECORDING_CACHE_LOG_PATH = HOME_DIR / "recording-cache.log"
RECORDING_THUMB_LOG_PATH = HOME_DIR / "recording-thumbnail.log"
RECORDING_LIVE_EDGE_DELAY_SECONDS = 1.0
MIN_RECORDING_FILE_READY_SECONDS = RECORDING_LIVE_EDGE_DELAY_SECONDS
RECORDING_FILE_MIN_PLAYABLE_BYTES = 1024
RECORDING_FILE_HEADER_CHECK_BYTES = 65536
RECORDING_FILE_HEADER_GRACE_SECONDS = 30.0
RECORDING_CACHE_REFRESH_SECONDS = 60
RECORDING_CACHE_MAX_SEGMENTS = 10000
RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS = 50000
RECORDING_CACHE_MIN_REBUILD_SECONDS = 300
RECORDING_CACHE_MIN_NEW_SEGMENTS = 30
RECORDING_CACHE_MIN_TIMEOUT_SECONDS = 1800
RECORDING_CACHE_MAX_TIMEOUT_SECONDS = 21600
ACTIVE_DAY_CACHE_DEFER_REASON = "active_day_deferred_until_midnight"
RECORDING_ENSURE_MIN_SECONDS = 30
RECORDING_RETENTION_INTERVAL_SECONDS = 3600
RECORDING_RETENTION_LOOP_STARTED = False
RECORDING_THUMBNAIL_WARMUP_INTERVAL_SECONDS = 300
RECORDING_THUMBNAIL_WARMUP_PER_CAMERA = 4
RECORDING_PROCESSES: dict[str, subprocess.Popen] = {}
RECORDING_CACHE_WORKERS: set[str] = set()
RECORDING_CACHE_LOOP_STARTED = False
RECORDING_THUMBNAIL_WARMUP_LOOP_STARTED = False
LAST_RECORDING_ENSURE_AT = 0.0
DEBUG_LOCK = threading.Lock()
RECORDING_ENSURE_LOCK = threading.Lock()
CONFIG_LOCK = threading.RLock()
CONFIG_SAVE_LOCK = threading.Lock()
RECORDING_CONTROL_LOCK = threading.RLock()
JANUS_RELOAD_LOCK = threading.Lock()
JANUS_MANAGED_PID = int(os.environ.get("EDGE_JANUS_PID", "0"))
RECORDING_DURATION_CACHE: dict[str, tuple[int, int, int]] = {}
RECORDING_DURATION_LOCK = threading.Lock()
RECORDING_CACHE_LOCK = threading.Lock()
RECORDING_CACHE_BUILD_SEMAPHORE = threading.BoundedSemaphore(value=1)
RECORDING_THUMBNAIL_SEMAPHORE = threading.BoundedSemaphore(value=1)
HIKVISION_MAIN_CHANNEL = "101"
HIKVISION_SUB_CHANNEL = "102"
STREAM_FALLBACK_CHANNELS = {"main": HIKVISION_MAIN_CHANNEL, "sub": HIKVISION_SUB_CHANNEL}
STREAM_ROLE_FIELDS = ("snapshot_stream", "live_stream", "record_stream", "tile_stream")
CAMERA_OVERRIDE_FIELDS = (
    "id",
    "name",
    "vendor",
    "host",
    "username",
    "password",
    "rtsp_main",
    "rtsp_sub",
    "camera_number",
    "access_protocol",
    "rtsp_transport",
    "rtsp_main_channel",
    "rtsp_sub_channel",
    "onvif_url",
    "isapi_base_url",
    "enabled",
    "record",
    "low_latency",
    *STREAM_ROLE_FIELDS,
)
STREAM_ENGINES = ("janus_webrtc", "mediamtx", "ll_hls", "srt")
REMOTE_ACCESS_MODES = ("local_only", "direct_public", "vps_relay", "turn_relay")
ALWAYS_ON_STREAM_SCOPES = ("tile", "live", "tile_live", "all")
EDGE_RTC_ENABLED = os.environ.get("EDGE_CORE_ENGINE", "") == "edge_rtc"
EDGE_RTC_CONFIG_PATH = Path(os.environ.get("EDGE_RTC_CONFIG", "/tmp/edge-runtime/edge-rtc.json"))
MEDIAMTX_ENABLED = os.environ.get("EDGE_MEDIAMTX_ENABLED", "true").lower() == "true"
MEDIAMTX_HOST = os.environ.get("EDGE_MEDIAMTX_HOST", "127.0.0.1")
MEDIAMTX_RTSP_PORT = int(os.environ.get("EDGE_MEDIAMTX_RTSP_PORT", "8556"))
MEDIAMTX_HLS_PORT = int(os.environ.get("EDGE_MEDIAMTX_HLS_PORT", "8888"))
MEDIAMTX_HLS_ALWAYS_REMUX = os.environ.get("EDGE_MEDIAMTX_HLS_ALWAYS_REMUX", "false").lower() == "true"
MEDIAMTX_WEBRTC_PORT = int(os.environ.get("EDGE_MEDIAMTX_WEBRTC_PORT", "8889"))
MEDIAMTX_WEBRTC_UDP_PORT = int(os.environ.get("EDGE_MEDIAMTX_WEBRTC_UDP_PORT", "8189"))
MEDIAMTX_WEBRTC_PUBLIC_HOSTS = [
    item.strip()
    for item in os.environ.get("EDGE_MEDIAMTX_WEBRTC_PUBLIC_HOSTS", "homeassistant.local,192.168.33.17").split(",")
    if item.strip()
]
MEDIAMTX_WEBRTC_PUBLIC_URL = os.environ.get("EDGE_MEDIAMTX_WEBRTC_PUBLIC_URL", "")
MEDIAMTX_REMOTE_ACCESS_MODE = os.environ.get("EDGE_MEDIAMTX_REMOTE_ACCESS_MODE", "local_only")
MEDIAMTX_WEBRTC_STUN_URL = os.environ.get("EDGE_MEDIAMTX_WEBRTC_STUN_URL", "stun:stun.l.google.com:19302")
MEDIAMTX_WEBRTC_TURN_URL = os.environ.get("EDGE_MEDIAMTX_WEBRTC_TURN_URL", "")
MEDIAMTX_WEBRTC_TURN_USERNAME = os.environ.get("EDGE_MEDIAMTX_WEBRTC_TURN_USERNAME", "")
MEDIAMTX_WEBRTC_TURN_PASSWORD = os.environ.get("EDGE_MEDIAMTX_WEBRTC_TURN_PASSWORD", "")
MEDIAMTX_WEBRTC_TCP_ONLY = os.environ.get("EDGE_MEDIAMTX_WEBRTC_TCP_ONLY", "false").lower() == "true"
MEDIAMTX_WEBRTC_ICE_TRANSPORT = os.environ.get(
    "EDGE_MEDIAMTX_WEBRTC_ICE_TRANSPORT",
    "tcp" if MEDIAMTX_WEBRTC_TCP_ONLY else "auto",
)
MEDIAMTX_SRT_PORT = int(os.environ.get("EDGE_MEDIAMTX_SRT_PORT", "8890"))
MEDIAMTX_API_PORT = int(os.environ.get("EDGE_MEDIAMTX_API_PORT", "9997"))
MEDIAMTX_CONFIG_PATH = Path(os.environ.get("EDGE_MEDIAMTX_CONFIG", "/tmp/edge-runtime/mediamtx.yml"))
MEDIAMTX_RECORD = os.environ.get("EDGE_MEDIAMTX_RECORD", "false").lower() == "true"
JANUS_ENABLED = os.environ.get("EDGE_JANUS_ENABLED", "true").lower() == "true"
JANUS_HOST = os.environ.get("EDGE_JANUS_HOST", "127.0.0.1")
JANUS_HTTP_PORT = int(os.environ.get("EDGE_JANUS_HTTP_PORT", "8192"))
JANUS_WS_PORT = int(os.environ.get("EDGE_JANUS_WS_PORT", "8193"))
JANUS_CONFIG_DIR = Path(os.environ.get("EDGE_JANUS_CONFIG_DIR", "/tmp/edge-runtime/janus"))
STUN_SERVERS = ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value or "camera")


def redact_rtsp(value: str) -> str:
    return re.sub(r"(rtsp://[^:/@]+:)[^@]+@", r"\1***@", value or "")


def redact_command(command: list[str]) -> list[str]:
    return [redact_rtsp(item) if isinstance(item, str) else item for item in command]


def safe_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def clamp_int(value, fallback: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, safe_int(value, fallback)))


def safe_bool(value, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
        return fallback
    return bool(value)


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each writer owns its temporary file; readers only see a complete JSON file.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            tmp_path = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def read_chunked_body_from_stream(stream, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> bytes:
    chunks = []
    total = 0
    while True:
        line = stream.readline(65537)
        if not line:
            break
        size_text = line.split(b";", 1)[0].strip()
        if not size_text:
            continue
        try:
            size = int(size_text, 16)
        except ValueError as error:
            raise ValueError(f"Invalid chunked request size: {size_text!r}") from error
        if size == 0:
            while True:
                trailer = stream.readline(65537)
                if trailer in (b"", b"\r\n", b"\n"):
                    break
            break
        total += size
        if total > max_bytes:
            raise ValueError("Request body is too large.")
        chunks.append(stream.read(size))
        stream.read(2)
    return b"".join(chunks)


def file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def read_text_tail(path: Path, limit: int = 4000) -> str:
    try:
        if not path.exists():
            return ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - limit))
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def redact_for_log(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in ("password", "token", "authorization", "secret")):
                redacted[key] = "***"
            else:
                redacted[key] = redact_for_log(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    if isinstance(value, str):
        return redact_rtsp(value)
    return value


def write_debug_event(event: str, payload: dict | None = None) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "server_version": SERVER_VERSION,
        "ui_build": UI_BUILD,
        **redact_for_log(payload or {}),
    }
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOCK:
            with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as error:
        print(f"[edge-panel] debug log write failed: {error}")


def append_jsonl_log(path: Path, event: str, payload: dict | None = None) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "server_version": SERVER_VERSION,
        "ui_build": UI_BUILD,
        **redact_for_log(payload or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as error:
        print(f"[edge-panel] jsonl log write failed: {error}")


def route_path(raw_path: str) -> str:
    path = raw_path or "/"
    for segment in ("/api/hassio_ingress/", "/api/hassio/ingress/"):
        if segment in path:
            after = path.split(segment, 1)[1]
            after = after.split("/", 1)[-1] if "/" in after else ""
            path = "/" + after.lstrip("/")
            break
    path = re.sub(r"/{2,}", "/", path)
    return path or "/"


def camera_debug_profile(camera: dict, stream_name: str, probe: dict | None = None) -> dict:
    probe = probe or {}
    video = probe.get("video") or {}
    audio = probe.get("audio") or {}
    stream = camera_stream(camera, stream_name)
    return {
        "camera_id": camera.get("id"),
        "name": camera.get("name"),
        "vendor": camera.get("vendor"),
        "host": camera.get("host"),
        "stream_name": stream_name,
        "rtsp": redact_rtsp(stream),
        "channel": hikvision_channel_from_rtsp(stream, ""),
        "configured_live": camera.get("live_stream"),
        "configured_record": camera.get("record_stream"),
        "configured_snapshot": camera.get("snapshot_stream"),
        "video_codec": video.get("codec_name") or camera.get("video_codec") or camera.get("codec"),
        "video_width": video.get("width") or camera.get("width"),
        "video_height": video.get("height") or camera.get("height"),
        "video_fps": video.get("r_frame_rate") or camera.get("fps"),
        "video_bitrate": video.get("bit_rate") or camera.get("bitrate"),
        "audio_codec": audio.get("codec_name") or camera.get("audio_codec"),
        "audio_sample_rate": audio.get("sample_rate") or camera.get("audio_sample_rate"),
        "audio_channels": audio.get("channels") or camera.get("audio_channels"),
        "effective_streams": effective_streams(camera),
    }


def backup_config() -> None:
    if CONFIG_PATH.exists():
        shutil.copyfile(CONFIG_PATH, CONFIG_BACKUP_PATH)


def backup_panel_config() -> None:
    if PANEL_CONFIG_PATH.exists():
        shutil.copyfile(PANEL_CONFIG_PATH, HOME_DIR / "panel-config.backup.json")


def build_rtsp(explicit: str, host: str, username: str, password: str, channel: str) -> str:
    if explicit:
        return explicit
    if host and username and password:
        return f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}@{host}:554/Streaming/Channels/{channel}"
    return ""


def build_hikvision_rtsp(host: str, username: str, password: str, channel: str) -> str:
    if host and username and password:
        return f"rtsp://{quote(username, safe='')}:{quote(password, safe='')}@{host}:554/Streaming/Channels/{channel}"
    return ""


def url_host(value: str) -> str:
    try:
        return urlparse(value or "").hostname or ""
    except ValueError:
        return ""


def refresh_hikvision_onvif_url(value: str, host: str) -> str:
    if not host:
        return value or ""
    if not value:
        return f"http://{host}:80/onvif/device_service"
    parsed = urlparse(value)
    path = parsed.path or ""
    if parsed.scheme not in ("http", "https") or parsed.hostname != host or path != "/onvif/device_service":
        return f"http://{host}:80/onvif/device_service"
    return value


def refresh_hikvision_isapi_base_url(value: str, host: str) -> str:
    if not host:
        return value or ""
    if not value:
        return f"http://{host}"
    parsed = urlparse(value)
    path = (parsed.path or "").rstrip("/")
    if parsed.scheme not in ("http", "https") or parsed.hostname != host or "/ISAPI/" in path or "/Streaming/" in path:
        return f"http://{host}"
    return value.rstrip("/")


def hikvision_channel_from_rtsp(value: str, fallback: str) -> str:
    match = re.search(r"/Streaming/Channels/(\d+)", value or "")
    if match:
        return match.group(1)
    return fallback


def rtsp_url_parts(value: str) -> dict:
    parsed = urlparse(value or "")
    return {
        "host": parsed.hostname or "",
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "channel": hikvision_channel_from_rtsp(value, ""),
        "is_hikvision_streaming_path": "/Streaming/Channels/" in (parsed.path or value or ""),
    }


def hikvision_rtsp_needs_rebuild(value: str, host: str, username: str, password: str, channel: str) -> bool:
    if not value:
        return False
    parts = rtsp_url_parts(value)
    if not parts["is_hikvision_streaming_path"]:
        return False
    if host and parts["host"] and parts["host"] != host:
        return True
    if username and parts["username"] and parts["username"] != username:
        return True
    if password and parts["password"] and parts["password"] != password:
        return True
    if channel and parts["channel"] and parts["channel"] != channel:
        return True
    return False


def refresh_hikvision_rtsp(value: str, host: str, username: str, password: str, channel: str) -> str:
    rebuilt = build_hikvision_rtsp(host, username, password, channel)
    if rebuilt:
        return rebuilt
    if hikvision_rtsp_needs_rebuild(value, host, username, password, channel):
        return rebuilt or hikvision_rtsp_with_channel(value, channel)
    return hikvision_rtsp_with_channel(value, channel)


def normalize_hikvision_channel(value: str | int | None, fallback: str) -> str:
    channel = str(value or "").strip()
    return channel if re.fullmatch(r"\d{3}", channel) else fallback


def normalize_stream_name(value: str | None, fallback: str) -> str:
    return value if value in ("main", "sub") else fallback


def hikvision_camera_number_from_channel(channel: str) -> str:
    if not channel:
        return ""
    if len(channel) <= 2:
        return channel
    return channel[:-2] or "1"


def normalize_camera_number(value: str | int | None, fallback: str = "1") -> str:
    number = str(value or "").strip()
    return number if re.fullmatch(r"\d{1,2}", number) else fallback


def hikvision_channel_for_camera_number(camera_number: str, stream_name: str) -> str:
    suffix = "01" if normalize_stream_name(stream_name, "sub") == "main" else "02"
    return f"{normalize_camera_number(camera_number)}{suffix}"


def normalize_access_protocol(value: str | None) -> str:
    return value if value in ("rtsp", "isapi", "onvif", "unicast", "multicast") else "rtsp"


def normalize_rtsp_transport(value: str | None) -> str:
    return value if value in ("tcp", "udp", "auto") else "tcp"


def normalize_webrtc_ice_transport(value: str | None, tcp_only: object = None) -> str:
    if value in ("auto", "udp", "tcp"):
        return value
    if safe_bool(tcp_only, False):
        return "tcp"
    if MEDIAMTX_WEBRTC_ICE_TRANSPORT in ("auto", "udp", "tcp"):
        return MEDIAMTX_WEBRTC_ICE_TRANSPORT
    return "auto"


def normalize_remote_access_mode(value: str | None) -> str:
    if value in REMOTE_ACCESS_MODES:
        return value
    if MEDIAMTX_REMOTE_ACCESS_MODE in REMOTE_ACCESS_MODES:
        return MEDIAMTX_REMOTE_ACCESS_MODE
    return "local_only"


def normalize_always_on_stream_scope(value: str | None) -> str:
    return value if value in ALWAYS_ON_STREAM_SCOPES else "tile"


def stream_channel(camera: dict, stream_name: str) -> str:
    stream_name = normalize_stream_name(stream_name, "sub")
    fallback = STREAM_FALLBACK_CHANNELS[stream_name]
    return hikvision_channel_from_rtsp(camera_stream(camera, stream_name), fallback)


def stream_profile(camera: dict, stream_name: str) -> dict:
    stream_name = normalize_stream_name(stream_name, "sub")
    rtsp = camera_stream(camera, stream_name)
    return {
        "stream": stream_name,
        "channel": hikvision_channel_from_rtsp(rtsp, STREAM_FALLBACK_CHANNELS[stream_name]),
        "rtsp": redact_rtsp(rtsp),
        "configured": bool(rtsp),
    }


def config_summary(payload: dict) -> list[dict]:
    cameras = payload.get("cameras") if isinstance(payload, dict) else []
    if not isinstance(cameras, list):
        return []
    summary = []
    for index, camera in enumerate(cameras):
        if not isinstance(camera, dict):
            continue
        summary.append(
            {
                "index": index,
                "id": camera.get("id"),
                "name": camera.get("name"),
                "host": camera.get("host"),
                "enabled": camera.get("enabled"),
                "record": camera.get("record"),
                "low_latency": camera.get("low_latency"),
                "camera_number": camera.get("camera_number"),
                "access_protocol": camera.get("access_protocol"),
                "rtsp_transport": camera.get("rtsp_transport"),
                "live_stream": camera.get("live_stream"),
                "tile_stream": camera.get("tile_stream"),
                "record_stream": camera.get("record_stream"),
                "snapshot_stream": camera.get("snapshot_stream"),
                "rtsp_main_channel": hikvision_channel_from_rtsp(camera.get("rtsp_main") or "", ""),
                "rtsp_sub_channel": hikvision_channel_from_rtsp(camera.get("rtsp_sub") or "", ""),
                "rtsp_main": redact_rtsp(camera.get("rtsp_main") or ""),
                "rtsp_sub": redact_rtsp(camera.get("rtsp_sub") or ""),
            }
        )
    return summary


def stream_override_key(camera: dict, index: int) -> str:
    camera_id = str(camera.get("id") or "").strip()
    return camera_id or f"index:{index}"


def clean_camera_override(camera: dict) -> dict:
    clean = {}
    for field in CAMERA_OVERRIDE_FIELDS:
        if field not in camera:
            continue
        value = camera.get(field)
        if field in STREAM_ROLE_FIELDS:
            if value in ("main", "sub"):
                clean[field] = value
        elif field in ("enabled", "record", "low_latency"):
            clean[field] = bool(value)
        elif value is not None:
            clean[field] = value
    return clean


def load_panel_camera_overrides() -> dict:
    payload = read_json(PANEL_CAMERA_OVERRIDES_PATH, {})
    if not isinstance(payload, dict):
        return {}
    cameras = payload.get("cameras") if isinstance(payload.get("cameras"), dict) else payload
    if not isinstance(cameras, dict):
        return {}
    overrides = {}
    for key, values in cameras.items():
        if not isinstance(values, dict):
            continue
        clean = clean_camera_override(values)
        if clean:
            overrides[str(key)] = clean
    return overrides


def save_panel_camera_overrides(config: dict) -> None:
    cameras = config.get("cameras") if isinstance(config, dict) else []
    if not isinstance(cameras, list):
        return
    overrides = {}
    for index, camera in enumerate(cameras):
        if not isinstance(camera, dict):
            continue
        values = clean_camera_override(normalize_camera(camera, index + 1))
        if values:
            overrides[stream_override_key(camera, index)] = values
            overrides[f"index:{index}"] = values
    write_json(PANEL_CAMERA_OVERRIDES_PATH, {"cameras": overrides})


def apply_panel_camera_overrides(raw_payload: dict) -> dict:
    if not isinstance(raw_payload, dict):
        return raw_payload
    overrides = load_panel_camera_overrides()
    if not overrides:
        return raw_payload
    payload = json.loads(json.dumps(raw_payload))
    cameras = payload.get("cameras")
    if not isinstance(cameras, list):
        return payload
    for index, camera in enumerate(cameras):
        if not isinstance(camera, dict):
            continue
        values = overrides.get(stream_override_key(camera, index)) or overrides.get(f"index:{index}") or {}
        if values:
            camera.update(values)
    return payload


def load_stream_overrides() -> dict:
    payload = read_json(STREAM_OVERRIDES_PATH, {})
    if not isinstance(payload, dict):
        return {}
    cameras = payload.get("cameras") if isinstance(payload.get("cameras"), dict) else payload
    if not isinstance(cameras, dict):
        return {}
    overrides = {}
    for key, values in cameras.items():
        if not isinstance(values, dict):
            continue
        clean = {
            field: value
            for field in STREAM_ROLE_FIELDS
            if (value := values.get(field)) in ("main", "sub")
        }
        if clean:
            overrides[str(key)] = clean
    return overrides


def save_stream_overrides(config: dict) -> None:
    cameras = config.get("cameras") if isinstance(config, dict) else []
    if not isinstance(cameras, list):
        return
    overrides = {}
    for index, camera in enumerate(cameras):
        if not isinstance(camera, dict):
            continue
        values = {
            field: value
            for field in STREAM_ROLE_FIELDS
            if (value := camera.get(field)) in ("main", "sub")
        }
        if values:
            overrides[stream_override_key(camera, index)] = values
            overrides[f"index:{index}"] = values
    write_json(STREAM_OVERRIDES_PATH, {"cameras": overrides})


def clear_legacy_override_files() -> None:
    for path in (PANEL_CAMERA_OVERRIDES_PATH, STREAM_OVERRIDES_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            write_debug_event("legacy_override_clear_error", {"path": str(path), "error": str(error)})


def apply_stream_overrides(config: dict) -> dict:
    cameras = config.get("cameras") if isinstance(config, dict) else []
    if not isinstance(cameras, list):
        return config
    overrides = load_stream_overrides()
    if not overrides:
        return config
    for index, camera in enumerate(cameras):
        if not isinstance(camera, dict):
            continue
        values = overrides.get(stream_override_key(camera, index)) or overrides.get(f"index:{index}") or {}
        for field in STREAM_ROLE_FIELDS:
            value = values.get(field)
            if value in ("main", "sub"):
                camera[field] = value
    return config


def effective_streams(camera: dict) -> dict:
    snapshot_stream = normalize_stream_name(camera.get("snapshot_stream"), "sub")
    live_stream = normalize_stream_name(camera.get("live_stream"), "sub")
    record_stream = normalize_stream_name(camera.get("record_stream"), "main")
    tile_stream = normalize_stream_name(camera.get("tile_stream"), "sub")
    return {
        "main": stream_profile(camera, "main"),
        "sub": stream_profile(camera, "sub"),
        "snapshot": stream_profile(camera, snapshot_stream),
        "live": stream_profile(camera, live_stream),
        "record": stream_profile(camera, record_stream),
        "tile": stream_profile(camera, tile_stream),
    }


def hikvision_rtsp_with_channel(value: str, channel: str) -> str:
    if not value or not channel or "/Streaming/Channels/" not in value:
        return value
    return re.sub(r"/Streaming/Channels/\d+", f"/Streaming/Channels/{channel}", value, count=1)


def build_dahua_rtsp(explicit: str, host: str, username: str, password: str, subtype: int) -> str:
    if explicit:
        return explicit
    if host and username and password:
        return f"rtsp://{username}:{password}@{host}:554/cam/realmonitor?channel=1&subtype={subtype}"
    return ""


CAMERA_VENDORS = {
    "hikvision": "Hikvision", "dahua": "Dahua", "uniview": "Uniview", "reolink": "Reolink",
    "axis": "Axis", "hanwha": "Hanwha / Samsung", "amcrest": "Amcrest", "foscam": "Foscam",
    "tapo": "TP-Link Tapo", "vigi": "TP-Link VIGI", "bosch": "Bosch (ONVIF)",
    "vivotek": "Vivotek (ONVIF)", "sony": "Sony (ONVIF)", "onvif": "Other ONVIF", "rtsp": "Custom RTSP",
}


def camera_stream_template(vendor: str, number: str, stream: str) -> str:
    channel = max(1, safe_int(number, 1))
    sub = stream == "sub"
    return {
        "uniview": f"/media/video{2 if sub else 1}",
        "reolink": f"/h264Preview_{channel:02d}_{'sub' if sub else 'main'}",
        "dahua": f"/cam/realmonitor?channel={channel}&subtype={1 if sub else 0}",
        "amcrest": f"/cam/realmonitor?channel={channel}&subtype={1 if sub else 0}",
        "axis": "/axis-media/media.amp" + ("?resolution=640x360" if sub else ""),
        "hanwha": f"/profile{2 if sub else 1}/media.smp",
        "foscam": "/videoSub" if sub else "/videoMain",
        "tapo": f"/stream{2 if sub else 1}", "vigi": f"/stream{2 if sub else 1}",
    }.get(vendor, "")


def rebind_camera_rtsp(value: str, host: str, username: str, password: str) -> str:
    if not value or not host:
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ("rtsp", "rtsps"):
        return value
    target = urlparse(f"rtsp://{host}")
    hostname = target.hostname or host
    authority = f"[{hostname}]" if ":" in hostname else hostname
    port = target.port or parsed.port or 554
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
    return urlunparse(parsed._replace(netloc=f"{credentials}{authority}:{port}"))


def camera_profile_rtsp(vendor: str, number: str, stream: str, host: str, username: str, password: str, explicit: str = "") -> str:
    path = camera_stream_template(vendor, number, stream)
    source = explicit or (f"rtsp://camera{path}" if path else "")
    return rebind_camera_rtsp(source, host, username, password) if source else ""


def normalize_camera(raw: dict, index: int) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    vendor = raw.get("vendor") if raw.get("vendor") in CAMERA_VENDORS else ("onvif" if raw.get("vendor") else "hikvision")
    vendor_labels = CAMERA_VENDORS
    vendor_label = vendor_labels.get(vendor, "Camera")
    camera_id = raw.get("id") or f"{vendor}_{index}"
    name = raw.get("name") or f"{vendor_label} {index}"
    host = raw.get("host") or ""
    username = raw.get("username") or "admin"
    password = raw.get("password") or ""
    rtsp_main = raw.get("rtsp_main") or ""
    rtsp_sub = raw.get("rtsp_sub") or ""
    camera_number = normalize_camera_number(
        raw.get("camera_number")
        or hikvision_camera_number_from_channel(hikvision_channel_from_rtsp(rtsp_main, ""))
        or hikvision_camera_number_from_channel(hikvision_channel_from_rtsp(rtsp_sub, "")),
        str(index),
    )
    access_protocol = normalize_access_protocol(raw.get("access_protocol"))
    rtsp_transport = normalize_rtsp_transport(raw.get("rtsp_transport"))
    default_main_channel = hikvision_channel_for_camera_number(camera_number, "main")
    default_sub_channel = hikvision_channel_for_camera_number(camera_number, "sub")
    raw_main_channel = raw.get("rtsp_main_channel") or hikvision_channel_from_rtsp(rtsp_main, default_main_channel)
    raw_sub_channel = raw.get("rtsp_sub_channel") or hikvision_channel_from_rtsp(rtsp_sub, default_sub_channel)
    rtsp_main_channel = normalize_hikvision_channel(
        raw_main_channel,
        default_main_channel,
    )
    rtsp_sub_channel = normalize_hikvision_channel(
        raw_sub_channel,
        default_sub_channel,
    )
    if vendor == "hikvision":
        rtsp_main = refresh_hikvision_rtsp(rtsp_main, host, username, password, rtsp_main_channel)
        rtsp_sub = refresh_hikvision_rtsp(rtsp_sub, host, username, password, rtsp_sub_channel)
        rtsp_main = build_rtsp(rtsp_main, host, username, password, rtsp_main_channel)
        rtsp_sub = build_rtsp(rtsp_sub, host, username, password, rtsp_sub_channel)
        rtsp_main_channel = hikvision_channel_from_rtsp(rtsp_main, rtsp_main_channel)
        rtsp_sub_channel = hikvision_channel_from_rtsp(rtsp_sub, rtsp_sub_channel)
    else:
        rtsp_main = camera_profile_rtsp(vendor, camera_number, "main", host, username, password, rtsp_main)
        rtsp_sub = camera_profile_rtsp(vendor, camera_number, "sub", host, username, password, rtsp_sub)
        rtsp_main_channel = ""
        rtsp_sub_channel = ""
    snapshot_stream = normalize_stream_name(raw.get("snapshot_stream"), "sub")
    live_stream = normalize_stream_name(raw.get("live_stream"), "sub")
    record_stream = normalize_stream_name(raw.get("record_stream"), "main")
    tile_stream = normalize_stream_name(raw.get("tile_stream"), "sub")

    onvif_port = 8000 if vendor == "reolink" else 80
    onvif_url = raw.get("onvif_url") or (f"http://{host}:{onvif_port}/onvif/device_service" if host else "")
    isapi_base_url = raw.get("isapi_base_url") or (f"http://{host}" if host and vendor == "hikvision" else "")
    if vendor == "hikvision":
        onvif_url = refresh_hikvision_onvif_url(onvif_url, host)
        isapi_base_url = refresh_hikvision_isapi_base_url(isapi_base_url, host)

    return {
        "id": camera_id,
        "name": name,
        "vendor": vendor,
        "host": host,
        "username": username,
        "password": password,
        "rtsp_main": rtsp_main,
        "rtsp_sub": rtsp_sub,
        "camera_number": camera_number,
        "access_protocol": access_protocol,
        "rtsp_transport": rtsp_transport,
        "rtsp_main_channel": rtsp_main_channel,
        "rtsp_sub_channel": rtsp_sub_channel,
        "onvif_url": onvif_url,
        "isapi_base_url": isapi_base_url,
        "enabled": bool(raw.get("enabled")),
        "record": bool(raw.get("record", True)),
        "low_latency": bool(raw.get("low_latency", True)),
        "snapshot_stream": snapshot_stream,
        "live_stream": live_stream,
        "record_stream": record_stream,
        "tile_stream": tile_stream,
    }


def normalize_config(payload: dict) -> dict:
    cameras = payload.get("cameras") if isinstance(payload, dict) else []
    if not isinstance(cameras, list):
        cameras = []

    normalized = [normalize_camera(camera, index + 1) for index, camera in enumerate(cameras[:8])]
    server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
    storage = payload.get("storage") if isinstance(payload.get("storage"), dict) else {}
    live = payload.get("live") if isinstance(payload.get("live"), dict) else {}
    nvr = payload.get("nvr") if isinstance(payload.get("nvr"), dict) else {}
    webrtc_ice_transport = normalize_webrtc_ice_transport(
        live.get("mobile_webrtc_ice_transport"),
        live.get("mobile_webrtc_tcp_only"),
    )
    return {
        "server": {
            "listen": server.get("listen") or "0.0.0.0:8088",
            "public_url": server.get("public_url") or "",
        },
        "storage": {
            "recordings_dir": storage.get("recordings_dir") or "/media/edge-of-infinity/recordings",
            "database_path": storage.get("database_path") or "/homeassistant/edge/edge.db",
            "retention_days": safe_int(storage.get("retention_days"), 14),
        },
        "live": {
            "engine": live.get("engine") if live.get("engine") in STREAM_ENGINES else "janus_webrtc",
            "remote_access_mode": normalize_remote_access_mode(live.get("remote_access_mode")),
            "frame_interval_ms": safe_int(live.get("frame_interval_ms"), 1200),
            "tile_fps": clamp_int(live.get("tile_fps"), 5, 1, 10),
            "tile_max_width": clamp_int(live.get("tile_max_width"), 960, 320, 1920),
            "prebuffer_enabled": safe_bool(live.get("prebuffer_enabled"), True),
            "always_on_enabled": safe_bool(live.get("always_on_enabled"), True),
            "always_on_stream_scope": normalize_always_on_stream_scope(live.get("always_on_stream_scope")),
            "prebuffer_local_ms": clamp_int(live.get("prebuffer_local_ms"), 4000, 0, 10000),
            "prebuffer_remote_ms": clamp_int(live.get("prebuffer_remote_ms"), 2000, 0, 10000),
            "mobile_webrtc_public_hosts": live.get("mobile_webrtc_public_hosts") or ",".join(MEDIAMTX_WEBRTC_PUBLIC_HOSTS),
            "mobile_webrtc_public_url": (live.get("mobile_webrtc_public_url", MEDIAMTX_WEBRTC_PUBLIC_URL)).rstrip("/"),
            "mobile_webrtc_stun_url": live.get("mobile_webrtc_stun_url") if live.get("mobile_webrtc_stun_url") is not None else MEDIAMTX_WEBRTC_STUN_URL,
            "mobile_webrtc_turn_url": live.get("mobile_webrtc_turn_url", MEDIAMTX_WEBRTC_TURN_URL),
            "mobile_webrtc_turn_username": live.get("mobile_webrtc_turn_username", MEDIAMTX_WEBRTC_TURN_USERNAME),
            "mobile_webrtc_turn_password": live.get("mobile_webrtc_turn_password", MEDIAMTX_WEBRTC_TURN_PASSWORD),
            "mobile_webrtc_ice_transport": webrtc_ice_transport,
            "mobile_webrtc_tcp_only": webrtc_ice_transport == "tcp",
        },
        "nvr": {
            "segment_seconds": clamp_int(nvr.get("segment_seconds"), 10, 2, 300),
            "retention_days": clamp_int(nvr.get("retention_days"), safe_int(storage.get("retention_days"), 14), 1, 365),
            "playback_cache_segments": clamp_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS, 12, RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS),
            "copy_all_streams": bool(nvr.get("copy_all_streams", True)),
            "browser_playback": nvr.get("browser_playback") if nvr.get("browser_playback") in ("auto_h264", "copy", "h264") else "auto_h264",
        },
        "cameras": normalized,
        "future_vendors": payload.get("future_vendors") or ["dahua", "onvif", "rtsp"],
    }


def preserve_submitted_stream_choices(payload: dict, *source_payloads: dict) -> dict:
    """Keep explicit UI stream choices after normalization and persistence.

    Index is the authoritative match because the settings form is ordered.
    ID is only a fallback for future API callers that may omit indexes.
    """
    cameras = payload.get("cameras") if isinstance(payload, dict) else []
    if not isinstance(cameras, list):
        return payload
    cameras_by_id = {
        str(camera.get("id")): camera
        for camera in cameras
        if isinstance(camera, dict) and camera.get("id")
    }
    for source_payload in source_payloads:
        raw_cameras = source_payload.get("cameras") if isinstance(source_payload, dict) else []
        if not isinstance(raw_cameras, list):
            continue
        for index, raw_camera in enumerate(raw_cameras):
            if not isinstance(raw_camera, dict):
                continue
            target = cameras[index] if index < len(cameras) else None
            if target is None and raw_camera.get("id"):
                target = cameras_by_id.get(str(raw_camera.get("id")))
            if not isinstance(target, dict):
                continue
            for field in STREAM_ROLE_FIELDS:
                value = raw_camera.get(field)
                if value in ("main", "sub"):
                    target[field] = value
    return payload


def merge_existing_camera_values(raw_payload: dict) -> dict:
    """Preserve saved connection fields when the UI posts blanks."""
    if not isinstance(raw_payload, dict):
        return {}
    raw_cameras = raw_payload.get("cameras")
    if not isinstance(raw_cameras, list):
        return raw_payload

    existing = load_config()
    existing_cameras = existing.get("cameras") if isinstance(existing.get("cameras"), list) else []
    existing_by_id = {
        str(camera.get("id")): camera
        for camera in existing_cameras
        if isinstance(camera, dict) and camera.get("id")
    }
    preserve_if_blank = (
        "id",
        "name",
        "vendor",
        "host",
        "username",
        "password",
        "rtsp_main",
        "rtsp_sub",
        "camera_number",
        "access_protocol",
        "rtsp_transport",
        "rtsp_main_channel",
        "rtsp_sub_channel",
        "onvif_url",
        "isapi_base_url",
    )
    merged_cameras = []
    for index, raw_camera in enumerate(raw_cameras):
        if not isinstance(raw_camera, dict):
            continue
        existing_camera = {}
        if index < len(existing_cameras) and isinstance(existing_cameras[index], dict):
            existing_camera = existing_cameras[index]
        if raw_camera.get("id"):
            existing_camera = existing_by_id.get(str(raw_camera.get("id")), existing_camera)

        merged_camera = dict(raw_camera)
        for field in preserve_if_blank:
            if merged_camera.get(field) in ("", None) and existing_camera.get(field) not in ("", None):
                merged_camera[field] = existing_camera[field]
        for field in STREAM_ROLE_FIELDS:
            value = raw_camera.get(field)
            if value in ("main", "sub"):
                merged_camera[field] = value
        connection_changed = any(raw_camera.get(field) is not None and raw_camera.get(field) != existing_camera.get(field) for field in ("host", "username", "password"))
        if connection_changed and merged_camera.get("vendor") != "hikvision":
            for field in ("rtsp_main", "rtsp_sub"):
                merged_camera[field] = rebind_camera_rtsp(merged_camera.get(field) or "", merged_camera.get("host") or "", merged_camera.get("username") or "", merged_camera.get("password") or "")
            old_endpoint = merged_camera.get("onvif_url") or ""
            if old_endpoint and existing_camera.get("host") != merged_camera.get("host"):
                endpoint = urlparse(old_endpoint)
                target = urlparse("http://" + (merged_camera.get("host") or ""))
                merged_camera["onvif_url"] = urlunparse(endpoint._replace(netloc=f"{target.hostname}:{target.port or endpoint.port or 80}"))
        merged_cameras.append(merged_camera)

    return {**existing, **raw_payload, "cameras": merged_cameras}


def redact_config(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ("password", "api_key") or key.endswith("_password"):
                result[key] = "***" if item else ""
            elif key.startswith("rtsp_") or key.endswith("_rtsp"):
                result[key] = redact_rtsp(str(item or ""))
            else:
                result[key] = redact_config(item)
        return result
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return value


def save_debug_payload(raw_payload: dict, merged_payload: dict, normalized_payload: dict, final_payload: dict) -> None:
    debug_payload = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "raw_summary": config_summary(raw_payload),
        "merged_summary": config_summary(merged_payload),
        "normalized_summary": config_summary(normalized_payload),
        "final_summary": config_summary(final_payload),
        "raw": redact_config(raw_payload),
        "merged": redact_config(merged_payload),
        "normalized": redact_config(normalized_payload),
        "final": redact_config(final_payload),
    }
    write_json(HOME_DIR / "last-save-debug.json", debug_payload)


def safe_json_file(path: Path) -> dict:
    payload = read_json(path, {})
    if isinstance(payload, dict):
        return redact_config(payload)
    return {"value": redact_config(payload)}


def disk_usage_payload(path: Path) -> dict:
    requested = Path(path)
    checked = requested
    while not checked.exists() and checked != checked.parent:
        checked = checked.parent
    try:
        usage = shutil.disk_usage(checked)
    except OSError as error:
        return {"path": str(requested), "checked_path": str(checked), "error": str(error)}
    used_percent = round((usage.used / usage.total) * 100, 2) if usage.total else 0
    free_percent = round((usage.free / usage.total) * 100, 2) if usage.total else 0
    return {
        "path": str(requested),
        "checked_path": str(checked),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": used_percent,
        "free_percent": free_percent,
    }


def proc_meminfo_payload() -> dict:
    path = Path("/proc/meminfo")
    if not path.exists():
        return {}
    values = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            match = re.search(r"\d+", raw_value)
            if match:
                values[key] = int(match.group(0)) * 1024
    except OSError as error:
        return {"error": str(error)}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    if total:
        values["available_percent"] = round((available / total) * 100, 2)
        values["used_percent"] = round(100 - values["available_percent"], 2)
    return values


def proc_status_payload(pid: int | str = "self") -> dict:
    path = Path("/proc") / str(pid) / "status"
    if not path.exists():
        return {}
    wanted = {
        "Name",
        "State",
        "VmPeak",
        "VmSize",
        "VmRSS",
        "VmData",
        "VmStk",
        "Threads",
        "voluntary_ctxt_switches",
        "nonvoluntary_ctxt_switches",
    }
    values = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, raw_value = line.split(":", 1)
            if key not in wanted:
                continue
            value = raw_value.strip()
            if value.endswith(" kB"):
                match = re.search(r"\d+", value)
                values[key] = int(match.group(0)) * 1024 if match else value
            elif re.fullmatch(r"\d+", value):
                values[key] = int(value)
            else:
                values[key] = value
    except OSError as error:
        return {"error": str(error)}
    return values


def proc_uptime_seconds() -> float | None:
    path = Path("/proc/uptime")
    if not path.exists():
        return None
    try:
        raw_value = path.read_text(encoding="utf-8", errors="replace").split()[0]
        return round(float(raw_value), 2)
    except (OSError, ValueError, IndexError):
        return None


def process_uptime_seconds(pid: int | str = "self") -> float | None:
    stat_path = Path("/proc") / str(pid) / "stat"
    system_uptime = proc_uptime_seconds()
    if not stat_path.exists() or system_uptime is None:
        return None
    try:
        raw_stat = stat_path.read_text(encoding="utf-8", errors="replace")
        fields = raw_stat[raw_stat.rfind(")") + 2:].split()
        start_ticks = int(fields[19])
        clock_ticks = os.sysconf("SC_CLK_TCK")
        return round(max(0.0, system_uptime - (start_ticks / clock_ticks)), 2)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def open_file_descriptor_count() -> int | None:
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.exists():
        return None
    try:
        return len(list(fd_dir.iterdir()))
    except OSError:
        return None


def system_diagnostics_payload(recordings_dir: Path) -> dict:
    cpu_count = os.cpu_count() or 1
    try:
        loadavg = [round(value, 3) for value in os.getloadavg()]
    except (AttributeError, OSError):
        loadavg = []
    load_per_core = round(loadavg[0] / cpu_count, 3) if loadavg else None
    active_recorders = []
    for key, process in RECORDING_PROCESSES.items():
        try:
            if process and process.poll() is None:
                active_recorders.append({
                    "key": key,
                    "pid": process.pid,
                    "returncode": process.returncode,
                    "status": proc_status_payload(process.pid),
                })
        except Exception:
            continue
    cache_workers = sorted(RECORDING_CACHE_WORKERS)
    return {
        "cpu_count": cpu_count,
        "loadavg": loadavg,
        "load_per_core_1m": load_per_core,
        "memory": proc_meminfo_payload(),
        "recordings_disk": disk_usage_payload(recordings_dir),
        "edge_disk": disk_usage_payload(HOME_DIR),
        "ffmpeg_path": shutil.which("ffmpeg") or "",
        "ffprobe_path": shutil.which("ffprobe") or "",
        "active_recorders": active_recorders,
        "active_recorder_count": len(active_recorders),
        "recording_cache_workers": cache_workers,
        "recording_cache_backlog": len(cache_workers),
        "recording_cache_worker_limit": 1,
        "recording_thumbnail_worker_limit": 1,
        "system_uptime_seconds": proc_uptime_seconds(),
        "process": {
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "uptime_seconds": process_uptime_seconds(),
            "open_file_descriptors": open_file_descriptor_count(),
            "status": proc_status_payload(),
        },
    }


def diagnostic_item(severity: str, title: str, detail: str, payload: dict | None = None) -> dict:
    return {
        "severity": severity if severity in ("ok", "warning", "error") else "warning",
        "title": title,
        "detail": detail,
        "payload": redact_config(payload or {}),
    }


def diagnostic_severity_from_text(text: str) -> str:
    lowered = str(text or "").lower()
    error_patterns = (
        " error",
        "error:",
        "failed",
        "exception",
        "traceback",
        "ui_global_error",
        "ui_unhandled_rejection",
        "ui_boot_error",
        "ui_recording_video_error",
        "cannot ",
        "not found",
    )
    warning_patterns = (
        "warning",
        "stalled",
        "waiting",
        "timeout",
        "too slow",
        "non-monotonic",
        "queue input is backward",
        "discarding",
        "stale",
        "blocked",
    )
    if any(pattern in lowered for pattern in error_patterns):
        return "error"
    if any(pattern in lowered for pattern in warning_patterns):
        return "warning"
    return "ok"


def diagnostic_from_tail(title: str, tail: str) -> dict:
    if title == "Recording cache" and tail:
        lowered = tail.lower()
        if "recording_cache_build_error" in lowered or "edge recording cache error" in lowered:
            severity = "error"
        elif "recording_cache_build_done" in lowered or "recording_cache_build_scheduled" in lowered:
            severity = "ok"
        else:
            severity = diagnostic_severity_from_text(tail)
    else:
        severity = diagnostic_severity_from_text(tail)
    detail = "No blocking issue detected." if severity == "ok" else "Recent log tail contains entries that need attention."
    return diagnostic_item(severity, title, detail, {"tail": tail[-2000:] if tail else ""})


def runtime_parameters_payload(config: dict, hardware: dict, recordings_dir: Path) -> dict:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    live = config.get("live") if isinstance(config.get("live"), dict) else {}
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    return redact_config(
        {
            "paths": {
                "home_dir": str(HOME_DIR),
                "data_dir": str(DATA_DIR),
                "config_path": str(CONFIG_PATH),
                "addon_config_path": str(ADDON_CONFIG_PATH),
                "recordings_dir": str(recordings_dir),
                "recording_cache_dir": str(RECORDING_CACHE_DIR),
                "recording_thumb_dir": str(RECORDING_THUMB_DIR),
            },
            "storage": storage,
            "live": live,
            "nvr": nvr,
            "runtime_limits": {
                "min_recording_file_ready_seconds": MIN_RECORDING_FILE_READY_SECONDS,
                "recording_live_edge_delay_seconds": RECORDING_LIVE_EDGE_DELAY_SECONDS,
                "recording_file_min_playable_bytes": RECORDING_FILE_MIN_PLAYABLE_BYTES,
                "recording_file_header_check_bytes": RECORDING_FILE_HEADER_CHECK_BYTES,
                "recording_file_header_grace_seconds": RECORDING_FILE_HEADER_GRACE_SECONDS,
                "recording_cache_refresh_seconds": RECORDING_CACHE_REFRESH_SECONDS,
                "recording_cache_max_segments": RECORDING_CACHE_MAX_SEGMENTS,
                "recording_cache_absolute_max_segments": RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS,
                "recording_cache_min_rebuild_seconds": RECORDING_CACHE_MIN_REBUILD_SECONDS,
                "recording_cache_min_new_segments": RECORDING_CACHE_MIN_NEW_SEGMENTS,
                "recording_cache_min_timeout_seconds": RECORDING_CACHE_MIN_TIMEOUT_SECONDS,
                "recording_cache_max_timeout_seconds": RECORDING_CACHE_MAX_TIMEOUT_SECONDS,
                "active_day_cache_defer_reason": ACTIVE_DAY_CACHE_DEFER_REASON,
                "recording_ensure_min_seconds": RECORDING_ENSURE_MIN_SECONDS,
                "recording_cache_worker_limit": hardware.get("recording_cache_worker_limit"),
                "recording_thumbnail_worker_limit": hardware.get("recording_thumbnail_worker_limit"),
                "recording_thumbnail_warmup_interval_seconds": RECORDING_THUMBNAIL_WARMUP_INTERVAL_SECONDS,
                "recording_thumbnail_warmup_per_camera": RECORDING_THUMBNAIL_WARMUP_PER_CAMERA,
            },
            "runtime_state": {
                "active_recorder_count": hardware.get("active_recorder_count"),
                "active_recorders": hardware.get("active_recorders"),
                "recording_cache_backlog": hardware.get("recording_cache_backlog"),
                "recording_cache_workers": hardware.get("recording_cache_workers"),
            },
            "engines": {
                "mediamtx_enabled": MEDIAMTX_ENABLED,
                "mediamtx_record": MEDIAMTX_RECORD,
                "janus_enabled": JANUS_ENABLED,
                "rtsp_port": MEDIAMTX_RTSP_PORT,
                "hls_port": MEDIAMTX_HLS_PORT,
                "webrtc_whep_port": MEDIAMTX_WEBRTC_PORT,
                "webrtc_ice_udp_port": MEDIAMTX_WEBRTC_UDP_PORT,
                "srt_port": MEDIAMTX_SRT_PORT,
                "api_port": MEDIAMTX_API_PORT,
            },
        }
    )


def build_panel_diagnostics(config: dict, hardware: dict, tails: dict, recording_logs: list[dict]) -> list[dict]:
    diagnostics = []
    ffmpeg_path = hardware.get("ffmpeg_path") or ""
    ffprobe_path = hardware.get("ffprobe_path") or ""
    diagnostics.append(
        diagnostic_item(
            "ok" if ffmpeg_path else "error",
            "FFmpeg",
            "FFmpeg is available for live, thumbnails, cache, and NVR recording." if ffmpeg_path else "FFmpeg is missing in the add-on runtime.",
            {"path": ffmpeg_path},
        )
    )
    diagnostics.append(
        diagnostic_item(
            "ok" if ffprobe_path else "warning",
            "FFprobe",
            "FFprobe is available for codec diagnostics." if ffprobe_path else "FFprobe is missing, so codec diagnostics will be limited.",
            {"path": ffprobe_path},
        )
    )

    load_per_core = hardware.get("load_per_core_1m")
    if isinstance(load_per_core, (int, float)):
        severity = "error" if load_per_core >= 1.25 else "warning" if load_per_core >= 0.75 else "ok"
        diagnostics.append(
            diagnostic_item(
                severity,
                "CPU load",
                f"1 minute load per CPU core is {load_per_core}.",
                {"loadavg": hardware.get("loadavg"), "cpu_count": hardware.get("cpu_count")},
            )
        )
    else:
        diagnostics.append(diagnostic_item("warning", "CPU load", "CPU load average is not available on this platform.", {}))

    memory = hardware.get("memory") if isinstance(hardware.get("memory"), dict) else {}
    available_percent = memory.get("available_percent")
    if isinstance(available_percent, (int, float)):
        severity = "error" if available_percent < 5 else "warning" if available_percent < 15 else "ok"
        diagnostics.append(diagnostic_item(severity, "Memory", f"Available memory is {available_percent}%.", {"memory": memory}))

    process_payload = hardware.get("process") if isinstance(hardware.get("process"), dict) else {}
    process_status = process_payload.get("status") if isinstance(process_payload.get("status"), dict) else {}
    thread_count = process_status.get("Threads")
    fd_count = process_payload.get("open_file_descriptors")
    process_severity = "warning" if isinstance(thread_count, int) and thread_count > 80 else "ok"
    diagnostics.append(
        diagnostic_item(
            process_severity,
            "Panel process",
            f"Panel process has {thread_count if thread_count is not None else 'unknown'} thread(s) and {fd_count if fd_count is not None else 'unknown'} open file descriptor(s).",
            process_payload,
        )
    )

    for title, disk in (("Recordings disk", hardware.get("recordings_disk")), ("Edge config disk", hardware.get("edge_disk"))):
        if not isinstance(disk, dict):
            continue
        free_percent = disk.get("free_percent")
        if isinstance(free_percent, (int, float)):
            severity = "error" if free_percent < 5 else "warning" if free_percent < 15 else "ok"
            diagnostics.append(diagnostic_item(severity, title, f"Free disk space is {free_percent}%.", disk))
        elif disk.get("error"):
            diagnostics.append(diagnostic_item("warning", title, f"Could not read disk usage: {disk.get('error')}", disk))

    active_recorders = hardware.get("active_recorders") if isinstance(hardware.get("active_recorders"), list) else []
    diagnostics.append(diagnostic_item("ok", "NVR recorders", f"{len(active_recorders)} active recorder process(es).", {"active_recorders": active_recorders}))
    cache_workers = hardware.get("recording_cache_workers") if isinstance(hardware.get("recording_cache_workers"), list) else []
    cache_limit = safe_int(hardware.get("recording_cache_worker_limit"), 1)
    cache_backlog = safe_int(hardware.get("recording_cache_backlog"), len(cache_workers))
    cache_severity = "warning" if cache_backlog > cache_limit else "ok"
    diagnostics.append(
        diagnostic_item(
            cache_severity,
            "Playback cache worker",
            f"{cache_backlog} daily playback cache job(s) queued/running; worker limit is {cache_limit}." if cache_workers else "No playback cache rebuild is running.",
            {"workers": cache_workers, "backlog": cache_backlog, "worker_limit": cache_limit},
        )
    )

    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    cache_segments = safe_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS)
    diagnostics.append(
        diagnostic_item(
            "warning" if cache_segments < 8640 else "ok",
            "Daily NVR cache window",
            f"Playback cache segment budget is {cache_segments}; 8640 ten-second clips cover a full day.",
            {"playback_cache_segments": cache_segments},
        )
    )

    for title, tail in tails.items():
        diagnostics.append(diagnostic_from_tail(title, tail))
    for item in recording_logs[:4]:
        diagnostics.append(diagnostic_from_tail(f"Recording FFmpeg {Path(item.get('path') or '').name}", item.get("tail") or ""))
    return diagnostics


def collect_panel_logs() -> dict:
    config = load_config()
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    recordings_dir = Path(storage.get("recordings_dir") or "/media/edge-of-infinity/recordings")
    recording_logs = []
    try:
        for log_path in sorted(recordings_dir.glob("**/ffmpeg.log"), key=lambda item: item.stat().st_mtime, reverse=True)[:8]:
            recording_logs.append({
                "path": str(log_path),
                "tail": redact_rtsp(read_text_tail(log_path, 8000)),
            })
    except OSError as error:
        recording_logs.append({"path": str(recordings_dir), "tail": f"recording_log_scan_error: {error}"})
    tails = {
        "Edge debug": redact_rtsp(read_text_tail(DEBUG_LOG_PATH, 16000)),
        "Recording stream": redact_rtsp(read_text_tail(RECORDING_STREAM_LOG_PATH, 12000)),
        "Recording cache": redact_rtsp(read_text_tail(RECORDING_CACHE_LOG_PATH, 12000)),
        "Recording thumbnails": redact_rtsp(read_text_tail(RECORDING_THUMB_LOG_PATH, 12000)),
    }
    hardware = system_diagnostics_payload(recordings_dir)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "server_version": SERVER_VERSION,
        "ui_build": UI_BUILD,
        "authoritative_config": str(CONFIG_PATH),
        "addon_config_mirror": str(ADDON_CONFIG_PATH),
        "panel_mirror_config": str(PANEL_CONFIG_PATH),
        "config_summary": config_summary(config),
        "hardware": hardware,
        "runtime_parameters": runtime_parameters_payload(config, hardware, recordings_dir),
        "diagnostics": build_panel_diagnostics(config, hardware, tails, recording_logs),
        "edge_debug": tails["Edge debug"],
        "last_save_debug": safe_json_file(HOME_DIR / "last-save-debug.json"),
        "last_runtime_sync": safe_json_file(HOME_DIR / "edge.last-runtime-sync.json"),
        "last_saved_config": safe_json_file(HOME_DIR / "edge.last-saved.json"),
        "addon_config_file": safe_json_file(ADDON_CONFIG_PATH),
        "panel_config": safe_json_file(PANEL_CONFIG_PATH),
        "runtime_config_file": safe_json_file(CONFIG_PATH),
        "runtime_config": engine_runtime_status(),
        "runtime_mediamtx_config": redact_rtsp(read_text_tail(MEDIAMTX_CONFIG_PATH, 20000)),
        "runtime_janus_streaming_config": redact_rtsp(read_text_tail(JANUS_CONFIG_DIR / "janus.plugin.streaming.jcfg", 20000)),
        "recording_stream_log": tails["Recording stream"],
        "recording_cache_log": tails["Recording cache"],
        "recording_thumbnail_log": tails["Recording thumbnails"],
        "recording_logs": recording_logs,
    }


def camera_from_payload(payload: dict) -> dict:
    raw_camera = payload.get("camera") if isinstance(payload.get("camera"), dict) else {}
    config = load_config()
    stored_camera = {}
    camera_index = payload.get("index")
    if isinstance(camera_index, int) and 0 <= camera_index < len(config.get("cameras", [])):
        stored_camera = config["cameras"][camera_index]
    elif str(camera_index).isdigit():
        index = int(str(camera_index))
        if 0 <= index < len(config.get("cameras", [])):
            stored_camera = config["cameras"][index]
    if not stored_camera and raw_camera.get("id"):
        stored_camera = next((item for item in config.get("cameras", []) if item.get("id") == raw_camera.get("id")), {})

    merged = {**stored_camera, **{key: value for key, value in raw_camera.items() if value not in ("", None)}}
    return normalize_camera(merged, int(camera_index) + 1 if str(camera_index).isdigit() else 1)


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
            "camera_number",
            "access_protocol",
            "rtsp_transport",
            "rtsp_main_channel",
            "rtsp_sub_channel",
            "onvif_url",
            "isapi_base_url",
            "enabled",
            "record",
            "low_latency",
            "snapshot_stream",
            "live_stream",
            "tile_stream",
            "record_stream",
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
    storage = payload.get("storage") if isinstance(payload.get("storage"), dict) else {}
    try:
        retention_days = int(storage.get("retention_days") or 14)
    except (TypeError, ValueError) as error:
        raise ValueError("Retention days must be a number.") from error
    if retention_days < 1 or retention_days > 365:
        raise ValueError("Retention days must be between 1 and 365.")
    live = payload.get("live") if isinstance(payload.get("live"), dict) else {}
    try:
        frame_interval_ms = int(live.get("frame_interval_ms") or 1200)
    except (TypeError, ValueError) as error:
        raise ValueError("Live frame interval must be a number.") from error
    if frame_interval_ms < 250 or frame_interval_ms > 10000:
        raise ValueError("Live frame interval must be between 250 and 10000 ms.")


def effective_config_from_payload(raw_payload: dict) -> dict:
    return normalize_config(raw_payload if isinstance(raw_payload, dict) else {"cameras": []})


def authoritative_config_from_payload(raw_payload: dict) -> dict:
    return normalize_config(raw_payload if isinstance(raw_payload, dict) else {"cameras": []})


def load_config() -> dict:
    with CONFIG_LOCK:
        if CONFIG_PATH.exists():
            # An existing authoritative file must never silently roll back to a mirror/backup.
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("cameras"), list):
                raise ValueError("Invalid authoritative config; repair edge.json instead of restoring old settings automatically.")
            config = effective_config_from_payload(raw)
            try:
                if read_json(PANEL_CONFIG_PATH, {}) != config:
                    write_json(PANEL_CONFIG_PATH, config)
            except OSError as error:
                write_debug_event("config_mirror_error", {"path": str(PANEL_CONFIG_PATH), "error": str(error)})
            return config

        for source in (PANEL_CONFIG_PATH, ADDON_CONFIG_PATH, CONFIG_BACKUP_PATH):
            raw = read_json(source, None)
            if not isinstance(raw, dict) or not isinstance(raw.get("cameras"), list):
                continue
            config = authoritative_config_from_payload(raw)
            if config.get("cameras"):
                write_debug_event("config_source_selected", {"source": str(source), "runtime_config": str(CONFIG_PATH)})
                return commit_panel_config(config)
        return normalize_config({"cameras": []})


def commit_panel_config(payload: dict) -> dict:
    with CONFIG_LOCK:
        committed = authoritative_config_from_payload(payload)
        write_json(CONFIG_PATH, committed)
        for mirror in dict.fromkeys((PANEL_CONFIG_PATH, ADDON_CONFIG_PATH)):
            if mirror == CONFIG_PATH:
                continue
            try:
                write_json(mirror, committed)
            except OSError as error:
                # The authoritative file is already saved; a diagnostic mirror is best effort.
                write_debug_event("config_mirror_error", {"path": str(mirror), "error": str(error)})
        clear_legacy_override_files()
        return committed


def persist_config_payload(request_payload: dict) -> tuple[dict, dict, dict, dict, dict]:
    with CONFIG_LOCK:
        raw, merged, normalized, final = prepare_config_for_save(request_payload)
        backup_config()
        try:
            backup_panel_config()
        except OSError as error:
            write_debug_event("config_mirror_backup_error", {"error": str(error)})
        committed = commit_panel_config(final)
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if saved != committed:
            raise OSError("Configuration verification failed; saved settings differ from submitted settings.")
        try:
            write_json(HOME_DIR / "edge.last-saved.json", saved)
        except OSError as error:
            write_debug_event("config_last_saved_mirror_error", {"error": str(error)})
        return raw, merged, normalized, final, saved


def prepare_config_for_save(raw_payload: dict) -> tuple[dict, dict, dict, dict]:
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    raw_cameras = raw_payload.get("cameras") if isinstance(raw_payload, dict) else None
    if not raw_cameras:
        existing = load_config()
        if existing.get("cameras"):
            raw_payload = {**existing, **raw_payload, "cameras": existing["cameras"]}
    merged_payload = merge_existing_camera_values(raw_payload)
    validate_config_for_save(merged_payload)
    normalized_payload = normalize_config(merged_payload)
    final_payload = preserve_submitted_stream_choices(
        json.loads(json.dumps(normalized_payload)),
        merged_payload,
        raw_payload,
    )
    validate_config_for_save(final_payload)
    return raw_payload, merged_payload, normalized_payload, final_payload


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


def probe_rtsp_stream(stream: str, timeout: int = 8) -> dict:
    if not stream:
        return {}
    probe = run_json(
        [
            "ffprobe",
            "-rtsp_transport",
            "tcp",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,r_frame_rate,bit_rate,sample_rate,channels",
            "-of",
            "json",
            stream,
        ],
        timeout=timeout,
    )
    streams = (probe or {}).get("streams", []) if probe else []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
    return {
        "video": video_stream,
        "audio": audio_stream,
    }


def snapshot_paths(target_id: str) -> tuple[Path, Path]:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SNAPSHOT_DIR / f"{target_id}.jpg", DATA_SNAPSHOT_DIR / f"{target_id}.jpg"


def capture_isapi_snapshot(camera: dict, target_id: str) -> tuple[str, str]:
    if (camera.get("vendor") or "").lower() != "hikvision":
        return "", ""
    base = isapi_base(camera)
    username = camera.get("username") or ""
    password = camera.get("password") or ""
    if not base or not username:
        return "", ""

    snapshot_stream = normalize_stream_name(camera.get("snapshot_stream"), "sub")
    channel = stream_channel(camera, snapshot_stream)
    home_path, data_path = snapshot_paths(target_id)
    tmp_path = data_path.with_suffix(".jpg.tmp")
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--anyauth",
        "--user",
        f"{username}:{password}",
        "--connect-timeout",
        "2",
        "--max-time",
        "4",
        "--output",
        str(tmp_path),
        "--write-out",
        "%{http_code}",
        f"{base}/ISAPI/Streaming/channels/{channel}/picture?snapShotImageType=JPEG",
    ]
    started_at = time.monotonic()
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        write_debug_event("snapshot_isapi_error", {
            "camera_id": camera.get("id"),
            "target": target_id,
            "channel": channel,
            "error": str(error),
        })
        return "", ""

    status_text = result.stdout.decode("utf-8", errors="replace").strip()
    try:
        status = int(status_text)
    except ValueError:
        status = 0
    if result.returncode != 0 or status < 200 or status >= 300 or not tmp_path.exists() or tmp_path.stat().st_size < 100:
        write_debug_event("snapshot_isapi_error", {
            "camera_id": camera.get("id"),
            "target": target_id,
            "channel": channel,
            "status": status,
            "returncode": result.returncode,
            "stderr": result.stderr.decode("utf-8", errors="replace")[-400:],
        })
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return "", ""

    tmp_path.replace(data_path)
    shutil.copyfile(data_path, home_path)
    write_debug_event("snapshot_isapi_done", {
        "camera_id": camera.get("id"),
        "target": target_id,
        "channel": channel,
        "bytes": data_path.stat().st_size,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
    })
    return f"snapshots/{target_id}.jpg", f"snapshots/{target_id}.jpg"


def capture_ffmpeg_snapshot(camera: dict, target_id: str) -> tuple[str, str]:
    stream = recording_source_stream(camera, 0, camera.get("snapshot_stream") or "sub")[0]
    if not stream:
        return "", ""

    home_path, data_path = snapshot_paths(target_id)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
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


def capture_snapshot(camera: dict, target_id: str) -> tuple[str, str]:
    snapshot_path, snapshot_url = capture_isapi_snapshot(camera, target_id)
    if snapshot_path and snapshot_url:
        return snapshot_path, snapshot_url
    return capture_ffmpeg_snapshot(camera, target_id)


def camera_stream(camera: dict, stream_name: str) -> str:
    stream = camera.get("rtsp_main") if stream_name == "main" else camera.get("rtsp_sub")
    return stream or camera.get("rtsp_main") or ""


def recording_key(camera: dict, index: int) -> str:
    return f"{safe_id(camera.get('id') or 'camera')}_{index}"


def mediamtx_path(camera: dict, index: int, stream_name: str) -> str:
    camera_id = safe_id(camera.get("id") or f"camera_{index + 1}")
    stream_name = normalize_stream_name(stream_name, "sub")
    return f"{camera_id}_{stream_name}"


def mediamtx_rtsp_url(camera: dict, index: int, stream_name: str) -> str:
    return f"rtsp://127.0.0.1:{MEDIAMTX_RTSP_PORT}/{mediamtx_path(camera, index, stream_name)}"


_HA_RESTREAM_HOST = None


def home_assistant_restream_payload(config: dict) -> dict:
    """Advertise shared NVR RTSP paths reachable from HA, never camera credentials."""
    global _HA_RESTREAM_HOST
    if _HA_RESTREAM_HOST is None:
        host = os.environ.get("EDGE_HA_RTSP_HOST", "").strip()
        if not host and os.environ.get("SUPERVISOR_TOKEN"):
            try:
                host = str(supervisor_self_request("/addons/self/info").get("hostname") or "")
            except (OSError, ValueError, RuntimeError):
                pass
        if not host:
            import socket
            host = socket.gethostname().replace("_", "-")
        _HA_RESTREAM_HOST = host if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host) else ""
    available = bool((MEDIAMTX_ENABLED or EDGE_RTC_ENABLED) and _HA_RESTREAM_HOST)
    cameras = []
    for index, camera in enumerate(config.get("cameras", [])):
        streams = {}
        recording = camera.get("record_stream") or "main"
        if not camera.get("rtsp_" + recording):
            recording = "main"
        if available and camera.get("enabled"):
            for profile in ("main", "sub"):
                if EDGE_RTC_ENABLED and profile != recording:
                    continue
                if camera.get("rtsp_" + profile):
                    streams[profile] = f"rtsp://{_HA_RESTREAM_HOST}:{MEDIAMTX_RTSP_PORT}/{mediamtx_path(camera, index, profile)}"
        streams["nvr"] = streams.get(recording) or streams.get("main") or ""
        row = {"camera_id": camera.get("id"), "streams": streams}
        if EDGE_RTC_ENABLED and available:
            row["edge_rtc"] = {"api": f"http://{_HA_RESTREAM_HOST}:{MEDIAMTX_API_PORT}", "path": mediamtx_path(camera, index, recording), "profile": recording}
        cameras.append(row)
    return {"available": available, "cameras": cameras, "source": "edge_rtc" if EDGE_RTC_ENABLED else "edge_nvr", "version": APP_VERSION}


def recording_source_stream(camera: dict, index: int, stream_name: str) -> tuple[str, str]:
    if MEDIAMTX_ENABLED or EDGE_RTC_ENABLED:
        return mediamtx_rtsp_url(camera, index, stream_name), ("edge_rtc_shared" if EDGE_RTC_ENABLED else "mediamtx_rebroadcast")
    return camera_stream(camera, stream_name), "camera_direct"


def janus_mount_id(index: int, stream_name: str) -> int:
    stream_offset = 1 if normalize_stream_name(stream_name, "sub") == "main" else 2
    return 10000 + (index * 10) + stream_offset


def yaml_string(value: str) -> str:
    return json.dumps(str(value or ""))


def bool_yaml(value: bool) -> str:
    return "true" if bool(value) else "false"


def csv_values(value: str) -> list[str]:
    seen = set()
    values = []
    for item in str(value or "").split(","):
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            values.append(cleaned)
            seen.add(cleaned)
    return values


def append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        return [*values, value]
    return values


def live_value(config: dict, key: str, fallback=""):
    live = config.get("live") if isinstance(config.get("live"), dict) else {}
    value = live.get(key)
    return fallback if value in (None, "") else value


def mediamtx_ice_servers_yaml(config: dict) -> list[str]:
    stun_url = str(live_value(config, "mobile_webrtc_stun_url", MEDIAMTX_WEBRTC_STUN_URL) or "")
    turn_url = str(live_value(config, "mobile_webrtc_turn_url", MEDIAMTX_WEBRTC_TURN_URL) or "")
    turn_username = str(live_value(config, "mobile_webrtc_turn_username", MEDIAMTX_WEBRTC_TURN_USERNAME) or "")
    turn_password = str(live_value(config, "mobile_webrtc_turn_password", MEDIAMTX_WEBRTC_TURN_PASSWORD) or "")
    if not stun_url and not turn_url:
        return ["webrtcICEServers2: []"]
    lines = ["webrtcICEServers2:"]
    if stun_url:
        lines += [
            f"  - url: {yaml_string(stun_url)}",
            '    username: ""',
            '    password: ""',
            "    clientOnly: false",
        ]
    if turn_url:
        lines += [
            f"  - url: {yaml_string(turn_url)}",
            f"    username: {yaml_string(turn_username)}",
            f"    password: {yaml_string(turn_password)}",
            "    clientOnly: false",
        ]
    return lines


def mediamtx_ice_addresses(config: dict) -> tuple[str, str]:
    live = config.get("live") if isinstance(config.get("live"), dict) else {}
    ice_transport = normalize_webrtc_ice_transport(
        live.get("mobile_webrtc_ice_transport"),
        live.get("mobile_webrtc_tcp_only"),
    )
    if ice_transport == "tcp":
        return "", f":{MEDIAMTX_WEBRTC_UDP_PORT}"
    if ice_transport == "udp":
        return f":{MEDIAMTX_WEBRTC_UDP_PORT}", ""
    return f":{MEDIAMTX_WEBRTC_UDP_PORT}", f":{MEDIAMTX_WEBRTC_UDP_PORT}"


def mediamtx_public_hosts(config: dict) -> list[str]:
    public_hosts = csv_values(str(live_value(config, "mobile_webrtc_public_hosts", ",".join(MEDIAMTX_WEBRTC_PUBLIC_HOSTS)) or ""))
    public_url_host = url_host(str(live_value(config, "mobile_webrtc_public_url", MEDIAMTX_WEBRTC_PUBLIC_URL) or ""))
    return append_unique(public_hosts, public_url_host)


def add_mediamtx_path(lines: list[str], path_name: str, source_url: str, should_record: bool, keep_warm: bool, recordings_dir: str, retention_days: int) -> None:
    if not source_url:
        return
    lines += [
        f"  {path_name}:",
        f"    source: {yaml_string(source_url)}",
        "    rtspTransport: tcp",
    ]
    if should_record and MEDIAMTX_RECORD:
        lines += [
            "    sourceOnDemand: no",
            "    record: yes",
            f"    recordPath: {yaml_string(f'{recordings_dir}/%path/%Y-%m-%d_%H-%M-%S-%f')}",
        ]
    elif keep_warm:
        lines += [
            "    sourceOnDemand: no",
            "    record: no",
        ]
    else:
        lines += [
            "    sourceOnDemand: yes",
            "    sourceOnDemandStartTimeout: 15s",
            "    sourceOnDemandCloseAfter: 30s",
            "    record: no",
        ]


def write_runtime_config_if_changed(path: Path, content: str) -> bool:
    try:
        if path.exists() and path.read_text(encoding="utf-8", errors="replace") == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return True


def write_mediamtx_runtime_config(config: dict) -> dict:
    if not MEDIAMTX_ENABLED:
        return {"enabled": False, "written": False, "path_count": 0}
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    live = config.get("live") if isinstance(config.get("live"), dict) else {}
    recordings_dir = str(storage.get("recordings_dir") or "/media/edge-of-infinity/recordings").rstrip("/")
    retention_days = clamp_int(
        (config.get("nvr") if isinstance(config.get("nvr"), dict) else {}).get("retention_days"),
        safe_int(storage.get("retention_days"), 14),
        1,
        365,
    )
    segment_seconds = clamp_int(
        (config.get("nvr") if isinstance(config.get("nvr"), dict) else {}).get("segment_seconds"),
        10,
        2,
        300,
    )
    prebuffer_enabled = safe_bool(live.get("prebuffer_enabled"), True)
    always_on_enabled = safe_bool(live.get("always_on_enabled"), True)
    always_on_scope = normalize_always_on_stream_scope(live.get("always_on_stream_scope"))
    udp_address, tcp_address = mediamtx_ice_addresses(config)
    public_hosts = mediamtx_public_hosts(config)
    lines = [
        f"logLevel: {os.environ.get('LOG_LEVEL', 'info')}",
        "logDestinations: [stdout]",
        "readTimeout: 10s",
        "writeTimeout: 10s",
        "writeQueueSize: 512",
        "udpMaxPayloadSize: 1452",
        "",
        "api: yes",
        f"apiAddress: :{MEDIAMTX_API_PORT}",
        "",
        "metrics: no",
        "pprof: no",
        "playback: yes",
        "playbackAddress: :9996",
        "",
        "rtsp: yes",
        "rtspTransports: [tcp]",
        'rtspEncryption: "no"',
        f"rtspAddress: :{MEDIAMTX_RTSP_PORT}",
        "",
        "moq: no",
        "rtmp: no",
        "",
        "hls: yes",
        f"hlsAddress: :{MEDIAMTX_HLS_PORT}",
        'hlsAllowOrigins: ["*"]',
        f"hlsAlwaysRemux: {bool_yaml(MEDIAMTX_HLS_ALWAYS_REMUX)}",
        "hlsVariant: lowLatency",
        "hlsSegmentCount: 4",
        "hlsSegmentDuration: 1s",
        "hlsPartDuration: 200ms",
        "hlsSegmentMaxSize: 50M",
        "",
        "webrtc: yes",
        f"webrtcAddress: :{MEDIAMTX_WEBRTC_PORT}",
        'webrtcAllowOrigins: ["*"]',
        f"webrtcLocalUDPAddress: {yaml_string(udp_address)}",
        f"webrtcLocalTCPAddress: {yaml_string(tcp_address)}",
        "webrtcIPsFromInterfaces: no",
        f"webrtcAdditionalHosts: {json.dumps(public_hosts)}",
        *mediamtx_ice_servers_yaml(config),
        "webrtcSTUNGatherTimeout: 5s",
        "webrtcHandshakeTimeout: 20s",
        "webrtcTrackGatherTimeout: 5s",
        "",
        "srt: yes",
        f"srtAddress: :{MEDIAMTX_SRT_PORT}",
        "",
        "pathDefaults:",
        "  sourceOnDemand: yes",
        "  rtspTransport: tcp",
        "  recordFormat: fmp4",
        "  recordPartDuration: 1s",
        f"  recordSegmentDuration: {segment_seconds}s",
        f"  recordDeleteAfter: {retention_days}d",
        "",
        "paths:",
    ]
    path_count = 0
    for index, camera in enumerate(config.get("cameras", [])):
        if not isinstance(camera, dict):
            continue
        record_stream = normalize_stream_name(camera.get("record_stream"), "main")
        tile_stream = normalize_stream_name(camera.get("tile_stream"), "sub")
        live_stream = normalize_stream_name(camera.get("live_stream"), "sub")
        enabled = safe_bool(camera.get("enabled"), False)
        record = safe_bool(camera.get("record"), False)
        low_latency = safe_bool(camera.get("low_latency"), True)
        record_main = enabled and record and record_stream == "main"
        record_sub = enabled and record and record_stream == "sub"
        selected_main = tile_stream == "main" or live_stream == "main"
        selected_sub = tile_stream == "sub" or live_stream == "sub"
        always_on_main = always_on_enabled and (
            always_on_scope == "all"
            or (always_on_scope in ("tile", "tile_live") and tile_stream == "main")
            or (always_on_scope in ("live", "tile_live") and live_stream == "main")
        )
        always_on_sub = always_on_enabled and (
            always_on_scope == "all"
            or (always_on_scope in ("tile", "tile_live") and tile_stream == "sub")
            or (always_on_scope in ("live", "tile_live") and live_stream == "sub")
        )
        warm_main = enabled and low_latency and prebuffer_enabled and (
            selected_main
            or always_on_main
            or record_main
        )
        warm_sub = enabled and low_latency and prebuffer_enabled and (
            selected_sub
            or always_on_sub
            or record_sub
        )
        before = len(lines)
        add_mediamtx_path(lines, mediamtx_path(camera, index, "main"), camera.get("rtsp_main") or "", record_main, warm_main, recordings_dir, retention_days)
        if len(lines) > before:
            path_count += 1
        before = len(lines)
        add_mediamtx_path(lines, mediamtx_path(camera, index, "sub"), camera.get("rtsp_sub") or "", record_sub, warm_sub, recordings_dir, retention_days)
        if len(lines) > before:
            path_count += 1
    written = write_runtime_config_if_changed(MEDIAMTX_CONFIG_PATH, "\n".join(lines) + "\n")
    return {
        "enabled": True,
        "written": written,
        "unchanged": not written,
        "path": str(MEDIAMTX_CONFIG_PATH),
        "path_count": path_count,
        "public_hosts": public_hosts,
        "ice_transport": normalize_webrtc_ice_transport(live.get("mobile_webrtc_ice_transport"), live.get("mobile_webrtc_tcp_only")),
        "recording_enabled": MEDIAMTX_RECORD,
        "segment_seconds": segment_seconds,
        "prebuffer_enabled": prebuffer_enabled,
        "always_on_enabled": always_on_enabled,
        "always_on_stream_scope": always_on_scope,
    }


def write_janus_streaming_runtime_config(config: dict) -> dict:
    if not JANUS_ENABLED:
        return {"enabled": False, "written": False, "mounts": 0}
    live = config.get("live") if isinstance(config.get("live"), dict) else {}
    bufferkf_ms = clamp_int(live.get("prebuffer_remote_ms"), 2000, 0, 10000)
    streaming_config = JANUS_CONFIG_DIR / "janus.plugin.streaming.jcfg"
    lines = [
        "general: {",
        '  admin_key = "edge-local"',
        "}",
    ]
    mounts = 0
    for index, camera in enumerate(config.get("cameras", [])):
        if not isinstance(camera, dict):
            continue
        camera_id = safe_id(camera.get("id") or f"camera_{index + 1}")
        name = str(camera.get("name") or camera_id).replace('"', '\\"')
        for stream_name in ("main", "sub"):
            path_name = mediamtx_path(camera, index, stream_name)
            mount_id = janus_mount_id(index, stream_name)
            url = f"rtsp://127.0.0.1:{MEDIAMTX_RTSP_PORT}/{path_name}"
            lines += [
                "",
                f"{path_name}: {{",
                '  type = "rtsp"',
                f"  id = {mount_id}",
                f"  description = {yaml_string(f'{name} {stream_name} via MediaMTX')}",
                "  audio = true",
                "  video = true",
                f"  url = {yaml_string(url)}",
                "  rtsp_reconnect_delay = 3",
                "  rtsp_timeout = 5",
                "  rtsp_conn_timeout = 3",
                "  rtsp_notify_changes = true",
                f"  bufferkf_ms = {bufferkf_ms}",
                "  playoutdelay_ext = true",
                "}",
            ]
            mounts += 1
    written = write_runtime_config_if_changed(streaming_config, "\n".join(lines) + "\n")
    return {
        "enabled": True,
        "written": written,
        "unchanged": not written,
        "path": str(streaming_config),
        "mounts": mounts,
        "restart_required": written,
    }


def reload_managed_janus() -> dict:
    global JANUS_MANAGED_PID
    with JANUS_RELOAD_LOCK:
        if JANUS_MANAGED_PID <= 0 or not JANUS_ENABLED:
            return {"applied": False, "reason": "not_managed_here"}
        try:
            command = Path(f"/proc/{JANUS_MANAGED_PID}/cmdline").read_bytes().split(b"\0")[0]
            if Path(os.fsdecode(command)).name != "janus":
                return {"applied": False, "reason": "janus_pid_mismatch"}
            os.kill(JANUS_MANAGED_PID, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    done, _ = os.waitpid(JANUS_MANAGED_PID, os.WNOHANG)
                except ChildProcessError:
                    done = JANUS_MANAGED_PID
                if done:
                    break
                time.sleep(0.05)
            else:
                os.kill(JANUS_MANAGED_PID, signal.SIGKILL)
                os.waitpid(JANUS_MANAGED_PID, 0)
            HOME_DIR.mkdir(parents=True, exist_ok=True)
            with (HOME_DIR / "janus.log").open("ab") as log:
                process = subprocess.Popen(["janus", f"--configs-folder={JANUS_CONFIG_DIR}", "--disable-colors"], stdout=log, stderr=subprocess.STDOUT)
            JANUS_MANAGED_PID = process.pid
            return {"applied": True, "pid": process.pid}
        except OSError:
            return {"applied": False, "reason": "janus_reload_failed"}


def supervisor_self_request(path: str, payload: dict | None = None) -> dict:
    import urllib.request
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("supervisor_unavailable")
    # This connector only ever addresses this add-on's own options.
    if path not in ("/addons/self/info", "/addons/self/options"):
        raise ValueError("Unsupported Supervisor path")
    request = urllib.request.Request("http://supervisor" + path, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read())
    if result.get("result") != "ok":
        raise RuntimeError("supervisor_options_rejected")
    return result.get("data") or {}


def addon_options_from_config(config: dict, existing: dict) -> dict:
    options = dict(existing)
    cameras = [{key: camera.get(key) for key in ("id", "name", "vendor", "host", "username", "password", "rtsp_main", "rtsp_sub", "camera_number", "access_protocol", "rtsp_transport", "onvif_url", "isapi_base_url", "enabled", "record", "low_latency", "snapshot_stream", "live_stream", "tile_stream", "record_stream")} for camera in config.get("cameras", [])]
    options["cameras"] = cameras
    for index in range(2):
        camera = cameras[index] if index < len(cameras) else {}
        prefix = f"hikvision_{index + 1}_"
        for field in ("enabled", "name", "host", "username", "password", "camera_number", "access_protocol", "rtsp_transport", "rtsp_main", "rtsp_sub", "onvif_url", "isapi_base_url", "record", "low_latency", "snapshot_stream", "live_stream", "record_stream"):
            default = {"enabled": False, "record": True, "low_latency": True, "camera_number": "1", "access_protocol": "rtsp", "rtsp_transport": "tcp", "snapshot_stream": "sub", "live_stream": "sub", "record_stream": "main"}.get(field, "")
            options[prefix + field] = camera.get(field, default)
    storage = config.get("storage") or {}
    for field in ("recordings_dir", "database_path", "retention_days"):
        if field in storage:
            options[field] = storage[field]
    return options


def sync_supervisor_options(config: dict) -> dict:
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return {"synced": False, "status": "unavailable"}
    try:
        current = supervisor_self_request("/addons/self/info").get("options") or {}
        desired = addon_options_from_config(config, current)
        if desired != current:
            supervisor_self_request("/addons/self/options", {"options": desired})
        observed = supervisor_self_request("/addons/self/info").get("options") or {}
        success = all(observed.get(key) == value for key, value in desired.items())
        result = {"synced": success, "status": "synced" if success else "verification_failed"}
    except (OSError, ValueError, RuntimeError):
        # Never include request bodies, credentials or Supervisor response bodies in diagnostics.
        result = {"synced": False, "status": "failed"}
    write_json(HOME_DIR / "addon-options-sync.json", result)
    return result


def sync_runtime_engine_config(config: dict, reason: str) -> dict:
    if str(Path(__file__).parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).parent))
    if EDGE_RTC_ENABLED:
        from edge_rtc_config import write_config
        with CONFIG_LOCK:
            current = load_config()
            write_config(current, HOME_DIR, EDGE_RTC_CONFIG_PATH, MEDIAMTX_RTSP_PORT, MEDIAMTX_API_PORT)
            write_json(HOME_DIR / "restream.json", home_assistant_restream_payload(current))
        return {"reason": reason, "edge_rtc": {"written": True}}
    result = {"reason": reason, "mediamtx": {}, "janus": {}}
    try:
        result["mediamtx"] = write_mediamtx_runtime_config(config)
    except OSError as error:
        result["mediamtx"] = {"written": False, "error": str(error), "type": type(error).__name__}
    try:
        result["janus"] = write_janus_streaming_runtime_config(config)
        if result["janus"].get("written"):
            result["janus"]["reload"] = reload_managed_janus()
    except OSError as error:
        result["janus"] = {"written": False, "error": str(error), "type": type(error).__name__}
    write_json(HOME_DIR / "restream.json", home_assistant_restream_payload(config))
    write_json(HOME_DIR / "edge.last-runtime-sync.json", result)
    write_debug_event("runtime_engine_config_sync", {
        "reason": reason,
        "result": result,
        "summary": config_summary(config),
    })
    return result


def mediamtx_api_url(path: str) -> str:
    return f"http://127.0.0.1:{MEDIAMTX_API_PORT}{path}"


def mediamtx_status() -> dict:
    api_reachable = False
    api_error = ""
    if MEDIAMTX_ENABLED:
        try:
            import urllib.request

            with urllib.request.urlopen(mediamtx_api_url("/v3/config/global/get"), timeout=1) as response:
                api_reachable = 200 <= response.status < 500
        except Exception as error:
            api_error = str(error)
    return {
        "enabled": MEDIAMTX_ENABLED,
        "binary": bool(shutil.which("mediamtx")),
        "config_path": str(MEDIAMTX_CONFIG_PATH),
        "config_exists": MEDIAMTX_CONFIG_PATH.exists(),
        "api_reachable": api_reachable,
        "api_error": api_error,
        "ports": {
            "rtsp": MEDIAMTX_RTSP_PORT,
            "ll_hls": MEDIAMTX_HLS_PORT,
            "webrtc_whep": MEDIAMTX_WEBRTC_PORT,
            "webrtc_ice_udp": MEDIAMTX_WEBRTC_UDP_PORT,
            "webrtc_public_hosts": MEDIAMTX_WEBRTC_PUBLIC_HOSTS,
            "srt": MEDIAMTX_SRT_PORT,
            "api": MEDIAMTX_API_PORT,
        },
    }


def janus_status() -> dict:
    api_reachable = False
    api_error = ""
    if JANUS_ENABLED:
        try:
            import urllib.request

            payload = json.dumps({"janus": "info", "transaction": "edge"}).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{JANUS_HTTP_PORT}/janus",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                api_reachable = 200 <= response.status < 500
        except Exception as error:
            api_error = str(error)
    streaming_config = JANUS_CONFIG_DIR / "janus.plugin.streaming.jcfg"
    return {
        "enabled": JANUS_ENABLED,
        "binary": bool(shutil.which("janus")),
        "config_dir": str(JANUS_CONFIG_DIR),
        "streaming_config": str(streaming_config),
        "streaming_config_exists": streaming_config.exists(),
        "api_reachable": api_reachable,
        "api_error": api_error,
        "ports": {
            "http": JANUS_HTTP_PORT,
            "websocket": JANUS_WS_PORT,
        },
    }


def edge_rtc_status() -> dict:
    import urllib.request
    try:
        token = (HOME_DIR / "edge-rtc-token").read_text().strip()
        request = urllib.request.Request(f"http://127.0.0.1:{MEDIAMTX_API_PORT}/api/status", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=1) as response:
            return {"enabled": True, "api_reachable": True, **json.loads(response.read())}
    except (OSError, ValueError):
        return {"enabled": EDGE_RTC_ENABLED, "api_reachable": False}


def engine_runtime_status() -> dict:
    if EDGE_RTC_ENABLED:
        return {"architecture": "camera RTSP -> EdgeRTC registry -> encoded frames/WebCodecs + RTSP recorder; one recording profile per camera", "edge_rtc": edge_rtc_status(), "mediamtx": {"api_reachable": False, "config_exists": False}, "janus": {"api_reachable": False, "streaming_config_exists": False}, "codec_notes": {"h264": "Native EdgeRTC/1 encoded frames, no WebRTC or MSE.", "microphone": "PCM over authenticated WS -> RTSP G711 backchannel on the existing source session."}}
    return {
        "architecture": "camera RTSP -> MediaMTX core -> Janus Streaming Plugin -> browser WebRTC. MediaMTX also exposes LL-HLS, WHEP, SRT, RTSP proxy, and fMP4 recording paths.",
        "mediamtx": mediamtx_status(),
        "janus": janus_status(),
        "codec_notes": {
            "h264": "Best browser/WebRTC compatibility.",
            "h265": "Kept for MediaMTX proxying, SRT, recording, and compatible players; browser WebRTC support is still client-dependent.",
            "pcm_alaw": "Camera audio can be proxied/recorded; WebRTC browsers usually prefer Opus, so audio may need a later transcode path.",
        },
    }


def stream_urls(camera: dict, index: int) -> dict:
    camera_id = safe_id(camera.get("id") or f"camera_{index + 1}")
    main_path = mediamtx_path(camera, index, "main")
    sub_path = mediamtx_path(camera, index, "sub")
    return {
        "ll_hls": f"http://{MEDIAMTX_HOST}:{MEDIAMTX_HLS_PORT}/{main_path}/index.m3u8",
        "mediamtx_ll_hls_main": f"http://{MEDIAMTX_HOST}:{MEDIAMTX_HLS_PORT}/{main_path}/index.m3u8",
        "mediamtx_ll_hls_sub": f"http://{MEDIAMTX_HOST}:{MEDIAMTX_HLS_PORT}/{sub_path}/index.m3u8",
        "mediamtx_webrtc_main": f"http://{MEDIAMTX_HOST}:{MEDIAMTX_WEBRTC_PORT}/{main_path}/whep",
        "mediamtx_webrtc_sub": f"http://{MEDIAMTX_HOST}:{MEDIAMTX_WEBRTC_PORT}/{sub_path}/whep",
        "mediamtx_rtsp_main": f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/{main_path}",
        "mediamtx_rtsp_sub": f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/{sub_path}",
        "srt_main": f"srt://{MEDIAMTX_HOST}:{MEDIAMTX_SRT_PORT}?streamid=read:{main_path}",
        "srt_sub": f"srt://{MEDIAMTX_HOST}:{MEDIAMTX_SRT_PORT}?streamid=read:{sub_path}",
        "janus_main": f"http://{JANUS_HOST}:{JANUS_HTTP_PORT}/janus/streaming/{janus_mount_id(index, 'main')}",
        "janus_sub": f"http://{JANUS_HOST}:{JANUS_HTTP_PORT}/janus/streaming/{janus_mount_id(index, 'sub')}",
        "rtsp_main": redact_rtsp(camera.get("rtsp_main") or ""),
        "rtsp_sub": redact_rtsp(camera.get("rtsp_sub") or ""),
    }


def stream_capabilities(config: dict | None = None) -> dict:
    config = config or load_config()
    live = config.get("live") if isinstance(config.get("live"), dict) else {}
    runtime = engine_runtime_status()
    if EDGE_RTC_ENABLED:
        return {"engines": {"edge_rtc": {"status": "active" if runtime["edge_rtc"].get("api_reachable") else "starting", "transport": "edgertc/1-websocket-webcodecs"}}, "codec_policy": {"live": "H264 access units, no video re-encoding", "recording": "original RTSP video", "microphone": "PCM16LE 8kHz -> RTSP G711 backchannel"}, "runtime": runtime, "cameras": [{"id": camera.get("id"), "record_stream": camera.get("record_stream") or "main"} for camera in config.get("cameras", [])]}
    mediamtx_ready = runtime["mediamtx"]["api_reachable"] or runtime["mediamtx"]["config_exists"]
    janus_ready = runtime["janus"]["api_reachable"] or runtime["janus"]["streaming_config_exists"]
    return {
        "engines": {
            "mediamtx": {"status": "active" if mediamtx_ready else "starting", "transport": "rtsp-proxy/webrtc-whep/ll-hls/srt"},
            "janus_webrtc": {"status": "active" if janus_ready else "starting", "transport": "janus-streaming-plugin-over-mediamtx-rtsp"},
            "ll_hls": {"status": "active" if mediamtx_ready else "starting", "transport": "hls-fmp4", "note": "MediaMTX low-latency HLS path."},
            "srt": {"status": "active" if mediamtx_ready else "starting", "transport": "srt"},
        },
        "codec_policy": {
            "webrtc_primary": "H264/AV1/VP9/VP8 where the browser supports them; H265/HEVC only when the browser WebRTC stack advertises support.",
            "hevc": {
                "rtsp_proxy": "supported by MediaMTX path proxying",
                "ll_hls": "supported by MediaMTX when the client/player can decode HEVC",
                "srt": "supported by MediaMTX transport",
                "recording": "preserved by fMP4 stream-copy recording",
                "webrtc": "experimental and browser/device dependent",
            },
            "mobile_lte_recommendation": "Use warmed sub stream for tiles/live start, keep main for recording, prefer H264 for universal WebRTC and HEVC for recording/LL-HLS when the phone supports it.",
        },
        "mobile_webrtc": {
            "remote_access_mode": normalize_remote_access_mode(live.get("remote_access_mode")),
            "public_hosts": live.get("mobile_webrtc_public_hosts") or ",".join(MEDIAMTX_WEBRTC_PUBLIC_HOSTS),
            "public_url": live.get("mobile_webrtc_public_url") or "",
            "stun_url": live.get("mobile_webrtc_stun_url") if live.get("mobile_webrtc_stun_url") is not None else MEDIAMTX_WEBRTC_STUN_URL,
            "turn_configured": bool(live.get("mobile_webrtc_turn_url", MEDIAMTX_WEBRTC_TURN_URL)),
            "ice_transport": normalize_webrtc_ice_transport(
                live.get("mobile_webrtc_ice_transport"),
                live.get("mobile_webrtc_tcp_only"),
            ),
            "tcp_only": normalize_webrtc_ice_transport(
                live.get("mobile_webrtc_ice_transport"),
                live.get("mobile_webrtc_tcp_only"),
            ) == "tcp",
            "ice_udp_port": MEDIAMTX_WEBRTC_UDP_PORT,
            "whep_port": MEDIAMTX_WEBRTC_PORT,
            "prebuffer_enabled": safe_bool(live.get("prebuffer_enabled"), True),
            "always_on_enabled": safe_bool(live.get("always_on_enabled"), True),
            "always_on_stream_scope": normalize_always_on_stream_scope(live.get("always_on_stream_scope")),
            "prebuffer_remote_ms": clamp_int(live.get("prebuffer_remote_ms"), 2000, 0, 10000),
            "diagnosis": "If LAN works but LTE fails, the browser usually cannot reach the advertised ICE host/ports. Nabu Casa exposes the HA panel, not MediaMTX WebRTC ports. Use a reachable DDNS/VPS relay public URL first; add TURN for CGNAT/firewalls.",
        },
        "runtime": runtime,
        "cameras": [
            {
                "index": index,
                "id": camera.get("id"),
                "name": camera.get("name"),
                "tile_stream": camera.get("tile_stream") or "sub",
                "live_stream": camera.get("live_stream") or "sub",
                "record_stream": camera.get("record_stream") or "main",
                "urls": stream_urls(camera, index),
            }
            for index, camera in enumerate(config.get("cameras", []))
        ],
    }


def cleanup_recording_processes() -> None:
    for key, process in list(RECORDING_PROCESSES.items()):
        if process.poll() is not None:
            RECORDING_PROCESSES.pop(key, None)


def stop_orphan_recordings(config: dict) -> None:
    active_keys = {
        recording_key(camera, index)
        for index, camera in enumerate(config.get("cameras", []))
    }
    for key, process in list(RECORDING_PROCESSES.items()):
        if key in active_keys:
            continue
        RECORDING_PROCESSES.pop(key, None)
        if process.poll() is not None:
            continue
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def camera_should_record(camera: dict) -> bool:
    return bool(camera.get("enabled")) and bool(camera.get("record"))


def recording_base_dir(camera: dict, index: int) -> Path:
    config = load_config()
    storage = config.get("storage") or {}
    recordings_dir = Path(storage.get("recordings_dir") or "/media/edge-of-infinity/recordings")
    return recordings_dir / safe_id(camera.get("id") or f"camera_{index + 1}")


def recording_segment_start_ts(path: Path, fallback_ts: float) -> int:
    name = path.stem
    patterns = (
        (r"^(\d{8})-(\d{6})", "%Y%m%d%H%M%S"),
        (r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})", "%Y%m%d%H%M%S"),
    )
    for pattern, fmt in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        stamp = "".join(match.groups())
        try:
            return int(time.mktime(time.strptime(stamp, fmt)))
        except (OverflowError, ValueError):
            continue
    return int(fallback_ts)


def recording_file_ready(path: Path, now: float | None = None, min_age_seconds: float | None = None) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size < RECORDING_FILE_MIN_PLAYABLE_BYTES:
        return False
    min_age_seconds = MIN_RECORDING_FILE_READY_SECONDS if min_age_seconds is None else min_age_seconds
    if min_age_seconds > 0 and (now or time.time()) - stat.st_mtime < min_age_seconds:
        return False
    return True


def recording_file_has_playable_header(path: Path, stat: os.stat_result | None = None, now: float | None = None) -> bool:
    try:
        stat = stat or path.stat()
        with path.open("rb") as handle:
            header = handle.read(RECORDING_FILE_HEADER_CHECK_BYTES)
            if stat.st_size > RECORDING_FILE_HEADER_CHECK_BYTES:
                handle.seek(max(0, stat.st_size - RECORDING_FILE_HEADER_CHECK_BYTES))
                header += handle.read(RECORDING_FILE_HEADER_CHECK_BYTES)
    except OSError:
        return False
    if b"ftyp" not in header[:64]:
        return False
    if b"moov" in header or b"moof" in header:
        return True
    age = (now or time.time()) - stat.st_mtime
    return age >= RECORDING_FILE_HEADER_GRACE_SECONDS


def playable_recording_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    now = time.time()
    return [path for path in directory.glob("*.mp4") if recording_file_ready(path, now)]


def recording_thumbnail_url(key: str, path: Path) -> str:
    base = f"recording-thumbs/{key}/{path.name}.jpg"
    try:
        stat = path.stat()
    except OSError:
        return base
    return f"{base}?v={stat.st_size}-{stat.st_mtime_ns}"


def recording_segments(camera: dict, index: int, limit: int = 24, segment_seconds: int = 10) -> list[dict]:
    directory = recording_base_dir(camera, index)
    if not directory.exists():
        return []
    key = recording_key(camera, index)
    files = sorted(playable_recording_files(directory), key=lambda item: item.stat().st_mtime, reverse=True)
    segments = []
    for path in files[:limit]:
        stat = path.stat()
        start_ts = recording_segment_start_ts(path, stat.st_mtime)
        duration_seconds = clamp_int(segment_seconds, 10, 1, 3600)
        segments.append(
            {
                "name": path.name,
                "url": f"recordings/{key}/{path.name}",
                "thumbnail_url": recording_thumbnail_url(key, path),
                "size_bytes": stat.st_size,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
                "start_ts": start_ts,
                "start_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start_ts)),
                "duration_seconds": duration_seconds,
                "end_ts": start_ts + duration_seconds,
                "kind": "video_segment",
            }
        )
    return segments


def recording_inferred_duration_seconds(start_ts: int | float, next_start_ts: int | float | None, fallback_seconds: int) -> int:
    fallback = clamp_int(fallback_seconds, 10, 1, 3600)
    try:
        current = int(float(start_ts))
        next_start = int(float(next_start_ts or 0))
    except (TypeError, ValueError, OverflowError):
        return fallback
    if next_start <= current:
        return fallback
    gap = next_start - current
    max_gap = max(2, min(3600, fallback * 6))
    if 1 <= gap <= max_gap:
        return max(1, min(3600, gap))
    return fallback


def mp4_duration_seconds(path: Path) -> float:
    """Read movie duration without spawning FFprobe for every short segment."""
    try:
        with path.open("rb") as handle:
            file_size = path.stat().st_size

            def boxes(start: int, end: int):
                cursor = start
                while cursor + 8 <= end:
                    handle.seek(cursor)
                    header = handle.read(8)
                    if len(header) != 8:
                        return
                    size = int.from_bytes(header[:4], "big")
                    kind = header[4:8]
                    header_size = 8
                    if size == 1:
                        extended = handle.read(8)
                        if len(extended) != 8:
                            return
                        size = int.from_bytes(extended, "big")
                        header_size = 16
                    elif size == 0:
                        size = end - cursor
                    if size < header_size or cursor + size > end:
                        return
                    yield kind, cursor + header_size, cursor + size
                    cursor += size

            for kind, payload_start, box_end in boxes(0, file_size):
                if kind != b"moov":
                    continue
                for child_kind, child_start, child_end in boxes(payload_start, box_end):
                    if child_kind != b"mvhd":
                        continue
                    handle.seek(child_start)
                    data = handle.read(min(32, child_end - child_start))
                    if len(data) >= 20 and data[0] == 0:
                        timescale = int.from_bytes(data[12:16], "big")
                        duration = int.from_bytes(data[16:20], "big")
                    elif len(data) >= 32 and data[0] == 1:
                        timescale = int.from_bytes(data[20:24], "big")
                        duration = int.from_bytes(data[24:32], "big")
                    else:
                        return 0.0
                    seconds = duration / timescale if timescale else 0.0
                    return seconds if 0 < seconds <= 86400 else 0.0
    except OSError:
        pass
    return 0.0


def recording_duration_seconds(path: Path, stat: os.stat_result, fallback: int, allow_probe: bool = True) -> int:
    cache_key = str(path)
    with RECORDING_DURATION_LOCK:
        cached = RECORDING_DURATION_CACHE.get(cache_key)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2] or fallback
    movie_duration = mp4_duration_seconds(path)
    if movie_duration:
        probe = {"format": {"duration": movie_duration}}
    elif allow_probe:
        probe = run_json(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], timeout=4) or {}
    else:
        return fallback
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0)
        measured = max(1, round(duration)) if 0 < duration <= 86400 else 0
    except (TypeError, ValueError, OverflowError):
        measured = 0
    with RECORDING_DURATION_LOCK:
        if len(RECORDING_DURATION_CACHE) >= RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS:
            RECORDING_DURATION_CACHE.clear()
        RECORDING_DURATION_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, measured)
    return measured or fallback


def recording_day_key(start_ts: int | float) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(float(start_ts)))
    except (OverflowError, OSError, ValueError):
        return ""


def recording_day_start_ts(day_key: str) -> int:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day_key or "")):
        return 0
    try:
        return int(time.mktime(time.strptime(day_key, "%Y-%m-%d")))
    except (OverflowError, OSError, ValueError):
        return 0


def recording_day_bounds(day_key: str) -> tuple[int, int]:
    start = recording_day_start_ts(day_key)
    if not start:
        return 0, 0
    next_day = (date.fromisoformat(day_key) + timedelta(days=1)).isoformat()
    return start, recording_day_start_ts(next_day)


def recording_day_is_today(day_key: str) -> bool:
    return bool(day_key) and day_key == recording_day_key(time.time())


def recording_file_entries(camera: dict, index: int, limit: int = 1000, segment_seconds: int = 10, day_key: str = "") -> list[dict]:
    directory = recording_base_dir(camera, index)
    if not directory.exists():
        return []
    candidates = []
    for path in playable_recording_files(directory):
        try:
            stat = path.stat()
        except OSError:
            continue
        candidates.append((path, stat, recording_segment_start_ts(path, stat.st_mtime)))
    candidates.sort(key=lambda item: (item[2], item[0].name))
    entries = []
    offset = 0
    selected_files = candidates if safe_int(limit, 0) <= 0 else candidates[-safe_int(limit, 0):]
    items = []
    for path, stat, start_ts in selected_files:
        items.append({"path": path, "stat": stat, "start_ts": start_ts, "day": recording_day_key(start_ts)})
    for position, item in enumerate(items):
        path = item["path"]
        start_ts = item["start_ts"]
        entry_day = recording_day_key(start_ts)
        if day_key and entry_day != day_key:
            continue
        next_item = items[position + 1] if position + 1 < len(items) else None
        next_start_ts = next_item.get("start_ts") if next_item and next_item.get("day") == entry_day else None
        duration_seconds = recording_inferred_duration_seconds(start_ts, next_start_ts, segment_seconds)
        irregular = next_start_ts is None or next_start_ts - start_ts > max(2, min(3600, segment_seconds * 6))
        duration_seconds = recording_duration_seconds(path, item["stat"], duration_seconds, allow_probe=irregular)
        day_start_ts, _day_end_ts = recording_day_bounds(entry_day)
        day_offset = max(0, min(_day_end_ts - day_start_ts - 1, start_ts - day_start_ts)) if day_start_ts else offset
        entries.append(
            {
                "path": path,
                "name": path.name,
                "start_ts": start_ts,
                "start_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start_ts)),
                "day": entry_day,
                "offset": offset,
                "playback_offset": offset,
                "day_offset": day_offset,
                "duration_seconds": duration_seconds,
            }
        )
        offset += duration_seconds
    return entries


def recording_day_groups(entries: list[dict], segment_seconds: int, key: str = "") -> list[dict]:
    groups: dict[str, dict] = {}
    for entry in entries:
        day = entry.get("day") or recording_day_key(safe_int(entry.get("start_ts"), 0))
        if not day:
            continue
        group = groups.setdefault(
            day,
            {
                "day": day,
                "file_count": 0,
                "total_seconds": 0,
                "recorded_seconds": 0,
                "day_start_ts": recording_day_bounds(day)[0],
                "day_end_ts": recording_day_bounds(day)[1],
                "day_total_seconds": recording_day_bounds(day)[1] - recording_day_bounds(day)[0],
                "oldest_at": entry.get("start_at") or "",
                "newest_at": entry.get("start_at") or "",
                "thumbnail_url": "",
            },
        )
        group["file_count"] += 1
        group["recorded_seconds"] += safe_int(entry.get("duration_seconds"), segment_seconds)
        group["total_seconds"] = group["day_total_seconds"]
        group["newest_at"] = entry.get("start_at") or group["newest_at"]
        path = entry.get("path")
        if key and not group.get("thumbnail_url") and isinstance(path, Path):
            group["thumbnail_url"] = recording_thumbnail_url(key, path)
    return sorted(groups.values(), key=lambda item: item["day"], reverse=True)


def recording_files_from_entries(key: str, entries: list[dict], segment_seconds: int) -> list[dict]:
    files = []
    for entry in sorted(entries, key=lambda item: (safe_int(item.get("start_ts"), 0), item.get("name") or ""), reverse=True):
        path = entry.get("path")
        if not isinstance(path, Path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        start_ts = safe_int(entry.get("start_ts"), int(stat.st_mtime))
        duration_seconds = safe_int(entry.get("duration_seconds"), segment_seconds)
        files.append(
            {
                "name": path.name,
                "url": f"recordings/{key}/{path.name}",
                "thumbnail_url": recording_thumbnail_url(key, path),
                "size_bytes": stat.st_size,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
                "start_ts": start_ts,
                "start_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start_ts)),
                "day": entry.get("day") or recording_day_key(start_ts),
                "duration_seconds": duration_seconds,
                "end_ts": start_ts + duration_seconds,
                "timeline_offset": safe_int(entry.get("day_offset"), safe_int(entry.get("offset"), 0)),
                "playback_offset": safe_int(entry.get("playback_offset"), safe_int(entry.get("offset"), 0)),
                "kind": "video_segment",
            }
        )
    return files


def parse_recording_day_query(query: dict) -> dict[str, str]:
    raw_values = query.get("days") or []
    raw = raw_values[0] if raw_values else ""
    result: dict[str, str] = {}
    for pair in str(raw).split(","):
        if ":" not in pair:
            continue
        index, day = pair.split(":", 1)
        day = day.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            result[str(index).strip()] = day
    return result


def camera_for_recording_key(config: dict, key: str) -> tuple[dict, int] | None:
    for index, camera in enumerate(config.get("cameras", [])):
        if recording_key(camera, index) == key:
            return camera, index
    return None


def ffconcat_file_line(path: Path) -> str:
    clean_path = str(path.resolve()).replace("\\", "/").replace("'", "\\'")
    return f"file '{clean_path}'"


def recording_stream_plan(key: str, start_seconds: int = 0, day_key: str = "") -> dict:
    config = load_config()
    target = camera_for_recording_key(config, key)
    if target is None:
        raise FileNotFoundError("recording_camera_not_found")
    camera, index = target
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, day_key=day_key)
    if not entries:
        raise FileNotFoundError("recording_stream_empty")

    total_seconds = sum(safe_int(item.get("duration_seconds"), segment_seconds) for item in entries)
    requested = max(0, min(safe_int(start_seconds, 0), max(0, total_seconds - 1)))
    selected_index = 0
    selected_seek = 0
    for position, entry in enumerate(entries):
        duration = safe_int(entry.get("duration_seconds"), segment_seconds)
        if requested < entry["offset"] + duration:
            selected_index = position
            selected_seek = max(0, requested - entry["offset"])
            break
    selected = entries[selected_index:]

    STREAM_LIST_DIR.mkdir(parents=True, exist_ok=True)
    concat_path = STREAM_LIST_DIR / f"{safe_id(key)}-{time.time_ns()}.ffconcat"
    concat_payload = "\n".join(ffconcat_file_line(item["path"]) for item in selected) + "\n"
    concat_path.write_text(concat_payload, encoding="utf-8")
    return {
        "key": key,
        "camera_id": camera.get("id"),
        "camera_index": index,
        "concat_path": concat_path,
        "requested_start_seconds": requested,
        "day": day_key,
        "seek_seconds": selected_seek,
        "file_count": len(selected),
        "first_file": selected[0]["name"],
        "total_seconds": total_seconds,
        "segment_seconds": segment_seconds,
    }


def build_recording_stream_command(concat_path: Path, seek_seconds: int = 0) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
    ]
    seek = max(0, safe_int(seek_seconds, 0))
    if seek:
        command += ["-ss", str(seek)]
    command += [
        "-i",
        str(concat_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-movflags",
        "frag_keyframe+empty_moov+default_base_moof",
        "-f",
        "mp4",
        "pipe:1",
    ]
    return command


def recording_cache_dir(key: str, cache_name: str = "") -> Path:
    base = RECORDING_CACHE_DIR / safe_id(key)
    return base / safe_id(cache_name) if cache_name else base


def recording_cache_video_path(key: str, cache_name: str = "") -> Path:
    return recording_cache_dir(key, cache_name) / "timeline.mp4"


def recording_cache_meta_path(key: str, cache_name: str = "") -> Path:
    return recording_cache_dir(key, cache_name) / "timeline.json"


def recording_cache_concat_path(key: str, cache_name: str = "") -> Path:
    return recording_cache_dir(key, cache_name) / "timeline.ffconcat"


def recording_cache_tmp_path(key: str, cache_name: str = "") -> Path:
    return recording_cache_dir(key, cache_name) / "timeline.tmp.mp4"


def recording_cache_source_signature(entries: list[dict], segment_seconds: int) -> dict:
    items = []
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, Path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "name": entry.get("name") or path.name,
                "start_ts": safe_int(entry.get("start_ts"), int(stat.st_mtime)),
                "duration_seconds": safe_int(entry.get("duration_seconds"), segment_seconds),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    raw = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return {"hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20], "items": items}


def low_priority_preexec():
    if os.name != "posix":
        return None

    def apply_priority() -> None:
        try:
            os.nice(10)
        except OSError:
            pass

    return apply_priority


def subprocess_run_low_priority(command: list[str], **kwargs):
    preexec_fn = low_priority_preexec()
    if preexec_fn:
        kwargs["preexec_fn"] = preexec_fn
    return subprocess.run(command, **kwargs)


def build_recording_cache_command(concat_path: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def recording_cache_timeout_seconds(entries: list[dict], segment_seconds: int) -> int:
    file_count = max(1, len(entries))
    total_seconds = sum(safe_int(entry.get("duration_seconds"), segment_seconds) for entry in entries)
    estimated = max(
        RECORDING_CACHE_MIN_TIMEOUT_SECONDS,
        int(file_count * 2),
        int(total_seconds * 0.12),
    )
    return max(RECORDING_CACHE_MIN_TIMEOUT_SECONDS, min(RECORDING_CACHE_MAX_TIMEOUT_SECONDS, estimated))


def recording_cache_url(key: str, cache_name: str, cache_id: str) -> str:
    if cache_name:
        return f"recording-cache/{safe_id(key)}/{safe_id(cache_name)}/timeline.mp4?v={cache_id}"
    return f"recording-cache/{safe_id(key)}/timeline.mp4?v={cache_id}"


def recording_cache_worker_id(key: str, cache_name: str = "") -> str:
    return f"{safe_id(key)}:{safe_id(cache_name)}" if cache_name else safe_id(key)


def recording_cache_rebuild_defer_reason(
    raw_ready: bool,
    current: bool,
    metadata: dict,
    metadata_file_count: int,
    source_count: int,
    video_modified_at: float,
) -> str:
    if current or not raw_ready:
        return ""
    if metadata_file_count <= 1 and source_count > 1:
        return ""
    new_segments = max(0, source_count - metadata_file_count)
    age = max(0, int(time.time() - video_modified_at)) if video_modified_at else RECORDING_CACHE_MIN_REBUILD_SECONDS
    if new_segments < RECORDING_CACHE_MIN_NEW_SEGMENTS and age < RECORDING_CACHE_MIN_REBUILD_SECONDS:
        return f"batching_new_segments:{new_segments}/{RECORDING_CACHE_MIN_NEW_SEGMENTS};age:{age}/{RECORDING_CACHE_MIN_REBUILD_SECONDS}"
    return ""


def recording_cache_status(
    key: str,
    entries: list[dict],
    segment_seconds: int,
    auto_refresh: bool = True,
    cache_name: str = "",
) -> dict:
    signature = recording_cache_source_signature(entries, segment_seconds)
    meta_path = recording_cache_meta_path(key, cache_name)
    video_path = recording_cache_video_path(key, cache_name)
    metadata = read_json(meta_path, {}) if meta_path.exists() else {}
    try:
        stat = video_path.stat()
        raw_ready = stat.st_size > 0
        file_size = stat.st_size
        video_modified_ts = stat.st_mtime
        modified_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime))
    except OSError:
        raw_ready = False
        file_size = 0
        video_modified_ts = 0.0
        modified_at = ""

    current = bool(
        raw_ready
        and metadata.get("source_hash") == signature["hash"]
        and safe_int(metadata.get("source_count"), -1) == len(signature["items"])
    )
    active_day_cache = recording_day_is_today(cache_name)
    metadata_file_count = safe_int(metadata.get("file_count"), 0)
    cache_too_short_for_sources = bool(raw_ready and not current and len(signature["items"]) > 1 and metadata_file_count <= 1)
    stale_active_day_cache = bool(raw_ready and active_day_cache and not current)
    # A stale cache can be only the tail of a day, so its offsets are unsafe.
    ready = current
    worker_id = recording_cache_worker_id(key, cache_name)
    with RECORDING_CACHE_LOCK:
        building = worker_id in RECORDING_CACHE_WORKERS
    defer_reason = recording_cache_rebuild_defer_reason(
        raw_ready,
        current,
        metadata,
        metadata_file_count,
        len(signature["items"]),
        video_modified_ts,
    )
    if auto_refresh and active_day_cache and not current:
        defer_reason = ACTIVE_DAY_CACHE_DEFER_REASON
    if auto_refresh and signature["items"] and not current and not defer_reason:
        schedule_recording_cache_refresh(key, entries, segment_seconds, signature, reason="recording_status", cache_name=cache_name)

    cache_id = metadata.get("cache_id") or metadata.get("source_hash") or str(int(time.time()))
    total_seconds = safe_int(
        metadata.get("total_seconds"),
        sum(safe_int(entry.get("duration_seconds"), segment_seconds) for entry in entries),
    )
    return {
        "enabled": True,
        "ready": ready,
        "raw_ready": raw_ready,
        "current": current,
        "active_day": active_day_cache,
        "stale_active_day": stale_active_day_cache,
        "building": building,
        "stale": bool(raw_ready and not current),
        "rebuild_deferred": bool(defer_reason),
        "rebuild_defer_reason": defer_reason,
        "new_source_count": max(0, len(signature["items"]) - metadata_file_count),
        "too_short_for_sources": cache_too_short_for_sources,
        "source_count": len(signature["items"]),
        "source_hash": signature["hash"],
        "cache_id": cache_id,
        "cache_name": cache_name,
        "url": recording_cache_url(key, cache_name, cache_id) if ready else "",
        "file_size": file_size,
        "file_count": metadata_file_count or len(signature["items"]),
        "total_seconds": total_seconds,
        "modified_at": modified_at,
        "built_at": metadata.get("built_at") or "",
        "log_path": str(RECORDING_CACHE_LOG_PATH),
    }


def schedule_recording_cache_refresh(
    key: str,
    entries: list[dict],
    segment_seconds: int,
    signature: dict | None = None,
    reason: str = "manual",
    cache_name: str = "",
) -> bool:
    if shutil.which("ffmpeg") is None or not entries:
        return False
    worker_entries = []
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, Path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size <= 1024:
            continue
        worker_entries.append(
            {
                "path": path,
                "name": entry.get("name") or path.name,
                "start_ts": safe_int(entry.get("start_ts"), int(stat.st_mtime)),
                "duration_seconds": safe_int(entry.get("duration_seconds"), segment_seconds),
            }
        )
    if not worker_entries:
        return False
    source_signature = signature or recording_cache_source_signature(worker_entries, segment_seconds)
    worker_id = recording_cache_worker_id(key, cache_name)
    with RECORDING_CACHE_LOCK:
        if worker_id in RECORDING_CACHE_WORKERS:
            return False
        RECORDING_CACHE_WORKERS.add(worker_id)
    write_debug_event("recording_cache_build_scheduled", {
        "key": key,
        "cache_name": cache_name,
        "reason": reason,
        "file_count": len(worker_entries),
        "source_hash": source_signature.get("hash"),
    })
    thread = threading.Thread(
        target=build_recording_cache_worker,
        args=(key, worker_entries, segment_seconds, source_signature, reason, cache_name, worker_id),
        daemon=True,
    )
    thread.start()
    return True


def build_recording_cache_worker(
    key: str,
    entries: list[dict],
    segment_seconds: int,
    signature: dict,
    reason: str,
    cache_name: str = "",
    worker_id: str = "",
) -> None:
    started = time.time()
    cache_dir = recording_cache_dir(key, cache_name)
    concat_path = recording_cache_concat_path(key, cache_name)
    tmp_path = recording_cache_tmp_path(key, cache_name)
    video_path = recording_cache_video_path(key, cache_name)
    meta_path = recording_cache_meta_path(key, cache_name)
    command: list[str] = []
    timeout_seconds = 0
    acquired = False
    try:
        acquired = RECORDING_CACHE_BUILD_SEMAPHORE.acquire(timeout=7200)
        if not acquired:
            raise RuntimeError("recording_cache_global_worker_timeout")
        cache_dir.mkdir(parents=True, exist_ok=True)
        concat_payload = "\n".join(ffconcat_file_line(entry["path"]) for entry in entries) + "\n"
        concat_path.write_text(concat_payload, encoding="utf-8")
        tmp_path.unlink(missing_ok=True)
        command = build_recording_cache_command(concat_path, tmp_path)
        total_seconds = sum(safe_int(entry.get("duration_seconds"), segment_seconds) for entry in entries)
        timeout_seconds = recording_cache_timeout_seconds(entries, segment_seconds)
        write_debug_event("recording_cache_build_start", {
            "key": key,
            "cache_name": cache_name,
            "reason": reason,
            "file_count": len(entries),
            "total_seconds": total_seconds,
            "timeout_seconds": timeout_seconds,
            "source_hash": signature.get("hash"),
            "command": redact_command(command),
            "global_worker": "acquired",
        })
        result = subprocess_run_low_priority(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_seconds)
        stderr = result.stderr.decode("utf-8", errors="replace")[-8000:]
        stdout = result.stdout.decode("utf-8", errors="replace")[-4000:]
        if result.returncode != 0:
            raise RuntimeError(f"recording_cache_ffmpeg_failed exit={result.returncode}: {stderr[-1200:]}")
        if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
            raise RuntimeError("recording_cache_output_empty")
        tmp_path.replace(video_path)
        metadata = {
            "key": key,
            "cache_name": cache_name,
            "cache_id": f"{signature.get('hash')}-{int(time.time())}",
            "source_hash": signature.get("hash"),
            "source_count": len(signature.get("items") or entries),
            "file_count": len(entries),
            "total_seconds": total_seconds,
            "segment_seconds": segment_seconds,
            "first_file": entries[0]["name"] if entries else "",
            "last_file": entries[-1]["name"] if entries else "",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_ms": int((time.time() - started) * 1000),
            "timeout_seconds": timeout_seconds,
            "video_path": str(video_path),
        }
        write_json(meta_path, metadata)
        RECORDING_CACHE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RECORDING_CACHE_LOG_PATH.open("ab") as log_file:
            log_file.write(("\n=== Edge recording cache ===\n" + json.dumps({
                "event": "recording_cache_build_done",
                **metadata,
                "stdout": stdout,
                "stderr": stderr,
            }, default=str) + "\n").encode("utf-8"))
        write_debug_event("recording_cache_build_done", {
            "key": key,
            "cache_name": cache_name,
            "file_count": len(entries),
            "total_seconds": total_seconds,
            "size_bytes": video_path.stat().st_size,
            "duration_ms": metadata["duration_ms"],
            "timeout_seconds": timeout_seconds,
            "source_hash": signature.get("hash"),
        })
    except Exception as error:  # noqa: BLE001
        write_debug_event("recording_cache_build_error", {
            "key": key,
            "cache_name": cache_name,
            "reason": reason,
            "error": str(error),
            "type": type(error).__name__,
            "timeout_seconds": timeout_seconds,
            "command": redact_command(command),
        })
        try:
            RECORDING_CACHE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with RECORDING_CACHE_LOG_PATH.open("ab") as log_file:
                log_file.write(("\n=== Edge recording cache error ===\n" + json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "key": key,
                    "cache_name": cache_name,
                    "reason": reason,
                    "error": str(error),
                    "type": type(error).__name__,
                    "timeout_seconds": timeout_seconds,
                    "command": redact_command(command),
                }, default=str) + "\n").encode("utf-8"))
        except OSError:
            pass
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        with RECORDING_CACHE_LOCK:
            RECORDING_CACHE_WORKERS.discard(worker_id or recording_cache_worker_id(key, cache_name))
        if acquired:
            RECORDING_CACHE_BUILD_SEMAPHORE.release()


def refresh_recording_caches(config: dict, reason: str) -> None:
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    cache_limit = clamp_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS, 12, RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS)
    for index, camera in enumerate(config.get("cameras", [])):
        if not camera_should_record(camera):
            continue
        key = recording_key(camera, index)
        all_entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds)
        days = recording_day_groups(all_entries, segment_seconds, key)
        active_day = days[0]["day"] if days else ""
        entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, day_key=active_day) if active_day else all_entries
        if entries and active_day and len(entries) <= cache_limit:
            recording_cache_status(key, entries, segment_seconds, auto_refresh=True, cache_name=active_day)


def recording_cache_refresh_loop() -> None:
    while True:
        try:
            refresh_recording_caches(load_config(), "background_loop")
        except Exception as error:  # noqa: BLE001
            write_debug_event("recording_cache_loop_error", {"error": str(error), "type": type(error).__name__})
        time.sleep(RECORDING_CACHE_REFRESH_SECONDS)


def start_recording_cache_refresh_loop() -> None:
    global RECORDING_CACHE_LOOP_STARTED
    if RECORDING_CACHE_LOOP_STARTED:
        return
    RECORDING_CACHE_LOOP_STARTED = True
    thread = threading.Thread(target=recording_cache_refresh_loop, daemon=True)
    thread.start()
    write_debug_event("recording_cache_loop_start", {
        "interval_seconds": RECORDING_CACHE_REFRESH_SECONDS,
        "max_segments": RECORDING_CACHE_MAX_SEGMENTS,
    })


def recording_file_for_key(key: str, filename: str) -> Path | None:
    safe_name = Path(filename).name
    if safe_name.endswith(".jpg"):
        safe_name = safe_name.removesuffix(".jpg")
    if not safe_name.endswith(".mp4"):
        return None
    config = load_config()
    target = camera_for_recording_key(config, key)
    if target is None:
        return None
    camera, index = target
    path = recording_base_dir(camera, index) / safe_name
    return path if path.exists() else None


def recording_thumbnail_path(key: str, filename: str) -> Path:
    safe_name = Path(filename).name
    if safe_name.endswith(".jpg"):
        safe_name = safe_name.removesuffix(".jpg")
    return RECORDING_THUMB_DIR / safe_id(key) / f"{safe_name}.jpg"


def build_recording_thumbnail_command(source: Path, target: Path, seek_seconds: float = 0.6) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        str(max(0, seek_seconds)),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        "scale=480:-2:force_original_aspect_ratio=decrease",
        "-f",
        "image2",
        "-vcodec",
        "mjpeg",
        "-q:v",
        "4",
        str(target),
    ]


def recording_thumbnail_ready(target: Path, source: Path) -> bool:
    try:
        target_stat = target.stat()
        source_stat = source.stat()
    except OSError:
        return False
    if target_stat.st_mtime < source_stat.st_mtime or target_stat.st_size < 512:
        return False
    try:
        with target.open("rb") as handle:
            return handle.read(3).startswith(b"\xff\xd8\xff")
    except OSError:
        return False


def ensure_recording_thumbnail(key: str, source: Path, filename: str) -> Path:
    target = recording_thumbnail_path(key, filename)
    if recording_thumbnail_ready(target, source):
        return target
    if not recording_file_ready(source):
        try:
            source_size = source.stat().st_size
        except OSError:
            source_size = 0
        payload = {
            "key": key,
            "source": source.name,
            "target": str(target),
            "size_bytes": source_size,
            "reason": "recording_source_not_ready",
        }
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_source_not_ready", payload)
        raise RuntimeError("recording_source_not_ready")
    if target.exists():
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_cache_invalid", {
            "key": key,
            "source": source.name,
            "target": str(target),
        })
        try:
            target.unlink(missing_ok=True)
        except OSError as error:
            append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_cache_unlink_error", {
                "key": key,
                "source": source.name,
                "target": str(target),
                "error": str(error),
            })
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg_not_installed_in_addon")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{time.time_ns()}.tmp")
    command = build_recording_thumbnail_command(source, tmp)
    acquired = RECORDING_THUMBNAIL_SEMAPHORE.acquire(timeout=20)
    if not acquired:
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_busy", {
            "key": key,
            "source": source.name,
            "target": str(target),
        })
        raise RuntimeError("thumbnail_generation_busy")
    started_at = time.monotonic()
    append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_start", {
        "key": key,
        "source": source.name,
        "target": str(target),
        "command": redact_command(command),
    })
    try:
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, check=False)
            if result.returncode == 0 and not recording_thumbnail_ready(tmp, source):
                command = build_recording_thumbnail_command(source, tmp, seek_seconds=0)
                result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, check=False)
        except subprocess.TimeoutExpired as error:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_timeout", {
                "key": key,
                "source": source.name,
                "target": str(target),
                "timeout": error.timeout,
                "command": redact_command(command),
            })
            raise
        if result.returncode != 0 or not recording_thumbnail_ready(tmp, source):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            error_text = result.stderr.decode("utf-8", errors="replace")[-600:] or f"thumbnail_ffmpeg_exit_{result.returncode}"
            append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_error", {
                "key": key,
                "source": source.name,
                "target": str(target),
                "exit_code": result.returncode,
                "error": error_text,
            })
            raise RuntimeError(error_text)
        tmp.replace(target)
        payload = {
            "key": key,
            "source": source.name,
            "target": str(target),
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "command": redact_command(command),
        }
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_done", payload)
        write_debug_event("recording_thumbnail_generated", payload)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            RECORDING_THUMBNAIL_SEMAPHORE.release()
    return target


def sampled_thumbnail_entries(entries: list[dict], max_items: int) -> list[dict]:
    clean = [entry for entry in entries if isinstance(entry.get("path"), Path)]
    if not clean:
        return []
    clean = sorted(clean, key=lambda item: (safe_int(item.get("start_ts"), 0), item.get("name") or ""))
    limit = max(1, safe_int(max_items, RECORDING_THUMBNAIL_WARMUP_PER_CAMERA))
    if len(clean) <= limit:
        return clean
    selected_indexes = {
        0,
        len(clean) - 1,
    }
    for slot in range(limit):
        selected_indexes.add(round((slot * (len(clean) - 1)) / max(1, limit - 1)))
    return [clean[index] for index in sorted(selected_indexes)[:limit]]


def recording_thumbnail_warmup_paused() -> tuple[bool, str]:
    try:
        load_one = os.getloadavg()[0]
    except (AttributeError, OSError):
        return False, ""
    cpu_count = os.cpu_count() or 1
    load_per_core = load_one / max(1, cpu_count)
    if load_per_core >= 1.35:
        return True, f"cpu_load_per_core:{load_per_core:.2f}"
    return False, ""


def warm_recording_thumbnails(config: dict, reason: str) -> dict:
    if shutil.which("ffmpeg") is None:
        return {"generated": 0, "skipped": "ffmpeg_not_installed"}
    paused, pause_reason = recording_thumbnail_warmup_paused()
    if paused:
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_warmup_deferred", {"reason": reason, "pause_reason": pause_reason})
        return {"generated": 0, "skipped": pause_reason}
    acquired = RECORDING_THUMBNAIL_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_warmup_deferred", {"reason": reason, "pause_reason": "thumbnail_worker_busy"})
        return {"generated": 0, "skipped": "thumbnail_worker_busy"}
    RECORDING_THUMBNAIL_SEMAPHORE.release()

    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    generated = 0
    errors = []
    for index, camera in enumerate(config.get("cameras", [])):
        key = recording_key(camera, index)
        entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds)
        newest_days = [item.get("day") for item in recording_day_groups(entries, segment_seconds, key)[:2]]
        for day in newest_days:
            day_entries = [entry for entry in entries if entry.get("day") == day]
            for entry in sampled_thumbnail_entries(day_entries, RECORDING_THUMBNAIL_WARMUP_PER_CAMERA):
                path = entry.get("path")
                if not isinstance(path, Path):
                    continue
                target = recording_thumbnail_path(key, path.name)
                if recording_thumbnail_ready(target, path):
                    continue
                try:
                    ensure_recording_thumbnail(key, path, path.name)
                    generated += 1
                except Exception as error:  # noqa: BLE001
                    errors.append({"key": key, "source": path.name, "error": str(error), "type": type(error).__name__})
                    break
    payload = {"reason": reason, "generated": generated, "errors": errors[:5]}
    append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_warmup_done", payload)
    write_debug_event("recording_thumbnail_warmup_done", payload)
    return payload


def recording_thumbnail_warmup_loop() -> None:
    time.sleep(20)
    while True:
        try:
            warm_recording_thumbnails(load_config(), "background_loop")
        except Exception as error:  # noqa: BLE001
            write_debug_event("recording_thumbnail_warmup_error", {"error": str(error), "type": type(error).__name__})
        time.sleep(RECORDING_THUMBNAIL_WARMUP_INTERVAL_SECONDS)


def start_recording_thumbnail_warmup_loop() -> None:
    global RECORDING_THUMBNAIL_WARMUP_LOOP_STARTED
    if RECORDING_THUMBNAIL_WARMUP_LOOP_STARTED:
        return
    RECORDING_THUMBNAIL_WARMUP_LOOP_STARTED = True
    thread = threading.Thread(target=recording_thumbnail_warmup_loop, daemon=True)
    thread.start()
    write_debug_event("recording_thumbnail_warmup_loop_start", {
        "interval_seconds": RECORDING_THUMBNAIL_WARMUP_INTERVAL_SECONDS,
        "per_camera": RECORDING_THUMBNAIL_WARMUP_PER_CAMERA,
    })


def recording_log_path(camera: dict, index: int) -> Path:
    return recording_base_dir(camera, index) / "ffmpeg.log"


def recording_log_tail(camera: dict, index: int, limit: int = 6000) -> str:
    return redact_rtsp(read_text_tail(recording_log_path(camera, index), limit))


def recording_health(camera: dict, index: int) -> dict:
    tail = recording_log_tail(camera, index, 5000)
    lower_tail = tail.lower()
    error_lines = [
        line.strip()
        for line in tail.splitlines()
        if any(token in line.lower() for token in ("error", "failed", "invalid", "not found", "unable", "permission denied"))
    ]
    last_error = error_lines[-1] if error_lines else ""
    return {
        "log_path": str(recording_log_path(camera, index)),
        "log_tail": tail,
        "last_error": last_error,
        "ffmpeg_present": shutil.which("ffmpeg") is not None,
        "ffprobe_present": shutil.which("ffprobe") is not None,
        "has_recent_errors": bool(last_error) or "no such file" in lower_tail,
    }


def recording_preflight_error(camera: dict, stream_name: str) -> str:
    stream = camera_stream(camera, stream_name)
    if stream:
        return ""
    vendor = camera.get("vendor") or "hikvision"
    host = camera.get("host") or ""
    username = camera.get("username") or ""
    password = camera.get("password") or ""
    if not host:
        return "recording_host_missing"
    if vendor in ("hikvision", "dahua") and not username:
        return "recording_username_missing"
    if vendor in ("hikvision", "dahua") and not password:
        return "recording_password_missing"
    return "recording_rtsp_not_configured"


def recording_codec_mode(stream: str, nvr: dict) -> tuple[str, str, str, dict]:
    probe = probe_rtsp_stream(stream, timeout=6)
    video = probe.get("video") or {}
    audio = probe.get("audio") or {}
    video_codec = str(video.get("codec_name") or "").lower()
    audio_codec = str(audio.get("codec_name") or "").lower()
    playback_policy = nvr.get("browser_playback") if nvr.get("browser_playback") in ("auto_h264", "copy", "h264") else "auto_h264"
    if playback_policy == "copy":
        return "copy", video_codec, audio_codec, probe
    if playback_policy == "h264":
        return "transcode_to_h264", video_codec, audio_codec, probe
    if video_codec and video_codec not in ("h264", "avc1"):
        return "transcode_to_h264", video_codec, audio_codec, probe
    return "copy_h264", video_codec, audio_codec, probe


def recording_mode_from_codec(video_codec: str, nvr: dict) -> str:
    codec = str(video_codec or "").lower()
    playback_policy = nvr.get("browser_playback") if nvr.get("browser_playback") in ("auto_h264", "copy", "h264") else "auto_h264"
    if playback_policy == "copy":
        return "copy"
    if playback_policy == "h264":
        return "transcode_to_h264"
    if codec and codec not in ("h264", "avc1"):
        return "transcode_to_h264"
    return "copy_h264"


def build_recording_command(stream: str, output_pattern: str, segment_seconds: int, mode: str) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostdin",
        "-y",
        "-fflags",
        "+genpts",
        "-flags",
        "low_delay",
        "-rtsp_transport",
        "tcp",
        "-rtsp_flags",
        "prefer_tcp",
        "-thread_queue_size",
        "512",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-use_wallclock_as_timestamps",
        "1",
        "-i",
        stream,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    if mode == "transcode_to_h264":
        command += [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ar",
            "48000",
            "-ac",
            "1",
        ]
    else:
        command += [
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ar",
            "48000",
            "-ac",
            "1",
        ]
    command += [
        "-f",
        "segment",
        "-segment_format",
        "mp4",
        "-segment_format_options",
        "movflags=+faststart",
        "-segment_time",
        str(segment_seconds),
        "-segment_time_delta",
        "0.05",
        "-reset_timestamps",
        "1",
        "-avoid_negative_ts",
        "make_zero",
        "-strftime",
        "1",
        output_pattern,
    ]
    return command


def recording_retention_days(config: dict) -> int:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    return clamp_int(nvr.get("retention_days"), clamp_int(storage.get("retention_days"), 14, 1, 365), 1, 365)


def cleanup_old_recordings(directory: Path, retention_days: int) -> int:
    """Remove expired recording segments without following directory symlinks."""
    if directory.is_symlink() or not directory.is_dir():
        return 0
    cutoff = time.time() - max(1, retention_days) * 86400
    removed = 0

    def scan_error(error: OSError) -> None:
        write_debug_event("recording_retention_error", {"path": str(error.filename), "error": str(error)})

    for root, dirs, files in os.walk(directory, followlinks=False, onerror=scan_error):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            # Only names produced by the FFmpeg or MediaMTX recorder qualify.
            if not re.fullmatch(r"(?:\d{8}-\d{6}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:-\d+)?)[.]mp4", name):
                continue
            path = Path(root) / name
            try:
                if not path.is_symlink() and path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
            except OSError as error:
                scan_error(error)
    return removed


def cleanup_configured_recordings(config: dict) -> int:
    storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
    directory = Path(storage.get("recordings_dir") or "/media/edge-of-infinity/recordings")
    retention_days = recording_retention_days(config)
    removed = cleanup_old_recordings(directory, retention_days)
    write_debug_event("recording_retention_done", {
        "directory": str(directory), "retention_days": retention_days, "removed": removed,
    })
    return removed


def recording_retention_loop() -> None:
    while True:
        try:
            cleanup_configured_recordings(load_config())
        except Exception as error:  # noqa: BLE001
            write_debug_event("recording_retention_error", {"error": str(error), "type": type(error).__name__})
        time.sleep(RECORDING_RETENTION_INTERVAL_SECONDS)


def start_recording_retention_loop() -> None:
    global RECORDING_RETENTION_LOOP_STARTED
    if RECORDING_RETENTION_LOOP_STARTED:
        return
    RECORDING_RETENTION_LOOP_STARTED = True
    threading.Thread(target=recording_retention_loop, daemon=True).start()


def recording_status_payload(config: dict | None = None, day_selection: dict[str, str] | None = None) -> dict:
    cleanup_recording_processes()
    config = config or load_config()
    day_selection = day_selection or {}
    cameras = []
    for index, camera in enumerate(config.get("cameras", [])):
        key = recording_key(camera, index)
        process = RECORDING_PROCESSES.get(key)
        directory = recording_base_dir(camera, index)
        total_segment_count = len(list(directory.glob("*.mp4"))) if directory.exists() else 0
        segment_count = len(playable_recording_files(directory))
        nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
        segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
        cache_limit = clamp_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS, 12, RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS)
        all_entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds)
        days = recording_day_groups(all_entries, segment_seconds, key)
        selected_day = day_selection.get(str(index)) or day_selection.get(str(camera.get("id") or "")) or (days[0]["day"] if days else "")
        if selected_day and not any(day.get("day") == selected_day for day in days):
            selected_day = days[0]["day"] if days else ""
        day_entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, day_key=selected_day) if selected_day else all_entries
        segment_files = recording_files_from_entries(key, day_entries, segment_seconds)
        timeline_files = sorted(segment_files, key=lambda item: (item.get("start_ts") or 0, item.get("name") or ""))
        day_start_ts, day_end_ts = recording_day_bounds(selected_day)
        recorded_seconds = sum(safe_int(item.get("duration_seconds"), segment_seconds) for item in timeline_files)
        timeline_total_seconds = day_end_ts - day_start_ts if day_start_ts else recorded_seconds
        available_start_ts = timeline_files[0].get("start_ts") if timeline_files else 0
        available_end_ts = max((safe_int(item.get("end_ts"), 0) for item in timeline_files), default=0)
        playback_cache = recording_cache_status(key, day_entries, segment_seconds, auto_refresh=len(day_entries) <= cache_limit, cache_name=selected_day)
        record_stream = camera.get("record_stream") or "main"
        direct_record_rtsp = camera_stream(camera, record_stream)
        record_rtsp, record_source = recording_source_stream(camera, index, record_stream)
        preflight_error = recording_preflight_error(camera, record_stream)
        desired_recording = camera_should_record(camera)
        is_recording = bool(process and process.poll() is None)
        if is_recording:
            recording_status = "recording"
        elif preflight_error:
            recording_status = "blocked"
        elif desired_recording:
            recording_status = "scheduled_stopped"
        else:
            recording_status = "disabled"
        video_codec = camera.get("record_video_codec") or camera.get("live_video_codec") or camera.get("video_codec") or camera.get("codec") or ""
        nvr_mode = recording_mode_from_codec(video_codec, nvr)
        health = recording_health(camera, index)
        cameras.append(
            {
                "index": index,
                "id": camera.get("id"),
                "key": key,
                "record_stream": record_stream,
                "record_rtsp": redact_rtsp(record_rtsp),
                "record_source": record_source,
                "record_mode": nvr_mode,
                "video_codec": video_codec,
                "desired_recording": desired_recording,
                "can_record": not preflight_error and bool(direct_record_rtsp),
                "record_error": preflight_error,
                "recording": is_recording,
                "recording_status": recording_status,
                "pid": process.pid if is_recording else None,
                "directory": str(directory),
                "directory_exists": directory.exists(),
                "segments": segment_count,
                "segments_total": total_segment_count,
                "segments_pending": max(0, total_segment_count - segment_count),
                "files": segment_files,
                "days": days,
                "selected_day": selected_day,
                "segment_seconds": segment_seconds,
                "playback_cache": playback_cache,
                "timeline": {
                    "continuous": True,
                    "mode": "day_wall_clock",
                    "day": selected_day,
                    "day_start_ts": day_start_ts,
                    "day_end_ts": day_end_ts,
                    "day_total_seconds": timeline_total_seconds,
                    "file_count": len(timeline_files),
                    "recorded_seconds": recorded_seconds,
                    "playback_total_seconds": recorded_seconds,
                    "total_seconds": timeline_total_seconds,
                    "available_start_ts": available_start_ts,
                    "available_end_ts": available_end_ts,
                    "oldest_at": timeline_files[0].get("start_at") if timeline_files else "",
                    "newest_at": timeline_files[-1].get("start_at") if timeline_files else "",
                },
                "last_error": health["last_error"],
                "log_path": health["log_path"],
                "log_tail": health["log_tail"],
                "ffmpeg_present": health["ffmpeg_present"],
                "ffprobe_present": health["ffprobe_present"],
            }
        )
    return {"cameras": cameras}


def serialized_recording(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with RECORDING_CONTROL_LOCK:
            return function(*args, **kwargs)
    return wrapped


@serialized_recording
def restart_changed_recordings(previous: dict, current: dict) -> list[str]:
    current_by_id = {recording_key(camera, index): camera for index, camera in enumerate(current.get("cameras", []))}
    stopped = []
    fields = ("host", "username", "password", "rtsp_main", "rtsp_sub", "record_stream", "enabled", "record")
    nvr_fields = ("segment_seconds", "browser_playback")
    global_change = previous.get("storage", {}).get("recordings_dir") != current.get("storage", {}).get("recordings_dir") or any(previous.get("nvr", {}).get(field) != current.get("nvr", {}).get(field) for field in nvr_fields)
    for index, camera in enumerate(previous.get("cameras", [])):
        key = recording_key(camera, index)
        target = current_by_id.get(key)
        if global_change or target is None or any(camera.get(field) != target.get(field) for field in fields):
            if key in RECORDING_PROCESSES:
                stop_recording(camera, index)
                stopped.append(key)
    return stopped


@serialized_recording
def ensure_configured_recordings(config: dict, reason: str) -> list[dict]:
    cleanup_recording_processes()
    results = []
    for index, camera in enumerate(config.get("cameras", [])):
        key = recording_key(camera, index)
        process = RECORDING_PROCESSES.get(key)
        running = bool(process and process.poll() is None)
        should_record = camera_should_record(camera)
        if should_record and not running:
            try:
                result = start_recording(camera, index)
                results.append({"index": index, "id": camera.get("id"), "action": "started", "result": result})
            except Exception as error:  # noqa: BLE001
                error_payload = {
                    "index": index,
                    "id": camera.get("id"),
                    "action": "start_failed",
                    "error": str(error),
                    "type": type(error).__name__,
                    "log_tail": recording_log_tail(camera, index, 4000),
                }
                results.append(error_payload)
                write_debug_event("recording_autostart_failed", {**error_payload, "reason": reason})
        elif not should_record and running:
            result = stop_recording(camera, index)
            results.append({"index": index, "id": camera.get("id"), "action": "stopped_disabled", "result": result})
        else:
            results.append({
                "index": index,
                "id": camera.get("id"),
                "action": "kept_running" if running else "kept_stopped",
                "desired_recording": should_record,
            })
    write_debug_event("recording_ensure_done", {"reason": reason, "results": results})
    return results


def schedule_recording_ensure(config: dict, reason: str) -> None:
    global LAST_RECORDING_ENSURE_AT
    if reason == "recording_status":
        now = time.monotonic()
        with RECORDING_ENSURE_LOCK:
            if now - LAST_RECORDING_ENSURE_AT < RECORDING_ENSURE_MIN_SECONDS:
                return
            LAST_RECORDING_ENSURE_AT = now
    def run_latest():
        with RECORDING_CONTROL_LOCK:
            ensure_configured_recordings(load_config(), reason)
    thread = threading.Thread(target=run_latest, daemon=True)
    thread.start()


@serialized_recording
def start_recording(camera: dict, index: int) -> dict:
    cleanup_recording_processes()
    key = recording_key(camera, index)
    existing = RECORDING_PROCESSES.get(key)
    if existing and existing.poll() is None:
        return {"started": False, "status": "already_recording", "key": key, "pid": existing.pid}

    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg_not_installed_in_addon")

    record_stream = camera.get("record_stream") or "main"
    direct_stream = camera_stream(camera, record_stream)
    if not direct_stream:
        raise ValueError(recording_preflight_error(camera, record_stream) or "recording_rtsp_not_configured")
    stream, record_source = recording_source_stream(camera, index, record_stream)

    directory = recording_base_dir(camera, index)
    directory.mkdir(parents=True, exist_ok=True)
    probe_file = directory / ".write-test"
    try:
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
    except OSError as error:
        raise RuntimeError(f"recording_directory_not_writable: {directory}: {error}") from error

    config = load_config()
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    retention_days = recording_retention_days(config)
    removed_old = cleanup_old_recordings(directory, retention_days)
    log_path = directory / "ffmpeg.log"
    output_pattern = str(directory / "%Y%m%d-%H%M%S.mp4")
    mode, video_codec, audio_codec, probe = recording_codec_mode(direct_stream, nvr)
    command = build_recording_command(stream, output_pattern, segment_seconds, mode)
    log_header = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": "recording_start_command",
        "key": key,
        "camera_id": camera.get("id"),
        "record_stream": record_stream,
        "record_source": record_source,
        "directory": str(directory),
        "output_pattern": output_pattern,
        "mode": mode,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "command": redact_command(command),
    }
    with log_path.open("ab") as header_file:
        header_file.write(("\n=== Edge recording start ===\n" + json.dumps(log_header, indent=2) + "\n").encode("utf-8"))
    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log_file)
    except OSError:
        raise
    finally:
        log_file.close()
    RECORDING_PROCESSES[key] = process
    time.sleep(1.2)
    early_exit = process.poll()
    if early_exit is not None:
        RECORDING_PROCESSES.pop(key, None)
        tail = recording_log_tail(camera, index, 6000)
        write_debug_event("recording_start_failed", {
            "key": key,
            "camera_id": camera.get("id"),
            "record_stream": record_stream,
            "record_source": record_source,
            "exit_code": early_exit,
            "log_tail": tail,
            "command": redact_command(command),
        })
        raise RuntimeError(f"ffmpeg_exited_immediately exit={early_exit}: {tail[-1200:]}")
    write_debug_event("recording_start", {
        "key": key,
        "camera_id": camera.get("id"),
        "record_stream": record_stream,
        "record_source": record_source,
        "segment_seconds": segment_seconds,
        "retention_days": retention_days,
        "removed_old": removed_old,
        "mode": mode,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "probe": probe,
        "command": redact_command(command),
    })
    return {
        "started": True,
        "status": "recording",
        "key": key,
        "pid": process.pid,
        "directory": str(directory),
        "record_stream": record_stream,
        "record_source": record_source,
        "record_rtsp": redact_rtsp(stream),
        "record_mode": mode,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "segment_seconds": segment_seconds,
    }


@serialized_recording
def stop_recording(camera: dict, index: int) -> dict:
    cleanup_recording_processes()
    key = recording_key(camera, index)
    process = RECORDING_PROCESSES.pop(key, None)
    if not process or process.poll() is not None:
        return {"stopped": False, "status": "not_recording", "key": key}
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    return {"stopped": True, "status": "stopped", "key": key}


def isapi_base(camera: dict) -> str:
    base = camera.get("isapi_base_url") or (f"http://{camera.get('host')}" if camera.get("host") else "")
    return base.rstrip("/")


def isapi_request(camera: dict, method: str, path: str, body: bytes | None = None, timeout: int = 15) -> str:
    base = isapi_base(camera)
    username = camera.get("username") or ""
    password = camera.get("password") or ""
    if not base:
        raise ValueError("isapi_base_url_missing")
    if not username:
        raise ValueError("camera_username_missing")

    command = [
        "curl",
        "--silent",
        "--show-error",
        "--anyauth",
        "--user",
        f"{username}:{password}",
        "--connect-timeout",
        "5",
        "--max-time",
        str(timeout),
        "--request",
        method,
        f"{base}{path}",
        "--write-out",
        "\n%{http_code}",
    ]
    if body is not None:
        command.extend(["--header", "Content-Type: application/xml", "--data-binary", "@-"])

    try:
        result = subprocess.run(
            command,
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout + 3,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("curl_not_installed") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("isapi_timeout") from error

    output = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    payload, _, status_text = output.rpartition("\n")
    try:
        status = int(status_text.strip())
    except ValueError:
        status = 0

    if result.returncode != 0:
        raise RuntimeError(f"{path}: {stderr or f'isapi_curl_error_{result.returncode}'}")
    if status >= 400 or status == 0:
        detail = payload.strip() or stderr or f"isapi_http_{status}"
        raise RuntimeError(f"{path}: isapi_http_{status}: {detail[-420:]}")
    return payload


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def first_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if local_name(child.tag) == name:
            return child
    return None


def child_text(element: ET.Element | None, name: str, fallback: str = "") -> str:
    if element is None:
        return fallback
    child = first_child(element, name)
    if child is None or child.text is None:
        return fallback
    return child.text.strip()


def set_child_text(element: ET.Element | None, name: str, value: str) -> bool:
    if element is None:
        return False
    child = first_child(element, name)
    if child is None:
        return False
    child.text = str(value)
    return True


def hik_fps(raw_value: str) -> str:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return raw_value or ""
    if value > 100:
        value = value / 100
    return str(int(value)) if value.is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def hik_raw_fps(value: str) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "")
    if number <= 60:
        number *= 100
    return str(int(number))


def keyframe_interval_from_fps(value: str) -> str:
    try:
        if "/" in str(value):
            numerator, denominator = str(value).split("/", 1)
            fps = float(numerator) / max(float(denominator), 1.0)
        else:
            fps = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    if fps > 100:
        fps = fps / 100
    return str(max(1, int(round(fps * 4))))


def parse_xml(payload: str) -> ET.Element:
    return ET.fromstring(payload.encode("utf-8"))


def xml_namespace(root: ET.Element) -> str:
    if root.tag.startswith("{"):
        return root.tag[1:].split("}", 1)[0]
    return ""


def xml_to_bytes(root: ET.Element) -> bytes:
    namespace = xml_namespace(root)
    if namespace:
        ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_device_info(payload: str) -> dict:
    root = parse_xml(payload)
    return {
        "device_name": child_text(root, "deviceName"),
        "model": child_text(root, "model"),
        "serial_number": child_text(root, "serialNumber"),
        "firmware": child_text(root, "firmwareVersion"),
        "mac": child_text(root, "macAddress"),
    }


def parse_stream_config(payload: str) -> dict:
    root = parse_xml(payload)
    video = first_child(root, "Video")
    audio = first_child(root, "Audio")
    max_frame_rate = child_text(video, "maxFrameRate")
    return {
        "id": child_text(root, "id"),
        "name": child_text(root, "channelName"),
        "enabled": child_text(root, "enabled"),
        "video": {
            "enabled": child_text(video, "enabled"),
            "codec": child_text(video, "videoCodecType"),
            "width": child_text(video, "videoResolutionWidth"),
            "height": child_text(video, "videoResolutionHeight"),
            "fps": hik_fps(max_frame_rate),
            "raw_fps": max_frame_rate,
            "bitrate_mode": child_text(video, "videoQualityControlType"),
            "bitrate": child_text(video, "constantBitRate"),
            "quality": child_text(video, "fixedQuality"),
            "keyframe_interval": child_text(video, "keyFrameInterval"),
        },
        "audio": {
            "enabled": child_text(audio, "enabled"),
            "codec": child_text(audio, "audioCompressionType") or child_text(audio, "audioEncoding"),
            "sample_rate": child_text(audio, "audioSamplingRate"),
            "bitrate": child_text(audio, "audioBitRate"),
        },
    }


def parse_stream_channel_list(payload: str) -> list[dict]:
    root = parse_xml(payload)
    channels = []
    for item in root.iter():
        if local_name(item.tag) != "StreamingChannel":
            continue
        video = first_child(item, "Video")
        audio = first_child(item, "Audio")
        max_frame_rate = child_text(video, "maxFrameRate")
        channel = {
            "id": child_text(item, "id"),
            "name": child_text(item, "channelName"),
            "enabled": child_text(item, "enabled"),
            "video": {
                "codec": child_text(video, "videoCodecType"),
                "width": child_text(video, "videoResolutionWidth"),
                "height": child_text(video, "videoResolutionHeight"),
                "fps": hik_fps(max_frame_rate),
                "raw_fps": max_frame_rate,
                "bitrate_mode": child_text(video, "videoQualityControlType"),
                "bitrate": child_text(video, "constantBitRate"),
                "quality": child_text(video, "fixedQuality"),
                "keyframe_interval": child_text(video, "keyFrameInterval"),
            },
            "audio": {
                "codec": child_text(audio, "audioCompressionType") or child_text(audio, "audioEncoding"),
                "sample_rate": child_text(audio, "audioSamplingRate"),
                "bitrate": child_text(audio, "audioBitRate"),
            },
        }
        if channel["id"]:
            channels.append(channel)
    return channels


def autoconfig_endpoints(camera: dict) -> dict[str, str]:
    main_channel = stream_channel(camera, "main")
    sub_channel = stream_channel(camera, "sub")
    image_channel = hikvision_camera_number_from_channel(main_channel)
    return {
        "deviceInfo": "/ISAPI/System/deviceInfo",
        "streamMain": f"/ISAPI/Streaming/channels/{main_channel}",
        "streamSub": f"/ISAPI/Streaming/channels/{sub_channel}",
        "streamChannels": "/ISAPI/Streaming/channels",
        "time": "/ISAPI/System/time",
        "videoInputs": "/ISAPI/System/Video/inputs/channels",
        "networkInterfaces": "/ISAPI/System/Network/interfaces",
        "imageChannel": f"/ISAPI/Image/channels/{image_channel}",
    }


def onvif_request(camera: dict, endpoint: str, action: str, body: str) -> ET.Element:
    import urllib.request
    endpoint_url = urlparse(endpoint)
    configured = urlparse(camera.get("onvif_url") or "")
    allowed_hosts = {configured.hostname, urlparse("http://" + (camera.get("host") or "")).hostname}
    if endpoint_url.scheme not in ("http", "https") or endpoint_url.hostname not in allowed_hosts:
        raise ValueError("ONVIF service must be hosted by the configured camera.")
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    password = camera.get("password") or ""
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    username = camera.get("username") or ""
    security = f'<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken><wsse:Username>{xml_escape(username)}</wsse:Username><wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password><wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce><wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>'
    envelope = f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><s:Header>{security}</s:Header><s:Body>{body}</s:Body></s:Envelope>'
    request = urllib.request.Request(endpoint, data=envelope.encode(), headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'}, method="POST")
    passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, endpoint, username, password)
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(passwords))
    try:
        with opener.open(request, timeout=8) as response:
            payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise RuntimeError("ONVIF response is too large.")
        root = ET.fromstring(payload)
    except (OSError, ET.ParseError) as error:
        raise RuntimeError("ONVIF request failed; check the service URL, ONVIF account and camera clock.") from error
    if any(element.tag.rsplit("}", 1)[-1] == "Fault" for element in root.iter()):
        raise RuntimeError("ONVIF camera rejected the request; check ONVIF permissions and clock synchronization.")
    return root


def onvif_value(root: ET.Element, name: str) -> str:
    return next((element.text or "" for element in root.iter() if element.tag.rsplit("}", 1)[-1] == name), "")


def fetch_onvif_camera_config(camera: dict) -> dict:
    endpoint = camera.get("onvif_url") or ""
    if not endpoint:
        raise ValueError("Enter the camera ONVIF device-service URL first.")
    capabilities = onvif_request(camera, endpoint, "http://www.onvif.org/ver10/device/wsdl/GetCapabilities", '<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>')
    media = next((node for node in capabilities.iter() if node.tag.rsplit("}", 1)[-1] == "Media"), None)
    media_url = onvif_value(media, "XAddr") if media is not None else ""
    if not media_url:
        raise RuntimeError("Camera did not expose an ONVIF Media service; use manual RTSP URLs.")
    profiles_xml = onvif_request(camera, media_url, "http://www.onvif.org/ver10/media/wsdl/GetProfiles", '<trt:GetProfiles/>')
    profiles = []
    for node in profiles_xml.iter():
        if node.tag.rsplit("}", 1)[-1] != "Profiles" or not node.get("token"):
            continue
        profiles.append({"token": node.get("token"), "area": safe_int(onvif_value(node, "Width"), 0) * safe_int(onvif_value(node, "Height"), 0)})
    if not profiles:
        raise RuntimeError("Camera returned no ONVIF profiles; enable ONVIF or enter manual RTSP URLs.")
    profiles.sort(key=lambda item: item["area"], reverse=True)
    discovered = dict(camera)
    for stream, profile in (("main", profiles[0]), ("sub", profiles[-1])):
        body = f'<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{xml_escape(profile["token"])}</trt:ProfileToken></trt:GetStreamUri>'
        uri_xml = onvif_request(camera, media_url, "http://www.onvif.org/ver10/media/wsdl/GetStreamUri", body)
        uri = onvif_value(uri_xml, "Uri")
        if urlparse(uri).scheme not in ("rtsp", "rtsps"):
            raise RuntimeError("Camera did not return a playable RTSP URL.")
        discovered[f"rtsp_{stream}"] = rebind_camera_rtsp(uri, camera.get("host") or urlparse(endpoint).hostname or "", camera.get("username") or "", camera.get("password") or "")
    return {"ok": True, "camera": discovered, "device": {"manufacturer": CAMERA_VENDORS.get(camera.get("vendor"), "ONVIF")}, "streams": {}, "channels": [], "sections": {"onvif": {"ok": True}}, "effective_streams": effective_streams(discovered), "recommendations": [{"severity": "notice", "stream": "all", "message": "ONVIF main/sub streams loaded into the form. Save cameras to apply them."}]}


def fetch_camera_autoconfig(camera: dict) -> dict:
    if camera.get("vendor") != "hikvision":
        return fetch_onvif_camera_config(camera)
    endpoints = autoconfig_endpoints(camera)
    raw_sections: dict[str, dict] = {}
    for name, path in endpoints.items():
        try:
            raw_sections[name] = {"ok": True, "xml": isapi_request(camera, "GET", path)}
        except (RuntimeError, ValueError) as error:
            raw_sections[name] = {"ok": False, "error": str(error)}

    streams = {}
    for stream_name, section in (("main", "streamMain"), ("sub", "streamSub")):
        if raw_sections[section]["ok"]:
            try:
                streams[stream_name] = parse_stream_config(raw_sections[section]["xml"])
            except ET.ParseError as error:
                raw_sections[section] = {"ok": False, "error": f"xml_parse_error: {error}"}

    channels = []
    if raw_sections["streamChannels"]["ok"]:
        try:
            channels = parse_stream_channel_list(raw_sections["streamChannels"]["xml"])
        except ET.ParseError as error:
            raw_sections["streamChannels"] = {"ok": False, "error": f"xml_parse_error: {error}"}

    device = {}
    if raw_sections["deviceInfo"]["ok"]:
        try:
            device = parse_device_info(raw_sections["deviceInfo"]["xml"])
        except ET.ParseError as error:
            raw_sections["deviceInfo"] = {"ok": False, "error": f"xml_parse_error: {error}"}

    essential_errors = [
        f"{section}: {raw_sections[section].get('error', 'not available')}"
        for section in ("deviceInfo", "streamMain", "streamSub")
        if not raw_sections[section].get("ok")
    ]
    autoconfig_error = ""
    if not device and not streams:
        autoconfig_error = "Camera ISAPI did not return device or stream configuration. " + " | ".join(essential_errors)

    recommendations = camera_autoconfig_recommendations(streams, channels)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": not autoconfig_error,
        "error": autoconfig_error,
        "device": device,
        "streams": streams,
        "channels": channels,
        "recommendations": recommendations,
        "effective_streams": effective_streams(camera),
        "sections": {
            key: {"ok": value.get("ok", False), "error": value.get("error", "")}
            for key, value in raw_sections.items()
        },
    }


def camera_autoconfig_recommendations(streams: dict, channels: list[dict]) -> list[dict]:
    recommendations = []
    source_streams = streams if streams else {
        ("main" if str(channel.get("id", "")).endswith("01") else "sub"): channel
        for channel in channels
        if isinstance(channel, dict)
    }
    for name, stream in source_streams.items():
        if not isinstance(stream, dict):
            continue
        video = stream.get("video") if isinstance(stream.get("video"), dict) else {}
        audio = stream.get("audio") if isinstance(stream.get("audio"), dict) else {}
        codec = str(video.get("codec") or "").lower()
        fps = safe_int(video.get("fps"), 0)
        keyframe = safe_int(video.get("keyframe_interval"), 0)
        target_keyframe = fps * 4 if fps else 0
        bitrate = safe_int(video.get("bitrate"), 0)
        width = safe_int(video.get("width"), 0)
        height = safe_int(video.get("height"), 0)
        if codec and codec not in ("h264", "h.264"):
            recommendations.append({
                "stream": name,
                "severity": "warning",
                "message": f"{name}: WebRTC in browsers is safest with H264. HEVC/H265 can be kept for recording/LL-HLS, but live WebRTC may need H264 or transcoding.",
            })
        if codec in ("h264+", "h.264+", "smart264", "smart h264"):
            recommendations.append({
                "stream": name,
                "severity": "warning",
                "message": f"{name}: disable H264+/Smart Codec for live streaming reliability.",
            })
        if target_keyframe and keyframe and abs(keyframe - target_keyframe) > max(4, fps):
            recommendations.append({
                "stream": name,
                "severity": "notice",
                "message": f"{name}: keyframe interval should be close to fps * 4 ({target_keyframe}) for faster starts; camera reports {keyframe}.",
            })
        if name == "sub" and width and height and (width > 1280 or height > 720):
            recommendations.append({
                "stream": name,
                "severity": "notice",
                "message": f"{name}: substream is {width}x{height}. For LTE tiles, 1280x720 around 1 Mbit or 640x360 around 500 Kbit starts faster.",
            })
        if name == "sub" and bitrate and bitrate > 1500:
            recommendations.append({
                "stream": name,
                "severity": "notice",
                "message": f"{name}: bitrate {bitrate} kbit/s is high for LTE tile/live start; consider 500-1000 kbit/s variable bitrate.",
            })
        audio_codec = str(audio.get("codec") or "").lower()
        if audio_codec and audio_codec not in ("pcmu", "pcm_mulaw", "g711ulaw", "g711mulaw", "aac", "opus", "pcm_alaw", "g711alaw"):
            recommendations.append({
                "stream": name,
                "severity": "notice",
                "message": f"{name}: audio codec {audio_codec} may need transcoding for browser/WebRTC playback.",
            })
    if not recommendations:
        recommendations.append({
            "stream": "all",
            "severity": "ok",
            "message": "Streams look compatible. Keep substream warmed for mobile live and main stream for recording.",
        })
    return recommendations


def update_stream_config(camera: dict, stream_name: str, settings: dict) -> dict:
    stream_name = normalize_stream_name(stream_name, "sub")
    channel_id = stream_channel(camera, stream_name)
    current_xml = isapi_request(camera, "GET", f"/ISAPI/Streaming/channels/{channel_id}")
    root = parse_xml(current_xml)
    video = first_child(root, "Video")
    audio = first_child(root, "Audio")
    video_settings = settings.get("video") if isinstance(settings.get("video"), dict) else {}
    audio_settings = settings.get("audio") if isinstance(settings.get("audio"), dict) else {}

    video_map = {
        "enabled": "enabled",
        "codec": "videoCodecType",
        "width": "videoResolutionWidth",
        "height": "videoResolutionHeight",
        "bitrate_mode": "videoQualityControlType",
        "bitrate": "constantBitRate",
        "quality": "fixedQuality",
        "keyframe_interval": "keyFrameInterval",
    }
    audio_map = {
        "enabled": "enabled",
        "sample_rate": "audioSamplingRate",
        "bitrate": "audioBitRate",
    }

    changed = False
    for source, target in video_map.items():
        if source in video_settings:
            changed = set_child_text(video, target, str(video_settings[source])) or changed
    if "fps" in video_settings:
        changed = set_child_text(video, "maxFrameRate", hik_raw_fps(str(video_settings["fps"]))) or changed
        if "keyframe_interval" not in video_settings:
            keyframe_interval = keyframe_interval_from_fps(str(video_settings["fps"]))
            if keyframe_interval:
                changed = set_child_text(video, "keyFrameInterval", keyframe_interval) or changed
    for source, target in audio_map.items():
        if source in audio_settings:
            changed = set_child_text(audio, target, str(audio_settings[source])) or changed
    if "codec" in audio_settings:
        changed = (
            set_child_text(audio, "audioCompressionType", str(audio_settings["codec"]))
            or set_child_text(audio, "audioEncoding", str(audio_settings["codec"]))
            or changed
        )

    if not changed:
        raise ValueError("No supported stream fields were changed.")

    updated_xml = xml_to_bytes(root)
    isapi_request(camera, "PUT", f"/ISAPI/Streaming/channels/{channel_id}", updated_xml)
    refreshed_xml = isapi_request(camera, "GET", f"/ISAPI/Streaming/channels/{channel_id}")
    return parse_stream_config(refreshed_xml)


def publish_pending_camera_status(config: dict) -> None:
    rows = []
    for index, camera in enumerate(config.get("cameras", [])):
        rows.append({
            "id": camera.get("id"), "index": index, "key": recording_key(camera, index),
            "name": camera.get("name"), "vendor": camera.get("vendor"), "host": camera.get("host"),
            "enabled": bool(camera.get("enabled")), "record": bool(camera.get("record")),
            "live_stream": camera.get("live_stream"), "tile_stream": camera.get("tile_stream"),
            "record_stream": camera.get("record_stream"), "snapshot_stream": camera.get("snapshot_stream"),
            "status": "checking" if camera.get("enabled") else "disabled", "detail": "Saved configuration applied; refreshing connection status.",
            "stream_urls": stream_urls(camera, index), "effective_streams": effective_streams(camera),
            "record_rtsp": redact_rtsp(camera_stream(camera, camera.get("record_stream") or "main")),
        })
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cameras": rows}
    write_json(DATA_DIR / "cameras.json", payload)
    write_json(HOME_DIR / "cameras.json", payload)


def refresh_status_after_save() -> None:
    try:
        refresh_status()
    except Exception as error:  # noqa: BLE001
        write_debug_event("camera_refresh_after_save_failed", {"type": type(error).__name__})


def refresh_status() -> dict:
    config = load_config()
    previous_payload = read_json(DATA_DIR / "cameras.json", {"cameras": []})
    previous_by_key = {
        f"{item.get('id')}::{item.get('index')}": item
        for item in previous_payload.get("cameras", [])
        if isinstance(item, dict)
    }
    cameras = []

    for index, camera in enumerate(config.get("cameras", [])):
        camera_id = safe_id(camera.get("id", "camera"))
        camera_key = f"{camera_id}_{index}"
        previous = previous_by_key.get(f"{camera.get('id')}::{index}") or {}
        status = "disabled"
        detail = "Camera is configured but disabled."
        video_codec = ""
        audio_codec = ""
        audio_sample_rate = ""
        audio_channels = ""
        width = ""
        height = ""
        fps = ""
        bitrate = ""
        live_stream_name = camera.get("live_stream") or "sub"
        live_rtsp = camera_stream(camera, live_stream_name)
        record_stream_name = camera.get("record_stream") or "main"
        record_rtsp = camera_stream(camera, record_stream_name)
        live_video_codec = ""
        live_width = ""
        live_height = ""
        live_fps = ""
        live_bitrate = ""
        live_probe_status = "not_checked"
        snapshot_url = ""
        snapshot_path = ""

        if camera.get("enabled"):
            rtsp_main = camera.get("rtsp_main") or ""
            if not rtsp_main:
                status = "missing_rtsp"
                detail = "Camera is enabled, but rtsp_main is empty."
            else:
                main_probe = probe_rtsp_stream(recording_source_stream(camera, index, "main")[0])
                video_stream = main_probe.get("video") or {}
                audio_stream = main_probe.get("audio") or {}
                if video_stream:
                    status = "online"
                    detail = "RTSP main stream is reachable."
                    video_codec = str(video_stream.get("codec_name") or "")
                    width = str(video_stream.get("width") or "")
                    height = str(video_stream.get("height") or "")
                    fps = str(video_stream.get("r_frame_rate") or "")
                    bitrate = str(video_stream.get("bit_rate") or "")
                    audio_codec = str(audio_stream.get("codec_name") or "")
                    audio_sample_rate = str(audio_stream.get("sample_rate") or "")
                    audio_channels = str(audio_stream.get("channels") or "")
                else:
                    status = "lost_connection" if previous.get("status") == "online" else "offline"
                    detail = "RTSP connection was lost." if status == "lost_connection" else "RTSP probe failed. Check IP, credentials, port 554, and camera stream path."
            if live_rtsp:
                if live_rtsp == rtsp_main and video_codec:
                    live_probe_status = "online"
                    live_video_codec = video_codec
                    live_width = width
                    live_height = height
                    live_fps = fps
                    live_bitrate = bitrate
                else:
                    live_probe = probe_rtsp_stream(recording_source_stream(camera, index, live_stream_name)[0])
                    live_video_stream = live_probe.get("video") or {}
                    if live_video_stream:
                        live_probe_status = "online"
                        live_video_codec = str(live_video_stream.get("codec_name") or "")
                        live_width = str(live_video_stream.get("width") or "")
                        live_height = str(live_video_stream.get("height") or "")
                        live_fps = str(live_video_stream.get("r_frame_rate") or "")
                        live_bitrate = str(live_video_stream.get("bit_rate") or "")
                    else:
                        live_probe_status = "offline"
            else:
                live_probe_status = "missing_rtsp"

        # Live is now served by MediaMTX/Janus. Status refresh intentionally does
        # not capture JPEG snapshots, so old fallback work cannot block live.

        camera_status = {
            "id": camera.get("id"),
            "index": index,
            "key": camera_key,
            "name": camera.get("name"),
            "vendor": camera.get("vendor"),
            "host": camera.get("host"),
            "enabled": bool(camera.get("enabled")),
            "record": bool(camera.get("record")),
            "low_latency": bool(camera.get("low_latency")),
            "snapshot_stream": camera.get("snapshot_stream") or "sub",
            "live_stream": live_stream_name,
            "tile_stream": camera.get("tile_stream") or "sub",
            "record_stream": record_stream_name,
            "record_rtsp": redact_rtsp(record_rtsp),
            "live_rtsp": redact_rtsp(live_rtsp),
            "effective_streams": effective_streams(camera),
            "stream_urls": stream_urls(camera, index),
            "main_channel": stream_channel(camera, "main"),
            "sub_channel": stream_channel(camera, "sub"),
            "live_probe_status": live_probe_status,
            "live_video_codec": live_video_codec,
            "live_width": live_width,
            "live_height": live_height,
            "live_fps": live_fps,
            "live_bitrate": live_bitrate,
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
            "bitrate": bitrate,
            "snapshot_url": snapshot_url,
            "snapshot_path": snapshot_path,
        }
        cameras.append(camera_status)

    with CONFIG_LOCK:
        if config != load_config():
            return read_json(DATA_DIR / "cameras.json", {"cameras": []})
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cameras": cameras}
        write_json(DATA_DIR / "cameras.json", payload)
        write_json(HOME_DIR / "cameras.json", payload)
    return payload


def home_assistant_dashboard_payload() -> dict:
    """Return entity IDs and camera labels only, never connection credentials."""
    config = load_config()
    mapping = read_json(HOME_DIR / "ha-entities.json", {})
    registered = mapping.get("entities", []) if isinstance(mapping, dict) else []
    if not isinstance(registered, list):
        registered = []
    cameras = []
    for camera in config.get("cameras", []):
        matches = [item for item in registered if isinstance(item, dict) and item.get("camera_id") == camera.get("id") and re.fullmatch(r"camera\.[a-z0-9_]+", str(item.get("entity_id") or ""))]
        local = [item for item in matches if item.get("local")]
        candidates = sorted({item["entity_id"] for item in (local or matches)})
        cameras.append({"id": camera.get("id"), "name": camera.get("name") or camera.get("id"), "enabled": bool(camera.get("enabled")), "entity_id": candidates[0] if len(candidates) == 1 else "", "entities": candidates})
    card_version = mapping.get("card_version") if isinstance(mapping, dict) else ""
    card_ready = bool(isinstance(card_version, str) and re.fullmatch(r"\d+\.\d+\.\d+", card_version) and mapping.get("card_type") == "custom:edge-live-card")
    return {"cameras": cameras, "integration_detected": any(item["entities"] for item in cameras), "card_ready": card_ready, "card_version": card_version if card_ready else "", "card_resource": "/edge/edge-live-card.js"}


def health_payload() -> dict:
    core = engine_runtime_status()
    healthy = not EDGE_RTC_ENABLED or core.get("edge_rtc", {}).get("api_reachable", False)
    return {
        "status": "ok" if healthy else "error",
        "product": "Edge of Infinity",
        "version": APP_VERSION,
        "server_version": SERVER_VERSION,
        "ui_build": UI_BUILD,
        "mode": "edge-rtc-native" if EDGE_RTC_ENABLED else "mediamtx-janus-webrtc-core",
        "authoritative_config": str(CONFIG_PATH),
        "addon_config_mirror": str(ADDON_CONFIG_PATH),
        "core": core,
    }


EDGE_RTC_PLAYER_HTML = r"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%;background:#080e15;color:#d9e7ee;font:13px system-ui}canvas{width:100%;height:100%;object-fit:contain}button{position:absolute;bottom:12px;right:12px;padding:10px;border:0;border-radius:8px;background:#20384b;color:white}p{position:absolute;top:8px;left:10px}</style><canvas></canvas><p>EdgeRTC · connecting…</p><button>Sound</button><script type="module">
import{EdgeRTCClient}from'../client.js';
const path=location.pathname.split('/').pop(),url=new URL('../stream/'+path,location.href);let client,timer,retries=0,muted=true;
function connect(){clearTimeout(timer);client?.close();if(document.hidden)return;client=new EdgeRTCClient({url:url.href,canvas:document.querySelector('canvas'),onError:m=>{document.querySelector('p').textContent=m;if(!document.hidden&&retries<5)timer=setTimeout(connect,Math.min(30000,2000*2**retries++));},onFrame:f=>{retries=0;document.querySelector('p').textContent='EdgeRTC · '+f.width+' × '+f.height;}});client.setAudio(.6,muted);client.start().catch(e=>{document.querySelector('p').textContent=e.message;});}
connect();document.querySelector('button').onclick=()=>{muted=!muted;client?.setAudio(.6,muted);document.querySelector('button').textContent=muted?'Sound':'Mute';};document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(document.hidden)client?.close();else connect();});window.addEventListener('pagehide',()=>{clearTimeout(timer);client?.close();});
</script>"""


INDEX_HTML = r"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="edge-panel-version" content="__EDGE_PANEL_VERSION__">
    <meta name="edge-ui-build" content="__EDGE_UI_BUILD__">
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
        --sidebar-width: 220px;
        --sidebar-collapsed-width: 74px;
      }
      * { box-sizing: border-box; }
      body { margin: 0; background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; }
      body.soft-fullscreen-active { overflow: hidden; }
      .app {
        min-height: 100vh;
        display: grid;
        grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
        transition: grid-template-columns .18s ease;
      }
      body.nav-collapsed .app { grid-template-columns: var(--sidebar-collapsed-width) minmax(0, 1fr); }
      .sidebar {
        border-right: 1px solid var(--line);
        background: #0f171c;
        padding: 18px 12px;
        position: sticky;
        top: 0;
        height: 100vh;
        overflow: hidden;
      }
      .sidebar-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 18px;
        min-height: 38px;
      }
      .menu-toggle {
        width: 38px;
        height: 38px;
        flex: 0 0 38px;
        display: grid;
        place-items: center;
        padding: 0;
      }
      .menu-toggle-lines {
        width: 18px;
        height: 14px;
        display: grid;
        gap: 4px;
      }
      .menu-toggle-lines span {
        height: 2px;
        border-radius: 999px;
        background: currentColor;
      }
      .brand { min-width: 0; font-size: 18px; font-weight: 800; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      body.nav-collapsed .brand { display: none; }
      .build-pill {
        margin-left: auto;
        border: 1px solid rgba(86,214,181,.28);
        border-radius: 999px;
        padding: 3px 7px;
        color: var(--accent);
        background: rgba(86,214,181,.07);
        font-size: 11px;
        font-weight: 750;
        white-space: nowrap;
      }
      body.nav-collapsed .build-pill { display: none; }
      .nav { display: grid; gap: 7px; }
      .nav button {
        width: 100%;
        text-align: left;
        background: transparent;
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 40px;
      }
      body.nav-collapsed .nav button { justify-content: center; padding-inline: 8px; }
      body.nav-collapsed .nav-label { display: none; }
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
      main { width: min(1180px, calc(100% - 28px)); min-width: 0; margin: 0 auto; padding: 24px 0 40px; }
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
      .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr)); gap: 14px; align-items: start; }
      .camera, .settings, .panel {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: linear-gradient(180deg, var(--panel-2), var(--panel));
        overflow: hidden;
      }
      .video-tile { min-width: 0; }
      .video-tile .body { padding: 10px 12px; }
      .video-tile .row { min-height: 28px; }
      .video-tile .vendor { font-size: 11px; padding: 3px 7px; }
      .preview { position: relative; aspect-ratio: 16 / 9; display: grid; place-items: center; background: #080d10; border-bottom: 1px solid var(--line); }
      .video-tile .preview { cursor: pointer; }
      .video-tile .preview:hover { outline: 1px solid rgba(86, 214, 181, .45); outline-offset: -1px; }
      .preview img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .preview iframe.live-frame { width: 100%; height: 100%; border: 0; display: block; background: #05080a; }
      .preview span { color: var(--muted); font-size: 13px; padding: 12px; text-align: center; }
      .fullscreen-button {
        position: absolute;
        left: 10px;
        top: 10px;
        z-index: 3;
        width: 32px;
        height: 32px;
        display: grid;
        place-items: center;
        padding: 0;
        border-color: rgba(86,214,181,.45);
        background: rgba(8, 13, 16, .68);
        color: var(--accent);
        backdrop-filter: blur(6px);
      }
      .fullscreen-button svg {
        width: 17px;
        height: 17px;
        display: block;
      }
      .edge-soft-fullscreen {
        position: fixed !important;
        inset: 0 !important;
        z-index: 9999 !important;
        width: 100vw !important;
        height: 100vh !important;
        max-width: none !important;
        max-height: none !important;
        aspect-ratio: auto !important;
        border-radius: 0 !important;
        background: #000 !important;
        display: grid !important;
        place-items: center !important;
      }
      .edge-soft-fullscreen .recording-player,
      .edge-soft-fullscreen iframe,
      .edge-soft-fullscreen .live-frame {
        width: 100% !important;
        height: 100% !important;
        max-height: 100vh !important;
        aspect-ratio: auto !important;
        object-fit: contain !important;
        border: 0 !important;
        border-radius: 0 !important;
      }
      .edge-soft-fullscreen .fullscreen-button {
        left: auto;
        right: 12px;
        top: 12px;
        width: 42px;
        height: 42px;
        background: rgba(0,0,0,.62);
      }
      .live-blocked {
        width: 100%;
        height: 100%;
        display: grid;
        place-items: center;
        padding: 14px;
        text-align: center;
        color: var(--muted);
        background: linear-gradient(180deg, rgba(250,110,126,.08), rgba(0,0,0,.1));
      }
      .live-blocked b {
        display: block;
        margin-bottom: 6px;
        color: var(--danger);
      }
      .live-blocked code {
        display: block;
        margin-top: 7px;
        font-size: 11px;
        color: #b9d6d3;
      }
      .tile-line {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-top: 7px;
        color: var(--muted);
        font-size: 12px;
      }
      .tile-line span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .add-camera-tile {
        min-height: 224px;
        display: grid;
        place-items: center;
        border-style: dashed;
        cursor: pointer;
        background: rgba(86, 214, 181, .05);
      }
      .add-camera-tile:hover {
        border-color: rgba(86, 214, 181, .65);
        background: rgba(86, 214, 181, .09);
      }
      .add-camera-content {
        display: grid;
        gap: 8px;
        justify-items: center;
        color: var(--accent);
        font-weight: 750;
      }
      .add-camera-plus {
        width: 54px;
        height: 54px;
        display: grid;
        place-items: center;
        border: 1px solid rgba(86, 214, 181, .55);
        border-radius: 8px;
        font-size: 38px;
        line-height: 1;
        background: rgba(86, 214, 181, .08);
      }
      .connection-badge {
        position: absolute;
        top: 10px;
        right: 10px;
        z-index: 2;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        min-height: 25px;
        padding: 4px 9px;
        border: 1px solid currentColor;
        border-radius: 999px;
        background: rgba(8, 13, 16, .58);
        backdrop-filter: blur(6px);
        font-size: 12px;
        font-weight: 750;
        line-height: 1;
        text-transform: uppercase;
      }
      .connection-badge::before {
        content: "";
        width: 7px;
        height: 7px;
        border-radius: 999px;
        background: currentColor;
        box-shadow: 0 0 10px currentColor;
      }
      .badge-online { color: #56d6b5; }
      .badge-lost-connection { color: #e4b45d; }
      .badge-offline, .badge-disabled, .badge-missing-rtsp { color: #e66b6b; }
      .body, .settings { padding: 14px; }
      .row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
      .name { font-weight: 750; overflow-wrap: anywhere; }
      .vendor { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; color: var(--accent); font-size: 12px; text-transform: uppercase; }
      .meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
      .metric { border: 1px solid var(--line); border-radius: 8px; padding: 9px; background: rgba(0,0,0,.14); }
      .metric.wide { grid-column: 1 / -1; }
      .metric b { display: block; margin-bottom: 3px; color: var(--muted); font-size: 11px; text-transform: uppercase; }
      .metric span { font-size: 14px; overflow-wrap: anywhere; }
      .state-online { color: var(--accent); }
      .state-lost_connection { color: var(--warn); }
      .state-offline, .state-disabled, .state-missing_rtsp { color: var(--danger); }
      .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
      .settings, .panel { padding: 14px; }
      .panel + .panel { margin-top: 14px; }
      .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 10px; }
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
      .autoconfig { border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-top: 12px; background: rgba(0,0,0,.14); }
      .autoconfig-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
      .stream-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); gap: 10px; margin-top: 10px; }
      .stream-editor { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: rgba(255,255,255,.03); }
      .stream-strip { display: grid; gap: 6px; margin-top: 10px; }
      .stream-pill {
        display: grid;
        grid-template-columns: 76px 72px minmax(0, 1fr);
        gap: 8px;
        align-items: center;
        border: 1px solid rgba(86, 214, 181, .22);
        border-radius: 8px;
        padding: 7px 8px;
        background: rgba(86, 214, 181, .07);
        color: var(--text);
        font-size: 12px;
      }
      .stream-pill b { color: var(--accent); text-transform: uppercase; }
      .stream-pill code { overflow-wrap: anywhere; }
      .section-status { margin-top: 8px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
      .notice { margin-top: 10px; color: var(--muted); font-size: 13px; }
      .notice.warning {
        border: 1px solid rgba(250,110,126,.36);
        border-radius: 8px;
        padding: 10px 12px;
        background: rgba(250,110,126,.08);
        color: var(--text);
      }
      .preset-bar {
        display: grid;
        grid-template-columns: minmax(180px, 1fr) 120px auto;
        gap: 8px;
        align-items: end;
        margin-bottom: 14px;
      }
      #nvr-grid { grid-template-columns: minmax(0, 1fr); }
      .nvr-card, .nvr-card .body, .recording-timeline { min-width: 0; }
      .nvr-card .body { display: grid; gap: 10px; }
      .nvr-card .section-status { overflow-wrap: anywhere; }
      .nvr-card .name { overflow-wrap: anywhere; }
      .nvr-playback-controls { display: grid; gap: 8px; padding: 10px; background: #0c1419; }
      .nvr-playback-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
      .nvr-playback-toolbar button, .nvr-playback-toolbar select { min-height: 44px; }
      .nvr-playback-clock { margin-left: auto; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .nvr-playback-seek { width: 100%; min-width: 0; min-height: 32px; accent-color: var(--accent); touch-action: pan-y; }
      .nvr-playback-message { min-height: 18px; font-size: 12px; color: var(--muted); }
      .recording-timeline .timeline-row { flex-wrap: wrap; overflow-wrap: anywhere; }
      @media (min-width: 1450px) { #nvr-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
      @media (max-width: 600px) {
        .nvr-card .body { padding: 10px; }
        .recording-day-bar { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
        .recording-day-strip { grid-column: 1 / -1; grid-row: 1; }
        .recording-day-bar > button { min-height: 44px; }
        .nvr-playback-toolbar { gap: 6px; }
        .nvr-playback-toolbar button { padding: 8px; }
        .nvr-playback-clock { width: 100%; margin-left: 0; }
        #nvr-grid .recording-film-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); max-height: 320px; }
      }
      .nvr-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
      .rec-badge {
        border: 1px solid rgba(86,214,181,.45);
        border-radius: 999px;
        padding: 5px 9px;
        color: var(--accent);
        background: rgba(86,214,181,.09);
        font-size: 12px;
        font-weight: 800;
        letter-spacing: .02em;
      }
      .rec-badge.off {
        border-color: rgba(250,110,126,.45);
        color: var(--danger);
        background: rgba(250,110,126,.08);
      }
      .recording-player {
        width: 100%;
        aspect-ratio: 16 / 9;
        display: block;
        border: 1px solid var(--line);
        border-radius: 8px;
        object-fit: contain;
        background: #05080a;
      }
      .recording-transition-frame { position: absolute; inset: 0 0 auto; width: 100%; aspect-ratio: 16 / 9; object-fit: contain; background: #05080a; pointer-events: none; }
      .recording-transition-frame[hidden] { display: none; }
      .recording-player-wrap {
        position: relative;
        border-radius: 8px;
        overflow: hidden;
      }
      .recording-player-wrap .recording-player {
        border-radius: 8px;
      }
      .recording-player-wrap:fullscreen, .recording-player-wrap.edge-soft-fullscreen {
        display: grid !important; grid-template-rows: minmax(0, 1fr) auto; width: 100%; height: 100%;
        background: #000; align-items: stretch !important;
      }
      .recording-player-wrap:fullscreen .recording-player, .recording-player-wrap.edge-soft-fullscreen .recording-player {
        height: 100% !important; min-height: 0; aspect-ratio: auto; object-fit: contain;
      }
      .recording-player-wrap:fullscreen .nvr-playback-controls, .recording-player-wrap.edge-soft-fullscreen .nvr-playback-controls { width: 100%; }
      .recording-day-seek { width: 100%; accent-color: #22c55e; cursor: pointer; }
      .recording-timeline {
        display: grid;
        gap: 6px;
        border: 1px solid rgba(86,214,181,.22);
        border-radius: 8px;
        padding: 9px 10px;
        background: rgba(86,214,181,.05);
      }
      .recording-native-timeline { padding: 8px 10px; }
      .timeline-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        color: var(--muted);
        font-size: 12px;
      }
      .recording-day-bar {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        gap: 8px;
        align-items: stretch;
        touch-action: pan-y;
      }
      .recording-day-strip {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        overscroll-behavior-inline: contain;
        scrollbar-width: thin;
      }
      .recording-day-tile {
        min-width: 190px;
        display: grid;
        grid-template-columns: 72px minmax(0, 1fr);
        align-items: center;
        gap: 8px;
        text-align: left;
        border-color: rgba(134,162,180,.24);
      }
      .recording-day-tile.active {
        border-color: rgba(86,214,181,.74);
        background: rgba(86,214,181,.1);
      }
      .recording-day-tile small {
        display: block;
        margin-top: 2px;
        color: var(--muted);
        font-size: 11px;
      }
      .recording-day-thumb {
        position: relative;
        width: 72px;
        height: 41px;
        aspect-ratio: 16 / 9;
        display: block;
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: #05080a;
      }
      .recording-day-thumb img {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        display: block;
        object-fit: cover;
      }
      .recording-day-thumb.thumb-error::after {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(135deg, rgba(86,214,181,.16), rgba(255,255,255,.02));
      }
      .recording-empty {
        aspect-ratio: 16 / 9;
        border: 1px dashed var(--line);
        border-radius: 8px;
        display: grid;
        place-items: center;
        background: rgba(0,0,0,.18);
        color: var(--muted);
      }
      .recording-film-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(160px, 100%), 1fr));
        grid-auto-rows: max-content;
        align-items: start;
        gap: 8px;
        max-height: 232px;
        overflow-y: auto;
        padding-right: 3px;
      }
      .recording-tile {
        display: block;
        width: 100%;
        min-width: 0;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0;
        background: rgba(0,0,0,.14);
        color: var(--text);
        text-align: left;
        cursor: pointer;
        overflow: hidden;
      }
      .recording-tile.active {
        border-color: rgba(86,214,181,.65);
        background: rgba(86,214,181,.08);
      }
      .recording-tile b {
        display: block;
        overflow-wrap: anywhere;
        font-size: 13px;
        padding: 8px 8px 0;
      }
      .recording-meta {
        display: block;
        margin-top: 4px;
        padding: 0 8px 8px;
        color: var(--muted);
        font-size: 11px;
      }
      .recording-thumb {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        aspect-ratio: 16 / 9;
        display: block;
        object-fit: cover;
        background: #05080a;
        border-bottom: 1px solid var(--line);
        opacity: 0;
        transition: opacity .12s ease;
      }
      .recording-thumb-time {
        position: absolute;
        left: 8px;
        bottom: 7px;
        padding: 3px 6px;
        border-radius: 999px;
        background: rgba(3, 6, 8, .72);
        color: #d7f5ef;
        font-size: 11px;
      }
      .recording-thumb-wrap {
        position: relative;
        background: #05080a;
        display: block;
        aspect-ratio: 16 / 9;
        overflow: hidden;
      }
      .recording-thumb-wrap::before {
        content: "Preview";
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
        color: var(--muted);
        font-size: 12px;
        background:
          linear-gradient(135deg, rgba(86,214,181,.10), rgba(255,255,255,.02)),
          repeating-linear-gradient(0deg, rgba(255,255,255,.04), rgba(255,255,255,.04) 1px, transparent 1px, transparent 8px);
      }
      .recording-thumb-wrap.thumb-loaded::before {
        display: none;
      }
      .recording-day-thumb.thumb-loaded .recording-thumb,
      .recording-thumb-wrap.thumb-loaded .recording-thumb {
        opacity: 1;
      }
      .recording-thumb-wrap.thumb-error::after {
        content: "No preview";
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
        color: var(--muted);
        font-size: 12px;
        background: rgba(5, 8, 10, .84);
      }
      .log-grid {
        display: grid;
        gap: 12px;
      }
      .diagnostic-list {
        display: grid;
        gap: 8px;
        margin-bottom: 12px;
      }
      .diagnostic-item {
        border: 1px solid var(--line);
        border-left-width: 4px;
        border-radius: 8px;
        padding: 10px 12px;
        background: rgba(0,0,0,.16);
      }
      .diagnostic-item b {
        display: block;
        margin-bottom: 3px;
      }
      .diagnostic-item span {
        display: block;
        color: var(--muted);
        font-size: 12px;
      }
      .diagnostic-item pre {
        margin: 8px 0 0;
        max-height: 160px;
        overflow: auto;
        color: #b9d6d3;
        font: 11px/1.45 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
        white-space: pre-wrap;
      }
      .diag-ok {
        border-color: rgba(86,214,181,.38);
        border-left-color: #56d6b5;
      }
      .diag-ok b { color: #56d6b5; }
      .diag-warning {
        border-color: rgba(228,180,93,.38);
        border-left-color: #e4b45d;
      }
      .diag-warning b { color: #e4b45d; }
      .diag-error {
        border-color: rgba(250,110,126,.42);
        border-left-color: #fa6e7e;
      }
      .diag-error b { color: #fa6e7e; }
      .log-block {
        border: 1px solid var(--line);
        border-left-width: 4px;
        border-radius: 8px;
        background: rgba(0,0,0,.16);
        overflow: hidden;
      }
      .log-block h2 {
        margin: 0;
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
        font-size: 15px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }
      .log-block.log-ok {
        border-color: rgba(86,214,181,.36);
        border-left-color: #56d6b5;
      }
      .log-block.log-ok h2 { color: #56d6b5; }
      .log-block.log-warning {
        border-color: rgba(228,180,93,.38);
        border-left-color: #e4b45d;
      }
      .log-block.log-warning h2 { color: #e4b45d; }
      .log-block.log-error {
        border-color: rgba(250,110,126,.42);
        border-left-color: #fa6e7e;
      }
      .log-block.log-error h2 { color: #fa6e7e; }
      .log-severity {
        border: 1px solid currentColor;
        border-radius: 999px;
        padding: 2px 8px;
        font-size: 11px;
        line-height: 1.4;
        letter-spacing: 0;
        opacity: .9;
      }
      .log-output {
        margin: 0;
        max-height: 340px;
        overflow: auto;
        padding: 12px;
        color: #b9d6d3;
        font: 12px/1.5 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      code { color: var(--accent); overflow-wrap: anywhere; }
      @media (max-width: 820px) {
        .app { grid-template-columns: 1fr; }
        body.nav-collapsed .app { grid-template-columns: 1fr; }
        .sidebar {
          position: sticky;
          top: 0;
          z-index: 20;
          height: auto;
          border-right: 0;
          border-bottom: 1px solid var(--line);
          padding: 10px;
        }
        .sidebar-header { margin-bottom: 10px; }
        body.nav-collapsed .brand { display: block; }
        body.nav-collapsed .nav { display: none; }
        body.nav-collapsed .nav-label { display: inline; }
        .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .nav button { justify-content: flex-start; }
        .preset-bar { grid-template-columns: 1fr; }
        header { align-items: start; flex-direction: column; }
        .toolbar { justify-content: flex-start; }
        main { width: min(100% - 18px, 1180px); padding-top: 16px; }
      }
      @media (max-width: 480px) {
        .nav { grid-template-columns: 1fr; }
        h1 { font-size: 32px; }
        .meta { grid-template-columns: 1fr; }
        .toolbar, .actions { width: 100%; }
        .toolbar button, .actions button { flex: 1 1 auto; }
      }

      :root { --bg: #0b1118; --panel: #121c27; --panel-2: #172431; --line: #293a4b; --accent: #65dfc0; --muted: #a7bac9; --sidebar-width: 232px; }
      body { font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.5; }
      .sidebar { background: #0e1721; overflow-y: auto; }
      .sidebar-header { flex-wrap: wrap; gap: 8px; }
      .brand { font-weight: 700; letter-spacing: -.035em; }
      .nav { gap: 5px; }
      .nav-section-label { padding: 20px 11px 6px; color: #728a9f; font-size: 10px; font-weight: 800; letter-spacing: .16em; }
      body.nav-collapsed .nav-section-label { font-size: 0; padding: 8px 0 0; }
      .nav button { border: 0; background: transparent; min-height: 46px; border-radius: 8px; }
      .nav button.active { box-shadow: inset 3px 0 var(--accent); background: rgba(101,223,192,.1); }
      main { max-width: 1320px; padding-top: 32px; }
      header { margin-bottom: 24px; }
      h1 { font-size: clamp(30px, 4vw, 42px); font-weight: 650; letter-spacing: -.045em; line-height: 1.15; margin-bottom: 8px; }
      h2 { font-size: 19px; font-weight: 650; letter-spacing: -.025em; }
      h3 { font-size: 15px; margin: 0 0 8px; }
      .eyebrow { font-size: 10px; font-weight: 800; letter-spacing: .18em; color: var(--accent); margin-bottom: 8px; }
      button, input, select, textarea { font: inherit; }
      button { min-height: 42px; border-radius: 8px; font-weight: 550; }
      button.primary { color: #062b23; background: var(--accent); border-color: var(--accent); }
      button.primary:hover { background: #8cebd2; }
      button:disabled { opacity: .45; cursor: not-allowed; }
      button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
      input, select, textarea { min-height: 44px; border-radius: 7px; }
      .panel, .settings { background: var(--panel); border-radius: 12px; padding: 20px; }
      .camera { border-radius: 12px; background: var(--panel); }
      .overview-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 24px; }
      .overview-stat { padding: 16px 20px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
      .overview-stat b { display: block; font-size: 27px; font-weight: 650; line-height: 1.2; font-variant-numeric: tabular-nums; }
      .overview-stat span { color: var(--muted); font-size: 12px; }
      .setup-guide { display: flex; flex-wrap: wrap; gap: 14px 24px; padding: 4px 0 20px; color: var(--muted); font-size: 13px; }
      .setup-guide b, .step-number { display: inline-grid; place-items: center; min-width: 28px; height: 28px; border: 1px solid rgba(101,223,192,.3); background: rgba(101,223,192,.08); border-radius: 8px; color: var(--accent); font: 650 12px/1 system-ui; margin-right: 6px; }
      .camera-settings-card { scroll-margin-top: 100px; border: 1px solid var(--line); border-radius: 10px; margin: 16px 0; overflow: hidden; background: #101a24; }
      .camera-settings-card > summary { display: flex; align-items: center; gap: 12px; padding: 18px; cursor: pointer; list-style: none; }
      .camera-settings-card > summary::after { content: '+'; color: var(--muted); font-size: 22px; margin-left: 6px; }
      .camera-settings-card[open] > summary::after { content: '−'; }
      .camera-avatar { display: grid; place-items: center; width: 38px; height: 38px; flex-shrink: 0; border-radius: 10px; background: #203547; color: var(--accent); font-weight: 700; }
      .camera-summary { min-width: 0; flex: 1; }
      .camera-summary b, .camera-summary small { display: block; overflow-wrap: anywhere; }
      .camera-summary small { color: var(--muted); font-size: 12px; }
      .camera-summary-status { color: var(--muted); font-size: 12px; }
      .camera-config-body { padding: 0 20px 20px; border-top: 1px solid var(--line); }
      .field-section { border: 0; padding: 22px 0; margin: 0; min-width: 0; }
      .field-section + .field-section { border-top: 1px solid var(--line); }
      .field-section legend { float: left; width: 100%; font-size: 17px; font-weight: 650; margin: 0 0 6px; padding: 0; }
      .field-section legend + .field-help { clear: both; }
      .field-help { color: var(--muted); font-size: 12px; margin: 4px 0 16px; }
      .form-grid { grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); gap: 16px; }
      .settings-group { border: 1px solid var(--line); border-radius: 9px; padding: 0 16px; margin: 14px 0; min-width: 0; }
      .settings-group > summary { padding: 16px 0; cursor: pointer; font-weight: 650; }
      .settings-group > summary small { display: block; margin-left: 19px; font-weight: 400; font-size: 12px; color: var(--muted); }
      .settings-group[open] { padding-bottom: 16px; }
      .settings-group[open] > summary { margin-bottom: 12px; border-bottom: 1px solid var(--line); }
      .autoconfig { background: #152431; border: 1px solid var(--line); border-radius: 8px; padding: 16px; margin-bottom: 22px; }
      .autoconfig-head { align-items: center; }
      .autoconfig p { font-size: 12px; }
      .camera-footer { display: flex; gap: 10px; justify-content: space-between; margin-top: 18px; }
      .save-bar { position: sticky; bottom: 12px; z-index: 5; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; background: rgba(18,28,39,.96); box-shadow: 0 8px 30px rgba(0,0,0,.24); backdrop-filter: blur(14px); }
      .save-hint { flex: 1; min-width: 100px; font-size: 12px; color: var(--muted); }
      .save-hint.dirty { color: var(--warn); }
      .save-message { margin: 12px 0 0; font-size: 12px; }
      .settings-jumps { display: flex; flex-wrap: wrap; gap: 8px; padding-bottom: 16px; border-bottom: 1px solid var(--line); }
      .section-heading { display: flex; align-items: flex-start; gap: 10px; margin: 0 0 20px; }
      .section-heading h2 { margin-bottom: 4px; }
      .section-heading p { font-size: 13px; }
      .settings-main { padding-top: 22px; }
      .dashboard-layout { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(300px, .9fr); gap: 20px; align-items: start; }
      .dashboard-builder { display: grid; gap: 16px; }
      .dashboard-builder .section-heading { margin: 2px 0 0; }
      #ha-card-yaml { width: 100%; resize: vertical; min-height: 215px; padding: 16px; background: #09121a; color: #b6f1df; border: 1px solid var(--line); font: 13px/1.75 ui-monospace, SFMono-Regular, Consolas, monospace; }
      .instruction-list { padding-left: 22px; margin: 18px 0; display: grid; gap: 24px; }
      .instruction-list p { font-size: 13px; margin-top: 5px; }
      .tip-box { padding: 16px; margin-top: 24px; border-left: 3px solid var(--accent); background: rgba(101,223,192,.05); border-radius: 0 8px 8px 0; }
      .tip-box p { font-size: 12px; margin-top: 5px; }
      @media(max-width:1000px) { .dashboard-layout { grid-template-columns: minmax(0,1fr); } }
      @media(max-width:820px) { .nav-section-label { grid-column: 1 / -1; padding-top: 10px; } main { padding-top: 24px; } .sidebar-header { margin-bottom: 0; } }
      @media(max-width:480px) { .panel, .settings { padding: 14px; } .camera-config-body { padding-inline: 14px; } .overview-strip { gap: 6px; } .overview-stat { padding: 12px; } .overview-stat b { font-size: 22px; } .camera-summary-status { display: none; } .save-bar { bottom: 6px; } .camera-footer { flex-wrap: wrap; } .dashboard-builder .actions { flex-direction: column; } }
    </style>
  </head>
  <body>
    <div class="app">
      <aside class="sidebar">
        <div class="sidebar-header">
          <button class="menu-toggle" id="menu-toggle" type="button" aria-label="Toggle navigation" aria-expanded="true">
            <span class="menu-toggle-lines" aria-hidden="true"><span></span><span></span><span></span></span>
          </button>
          <div class="brand">Edge of Infinity</div>
          <div class="build-pill" title="Panel build">v__EDGE_PANEL_VERSION__</div>
        </div>
        <nav class="nav" aria-label="Main navigation">
          <div class="nav-section-label">MONITOR</div>
          <button class="active" data-page-target="home"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></svg></span><span class="nav-label">Live view</span></button>
          <button data-page-target="nvr"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/></svg></span><span class="nav-label">Recordings</span></button>
          <div class="nav-section-label">SETUP</div>
          <button data-page-target="camera-settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 8h11l5 4-5 4H4z"/><circle cx="9" cy="12" r="2"/></svg></span><span class="nav-label">Cameras</span></button>
          <button data-page-target="edge-settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a8 8 0 0 0 .1-6"/><path d="M4.5 9a8 8 0 0 0 .1 6"/><path d="M15 4.6a8 8 0 0 0-6 0"/><path d="M9 19.4a8 8 0 0 0 6 0"/></svg></span><span class="nav-label">Settings</span></button>
          <button data-page-target="ha-dashboard"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></svg></span><span class="nav-label">Home Assistant</span></button>
          <div class="nav-section-label">SYSTEM</div>
          <button data-page-target="logs"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3h9l3 3v15H6z"/><path d="M14 3v4h4"/><path d="M9 12h6"/><path d="M9 16h6"/><path d="M9 8h2"/></svg></span><span class="nav-label">Diagnostics</span></button>
        </nav>
      </aside>
      <main>
        <section class="page" data-page="home">
          <header>
            <div>
              <p class="eyebrow">MONITOR</p><h1>Live view</h1>
              <p>Your cameras at a glance. Open a feed or browse its recordings.</p>
            </div>
            <div class="toolbar">
              <button class="primary" id="refresh">Refresh status</button>
              <button data-page-target="camera-settings">Manage cameras</button>
              <button data-page-target="ha-dashboard">Add to Home Assistant</button>
            </div>
          </header>
          <div class="overview-strip" id="camera-overview" aria-label="Camera overview"></div>
          <section class="grid" id="camera-grid"></section>
        </section>

        <section class="page" data-page="nvr" hidden>
          <header>
            <div>
              <p class="eyebrow">MONITOR</p><h1>Recordings</h1>
              <p>Choose a day, find a moment and watch the full recording.</p>
            </div>
            <div class="toolbar">
              <button class="primary" id="refresh-nvr">Refresh NVR</button>
            </div>
          </header>
          <section class="panel">
            <div id="nvr-grid" class="grid"></div>
          </section>
        </section>

        <section class="page" data-page="camera-settings" hidden>
          <header>
            <div>
              <p class="eyebrow">SETUP</p><h1>Cameras</h1>
              <p>Connect a camera, choose its streams, then save your changes.</p>
            </div>
          </header>
        <section class="settings" id="settings">
            <div class="setup-guide"><span><b>1</b> Connect the camera</span><span><b>2</b> Choose live & recording streams</span><span><b>3</b> Save & check the live view</span></div>
            <details class="settings-group preset-group"><summary>Saved camera presets <small>Reuse an existing connection</small></summary><div class="preset-bar">
              <label>Saved camera preset<select id="preset-select"></select></label>
              <label>Slot<select id="preset-slot">
                <option value="0">Camera 1</option>
                <option value="1">Camera 2</option>
              </select></label>
              <button id="apply-preset" type="button">Load preset</button>
            </div>
            </details>
            <form id="config-form"></form>
            <div class="actions save-bar">
              <span class="save-hint" data-unsaved-indicator>All changes saved</span>
              <button id="add-camera" type="button">Add camera</button>
              <button class="primary" id="save-config" type="button">Save cameras</button>
            </div>
            <p class="notice save-message" id="save-state" role="status" aria-live="polite">Camera and app settings are saved together. New connection details are applied automatically.</p>
          </section>
        </section>

        <section class="page" data-page="edge-settings" hidden>
          <header>
            <div>
              <p class="eyebrow">SETUP</p><h1>Settings</h1>
              <p>Recording, storage and network preferences in one place.</p>
            </div>
          </header>
          <section class="settings">
            <div class="settings-jumps"><button type="button" data-settings-jump="recording-settings">Recording</button><button type="button" data-settings-jump="network-settings">Network</button><button type="button" data-settings-jump="storage-settings">Storage</button><button type="button" data-settings-jump="advanced-settings">Advanced</button></div>
            <form id="edge-settings-form"></form>
            <div class="actions save-bar">
              <span class="save-hint" data-unsaved-indicator>All changes saved</span>
              <button class="primary" id="save-edge-settings" type="button">Save Edge settings</button>
            </div>
            <p class="notice save-message" id="edge-save-state" role="status" aria-live="polite">Your camera and app settings are saved together.</p>
          </section>
        </section>

        <section class="page" data-page="logs" hidden>
          <header>
            <div>
              <p class="eyebrow">SYSTEM</p><h1>Diagnostics</h1>
              <p>Connection checks and logs when you need to troubleshoot.</p>
            </div>
            <div class="toolbar">
              <button class="primary" id="refresh-logs">Refresh logs</button>
            </div>
          </header>
          <section class="panel">
            <div id="logs-view" class="log-grid"></div>
          </section>
        </section>

        <section class="page" data-page="ha-dashboard" hidden>
          <header><div><p class="eyebrow">INTEGRATION</p><h1>Home Assistant</h1><p>Put live camera video on your Home Assistant dashboard.</p></div></header>
          <div class="dashboard-layout">
            <section class="panel dashboard-builder">
              <div class="section-heading"><span class="step-number">1</span><div><h2>Choose your camera</h2><p>Choose 1–4 cameras. Edge Live uses shared NVR streams with quality and audio controls.</p></div></div>
              <label>Number of cameras<select id="ha-camera-count"><option value="1">1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option></select></label><label>Camera 1<select id="ha-camera-select"></select></label>
              <label>Card type<select id="ha-card-type"><option value="edge">Edge Live · WebRTC, sound & microphone</option><option value="native">Standard Home Assistant camera</option></select></label>
              <p class="notice" id="ha-card-resource-status" role="status"></p>
              <label>Home Assistant camera entity<input id="ha-camera-entity" placeholder="camera.front_door" autocomplete="off" spellcheck="false"></label>
              <div id="ha-extra-cameras"></div><label>Stream quality<select id="ha-card-quality"><option value="nvr">NVR · same profile as recording</option><option value="main">Main stream</option><option value="sub">Substream · less data</option></select></label><p class="notice">NVR shares the recording feed without reducing its resolution. Different main/sub profiles may open separate upstream camera streams.</p><p class="notice" id="ha-entity-status" role="status"></p>
              <div class="section-heading"><span class="step-number">2</span><div><h2>Copy your card</h2><p>The code contains the camera entity and display options. Passwords and stream addresses stay private.</p></div></div>
              <textarea id="ha-card-yaml" rows="9" readonly spellcheck="false" aria-label="Home Assistant card YAML"></textarea>
              <div class="actions"><button class="primary" id="ha-copy-card" type="button">Copy card YAML</button><button id="ha-refresh-entities" type="button">Refresh entities</button></div>
              <p id="ha-copy-status" role="status" aria-live="polite" class="notice"></p>
            </section>
            <aside class="panel dashboard-instructions">
              <h2>Add it to your dashboard</h2>
              <ol class="instruction-list"><li><b>Enable the Edge integration</b><p>After updating the add-on, restart Home Assistant Core once. Go to Settings → Devices & services → Add integration → Edge of Infinity. Use <code>local</code> as the connection.</p></li><li><b>Select the camera entity</b><p>The Edge integration registers our custom card automatically. Registered camera entities appear here automatically. If needed, copy the exact <code>camera.…</code> ID from the integration's entity list.</p></li><li><b>Paste the card</b><p>Open your dashboard → Edit dashboard → Add card → Manual. Paste the YAML and save.</p></li></ol>
              <div class="tip-box"><b>Live on your dashboard</b><p>__EDGE_HA_LIVE_HELP__</p></div>
            </aside>
          </div>
        </section>

      </main>
    </div>
    <script>
      const grid = document.getElementById('camera-grid');
      const nvrGrid = document.getElementById('nvr-grid');
      const form = document.getElementById('config-form');
      const edgeForm = document.getElementById('edge-settings-form');
      const logsView = document.getElementById('logs-view');
      const presetSelect = document.getElementById('preset-select');
      const presetSlot = document.getElementById('preset-slot');
      const saveState = document.getElementById('save-state');
      const edgeSaveState = document.getElementById('edge-save-state');
      const menuToggle = document.getElementById('menu-toggle');
      const EDGE_PANEL_VERSION = '__EDGE_PANEL_VERSION__';
      const EDGE_UI_BUILD = '__EDGE_UI_BUILD__';
      const EDGE_NATIVE_RTC = __EDGE_NATIVE_RTC__;
      const CAMERA_VENDORS = __EDGE_CAMERA_VENDORS__;
      const CAMERA_TEMPLATES = __EDGE_CAMERA_TEMPLATES__;
      let config = { cameras: [] };
      let live = {};
      let presets = [];
      let liveTimer = null;
      let nvrTimer = null;
      let cameraAuto = {};
      let recordingStatus = {};
      let selectedRecording = {};
      let selectedRecordingSeek = {};
      let selectedRecordingTimeline = {};
      let selectedRecordingDay = {};
      let recordingStreamStartOffset = {};
      let recordingStreamNonce = {};
      let recordingAutoplayAfterRender = {};
      let recordingContinueTimers = {};
      let recordingSwipeStart = {};
      let lastNvrRenderSignature = '';
      let nvrInteractionUntil = 0;
      let lastRecordingStatusLogAt = 0;
      let lastNvrRenderSkipLogAt = 0;
      let configDirty = false;
      let lastFormDraft = null;
      let lastFormDraftAt = 0;
      let panelLogs = null;
      let activePage = 'home';
      let nvrLoading = false;
      let softFullscreenTarget = null;
      let thumbnailHydrationTimer = 0;
      let thumbnailObserver = null;
      const THUMB_PLACEHOLDER = 'data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22480%22%20height%3D%22270%22%20viewBox%3D%220%200%20480%20270%22%3E%3Crect%20width%3D%22480%22%20height%3D%22270%22%20fill%3D%22%2305080a%22%2F%3E%3Ctext%20x%3D%22240%22%20y%3D%22136%22%20fill%3D%22%238ea4a1%22%20text-anchor%3D%22middle%22%3EPreview%20loading%3C%2Ftext%3E%3C%2Fsvg%3E';
      const NVR_STATUS_REFRESH_MS = 15000;
      const NVR_INTERACTION_PROTECT_MS = 30000;
      const NVR_TIMER_LOG_THROTTLE_MS = 30000;
      const liveFrameTimers = new Map();

      const panelBase = window.location.pathname.endsWith('/')
        ? window.location.pathname
        : `${window.location.pathname}/`;

      function panelPath(path) {
        return `${panelBase}${String(path).replace(/^\/+/, '')}`;
      }

      function directPanelPath(path) {
        const clean = String(path).replace(/^\/+/, '');
        const publicUrl = String(config?.server?.public_url || '').trim().replace(/\/+$/, '');
        if (publicUrl) return `${publicUrl}/${clean}`;
        if (window.location.hostname) {
          return `${window.location.protocol}//${window.location.hostname}:8088/${clean}`;
        }
        return panelPath(clean);
      }

      function directMediaMtxPath(path) {
        const clean = String(path).replace(/^\/+/, '').replace(/\/+$/, '');
        const publicUrl = String(config?.live?.mobile_webrtc_public_url || '').trim().replace(/\/+$/, '');
        if (publicUrl) return `${publicUrl}/${clean}`;
        if (window.location.hostname) {
          return `http://${window.location.hostname}:8889/${clean}`;
        }
        return `http://127.0.0.1:8889/${clean}`;
      }

      function mediaMtxPlayerUrl(url) {
        const separator = String(url).includes('?') ? '&' : '?';
        return `${url}${separator}controls=true&muted=false&autoplay=true&playsInline=true&disablepictureinpicture=true`;
      }

      function fullscreenIcon() {
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M8 3H3v5"/><path d="M16 3h5v5"/><path d="M21 16v5h-5"/><path d="M8 21H3v-5"/><path d="M3 3l7 7"/><path d="M21 3l-7 7"/><path d="M21 21l-7-7"/><path d="M3 21l7-7"/></svg>';
      }

      function exitSoftFullscreen(context = 'unknown') {
        if (!softFullscreenTarget) return;
        softFullscreenTarget.classList.remove('edge-soft-fullscreen');
        document.body.classList.remove('soft-fullscreen-active');
        debugEvent('ui_fullscreen_close', { context, mode: 'soft' });
        softFullscreenTarget = null;
      }

      function enterSoftFullscreen(target, context) {
        if (!target) return;
        exitSoftFullscreen(context);
        softFullscreenTarget = target;
        target.classList.add('edge-soft-fullscreen');
        document.body.classList.add('soft-fullscreen-active');
        debugEvent('ui_fullscreen_open', { context, mode: 'soft_fallback' });
      }

      function requestEdgeFullscreen(target, context) {
        if (!target) return;
        const media = target.querySelector('video, iframe') || target;
        if (softFullscreenTarget === target) {
          exitSoftFullscreen(context);
          return;
        }
        const activeFullscreen = document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
        if (activeFullscreen) {
          const exit = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
          if (exit) {
            Promise.resolve(exit.call(document))
              .then(() => debugEvent('ui_fullscreen_close', { context, mode: 'standard' }))
              .catch((error) => debugEvent('ui_fullscreen_error', { context, message: error.message, phase: 'exit' }));
            return;
          }
        }
        try {
          if (media.tagName === 'VIDEO' && typeof media.webkitEnterFullscreen === 'function') {
            media.webkitEnterFullscreen();
            debugEvent('ui_fullscreen_open', { context, mode: 'webkit_video' });
            return;
          }
          const mediaRequest = media.requestFullscreen
            || media.webkitRequestFullscreen
            || media.msRequestFullscreen;
          const targetRequest = target.requestFullscreen
            || target.webkitRequestFullscreen
            || target.msRequestFullscreen;
          const request = mediaRequest || targetRequest;
          const requestTarget = mediaRequest ? media : target;
          if (!request) {
            debugEvent('ui_fullscreen_unavailable', { context, fallback: 'soft' });
            enterSoftFullscreen(target, context);
            return;
          }
          Promise.resolve(request.call(requestTarget))
            .then(() => debugEvent('ui_fullscreen_open', { context, mode: 'standard' }))
            .catch((error) => {
              debugEvent('ui_fullscreen_error', { context, message: error.message, fallback: 'soft' });
              enterSoftFullscreen(target, context);
            });
        } catch (error) {
          debugEvent('ui_fullscreen_error', { context, message: error.message, fallback: 'soft' });
          enterSoftFullscreen(target, context);
        }
      }

      function parseUrl(value) {
        try {
          return new URL(value, window.location.href);
        } catch (_) {
          return null;
        }
      }

      function isLanHost(hostname) {
        const host = String(hostname || '').toLowerCase();
        return host === 'localhost'
          || host === '127.0.0.1'
          || host === '::1'
          || host.endsWith('.local')
          || host.startsWith('192.168.')
          || host.startsWith('10.')
          || /^172\.(1[6-9]|2\d|3[0-1])\./.test(host);
      }

      function isNabuCasaHost(hostname) {
        return String(hostname || '').toLowerCase().endsWith('.ui.nabu.casa');
      }

      function liveConnectionPlan(path) {
        const publicUrl = String(config?.live?.mobile_webrtc_public_url || '').trim().replace(/\/+$/, '');
        const mediaUrl = directMediaMtxPath(path);
        const parsedMediaUrl = parseUrl(mediaUrl);
        const pageHost = window.location.hostname || '';
        const mediaHost = parsedMediaUrl?.hostname || '';
        const pageIsLan = isLanHost(pageHost);
        const mediaIsLan = isLanHost(mediaHost);
        const pageIsNabuCasa = isNabuCasaHost(pageHost);
        const mediaIsNabuCasa = isNabuCasaHost(mediaHost);
        const pageIsHttps = window.location.protocol === 'https:';
        const mediaIsHttp = parsedMediaUrl?.protocol === 'http:';
        const publicUrlConfigured = Boolean(publicUrl);
        const remotePage = !pageIsLan;
        const iceTransport = String(config?.live?.mobile_webrtc_ice_transport || 'auto');
        const remoteAccessMode = String(config?.live?.remote_access_mode || 'local_only');
        const diagnostics = {
          page_protocol: window.location.protocol,
          page_host: pageHost,
          page_is_lan: pageIsLan,
          page_is_nabu_casa: pageIsNabuCasa,
          media_url: mediaUrl,
          media_protocol: parsedMediaUrl?.protocol || 'invalid',
          media_host: mediaHost || 'invalid',
          media_is_lan: mediaIsLan,
          media_is_nabu_casa: mediaIsNabuCasa,
          remote_access_mode: remoteAccessMode,
          public_url_configured: publicUrlConfigured,
          mobile_webrtc_public_url: publicUrl || '',
          ice_transport: iceTransport,
          user_agent: navigator.userAgent,
          viewport: `${window.innerWidth}x${window.innerHeight}`,
        };
        if (publicUrlConfigured && !/^https?:\/\//i.test(publicUrl)) {
          return {
            url: mediaUrl,
            canEmbed: false,
            reason: 'public_webrtc_url_missing_scheme',
            message: 'WebRTC public URL must start with http:// or https://.',
            diagnostics,
          };
        }
        if (pageIsNabuCasa && !publicUrlConfigured) {
          return {
            url: '(no public MediaMTX URL configured)',
            canEmbed: false,
            reason: 'nabu_casa_needs_stream_relay',
            message: 'Nabu Casa opens the Home Assistant panel, but it does not expose MediaMTX WebRTC ports. Set WebRTC public URL to a VPS/reverse-proxy/TURN reachable endpoint.',
            diagnostics,
          };
        }
        if (remotePage && !publicUrlConfigured) {
          return {
            url: mediaUrl,
            canEmbed: false,
            reason: 'public_webrtc_url_missing',
            message: 'Remote live needs Edge Settings -> WebRTC public URL. A LAN fallback cannot work on LTE or another Wi-Fi.',
            diagnostics,
          };
        }
        if (publicUrlConfigured && mediaIsNabuCasa) {
          return {
            url: mediaUrl,
            canEmbed: false,
            reason: 'nabu_casa_public_url_not_supported',
            message: 'Do not use ui.nabu.casa as WebRTC public URL. It is Home Assistant remote UI, not a MediaMTX relay endpoint.',
            diagnostics,
          };
        }
        if (!parsedMediaUrl) {
          return {
            url: mediaUrl,
            canEmbed: false,
            reason: 'invalid_media_url',
            message: 'WebRTC URL is invalid. Check Edge Settings -> WebRTC public URL.',
            diagnostics,
          };
        }
        if (pageIsHttps && mediaIsHttp) {
          return {
            url: mediaUrl,
            canEmbed: false,
            reason: 'mixed_content_blocked',
            message: 'This Home Assistant page is HTTPS, but WebRTC URL is HTTP. Use HTTPS reverse proxy for MediaMTX or open the panel over HTTP on LAN.',
            diagnostics,
          };
        }
        if (remotePage && publicUrlConfigured && mediaIsLan) {
          return {
            url: mediaUrl,
            canEmbed: false,
            reason: 'public_webrtc_url_is_lan',
            message: 'WebRTC public URL points to a private LAN address. LTE cannot reach 192.168/10/172.16 addresses without VPN, TURN, or a public reverse proxy.',
            diagnostics,
          };
        }
        return {
          url: mediaUrl,
          canEmbed: true,
          reason: 'ok',
          message: 'WebRTC URL can be embedded from this browser context.',
          diagnostics,
        };
      }

      function remoteLiveNotice() {
        const publicUrl = String(config?.live?.mobile_webrtc_public_url || '').trim();
        const plan = liveConnectionPlan('diagnostic');
        if (plan.canEmbed || isLanHost(window.location.hostname)) return '';
        return `<p class="notice warning">${escapeHtml(plan.message)} MediaMTX handshake must be reachable on TCP 8889 and ICE TCP/UDP 8189, or through TURN/VPS. Current public URL: ${escapeHtml(publicUrl || 'not set')}.</p>`;
      }

      function liveBlockedPreview(plan) {
        return `
          <div class="live-blocked" data-live-blocked="${escapeHtml(plan.reason)}">
            <div>
              <b>Remote live blocked</b>
              <div>${escapeHtml(plan.message)}</div>
              <code>${escapeHtml(plan.url)}</code>
            </div>
          </div>
        `;
      }

      function debugEvent(event, payload = {}) {
        const body = JSON.stringify({
          event,
          panel_version: EDGE_PANEL_VERSION,
          ui_build: EDGE_UI_BUILD,
          timestamp: new Date().toISOString(),
          page: document.querySelector('[data-page]:not([hidden])')?.dataset?.page || 'unknown',
          location: window.location.pathname,
          client: {
            protocol: window.location.protocol,
            host: window.location.hostname,
            href: window.location.href,
            user_agent: navigator.userAgent,
            viewport: `${window.innerWidth}x${window.innerHeight}`,
          },
          payload
        });
        const query = new URLSearchParams({
          event,
          panel_version: EDGE_PANEL_VERSION,
          ui_build: EDGE_UI_BUILD,
        });
        fetch(panelPath(`api/debug?${query.toString()}`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body
        }).catch(() => {});
      }

      function setNavCollapsed(collapsed, persist = true) {
        document.body.classList.toggle('nav-collapsed', collapsed);
        menuToggle.setAttribute('aria-expanded', String(!collapsed));
        debugEvent('ui_nav_collapse', { collapsed });
        if (persist) {
          try {
            window.localStorage.setItem('edge-nav-collapsed', collapsed ? 'true' : 'false');
          } catch (_) {}
        }
      }

      function restoreNavState() {
        let saved = null;
        try {
          saved = window.localStorage.getItem('edge-nav-collapsed');
        } catch (_) {}
        const shouldCollapse = saved === null
          ? window.matchMedia('(max-width: 820px)').matches
          : saved === 'true';
        setNavCollapsed(shouldCollapse, false);
      }

      function text(value, fallback = 'unknown') {
        return value === undefined || value === null || value === '' ? fallback : value;
      }

      function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (char) => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[char]));
      }

      function statusLabel(status) {
        if (status === 'online') return 'online';
        if (status === 'lost_connection') return 'lost connection';
        return 'offline';
      }

      function statusClass(status) {
        if (status === 'online') return 'badge-online';
        if (status === 'lost_connection') return 'badge-lost-connection';
        return 'badge-offline';
      }

      function bitrateText(value) {
        const numeric = Number(value || 0);
        if (!numeric) return 'unknown';
        if (numeric >= 1000000) return `${(numeric / 1000000).toFixed(2)} Mbps`;
        return `${Math.round(numeric / 1000)} kbps`;
      }

      function formatBytes(value) {
        const numeric = Number(value || 0);
        if (!numeric) return '0 B';
        if (numeric >= 1073741824) return `${(numeric / 1073741824).toFixed(2)} GB`;
        if (numeric >= 1048576) return `${(numeric / 1048576).toFixed(1)} MB`;
        if (numeric >= 1024) return `${Math.round(numeric / 1024)} KB`;
        return `${numeric} B`;
      }

      function formatDate(value) {
        if (!value) return 'unknown';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString();
      }

      function formatTimestampSeconds(value) {
        if (!value) return 'unknown time';
        const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString(undefined, {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        });
      }

      function formatDuration(value) {
        const total = Math.max(0, Math.floor(Number(value || 0)));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const seconds = total % 60;
        const mm = String(minutes).padStart(hours ? 2 : 1, '0');
        const ss = String(seconds).padStart(2, '0');
        return hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
      }

      function sortedRecordingFiles(files) {
        return [...(Array.isArray(files) ? files : [])]
          .filter((file) => file && file.url)
          .sort((a, b) => {
            const at = Number(a.start_ts || 0);
            const bt = Number(b.start_ts || 0);
            if (at !== bt) return at - bt;
            return String(a.name || '').localeCompare(String(b.name || ''));
          });
      }

      function recordingTimeline(files, status = {}) {
        let playbackOffset = 0;
        const timelineMeta = status?.timeline || {};
        const dayStart = Number(timelineMeta.day_start_ts || 0);
        const requestedTotal = Number(timelineMeta.day_total_seconds || timelineMeta.total_seconds || 0);
        const entries = sortedRecordingFiles(files).map((file) => {
          const duration = Math.max(1, Math.floor(Number(file.duration_seconds || 10)));
          const rawTimelineOffset = Number(file.timeline_offset);
          const rawPlaybackOffset = Number(file.playback_offset);
          const offset = Number.isFinite(rawTimelineOffset)
            ? Math.max(0, Math.floor(rawTimelineOffset))
            : dayStart && Number(file.start_ts || 0)
              ? Math.max(0, Math.min(86399, Math.floor(Number(file.start_ts || 0) - dayStart)))
              : playbackOffset;
          const entry = {
            file,
            offset,
            duration,
            playbackOffset: Number.isFinite(rawPlaybackOffset) ? Math.max(0, Math.floor(rawPlaybackOffset)) : playbackOffset,
          };
          playbackOffset += duration;
          return entry;
        });
        const maxEnd = entries.reduce((max, entry) => Math.max(max, entry.offset + entry.duration), 0);
        const total = Math.max(0, Math.floor(requestedTotal || maxEnd || playbackOffset));
        return { entries, total, playbackTotal: playbackOffset, dayStart };
      }

      function recordingFilmstripFiles(files, selectedUrl = '', maxItems = 72) {
        const sorted = sortedRecordingFiles(files);
        const limit = Math.max(1, Math.floor(Number(maxItems || 72)));
        if (sorted.length <= limit) return sorted;
        const selectedIndex = sorted.findIndex((file) => file.url === selectedUrl);
        const used = new Set();
        const addIndex = (index) => {
          const safeIndex = Math.max(0, Math.min(sorted.length - 1, Math.floor(Number(index || 0))));
          used.add(safeIndex);
        };
        for (let slot = 0; slot < limit; slot += 1) {
          addIndex(Math.round((slot * (sorted.length - 1)) / Math.max(1, limit - 1)));
        }
        if (selectedIndex >= 0) addIndex(selectedIndex);
        return Array.from(used)
          .sort((a, b) => a - b)
          .map((index) => sorted[index]);
      }

      function recordingTargetInTimeline(timeline, offset) {
        const entries = timeline.entries || [];
        if (!entries.length) return null;
        const timelineMax = Math.max(0, Number(timeline.total || 0) - 1);
        const input = Math.max(0, Math.min(Math.floor(Number(offset || 0)), timelineMax));
        let target = entries.find((entry) => input >= entry.offset && input < entry.offset + entry.duration);
        if (!target) target = entries.find((entry) => input < entry.offset) || entries[entries.length - 1];
        const seek = Math.max(0, Math.min(input - target.offset, Math.max(0, target.duration - 1)));
        const requested = target.offset + seek;
        const playbackSeek = Math.max(0, Number(target.playbackOffset || 0) + seek);
        return { timeline, target, requested, input, seek, playbackSeek };
      }

      function recordingOffsetForSelection(files, selectedUrl, seekSeconds = 0, status = {}) {
        const timeline = recordingTimeline(files, status);
        const entry = timeline.entries.find((item) => item.file.url === selectedUrl) || timeline.entries[timeline.entries.length - 1];
        if (!entry) return { timeline, value: 0, seek: 0 };
        const seek = Math.max(0, Math.min(Math.floor(Number(seekSeconds || 0)), Math.max(0, entry.duration - 1)));
        return { timeline, value: entry.offset + seek, seek, playbackValue: Number(entry.playbackOffset || 0) + seek };
      }

      function recordingWallClockAtOffset(files, offset, status = {}) {
        const target = recordingTargetInTimeline(recordingTimeline(files, status), offset);
        if (!target) return '';
        const baseTs = Number(target.target.file.start_ts || 0);
        return baseTs ? baseTs + target.seek : '';
      }

      function recordingTimelineLabel(index, value, total) {
        const status = recordingStatus[index] || {};
        const files = Array.isArray(status.files) ? status.files : [];
        const wallClock = recordingWallClockAtOffset(files, value, status);
        const target = recordingTargetForOffset(index, value);
        const recorded = Number(status.timeline?.recorded_seconds || status.timeline?.playback_total_seconds || 0);
        const gapNote = target && Number(target.input || 0) !== Number(target.requested || 0) ? ' -> nearest clip' : '';
        return `${formatTimestampSeconds(wallClock)}${gapNote} | day ${formatDuration(value)} / ${formatDuration(total)} | recorded ${formatDuration(recorded)}`;
      }

      function recordingDayLabel(day) {
        if (!day) return 'No day';
        const [year, month, date] = String(day).split('-').map((value) => Number(value));
        if (!year || !month || !date) return day;
        return new Date(year, month - 1, date).toLocaleDateString(undefined, {
          weekday: 'short',
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
        });
      }

      function selectRecordingDay(index, day, reason = 'select') {
        if (!day || selectedRecordingDay[index] === day) return;
        selectedRecordingDay[index] = day;
        delete selectedRecording[index];
        delete selectedRecordingSeek[index];
        delete selectedRecordingTimeline[index];
        delete recordingStreamStartOffset[index];
        delete recordingStreamNonce[index];
        recordingAutoplayAfterRender[index] = true;
        debugEvent('ui_recording_day_select', { index, day, reason });
        loadRecordingStatus({ forceRender: true, reason: `day_${reason}` }).catch((error) => {
          debugEvent('ui_recording_day_select_error', { index, day, reason, message: error.message });
        });
      }

      function moveRecordingDay(index, direction, reason = 'button') {
        const days = Array.isArray(recordingStatus[index]?.days) ? recordingStatus[index].days : [];
        if (days.length < 2) return;
        const currentDay = selectedRecordingDay[index] || recordingStatus[index]?.selected_day || days[0]?.day || '';
        const currentIndex = Math.max(0, days.findIndex((item) => item.day === currentDay));
        const nextIndex = direction === 'older'
          ? Math.min(days.length - 1, currentIndex + 1)
          : Math.max(0, currentIndex - 1);
        const nextDay = days[nextIndex]?.day;
        if (nextDay && nextDay !== currentDay) selectRecordingDay(index, nextDay, reason);
      }

      function recordingStreamUrl(status, index, playbackOffset, timelineOffset = 0) {
        const key = status?.key;
        if (!key) return '';
        if (!recordingStreamNonce[index]) recordingStreamNonce[index] = Date.now();
        const start = Math.max(0, Math.floor(Number(playbackOffset || 0)));
        const timelineStart = Math.max(0, Math.floor(Number(timelineOffset || 0)));
        const day = status?.selected_day ? `&day=${encodeURIComponent(status.selected_day)}` : '';
        return panelPath(`recordings-stream/${encodeURIComponent(key)}.mp4?start=${start}&timeline_start=${timelineStart}&mode=playback${day}&v=${recordingStreamNonce[index]}`);
      }

      function isMobileNvrPlayback() {
        return window.matchMedia('(max-width: 820px)').matches
          || /Android|iPhone|iPad|iPod|Mobile|Home Assistant/i.test(navigator.userAgent || '');
      }

      function recordingPlaybackModeForClient(status = {}, timeline = null) {
        const cache = status?.playback_cache || {};
        if (cache.ready && cache.current && cache.url && Number(cache.total_seconds || 0) > 0) return 'server_cache_mp4';
        // A virtual day player provides one duration while loading only the needed MP4.
        // Native fragmented-MP4 duration can initially be only a few seconds on mobile.
        return 'server_file_sequence';
      }

      function isRecordedDayStreamMode(playbackMode) {
        return playbackMode === 'recorded_day_stream' || playbackMode === 'continuous_stream';
      }

      function recordingCacheCoversTarget(status = {}, target = null) {
        const cache = status?.playback_cache || {};
        if (!target || !cache.ready || !cache.url || cache.stale_active_day) return false;
        const cacheTotal = Number(cache.total_seconds || 0);
        return cacheTotal > 0 && Number(target.playbackSeek || 0) <= Math.max(0, cacheTotal - 1);
      }

      function mediaUrlWithTimeFragment(url, seekSeconds) {
        const seek = Math.max(0, Number(seekSeconds || 0));
        if (!url || !seek) return url;
        const clean = String(url).split('#', 1)[0];
        return `${clean}#t=${seek.toFixed(3)}`;
      }

      function recordingDefaultTarget(files, status = {}) {
        const timeline = recordingTimeline(files, status);
        const first = timeline.entries[0];
        if (!first) return null;
        return { timeline, target: first, requested: first.offset, input: first.offset, seek: 0, playbackSeek: Number(first.playbackOffset || 0) };
      }

      const recordingThumbnailQueue = [];
      let recordingThumbnailActive = 0;
      function thumbnailContainer(image) { return image.closest('.recording-thumb-wrap, .recording-day-thumb'); }
      function markRecordingThumbnailFailed(image) {
        image.onload = null;
        image.onerror = null;
        thumbnailContainer(image)?.classList.add('thumb-error');
        image.removeAttribute('data-recording-thumb-src');
        image.src = THUMB_PLACEHOLDER;
      }
      function pumpRecordingThumbnails() {
        while (recordingThumbnailActive < 2 && recordingThumbnailQueue.length) {
          const image = recordingThumbnailQueue.shift();
          if (!image.isConnected) continue;
          const source = image.dataset.recordingThumbSrc;
          if (!source) continue;
          recordingThumbnailActive += 1;
          let finished = false;
          const finish = (ok) => {
            if (finished) return;
            finished = true;
            image.onload = null; image.onerror = null;
            delete image.dataset.recordingThumbQueued;
            if (ok) { thumbnailContainer(image)?.classList.remove('thumb-error'); thumbnailContainer(image)?.classList.add('thumb-loaded'); image.removeAttribute('data-recording-thumb-src'); }
            else markRecordingThumbnailFailed(image);
            recordingThumbnailActive -= 1;
            pumpRecordingThumbnails();
          };
          const expectedUrl = new URL(source, window.location.href).href;
          image.onload = () => { if (image.currentSrc === expectedUrl && image.naturalWidth > 1) finish(true); };
          image.onerror = () => { if (image.currentSrc === expectedUrl) finish(false); };
          image.loading = 'eager';
          image.src = source;
        }
      }
      function loadRecordingThumbnail(image) {
        if (!image?.dataset?.recordingThumbSrc || image.dataset.recordingThumbQueued) return;
        image.dataset.recordingThumbQueued = 'true';
        recordingThumbnailQueue.push(image);
        pumpRecordingThumbnails();
      }

      function hydrateRecordingThumbnails() {
        if (thumbnailObserver) {
          thumbnailObserver.disconnect();
          thumbnailObserver = null;
        }
        const images = Array.from(nvrGrid.querySelectorAll('img[data-recording-thumb-src]'));
        if (!images.length) return;
        const immediate = images.slice(0, isMobileNvrPlayback() ? 4 : 8);
        immediate.forEach(loadRecordingThumbnail);
        const deferred = images.slice(immediate.length);
        if (!deferred.length) return;
        if ('IntersectionObserver' in window) {
          thumbnailObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
              if (!entry.isIntersecting) return;
              loadRecordingThumbnail(entry.target);
              thumbnailObserver.unobserve(entry.target);
            });
          }, { root: null, rootMargin: '220px 0px', threshold: 0.01 });
          deferred.forEach((image) => thumbnailObserver.observe(image));
          return;
        }
        deferred.slice(0, 8).forEach(loadRecordingThumbnail);
      }

      function scheduleRecordingThumbnailHydration() {
        if (thumbnailHydrationTimer) window.clearTimeout(thumbnailHydrationTimer);
        thumbnailHydrationTimer = window.setTimeout(hydrateRecordingThumbnails, isMobileNvrPlayback() ? 120 : 80);
      }

      function offsetForRecordingUrl(index, url) {
        const status = recordingStatus[index] || {};
        const timeline = recordingTimeline(status.files || [], status);
        const entry = timeline.entries.find((item) => item.file.url === url);
        return entry ? entry.offset : 0;
      }

      function updateRecordingTileSelection(index, selectedUrl) {
        nvrGrid.querySelectorAll(`[data-record-index="${index}"][data-play-recording]`).forEach((button) => {
          button.classList.toggle('active', button.dataset.playRecording === selectedUrl);
        });
      }

      function holdRecordingFrame(video) {
        const frame = video.closest('.recording-player-wrap')?.querySelector('[data-recording-transition]');
        if (!frame || video.readyState < 2 || !video.videoWidth) return;
        try {
          frame.width = video.videoWidth; frame.height = video.videoHeight;
          frame.style.height = `${video.clientHeight}px`;
          frame.getContext('2d').drawImage(video, 0, 0);
          frame.hidden = false;
        } catch (_) { frame.hidden = true; }
      }
      function releaseRecordingFrame(video) {
        video.removeAttribute('poster');
        const frame = video.closest('.recording-player-wrap')?.querySelector('[data-recording-transition]');
        if (!frame) return;
        const release = () => {
          const expectedSeek = Number(video.dataset.recordingSeek || 0);
          if (!video.seeking && video.currentTime + 0.1 >= expectedSeek) frame.hidden = true;
        };
        if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(release);
        else requestAnimationFrame(() => requestAnimationFrame(release));
      }

      function switchRecordingVideoToFile(index, target, autoplay = false, reason = 'server_file_seek') {
        if (!target?.target?.file?.url) return false;
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        if (!video) return false;
        const fileUrl = target.target.file.url;
        const cleanCurrent = String(video.currentSrc || video.getAttribute('src') || '').split('#', 1)[0];
        const cleanNext = new URL(panelPath(fileUrl), window.location.href).href;
        const nextPath = panelPath(fileUrl);
        const nextSrc = mediaUrlWithTimeFragment(nextPath, target.seek);
        selectedRecording[index] = fileUrl;
        selectedRecordingSeek[index] = target.seek;
        selectedRecordingTimeline[index] = target.requested;
        recordingStreamStartOffset[index] = target.target.offset;
        video.dataset.recordingSeek = String(target.seek);
        video.dataset.recordingStreamStart = String(target.target.offset);
        video.dataset.recordingPlaybackStart = String(target.target.playbackOffset || 0);
        video.dataset.recordingPlaybackMode = 'server_file_sequence';
        video.dataset.recordingFileUrl = fileUrl;
        video.defaultPlaybackRate = video.playbackRate;
        updateRecordingTimelineUi(index, target.requested, target.timeline.total);
        updateRecordingTileSelection(index, fileUrl);
        if (video.dataset.recordingLoadedFile === fileUrl && video.readyState >= 1) {
          try {
            video.currentTime = target.seek;
          } catch (error) {
            debugEvent('ui_recording_server_file_seek_error', { index, reason, message: error.message });
          }
          if (autoplay) video.play().catch((error) => debugEvent('ui_recording_server_file_autoplay_error', { index, reason, message: error.message }));
          debugEvent('ui_recording_server_file_fast_seek', { index, reason, recording: target.target.file.name, timeline_second: target.requested, playback_second: target.playbackSeek, segment_second: target.seek });
          return true;
        }
        holdRecordingFrame(video);
        video.removeAttribute("poster");
        video.dataset.recordingLoadedFile = fileUrl;
        video.src = nextSrc;
        video.dataset.recordingSrc = nextSrc;
        video.load();
        if (autoplay) {
          video.play().catch((error) => debugEvent('ui_recording_server_file_autoplay_error', { index, reason, message: error.message }));
        }
        debugEvent('ui_recording_server_file_switch', { index, reason, recording: target.target.file.name, timeline_second: target.requested, playback_second: target.playbackSeek, segment_second: target.seek });
        return true;
      }

      function seekRecordingCache(index, target, autoplay = false, reason = 'server_cache_seek') {
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        if (!video || !target) return false;
        selectedRecording[index] = target.target.file.url;
        selectedRecordingSeek[index] = target.seek;
        selectedRecordingTimeline[index] = target.requested;
        recordingStreamStartOffset[index] = 0;
        video.dataset.recordingSeek = String(target.playbackSeek);
        video.dataset.recordingStreamStart = '0';
        video.dataset.recordingPlaybackStart = '0';
        video.dataset.recordingPlaybackMode = 'server_cache_mp4';
        updateRecordingTimelineUi(index, target.requested, target.timeline.total);
        updateRecordingTileSelection(index, target.target.file.url);
        setCurrentRecordingTime(index, target.requested);
        if (autoplay) {
          video.play().catch((error) => debugEvent('ui_recording_cache_autoplay_error', { index, reason, message: error.message }));
        }
        debugEvent('ui_recording_cache_seek', {
          index,
          reason,
          recording: target.target.file.name,
          timeline_second: target.requested,
          playback_second: target.playbackSeek,
          segment_second: target.seek,
        });
        return true;
      }

      function recordingStatusSignature() {
        return Object.values(recordingStatus)
          .sort((a, b) => Number(a.index || 0) - Number(b.index || 0))
          .map((item) => {
            const files = Array.isArray(item.files) ? item.files : [];
            const fileSignature = files.map((file) => `${file.url}:${file.size_bytes}:${file.start_ts}`).join(',');
            const cache = item.playback_cache || {};
            const daySignature = `${item.selected_day || ''}:${(item.days || []).map((day) => `${day.day}:${day.file_count}`).join(',')}`;
            const cacheSignature = `${cache.cache_name || ''}:${cache.ready}:${cache.current}:${cache.building}:${cache.cache_id}:${cache.source_hash}`;
            return `${item.index}:${item.recording_status}:${item.recording}:${item.segments}:${daySignature}:${cacheSignature}:${fileSignature}`;
          })
          .join('|');
      }

      function isNvrPlaybackBusy() {
        return Array.from(nvrGrid.querySelectorAll('video[data-recording-player]')).some((video) => {
          return !video.paused && !video.ended && video.readyState >= 1;
        });
      }

      function markNvrInteraction(reason = 'interaction', payload = {}) {
        nvrInteractionUntil = Math.max(nvrInteractionUntil, Date.now() + NVR_INTERACTION_PROTECT_MS);
        if (reason !== 'timeupdate') {
          debugEvent('ui_nvr_interaction_protected', {
            reason,
            protected_until_ms: nvrInteractionUntil,
            ...payload,
          });
        }
      }

      function isNvrPlaybackProtected() {
        if (activePage !== 'nvr') return false;
        if (Date.now() < nvrInteractionUntil) return true;
        return Array.from(nvrGrid.querySelectorAll('video[data-recording-player]')).some((video) => {
          return video.readyState >= 1 && !video.ended;
        });
      }

      function updateRecordingTimelineUi(index, value, total) {
        const safeValue = Math.max(0, Math.floor(Number(value || 0)));
        const safeTotal = Math.max(0, Math.floor(Number(total || 0)));
        selectedRecordingTimeline[index] = Math.min(safeValue, Math.max(0, safeTotal - 1));
        const slider = nvrGrid.querySelector(`[data-recording-day-seek="${index}"]`);
        if (slider && document.activeElement !== slider) slider.value = String(selectedRecordingTimeline[index]);
        const label = nvrGrid.querySelector(`[data-recording-timeline-label="${index}"]`);
        if (label) {
          label.textContent = recordingTimelineLabel(index, safeValue, safeTotal);
        }
      }

      function recordingTargetForOffset(index, offset) {
        const status = recordingStatus[index] || {};
        const files = Array.isArray(status.files) ? status.files : [];
        const timeline = recordingTimeline(files, status);
        const { entries } = timeline;
        if (!entries.length) return null;
        return recordingTargetInTimeline(timeline, offset);
      }

      function recordingTargetForPlaybackOffset(index, playbackOffset) {
        const status = recordingStatus[index] || {};
        const files = Array.isArray(status.files) ? status.files : [];
        const timeline = recordingTimeline(files, status);
        const entries = timeline.entries || [];
        if (!entries.length) return null;
        const playbackMax = Math.max(0, Number(timeline.playbackTotal || 0) - 1);
        const input = Math.max(0, Math.min(Math.floor(Number(playbackOffset || 0)), playbackMax));
        const target = entries.find((entry) => input >= entry.playbackOffset && input < entry.playbackOffset + entry.duration) || entries[entries.length - 1];
        const seek = Math.max(0, Math.min(input - target.playbackOffset, Math.max(0, target.duration - 1)));
        const requested = target.offset + seek;
        return { timeline, target, requested, input, seek, playbackSeek: target.playbackOffset + seek };
      }

      function setCurrentRecordingTime(index, timelineSecond) {
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        if (!video) return false;
        const playbackMode = video.dataset.recordingPlaybackMode || 'recorded_day_stream';
        const streamStart = Math.max(0, Number(video.dataset.recordingStreamStart || recordingStreamStartOffset[index] || 0));
        const playbackStart = Math.max(0, Number(video.dataset.recordingPlaybackStart || 0));
        const streamLike = playbackMode === 'server_cache_mp4' || isRecordedDayStreamMode(playbackMode);
        const target = streamLike
          ? recordingTargetForOffset(index, timelineSecond)
          : null;
        const relativeSeek = streamLike
          ? Math.max(0, Number(target?.playbackSeek || 0) - playbackStart)
          : Math.max(0, Math.floor(Number(timelineSecond || 0)) - streamStart);
        const applySeek = () => {
          try {
            video.currentTime = relativeSeek;
          } catch (error) {
            debugEvent('ui_recording_seek_error', {
              index,
              timeline_second: timelineSecond,
              relative_seek: relativeSeek,
              stream_start: streamStart,
              message: error.message,
            });
          }
        };
        if (video.readyState >= 1) {
          applySeek();
        } else {
          video.addEventListener('loadedmetadata', applySeek, { once: true });
        }
        return true;
      }

      function canSeekBufferedVideo(video, relativeSeek) {
        if (!video || relativeSeek < 0 || video.readyState < 1) return false;
        const ranges = video.seekable || video.buffered;
        if (ranges && ranges.length) {
          for (let i = 0; i < ranges.length; i += 1) {
            if (relativeSeek >= ranges.start(i) && relativeSeek <= ranges.end(i)) return true;
          }
        }
        const duration = Number(video.duration);
        if (Number.isFinite(duration) && duration > 0) return relativeSeek <= duration;
        return relativeSeek <= Number(video.currentTime || 0) + 1.5;
      }

      function seekCurrentRecordingStream(index, timelineSecond, total) {
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        if (!video) return false;
        const streamStart = Math.max(0, Number(video.dataset.recordingStreamStart || recordingStreamStartOffset[index] || 0));
        const playbackMode = video.dataset.recordingPlaybackMode || 'recorded_day_stream';
        const playbackStart = Math.max(0, Number(video.dataset.recordingPlaybackStart || 0));
        const streamLike = playbackMode === 'server_cache_mp4' || isRecordedDayStreamMode(playbackMode);
        const target = streamLike
          ? recordingTargetForOffset(index, timelineSecond)
          : null;
        const relativeSeek = streamLike
          ? Math.floor(Number(target?.playbackSeek || 0) - playbackStart)
          : Math.floor(Number(timelineSecond || 0)) - streamStart;
        if (!canSeekBufferedVideo(video, relativeSeek)) return false;
        try {
          video.currentTime = Math.max(0, relativeSeek);
          updateRecordingTimelineUi(index, target?.requested ?? timelineSecond, total);
          debugEvent('ui_recording_fast_seek', {
            index,
            timeline_second: target?.requested ?? timelineSecond,
            playback_second: target?.playbackSeek,
            relative_seek: relativeSeek,
            stream_start: streamStart,
          });
          return true;
        } catch (error) {
          debugEvent('ui_recording_fast_seek_error', {
            index,
            timeline_second: timelineSecond,
            relative_seek: relativeSeek,
            stream_start: streamStart,
            message: error.message,
          });
          return false;
        }
      }

      function selectRecordingAtOffset(index, offset) {
        clearRecordingContinuation(index);
        markNvrInteraction('select_recording_offset', { index, offset });
        const target = recordingTargetForOffset(index, offset);
        if (!target) return;
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        const wasPlaying = Boolean(video && !video.paused && !video.ended);
        selectedRecording[index] = target.target.file.url;
        selectedRecordingSeek[index] = target.seek;
        selectedRecordingTimeline[index] = target.requested;
        if (video?.dataset?.recordingPlaybackMode === 'server_cache_mp4') {
          if (recordingCacheCoversTarget(recordingStatus[index] || {}, target)) {
            seekRecordingCache(index, target, wasPlaying, 'server_cache_select');
          } else {
            recordingStreamStartOffset[index] = target.requested;
            recordingStreamNonce[index] = Date.now();
            recordingAutoplayAfterRender[index] = wasPlaying;
            debugEvent('ui_recording_cache_tail_day_stream_select', {
              index,
              recording: target.target.file.name,
              timeline_second: target.requested,
              playback_second: target.playbackSeek,
              segment_second: target.seek,
              cache: recordingStatus[index]?.playback_cache || {},
            });
            switchRecordingVideoToFile(index, target, wasPlaying, 'server_cache_tail_day_stream_select');
          }
          return;
        }
        if (video?.dataset?.recordingPlaybackMode === 'server_file_sequence') {
          switchRecordingVideoToFile(index, target, wasPlaying, 'server_file_select');
          return;
        }
        if (seekCurrentRecordingStream(index, target.requested, target.timeline.total)) {
          return;
        }
        recordingStreamStartOffset[index] = target.requested;
        recordingStreamNonce[index] = Date.now();
        debugEvent('ui_recording_timeline_seek', {
          index,
          recording: target.target.file.name,
          timeline_second: target.requested,
          segment_second: selectedRecordingSeek[index],
          recorded_day_stream_start: recordingStreamStartOffset[index],
        });
        recordingAutoplayAfterRender[index] = wasPlaying;
        renderNvrGrid({ reason: 'timeline_seek_continuous' });
      }

      function applyRecordingSeek() {
        document.querySelectorAll('video[data-recording-player]').forEach((video) => {
          if (video.dataset.recordingInitialized) return;
          video.dataset.recordingInitialized = 'true';
          const index = video.dataset.recordingPlayer;
          const seek = Math.max(0, Number(video.dataset.recordingSeek || 0));
          const applySeek = () => {
            if (seek) {
              try {
                video.currentTime = seek;
              } catch (error) {
                debugEvent('ui_recording_seek_error', {
                  index,
                  seek,
                  message: error.message,
                });
              }
            }
            if (recordingAutoplayAfterRender[index]) {
              delete recordingAutoplayAfterRender[index];
              video.play().catch((error) => {
                debugEvent('ui_recording_autoplay_error', { index, message: error.message });
              });
            }
          };
          updateRecordingPlayerControls(video);
          if (video.readyState >= 1) {
            applySeek();
          } else {
            video.addEventListener('loadedmetadata', applySeek, { once: true });
          }
        });
      }

      function syncRecordingVideoProgress(video) {
        const index = video?.dataset?.recordingPlayer;
        if (index === undefined) return;
        const status = recordingStatus[index] || {};
        const files = Array.isArray(status.files) ? status.files : [];
        const streamStart = Math.max(0, Number(video.dataset.recordingStreamStart || recordingStreamStartOffset[index] || 0));
        const playbackMode = video.dataset.recordingPlaybackMode || 'recorded_day_stream';
        const playbackStart = Math.max(0, Number(video.dataset.recordingPlaybackStart || 0));
        const current = Math.max(0, Math.floor(Number(video.currentTime || 0)));
        updateRecordingPlayerControls(video);
        const playbackSecond = playbackStart + current;
        const timelineSecond = streamStart + current;
        const target = playbackMode === 'server_cache_mp4' || isRecordedDayStreamMode(playbackMode)
          ? recordingTargetForPlaybackOffset(index, playbackSecond)
          : recordingTargetForOffset(index, timelineSecond);
        if (target) {
          selectedRecording[index] = target.target.file.url;
          selectedRecordingSeek[index] = target.seek;
          selectedRecordingTimeline[index] = target.requested;
          updateRecordingTimelineUi(index, target.requested, target.timeline.total);
        } else {
          updateRecordingTimelineUi(index, timelineSecond, recordingTimeline(files, status).total);
        }
      }

      function clearRecordingContinuation(index) {
        if (!recordingContinueTimers[index]) return;
        window.clearTimeout(recordingContinueTimers[index]);
        delete recordingContinueTimers[index];
      }

      async function resumeRecordingWhenNextSegmentCloses(index, previousTotal, attempt = 0, playbackMode = 'recorded_day_stream') {
        clearRecordingContinuation(index);
        const dayAtStart = selectedRecordingDay[index];
        await loadRecordingStatus({ reason: 'recording_continuation_refresh' });
        if (dayAtStart !== selectedRecordingDay[index]) return;
        const status = recordingStatus[index] || {};
        const files = Array.isArray(status.files) ? status.files : [];
        const timeline = recordingTimeline(files, status);
        const cacheTotal = Number(status.playback_cache?.total_seconds || 0);
        const hasNewCache = playbackMode === 'server_cache_mp4'
          ? Boolean(status.playback_cache?.ready && cacheTotal > previousTotal)
          : timeline.playbackTotal > previousTotal;
        if (hasNewCache) {
          const target = recordingTargetForPlaybackOffset(index, previousTotal);
          if (target) {
            selectedRecording[index] = target.target.file.url;
            selectedRecordingSeek[index] = target.seek;
            selectedRecordingTimeline[index] = target.requested;
            recordingStreamStartOffset[index] = playbackMode === 'server_cache_mp4' ? 0 : target.requested;
            recordingAutoplayAfterRender[index] = true;
            if (playbackMode === 'server_cache_mp4') {
              recordingStreamNonce[index] = Date.now();
              debugEvent('ui_recording_cache_resume', {
                index,
                previous_total: previousTotal,
                new_total: timeline.playbackTotal,
                attempt,
                cache: status.playback_cache || {},
              });
              seekRecordingCache(index, target, true, 'recording_cache_resume');
              return;
            }
            if (playbackMode === 'server_file_sequence') {
              switchRecordingVideoToFile(index, target, true, 'server_file_resume');
              return;
            }
            recordingStreamNonce[index] = Date.now();
            debugEvent('ui_recording_day_stream_resume', {
              index,
              previous_total: previousTotal,
              new_total: timeline.playbackTotal,
              attempt,
            });
            switchRecordingVideoToFile(index, target, true, 'recorded_day_stream_resume');
            return;
          }
        }
        if (playbackMode === 'server_cache_mp4' && timeline.playbackTotal > previousTotal) {
          const target = recordingTargetForPlaybackOffset(index, previousTotal);
          if (target) {
            recordingStreamStartOffset[index] = target.requested;
            recordingStreamNonce[index] = Date.now();
            recordingAutoplayAfterRender[index] = true;
            debugEvent('ui_recording_cache_tail_day_stream_resume', {
              index,
              previous_total: previousTotal,
              new_total: timeline.playbackTotal,
              attempt,
              cache: status.playback_cache || {},
            });
            switchRecordingVideoToFile(index, target, true, 'cache_tail_day_stream_resume');
            return;
          }
        }
        if ((status.recording || status.desired_recording) && status.selected_day === (status.days || [])[0]?.day && attempt < 12) {
          recordingContinueTimers[index] = window.setTimeout(() => {
            resumeRecordingWhenNextSegmentCloses(index, previousTotal, attempt + 1, playbackMode);
          }, 1500);
          return;
        }
        debugEvent('ui_recording_stream_ended', {
          index,
          timeline_second: selectedRecordingTimeline[index],
          stream_start: recordingStreamStartOffset[index],
          playback_mode: playbackMode,
          previous_total: previousTotal,
          attempt,
        });
      }

      function playNextRecordingSegment(index, video = null) {
        const status = recordingStatus[index] || {};
        const files = Array.isArray(status.files) ? status.files : [];
        const timeline = recordingTimeline(files, status);
        let previousTotal = timeline.playbackTotal;
        if (video?.dataset?.recordingPlaybackMode === 'server_cache_mp4') {
          previousTotal = Math.max(0, Number(status.playback_cache?.total_seconds || previousTotal));
        }
        if (video?.dataset?.recordingPlaybackMode === 'server_cache_mp4') {
          if (status.recording || status.desired_recording) {
            resumeRecordingWhenNextSegmentCloses(index, previousTotal, 0, 'server_cache_mp4');
            return;
          }
        }
        if (video?.dataset?.recordingPlaybackMode === 'server_file_sequence') {
          const entries = timeline.entries || [];
          const current = entries.findIndex((entry) => entry.file.url === (video.dataset.recordingFileUrl || selectedRecording[index]));
          if (current >= 0 && current < entries.length - 1) {
            const next = entries[current + 1];
            switchRecordingVideoToFile(index, { timeline, target: next, requested: next.offset, input: next.offset, seek: 0, playbackSeek: next.playbackOffset }, true, 'server_file_next');
            return;
          }
          if (status.recording || status.desired_recording) {
            resumeRecordingWhenNextSegmentCloses(index, previousTotal, 0, 'server_file_sequence');
            return;
          }
        }
        if (isRecordedDayStreamMode(video?.dataset?.recordingPlaybackMode) && (status.recording || status.desired_recording)) {
          resumeRecordingWhenNextSegmentCloses(index, previousTotal, 0);
          return;
        }
        updateRecordingPlayerControls(video, 'End of available recordings for this day');
        debugEvent('ui_recording_stream_ended', {
          index,
          timeline_second: selectedRecordingTimeline[index],
          stream_start: recordingStreamStartOffset[index],
          playback_mode: video?.dataset?.recordingPlaybackMode || 'recorded_day_stream',
          previous_total: previousTotal,
        });
      }

      function recordingPlaybackClock(seconds) {
        const value = Math.max(0, Math.floor(Number(seconds || 0)));
        return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60]
          .map((part) => String(part).padStart(2, '0')).join(':');
      }

      function recordingPlayerControls(index, timeline, position = 0) {
        const total = Math.max(0, Number(timeline.playbackTotal || 0));
        return `<div class="nvr-playback-controls">
          <div class="nvr-playback-toolbar">
            <button type="button" data-recording-toggle="${index}" aria-label="Play selected day's recordings">Play</button>
            <button type="button" data-recording-jump="-10" data-record-index="${index}" aria-label="Back 10 seconds">−10 s</button>
            <button type="button" data-recording-jump="10" data-record-index="${index}" aria-label="Forward 10 seconds">+10 s</button>
            <button type="button" data-recording-mute="${index}" aria-label="Mute or unmute">Mute</button>
            <label>Speed <select data-recording-speed="${index}" aria-label="Playback speed">
              <option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option>
            </select></label>
            <output class="nvr-playback-clock" data-recording-duration="${index}">${recordingPlaybackClock(position)} / ${recordingPlaybackClock(total)}</output>
          </div>
          <input class="nvr-playback-seek" type="range" min="0" max="${Math.max(0, total - 0.1)}" step="0.1" value="${position}" data-recording-playback-seek="${index}" aria-label="Seek through all recorded video in selected day">
          <p class="nvr-playback-message" data-recording-playback-message="${index}" role="status">${recordingPlaybackClock(total)} recorded in this day</p>
        </div>`;
      }

      function recordingPlaybackPosition(video) {
        return Math.max(0, Number(video?.dataset?.recordingPlaybackStart || 0) + Number(video?.currentTime || 0));
      }

      function updateRecordingPlayerControls(video, message = null) {
        if (!video) return;
        const index = video.dataset.recordingPlayer;
        const status = recordingStatus[index] || {};
        const total = Number(status.timeline?.playback_total_seconds || status.timeline?.recorded_seconds || 0);
        const position = Math.min(total, recordingPlaybackPosition(video));
        const clock = nvrGrid.querySelector(`[data-recording-duration="${index}"]`);
        if (clock) clock.textContent = `${recordingPlaybackClock(position)} / ${recordingPlaybackClock(total)}`;
        const slider = nvrGrid.querySelector(`[data-recording-playback-seek="${index}"]`);
        if (slider) {
          slider.max = String(Math.max(0, total - 0.1));
          if (document.activeElement !== slider) slider.value = String(position);
        }
        const toggle = nvrGrid.querySelector(`[data-recording-toggle="${index}"]`);
        if (toggle) { toggle.textContent = video.paused ? 'Play' : 'Pause'; toggle.setAttribute('aria-pressed', String(!video.paused)); }
        const mute = nvrGrid.querySelector(`[data-recording-mute="${index}"]`);
        if (mute) mute.textContent = video.muted ? 'Unmute' : 'Mute';
        const note = nvrGrid.querySelector(`[data-recording-playback-message="${index}"]`);
        if (note && message !== null) note.textContent = message;
      }

      function seekRecordingPlayback(index, position) {
        const target = recordingTargetForPlaybackOffset(index, position);
        if (target) selectRecordingAtOffset(index, target.requested);
      }

      function recordingVideoDiagnostics(video) {
        const errorNames = {
          1: 'MEDIA_ERR_ABORTED',
          2: 'MEDIA_ERR_NETWORK',
          3: 'MEDIA_ERR_DECODE',
          4: 'MEDIA_ERR_SRC_NOT_SUPPORTED',
        };
        return {
          index: video?.dataset?.recordingPlayer,
          src: video?.currentSrc || video?.querySelector?.('source')?.src || video?.getAttribute?.('src') || '',
          playback_mode: video?.dataset?.recordingPlaybackMode || '',
          recording_seek: video?.dataset?.recordingSeek || '',
          stream_start: video?.dataset?.recordingStreamStart || '',
          playback_start: video?.dataset?.recordingPlaybackStart || '',
          ready_state: video?.readyState,
          network_state: video?.networkState,
          current_time: Number(video?.currentTime || 0),
          duration: Number.isFinite(Number(video?.duration)) ? Number(video.duration) : String(video?.duration || ''),
          paused: Boolean(video?.paused),
          error_code: video?.error?.code || 0,
          error_name: errorNames[video?.error?.code] || '',
          error_message: video?.error?.message || '',
        };
      }

      function pretty(value) {
        if (typeof value === 'string') return value || 'empty';
        try {
          return JSON.stringify(value ?? {}, null, 2);
        } catch (_) {
          return String(value ?? '');
        }
      }

      function severityRank(severity) {
        if (severity === 'error') return 3;
        if (severity === 'warning') return 2;
        return 1;
      }

      function strongestSeverity(items, titles = []) {
        const allowed = new Set(titles);
        return (Array.isArray(items) ? items : []).reduce((strongest, item) => {
          if (allowed.size && !allowed.has(item?.title)) return strongest;
          const severity = ['ok', 'warning', 'error'].includes(item?.severity) ? item.severity : 'ok';
          return severityRank(severity) > severityRank(strongest) ? severity : strongest;
        }, 'ok');
      }

      function logSeverityFromText(text) {
        const lowered = String(text || '').toLowerCase();
        const errorPatterns = [
          /(^|\n|\s)error[:\s(]/,
          /"severity"\s*:\s*"error"/,
          /"event"\s*:\s*"[^"]*error"/,
          /\bfailed\b/,
          /\bexception\b/,
          /\btraceback\b/,
          /\bcannot\b/,
          /\bnot found\b/,
          /\bunsupported\b/,
          /\bfatal\b/,
        ];
        const warningPatterns = [
          /(^|\n|\s)warning[:\s(]/,
          /"severity"\s*:\s*"warning"/,
          /\btimeout\b/,
          /\bdeadline exceeded\b/,
          /\bstalled\b/,
          /\bwaiting\b/,
          /\btoo slow\b/,
          /\bstale\b/,
          /\bblocked\b/,
          /\bdeferred\b/,
          /\bdiscarding\b/,
          /\bbroken pipe\b/,
          /\bbacklog\b/,
        ];
        if (errorPatterns.some((pattern) => pattern.test(lowered))) return 'error';
        if (warningPatterns.some((pattern) => pattern.test(lowered))) return 'warning';
        return 'ok';
      }

      function logSeverityFromValue(value) {
        return logSeverityFromText(pretty(value));
      }

      function logBlock(title, value, severity = '') {
        const resolvedSeverity = ['ok', 'warning', 'error'].includes(severity) ? severity : logSeverityFromValue(value);
        return `
          <div class="log-block log-${resolvedSeverity}">
            <h2><span>${escapeHtml(title)}</span><span class="log-severity">${escapeHtml(resolvedSeverity.toUpperCase())}</span></h2>
            <pre class="log-output">${escapeHtml(pretty(value))}</pre>
          </div>
        `;
      }

      function diagnosticClass(severity) {
        if (severity === 'error') return 'diag-error';
        if (severity === 'warning') return 'diag-warning';
        return 'diag-ok';
      }

      function renderDiagnostics(items) {
        const diagnostics = Array.isArray(items) ? items : [];
        if (!diagnostics.length) return '<p class="notice">No diagnostics yet.</p>';
        return `<div class="diagnostic-list">${diagnostics.map((item) => `
          <div class="diagnostic-item ${diagnosticClass(item.severity)}">
            <b>${escapeHtml(String(item.severity || 'ok').toUpperCase())}: ${escapeHtml(item.title || 'Diagnostic')}</b>
            <span>${escapeHtml(item.detail || '')}</span>
            ${item.payload && Object.keys(item.payload).length ? `<pre>${escapeHtml(pretty(item.payload))}</pre>` : ''}
          </div>
        `).join('')}</div>`;
      }

      function renderLogs() {
        if (!panelLogs) {
          logsView.innerHTML = '<p class="notice">Click Refresh logs to load diagnostics.</p>';
          return;
        }
        const recordingLogs = Array.isArray(panelLogs.recording_logs) ? panelLogs.recording_logs : [];
        const hardwareSeverity = strongestSeverity(panelLogs.diagnostics, [
          'CPU load',
          'Memory',
          'Panel process',
          'Recordings disk',
          'Edge config disk',
          'NVR recorders',
          'Playback cache worker',
          'Daily NVR cache window',
        ]);
        logsView.innerHTML = [
          renderDiagnostics(panelLogs.diagnostics || []),
          logBlock('Runtime summary', {
            generated_at: panelLogs.generated_at,
            server_version: panelLogs.server_version,
            ui_build: panelLogs.ui_build,
            authoritative_config: panelLogs.authoritative_config,
            runtime_config: panelLogs.runtime_config,
            config_summary: panelLogs.config_summary
          }, 'ok'),
          ...(EDGE_NATIVE_RTC ? [logBlock('EdgeRTC source connections', panelLogs.runtime_config?.edge_rtc || {})] : []),
          logBlock('Hardware diagnostics', panelLogs.hardware || {}, hardwareSeverity),
          logBlock('Runtime parameters', panelLogs.runtime_parameters || {}, 'ok'),
          logBlock('Edge debug', panelLogs.edge_debug || 'No debug log yet.'),
          logBlock('Last save debug', panelLogs.last_save_debug || {}),
          logBlock('Last runtime sync', panelLogs.last_runtime_sync || {}),
          logBlock('Panel config', panelLogs.panel_config || {}),
          logBlock('Runtime edge.json', panelLogs.runtime_config_file || {}),
          logBlock('MediaMTX runtime config', panelLogs.runtime_mediamtx_config || 'missing'),
          logBlock('Janus streaming config', panelLogs.runtime_janus_streaming_config || 'missing'),
          logBlock('Recording stream', panelLogs.recording_stream_log || 'empty'),
          logBlock('Recording cache', panelLogs.recording_cache_log || 'empty'),
          logBlock('Recording thumbnails', panelLogs.recording_thumbnail_log || 'empty'),
          logBlock('Last saved response', panelLogs.last_saved_config || {}),
          ...recordingLogs.map((item) => logBlock(`Recording ffmpeg: ${item.path}`, item.tail || 'empty'))
        ].join('');
      }

      async function loadLogs() {
        logsView.innerHTML = '<p class="notice">Loading logs...</p>';
        const response = await fetch(panelPath('api/logs'), { cache: 'no-store' });
        panelLogs = await response.json();
        if (!response.ok) {
          logsView.innerHTML = `<p class="notice">${escapeHtml(panelLogs.error || 'Could not load logs.')}</p>`;
          return;
        }
        debugEvent('ui_logs_loaded', {
          generated_at: panelLogs.generated_at,
          config_summary: panelLogs.config_summary,
          recording_logs: Array.isArray(panelLogs.recording_logs) ? panelLogs.recording_logs.length : 0
        });
        renderLogs();
      }

      function renderEffectiveStreams(streams) {
        const order = ['main', 'sub', 'tile', 'live', 'record', 'snapshot'];
        if (!streams || typeof streams !== 'object') return '';
        const rows = order
          .filter((name) => streams[name])
          .map((name) => {
            const item = streams[name] || {};
            return `
              <div class="stream-pill">
                <b>${escapeHtml(name)}</b>
                <span>${escapeHtml(text(item.stream))} / ch ${escapeHtml(text(item.channel))}</span>
                <code>${escapeHtml(text(item.rtsp, 'missing'))}</code>
              </div>
            `;
          }).join('');
        return rows ? `<div class="stream-strip">${rows}</div>` : '';
      }

      function isStreamConfigured(camera, streamName) {
        if (!camera || !streamName) return false;
        if (camera.effective_streams?.[streamName]?.configured) return true;
        return Boolean(camera[`rtsp_${streamName}`]);
      }

      function chooseHomePreviewStream(camera) {
        const requestedTileStream = camera.tile_stream || 'sub';
        if (EDGE_NATIVE_RTC) return {stream:camera.record_stream||'main',requested:requestedTileStream,reason:'single_source_record_profile'};
        const liveStream = camera.live_stream || 'sub';
        const subConfigured = isStreamConfigured(camera, 'sub');
        if (isStreamConfigured(camera, requestedTileStream)) {
          return {
            stream: requestedTileStream,
            requested: requestedTileStream,
            reason: 'configured_tile_stream',
          };
        }
        const fallbackStream = subConfigured ? 'sub' : (isStreamConfigured(camera, 'main') ? 'main' : liveStream);
        return {
          stream: fallbackStream,
          requested: requestedTileStream,
          reason: 'tile_stream_missing_fallback',
        };
      }

      function cameraCard(camera) {
        const online = camera.status === 'online';
        const liveKey = camera.key || `${camera.id || 'camera'}_${camera.index ?? 0}`;
        const liveId = camera.id || liveKey;
        const videoCodec = camera.video_codec || camera.codec;
        const liveStream = camera.live_stream || 'sub';
        const previewChoice = chooseHomePreviewStream(camera);
        const previewStream = previewChoice.stream;
        const liveResolution = camera.live_width && camera.live_height ? `${camera.live_width}x${camera.live_height}` : 'unknown';
        const previewResolution = previewStream === liveStream ? liveResolution : 'tile';
        const liveCodec = camera.live_video_codec || videoCodec || 'video';
        const mediamtxPath = `${encodeURIComponent(liveId)}_${encodeURIComponent(previewStream)}`;
        const plan = EDGE_NATIVE_RTC ? {canEmbed:true,url:"",reason:"edge_rtc"} : liveConnectionPlan(mediamtxPath);
        const mediamtxUrl = EDGE_NATIVE_RTC ? panelPath(`api/edge-rtc/player/${mediamtxPath}`) : plan.canEmbed ? mediaMtxPlayerUrl(plan.url) : plan.url;
        const statusBadge = `<div class="connection-badge ${statusClass(camera.status)}">${escapeHtml(statusLabel(camera.status))}</div>`;
        if (live[liveKey]) {
          debugEvent('ui_live_plan', {
            liveKey,
            cameraIndex: camera.index,
            cameraId: camera.id,
            stream: previewStream,
            requested_tile_stream: previewChoice.requested,
            preview_reason: previewChoice.reason,
            can_embed: plan.canEmbed,
            reason: plan.reason,
            diagnostics: plan.diagnostics,
          });
        }
        const preview = live[liveKey]
          ? (plan.canEmbed
            ? `<iframe class="live-frame" src="${escapeHtml(mediamtxUrl)}" data-live-key="${escapeHtml(liveKey)}" data-live-url="${escapeHtml(mediamtxUrl)}" data-live-path="${escapeHtml(mediamtxPath)}" title="${escapeHtml(text(camera.name, camera.id))} ${EDGE_NATIVE_RTC ? 'EdgeRTC' : 'WebRTC'} live" loading="eager" allow="autoplay; fullscreen; encrypted-media" allowfullscreen onload="window.edgeFrameLoaded && window.edgeFrameLoaded(this)" onerror="window.edgeFrameError && window.edgeFrameError(this)"></iframe>`
            : liveBlockedPreview(plan))
          : `<span>${online ? 'Click to start live' : escapeHtml(text(camera.detail, 'Waiting for camera'))}</span>`;
        const fullscreenButton = live[liveKey] && plan.canEmbed
          ? `<button class="fullscreen-button" type="button" data-fullscreen-live aria-label="Fullscreen" title="Fullscreen">${fullscreenIcon()}</button>`
          : '';
        return `
          <article class="camera video-tile">
            <div class="preview" data-live-key="${escapeHtml(liveKey)}" data-live-index="${escapeHtml(camera.index ?? 0)}" data-live-path="${escapeHtml(mediamtxPath)}" title="Click to toggle live preview">${preview}${statusBadge}${fullscreenButton}</div>
            <div class="body">
              <div class="row">
                <div class="name">${escapeHtml(text(camera.name, camera.id))}</div>
                <div class="vendor">${escapeHtml(text(camera.vendor))}</div>
              </div>
              <div class="tile-line">
                <span>preview ${escapeHtml(previewStream)} ${escapeHtml(liveCodec)} ${escapeHtml(previewResolution)}</span>
                <span>${escapeHtml(text(camera.fps, ''))}</span>
              </div>
            </div>
          </article>
        `;
      }

      window.edgeFrameLoaded = function edgeFrameLoaded(frame) {
        const liveKey = frame?.dataset?.liveKey || 'unknown';
        const timer = liveFrameTimers.get(liveKey);
        if (timer) {
          clearTimeout(timer);
          liveFrameTimers.delete(liveKey);
        }
        debugEvent('ui_live_frame_load', {
          liveKey,
          url: frame?.dataset?.liveUrl || '',
          path: frame?.dataset?.livePath || '',
        });
      };

      window.edgeFrameError = function edgeFrameError(frame) {
        debugEvent('ui_live_frame_error', {
          liveKey: frame?.dataset?.liveKey || 'unknown',
          url: frame?.dataset?.liveUrl || '',
          path: frame?.dataset?.livePath || '',
        });
      };

      function monitorLiveFrames() {
        document.querySelectorAll('iframe.live-frame[data-live-key]').forEach((frame) => {
          const liveKey = frame.dataset.liveKey || 'unknown';
          const url = frame.dataset.liveUrl || frame.src || '';
          if (liveFrameTimers.has(liveKey)) {
            clearTimeout(liveFrameTimers.get(liveKey));
          }
          debugEvent('ui_live_frame_rendered', {
            liveKey,
            url,
            path: frame.dataset.livePath || '',
          });
          const timer = setTimeout(() => {
            debugEvent('ui_live_frame_timeout', {
              liveKey,
              url,
              path: frame.dataset.livePath || '',
              timeout_ms: 12000,
              hint: 'If this happens only on LTE or another Wi-Fi, check WebRTC public URL, ICE candidates, TCP 8889, and ICE TCP/UDP 8189.',
            });
          }, 12000);
          liveFrameTimers.set(liveKey, timer);
        });
        document.querySelectorAll('[data-live-blocked]').forEach((node) => {
          const preview = node.closest('[data-live-key]');
          const livePath = preview?.dataset?.livePath || 'diagnostic';
          const plan = liveConnectionPlan(livePath);
          debugEvent('ui_live_blocked', {
            liveKey: preview?.dataset?.liveKey || 'unknown',
            reason: node.dataset.liveBlocked || plan.reason,
            diagnostics: plan.diagnostics,
          });
        });
      }

      function addCameraTile() {
        return `
          <article class="camera add-camera-tile" data-add-camera-tile title="Add camera">
            <div class="add-camera-content">
              <div class="add-camera-plus">+</div>
              <div>Add camera</div>
            </div>
          </article>
        `;
      }

      function recordErrorText(code) {
        const messages = {
          recording_host_missing: 'Cannot record: camera host/IP is missing.',
          recording_username_missing: 'Cannot record: camera username is missing.',
          recording_password_missing: 'Cannot record: camera password is missing. Fill it in Camera settings and save.',
          recording_rtsp_not_configured: 'Cannot record: RTSP URL is not configured.',
          ffmpeg_not_installed_in_addon: 'Cannot record: FFmpeg is not available inside the add-on.',
        };
        return messages[code] || code || '';
      }

      function nvrCard(camera, index) {
        const status = recordingStatus[index] || {};
        const isRecording = Boolean(status.recording);
        const desiredRecording = Boolean(status.desired_recording);
        const recordingState = status.recording_status || (isRecording ? 'recording' : desiredRecording ? 'scheduled_stopped' : 'disabled');
        const badgeText = isRecording
          ? 'RECORDING'
          : recordingState === 'blocked'
            ? 'BLOCKED'
          : desiredRecording
              ? 'SCHEDULED'
              : 'OFF';
        const canRecord = status.can_record !== false;
        const recordError = status.record_error || status.last_error || '';
        const files = Array.isArray(status.files) ? status.files : [];
        const days = Array.isArray(status.days) ? status.days : [];
        const currentDay = selectedRecordingDay[index] || status.selected_day || days[0]?.day || '';
        const currentDayIndex = Math.max(0, days.findIndex((day) => day.day === currentDay));
        const selected = selectedRecording[index] || '';
        const displayFiles = recordingFilmstripFiles(files, selected, 24);
        const seekSeconds = Number(selectedRecordingSeek[index] || 0);
        const selectedIndex = files.findIndex((file) => file.url === selected);
        const selection = recordingOffsetForSelection(files, selected, seekSeconds, status);
        const timeline = selection.timeline;
        const timelineTotal = timeline.total;
        const timelineMax = Math.max(0, timelineTotal - 1);
        const storedTimeline = Number(selectedRecordingTimeline[index]);
        let timelineValue = Math.max(0, Math.min(Number.isFinite(storedTimeline) ? storedTimeline : selection.value, timelineMax));
        let targetForPoster = recordingTargetForOffset(index, timelineValue) || recordingDefaultTarget(files, status);
        if (targetForPoster) {
          timelineValue = Math.max(0, Math.min(Number(targetForPoster.requested || 0), timelineMax));
          selectedRecording[index] = targetForPoster.target.file.url;
          selectedRecordingSeek[index] = targetForPoster.seek;
          selectedRecordingTimeline[index] = timelineValue;
          recordingStreamStartOffset[index] = timelineValue;
        } else if (!Number.isFinite(storedTimeline)) {
          selectedRecordingTimeline[index] = timelineValue;
          recordingStreamStartOffset[index] = timelineValue;
        }
        const selectedFile = targetForPoster?.target?.file || null;
        const cache = status.playback_cache || {};
        let playbackMode = recordingPlaybackModeForClient(status, timeline);
        if (playbackMode === 'server_cache_mp4' && !recordingCacheCoversTarget(status, targetForPoster) && selectedFile?.url) {
          playbackMode = files.length > 1 || timeline.playbackTotal > Math.max(1, Number(status.segment_seconds || 10))
            ? 'recorded_day_stream'
            : 'server_file_sequence';
        }
        let playerSeek = 0;
        let playerStreamStart = Number(targetForPoster?.requested || timelineValue || 0);
        let playerPlaybackStart = 0;
        let rawPlayerSrc = '';
        if (timelineTotal) {
          if (playbackMode === 'server_cache_mp4' && cache.url) {
            const cacheTotal = Math.max(0, Number(cache.total_seconds || 0));
            playerSeek = cacheTotal ? Math.min(Number(targetForPoster?.playbackSeek || 0), Math.max(0, cacheTotal - 1)) : Number(targetForPoster?.playbackSeek || 0);
            playerStreamStart = 0;
            playerPlaybackStart = 0;
            rawPlayerSrc = panelPath(cache.url);
          } else if (playbackMode === 'server_file_sequence' && selectedFile?.url) {
            playerSeek = Number(targetForPoster?.seek || 0);
            playerStreamStart = Number(targetForPoster?.target?.offset || 0);
            playerPlaybackStart = Number(targetForPoster?.target?.playbackOffset || 0);
            rawPlayerSrc = panelPath(selectedFile.url);
          } else {
            playerStreamStart = Number(targetForPoster?.requested || timelineValue || 0);
            playerPlaybackStart = Number(targetForPoster?.playbackSeek || 0);
            rawPlayerSrc = recordingStreamUrl(status, index, playerPlaybackStart, playerStreamStart);
          }
        }
        const playerSrc = playbackMode === 'server_file_sequence' || playbackMode === 'server_cache_mp4'
          ? mediaUrlWithTimeFragment(rawPlayerSrc, playerSeek)
          : rawPlayerSrc;
        const posterUrl = targetForPoster?.target?.file?.thumbnail_url ? panelPath(targetForPoster.target.file.thumbnail_url) : '';
        const preloadMode = 'metadata';
        const playable = Boolean(timelineTotal && playerSrc);
        const daySelector = days.length
          ? `<div class="recording-day-bar" data-recording-day-swipe="${index}">
              <button type="button" data-recording-day-step="older" data-record-index="${index}" ${currentDayIndex < days.length - 1 ? '' : 'disabled'}>Day back</button>
              <div class="recording-day-strip">
                ${days.map((day) => {
                  const thumb = day.thumbnail_url ? panelPath(day.thumbnail_url) : '';
                  return `<button class="recording-day-tile ${day.day === currentDay ? 'active' : ''}" type="button" data-recording-day="${escapeHtml(day.day)}" data-record-index="${index}">
                    <span class="recording-day-thumb">${thumb ? `<img class="recording-thumb" src="${THUMB_PLACEHOLDER}" data-recording-thumb-src="${escapeHtml(thumb)}" alt="${escapeHtml(day.day)} preview" loading="lazy" decoding="async">` : ''}</span>
                    <span><b>${escapeHtml(recordingDayLabel(day.day))}</b><small data-recording-day-summary="${escapeHtml(day.day)}">${escapeHtml(formatDuration(day.recorded_seconds || 0))} / ${escapeHtml(formatDuration(day.day_total_seconds || day.total_seconds || 86400))} | 00:00–24:00</small></span>
                  </button>`;
                }).join('')}
              </div>
              <button type="button" data-recording-day-step="newer" data-record-index="${index}" ${currentDayIndex > 0 ? '' : 'disabled'}>Day forward</button>
            </div>`
          : '';
        const player = playable
          ? `<div class="recording-player-wrap" data-recording-wrap="${index}">
              <video class="recording-player" tabindex="0" aria-label="Recordings for selected day" src="${escapeHtml(playerSrc)}" ${posterUrl ? `poster="${escapeHtml(posterUrl)}"` : ''} preload="${preloadMode}" playsinline webkit-playsinline data-recording-player="${index}" data-recording-file-url="${escapeHtml(selectedFile?.url || '')}" data-recording-loaded-file="${escapeHtml(selectedFile?.url || '')}" data-recording-src="${escapeHtml(playerSrc)}" data-recording-seek="${escapeHtml(playerSeek)}" data-recording-stream-start="${escapeHtml(playerStreamStart)}" data-recording-playback-start="${escapeHtml(playerPlaybackStart)}" data-recording-playback-mode="${escapeHtml(playbackMode)}"></video>
              <canvas class="recording-transition-frame" data-recording-transition hidden aria-hidden="true"></canvas>
              ${recordingPlayerControls(index, timeline, targetForPoster?.playbackSeek || 0)}
              <button class="fullscreen-button" type="button" data-recording-fullscreen="${index}" aria-label="Fullscreen" title="Fullscreen">${fullscreenIcon()}</button>
            </div>`
          : `<div class="recording-empty">No video yet</div>`;
        const timelineControl = timelineTotal
          ? `<div class="recording-timeline recording-native-timeline">
              <label>Day timeline
                <input class="recording-day-seek" type="range" min="0" max="${timelineMax}" step="1" value="${timelineValue}" data-recording-day-seek="${index}" aria-label="Seek within selected recording day">
              </label>
              <div class="timeline-row"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
              <p class="notice">${escapeHtml(recordingDayLabel(currentDay))}: one continuous day. Empty periods jump to the next available recording.</p>
              <div class="timeline-row">
                <span>Recording time</span>
                <span data-recording-timeline-label="${index}">${escapeHtml(recordingTimelineLabel(index, timelineValue, timelineTotal))}</span>
              </div>
              <div class="timeline-row">
                <span>${escapeHtml(text(status.timeline?.oldest_at, 'oldest'))}</span>
                <span>${escapeHtml(text(status.timeline?.newest_at, 'newest'))}</span>
              </div>
            </div>`
          : '';
        const recordingList = displayFiles.length
          ? `<div class="recording-film-grid">${displayFiles.map((file) => `
              <button class="recording-tile ${file.url === selected ? 'active' : ''}" type="button" data-play-recording="${escapeHtml(file.url)}" data-recording-offset="${escapeHtml(offsetForRecordingUrl(index, file.url))}" data-record-index="${index}">
                <span class="recording-thumb-wrap">
                  <img class="recording-thumb" src="${THUMB_PLACEHOLDER}" ${file.thumbnail_url ? `data-recording-thumb-src="${escapeHtml(panelPath(file.thumbnail_url))}"` : ''} alt="${escapeHtml(file.name)} snapshot" loading="lazy" decoding="async" fetchpriority="low">
                  <span class="recording-thumb-time">${escapeHtml(formatTimestampSeconds(file.start_ts || file.start_at || file.modified_at))}</span>
                </span>
                <b>${escapeHtml(formatTimestampSeconds(file.start_ts))}</b>
                <span class="recording-meta">Preview — click to watch from this time</span>
              </button>
            `).join('')}</div>`
          : `<p class="notice">${desiredRecording ? 'Continuous video recording is scheduled. The first playable clip appears after the first' : 'Start continuous video recording. The first playable clip appears after the first'} ${escapeHtml(text(status.segment_seconds, 10))}-second segment closes.</p>`;
        const diagnostics = recordError
          ? `<p class="section-status state-offline">${escapeHtml(recordErrorText(recordError))}</p>`
          : `<p class="section-status">${escapeHtml(text(status.record_rtsp, 'Record RTSP not ready'))}</p>`;
        return `
          <article class="camera nvr-card" data-nvr-index="${index}" data-nvr-key="${escapeHtml(status.key || '')}" data-nvr-day="${escapeHtml(currentDay)}">
            <div class="body">
              <div class="nvr-head">
                <div class="name">${escapeHtml(text(camera.name, `Camera ${index + 1}`))}</div>
                <div class="rec-badge ${isRecording ? '' : 'off'}">${badgeText}</div>
              </div>
              ${daySelector}
              ${player}
              ${timelineControl}
              ${diagnostics}
              <div class="actions">
                <button class="primary" data-record-action="${isRecording ? 'stop' : 'start'}" data-record-index="${index}" ${!isRecording && !canRecord ? 'disabled' : ''}>${isRecording ? 'Stop' : 'Record'}</button>
                <button data-recording-day-step="older" data-record-index="${index}" ${currentDayIndex < days.length - 1 ? '' : 'disabled'}>Previous day</button>
                <button data-recording-day-step="newer" data-record-index="${index}" ${currentDayIndex > 0 ? '' : 'disabled'}>Next day</button>
              </div>
              ${recordingList}
            </div>
          </article>
        `;
      }

      let overviewCameras = [];
      let haDashboard = { cameras: [] };
      let haRequestedCamera = '';
      function updateCameraOverview() {
        const online = overviewCameras.filter((camera) => camera.status === 'online').length;
        const total = (config.cameras || []).length;
        const recording = Object.values(recordingStatus).filter((camera) => camera.recording).length;
        document.getElementById('camera-overview').innerHTML = `<div class="overview-stat"><b>${online} / ${total}</b><span>Cameras online</span></div><div class="overview-stat"><b>${recording}</b><span>Recording now</span></div><div class="overview-stat"><b>${config.nvr?.retention_days || config.storage?.retention_days || 14} days</b><span>Recording retention</span></div>`;
      }
      function updateUnsavedState() {
        document.querySelectorAll('[data-unsaved-indicator]').forEach((node) => { node.textContent = configDirty ? 'Unsaved changes' : 'All changes saved'; node.classList.toggle('dirty', configDirty); });
      }
      function updateDashboardCard() {
        const selected = document.getElementById('ha-camera-select').value;
        const camera = haDashboard.cameras.find((item) => item.id === selected);
        const entity = document.getElementById('ha-camera-entity').value.trim();
        const yaml = document.getElementById('ha-card-yaml');
        const copy = document.getElementById('ha-copy-card');
        const status = document.getElementById('ha-entity-status');
        document.getElementById('ha-copy-status').textContent = '';
        const custom = document.getElementById('ha-card-type').value === 'edge';
        const count = Number(document.getElementById('ha-camera-count').value);
        const quality = EDGE_NATIVE_RTC ? 'nvr' : document.getElementById('ha-card-quality').value;
        const items = [{camera, entity}, ...Array.from(document.querySelectorAll('[data-ha-extra-slot]')).map(slot => ({camera: haDashboard.cameras.find(item => item.id === slot.querySelector('select').value), entity: slot.querySelector('input').value.trim()}))];
        const valid = items.length === count && items.every(item => item.camera && /^camera\.[a-z0-9_]+$/.test(item.entity)) && new Set(items.map(item => item.entity)).size === count;
        document.getElementById('ha-card-quality').disabled = !custom || EDGE_NATIVE_RTC;
        if (EDGE_NATIVE_RTC) {document.getElementById('ha-card-quality').value='nvr';document.querySelector('#ha-card-type option[value="edge"]').textContent='Edge Live · native EdgeRTC';}
        document.getElementById('ha-card-resource-status').textContent = EDGE_NATIVE_RTC ? 'EdgeRTC beta · live, NVR and a compatible RTSP microphone share one camera session. Use the NVR quality profile. HTTPS and WebCodecs are required.' : haDashboard.card_ready ? `Edge Live card registered in HA (${haDashboard.card_version}). NVR is the shared recording feed. The microphone starts only when you click it and may temporarily use a direct talk connection.` : 'Restart Home Assistant Core after updating Edge to register the Edge Live card and its NVR profile entities.';
        copy.disabled = !valid;
        if (!valid) { yaml.value = ''; status.textContent = 'Choose 1–4 different cameras and enter their exact Home Assistant entity IDs. Enable/update the Edge integration and restart Core if entities are missing.'; return; }
        if (custom) {
          const lines = ['type: custom:edge-live-card'];
          if (count === 1) lines.push(`entity: ${entity}`, `name: ${JSON.stringify(camera.name)}`, `quality: ${quality}`);
          else { lines.push('columns: auto', 'cameras:'); items.forEach(item => lines.push(`  - entity: ${item.entity}`, `    name: ${JSON.stringify(item.camera.name)}`, `    quality: ${quality}`)); }
          lines.push('autoplay: true', 'muted: true', 'volume: 0.6', 'microphone: true', 'reconnect: true', 'fit: contain'); yaml.value = lines.join('\n') + '\n';
        } else if (count === 1) yaml.value = ['type: picture-entity', `entity: ${entity}`, 'camera_view: live', `name: ${JSON.stringify(camera.name)}`, 'show_name: true', 'show_state: false', 'aspect_ratio: "16:9"'].join('\n') + '\n';
        else { const lines = ['type: grid', 'columns: 2', 'square: false', 'cards:']; items.forEach(item => lines.push('  - type: picture-entity', `    entity: ${item.entity}`, '    camera_view: live', `    name: ${JSON.stringify(item.camera.name)}`, '    show_state: false')); yaml.value = lines.join('\n') + '\n'; }
        status.textContent = items.some(item => !item.camera.enabled) ? 'A selected camera is disabled. Enable it in Cameras to view live video.' : 'Card ready. Copy the YAML into your Home Assistant dashboard.';
      }
      function renderDashboardSlots() {
        const count = Number(document.getElementById('ha-camera-count').value);
        const root = document.getElementById('ha-extra-cameras');
        const previous = Array.from(root.querySelectorAll('[data-ha-extra-slot]')).map(slot => ({id: slot.querySelector('select').value, entity: slot.querySelector('input').value}));
        root.replaceChildren(); const used = new Set([document.getElementById('ha-camera-select').value]);
        for (let i = 1; i < count; i++) {
          const selected = previous[i-1]?.id || haDashboard.cameras.find(camera => !used.has(camera.id))?.id || ''; used.add(selected);
          const camera = haDashboard.cameras.find(item => item.id === selected);
          const slot = document.createElement('div'); slot.dataset.haExtraSlot = String(i);
          slot.innerHTML = `<label>Camera ${i+1}<select><option value="">Select camera</option>${haDashboard.cameras.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('')}</select></label><label>Home Assistant camera entity<input autocomplete="off" spellcheck="false" placeholder="camera.front_door"></label>`;
          slot.querySelector('select').value = selected;slot.querySelector('input').value = previous[i-1]?.entity || camera?.entity_id || '';
          slot.querySelector('select').onchange = () => {slot.querySelector('input').value = haDashboard.cameras.find(item => item.id === slot.querySelector('select').value)?.entity_id || '';updateDashboardCard();};slot.querySelector('input').oninput = updateDashboardCard;root.append(slot);
        }
      }

      function selectDashboardCamera() {
        const camera = haDashboard.cameras.find((item) => item.id === document.getElementById('ha-camera-select').value);
        document.getElementById('ha-camera-entity').value = camera?.entity_id || '';
        updateDashboardCard();
      }
      async function loadDashboardEntities() {
        const status = document.getElementById('ha-entity-status');
        status.textContent = 'Looking for registered camera entities…';
        try {
          const response = await fetch(panelPath('api/home-assistant/dashboard'), { cache: 'no-store' });
          if (!response.ok) throw new Error('Could not read Home Assistant camera entities.');
          haDashboard = await response.json();
          const select = document.getElementById('ha-camera-select');
          const selected = haRequestedCamera || select.value;
          select.innerHTML = haDashboard.cameras.map((camera) => `<option value="${escapeHtml(camera.id)}">${escapeHtml(camera.name)}${camera.enabled ? '' : ' (disabled)'}</option>`).join('');
          if (haDashboard.cameras.some((camera) => camera.id === selected)) select.value = selected;
          haRequestedCamera = '';
          selectDashboardCamera();renderDashboardSlots();updateDashboardCard();
        } catch (error) {
          status.textContent = error.message;
          document.getElementById('ha-copy-card').disabled = true;
        }
      }
      document.getElementById('ha-camera-count').addEventListener('change', () => {renderDashboardSlots();updateDashboardCard();});
      document.getElementById('ha-card-quality').addEventListener('change', updateDashboardCard);
      document.getElementById('ha-card-type').addEventListener('change', updateDashboardCard);
      document.getElementById('ha-camera-select').addEventListener('change', selectDashboardCamera);
      document.getElementById('ha-camera-entity').addEventListener('input', updateDashboardCard);
      document.getElementById('ha-refresh-entities').addEventListener('click', loadDashboardEntities);
      document.getElementById('ha-copy-card').addEventListener('click', async () => {
        const area = document.getElementById('ha-card-yaml');
        const status = document.getElementById('ha-copy-status');
        try {
          if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(area.value);
          else { area.focus(); area.select(); if (!document.execCommand('copy')) throw new Error('Select and copy the YAML above.'); }
          status.textContent = 'Copied. Paste this into a Manual card in your Home Assistant dashboard.';
        } catch (_) { area.focus(); area.select(); status.textContent = 'Automatic copying is unavailable here. Copy the selected YAML manually.'; }
      });
      document.querySelectorAll('[data-settings-jump]').forEach((button) => button.addEventListener('click', () => {
        const target = document.getElementById(button.dataset.settingsJump);
        if (target.tagName === 'DETAILS') target.open = true;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }));

      function original(value) {
        return `data-original="${escapeHtml(value || '')}"`;
      }

      function option(value, current, label = value) {
        return `<option value="${escapeHtml(value)}" ${String(current || '') === String(value) ? 'selected' : ''}>${escapeHtml(label)}</option>`;
      }

      function streamEditor(index, streamName, stream) {
        const video = stream?.video || {};
        const audio = stream?.audio || {};
        return `
          <div class="stream-editor" data-stream-editor data-camera-index="${index}" data-stream="${streamName}">
            <div class="row">
              <div class="name">${streamName === 'main' ? 'Main stream' : 'Sub stream'}</div>
              <div class="vendor">${escapeHtml(text(stream?.id, streamName))}</div>
            </div>
            <div class="form-grid">
              <label>Video enabled<input data-stream-field="video.enabled" ${original(video.enabled)} value="${escapeHtml(video.enabled || '')}"></label>
              <label>Codec<input data-stream-field="video.codec" ${original(video.codec)} value="${escapeHtml(video.codec || '')}"></label>
              <label>Width<input data-stream-field="video.width" ${original(video.width)} value="${escapeHtml(video.width || '')}"></label>
              <label>Height<input data-stream-field="video.height" ${original(video.height)} value="${escapeHtml(video.height || '')}"></label>
              <label>FPS<input data-stream-field="video.fps" ${original(video.fps)} value="${escapeHtml(video.fps || '')}"></label>
              <label>Bitrate kbps<input data-stream-field="video.bitrate" ${original(video.bitrate)} value="${escapeHtml(video.bitrate || '')}"></label>
              <label>Bitrate mode<select data-stream-field="video.bitrate_mode" ${original(video.bitrate_mode)}>
                ${option('', video.bitrate_mode, 'keep')}
                ${option('VBR', video.bitrate_mode)}
                ${option('CBR', video.bitrate_mode)}
                ${option('variable', video.bitrate_mode)}
                ${option('constant', video.bitrate_mode)}
              </select></label>
              <label>Quality<input data-stream-field="video.quality" ${original(video.quality)} value="${escapeHtml(video.quality || '')}"></label>
              <label>Keyframe interval<input data-stream-field="video.keyframe_interval" ${original(video.keyframe_interval)} value="${escapeHtml(video.keyframe_interval || '')}"></label>
              <label>Audio enabled<input data-stream-field="audio.enabled" ${original(audio.enabled)} value="${escapeHtml(audio.enabled || '')}"></label>
              <label>Audio codec<input data-stream-field="audio.codec" ${original(audio.codec)} value="${escapeHtml(audio.codec || '')}"></label>
              <label>Audio sample rate<input data-stream-field="audio.sample_rate" ${original(audio.sample_rate)} value="${escapeHtml(audio.sample_rate || '')}"></label>
            </div>
            <div class="actions">
              <button class="primary" type="button" data-save-stream="${streamName}" data-camera-index="${index}">Save ${streamName}</button>
            </div>
          </div>
        `;
      }

      function cameraAutoconfig(index) {
        const state = cameraAuto[index] || {};
        const device = state.data?.device || {};
        const streams = state.data?.streams || {};
        const channels = Array.isArray(state.data?.channels) ? state.data.channels : [];
        const recommendations = Array.isArray(state.data?.recommendations) ? state.data.recommendations : [];
        const effective = renderEffectiveStreams(state.data?.effective_streams);
        const sectionSummary = state.data?.sections
          ? Object.entries(state.data.sections).map(([name, item]) => `${name}: ${item.ok ? 'ok' : item.error}`).join(' | ')
          : '';
        return `
          <div class="autoconfig">
            <div class="autoconfig-head">
              <div>
                <h3>Discover camera streams</h3>
                <p>${device.model ? `${escapeHtml(device.model)} ${escapeHtml(device.firmware || '')}` : 'Read camera stream profiles via ONVIF (Hikvision uses ISAPI).'}</p>
              </div>
              <button type="button" data-autoconfig="${index}" ${state.loading ? 'disabled' : ''}>${state.loading ? 'Reading...' : 'Read camera / ONVIF'}</button>
            </div>
            ${state.error ? `<p class="section-status state-offline">${escapeHtml(state.error)}</p>` : ''}
            ${state.message ? `<p class="section-status state-online">${escapeHtml(state.message)}</p>` : ''}
            ${device.serial_number ? `<p class="section-status">Serial: ${escapeHtml(device.serial_number)} ${device.device_name ? `| Name: ${escapeHtml(device.device_name)}` : ''}</p>` : ''}
            ${effective}
            ${recommendations.length ? `<div class="stream-strip">${recommendations.map((item) => `
              <div class="stream-pill">
                <b>${escapeHtml(text(item.severity, 'notice')).toUpperCase()}</b>
                <span>${escapeHtml(text(item.stream, 'all'))}</span>
                <code>${escapeHtml(text(item.message))}</code>
              </div>
            `).join('')}</div>` : ''}
            ${channels.length ? `<div class="stream-strip">${channels.map((channel) => `
              <div class="stream-pill">
                <b>ch ${escapeHtml(text(channel.id))}</b>
                <span>${escapeHtml(text(channel.video?.codec))} ${escapeHtml(text(channel.video?.width))}x${escapeHtml(text(channel.video?.height))}</span>
                <code>${escapeHtml(text(channel.video?.bitrate_mode, 'bitrate'))} ${escapeHtml(text(channel.video?.bitrate, 'unknown'))}</code>
              </div>
            `).join('')}</div>` : ''}
            ${Object.keys(streams).length ? `<div class="stream-grid">${Object.entries(streams).map(([name, stream]) => streamEditor(index, name, stream)).join('')}</div>` : ''}
            ${sectionSummary ? `<p class="section-status">${escapeHtml(sectionSummary)}</p>` : ''}
          </div>
        `;
      }

      let expandedCameraIds = new Set();
      let cameraExpansionInitialized = false;
      let cameraToExpand = '';
      function cameraForm(camera, index) {
        const prefix = `camera-${index}`;
        const cameraNumber = camera.camera_number || String(index + 1);
        const accessProtocol = camera.access_protocol || 'rtsp';
        const rtspTransport = camera.rtsp_transport || 'tcp';
        const mainChannel = camera.rtsp_main_channel || hikvisionChannelFromRtsp(camera.rtsp_main, '101');
        const subChannel = camera.rtsp_sub_channel || hikvisionChannelFromRtsp(camera.rtsp_sub, '102');
        return `
          <details class="camera-settings-card" data-camera-form data-camera-id="${escapeHtml(camera.id)}" data-index="${index}" ${expandedCameraIds.has(camera.id) ? 'open' : ''}>
            <summary><span class="camera-avatar">${index + 1}</span><span class="camera-summary"><b data-camera-summary="${index}">${escapeHtml(text(camera.name, `Camera ${index + 1}`))}</b><small>${escapeHtml(CAMERA_VENDORS[camera.vendor] || 'Camera')} · ${escapeHtml(camera.host || 'Not connected yet')}</small></span><span class="camera-summary-status">${camera.enabled ? 'Enabled' : 'Disabled'}</span></summary>
            <div class="camera-config-body">
              <fieldset class="field-section"><legend>Connection</legend><p class="field-help">Enter the local camera account, not your manufacturer's cloud login.</p><div class="form-grid"><label>Name<input name="${prefix}-name" value="${escapeHtml(text(camera.name, `Camera ${index + 1}`))}"></label><label>Vendor<select name="${prefix}-vendor">
                ${Object.entries(CAMERA_VENDORS).map(([value, label]) => option(value, camera.vendor, label)).join('')}
              </select></label><label>Host/IP<input name="${prefix}-host" value="${escapeHtml(camera.host || '')}"></label><label>Username<input name="${prefix}-username" value="${escapeHtml(camera.username || 'admin')}"></label><label>Password<input name="${prefix}-password" type="password" value="${escapeHtml(camera.password || '')}"></label></div></fieldset>
              ${cameraAutoconfig(index)}
              <fieldset class="field-section"><legend>Live & recording</legend><p class="field-help">Sub stream is lighter for live tiles. Main stream gives the best recording detail.</p><div class="form-grid"><label class="check-row"><input name="${prefix}-enabled" type="checkbox" ${camera.enabled ? 'checked' : ''}> Camera enabled</label><label class="check-row"><input name="${prefix}-record" type="checkbox" ${camera.record !== false ? 'checked' : ''}> Record continuously</label><label>Live stream<select name="${prefix}-live-stream">
                <option value="sub" ${camera.live_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.live_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label><label>Tile stream<select name="${prefix}-tile-stream">
                <option value="sub" ${camera.tile_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.tile_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label><label>Recording stream<select name="${prefix}-record-stream">
                <option value="main" ${camera.record_stream !== 'sub' ? 'selected' : ''}>main</option>
                <option value="sub" ${camera.record_stream === 'sub' ? 'selected' : ''}>sub</option>
              </select></label></div></fieldset>
              <details class="settings-group camera-advanced"><summary>Advanced connection <small>Custom RTSP, ONVIF, channels and transport</small></summary><div class="form-grid"><label>Camera / NVR channel number<input name="${prefix}-camera-number" value="${escapeHtml(cameraNumber)}"></label><label>Connection<select name="${prefix}-access-protocol">
                ${option('rtsp', accessProtocol, 'RTSP')}
                ${option('isapi', accessProtocol, 'ISAPI')}
                ${option('onvif', accessProtocol, 'ONVIF')}
                ${option('unicast', accessProtocol, 'Unicast')}
                ${option('multicast', accessProtocol, 'Multicast')}
              </select></label><label>RTSP transport<select name="${prefix}-rtsp-transport">
                ${option('tcp', rtspTransport, 'TCP stable')}
                ${option('udp', rtspTransport, 'UDP low latency')}
                ${option('auto', rtspTransport, 'Auto')}
              </select></label><label>Main channel (Hikvision)<input ${camera.vendor === 'hikvision' ? '' : 'disabled'} name="${prefix}-rtsp-main-channel" value="${escapeHtml(mainChannel)}"></label><label>Sub channel (Hikvision)<input ${camera.vendor === 'hikvision' ? '' : 'disabled'} name="${prefix}-rtsp-sub-channel" value="${escapeHtml(subChannel)}"></label><label>RTSP main<input name="${prefix}-rtsp-main" value="${escapeHtml(camera.rtsp_main || '')}"></label><label>RTSP sub<input name="${prefix}-rtsp-sub" value="${escapeHtml(camera.rtsp_sub || '')}"></label><label>ONVIF URL<input name="${prefix}-onvif" value="${escapeHtml(camera.onvif_url || '')}"></label><label>ISAPI URL<input name="${prefix}-isapi" value="${escapeHtml(camera.isapi_base_url || '')}"></label><label>Snapshot stream<select name="${prefix}-snapshot-stream">
                <option value="sub" ${camera.snapshot_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.snapshot_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label><label class="check-row"><input name="${prefix}-low-latency" type="checkbox" ${camera.low_latency !== false ? 'checked' : ''}> Low latency</label></div><div class="actions"><button type="button" data-build-rtsp="${index}">Build RTSP URLs</button></div></details>
              <div class="camera-footer"><button type="button" data-camera-dashboard="${index}">Add to Home Assistant</button><button class="danger" type="button" data-remove-camera="${index}" ${config.cameras.length <= 1 ? 'disabled' : ''}>Remove camera</button></div>
            </div>
          </details>
        `;
      }

      function renderConfig() {
        const cameras = config.cameras && config.cameras.length ? config.cameras : [
          { id: 'hikvision_1', name: 'Hikvision 1', vendor: 'hikvision', username: 'admin', camera_number: '1', access_protocol: 'rtsp', rtsp_transport: 'tcp', rtsp_main_channel: '101', rtsp_sub_channel: '102', snapshot_stream: 'sub', live_stream: 'sub', tile_stream: 'sub', record_stream: 'main', record: true, low_latency: true },
          { id: 'hikvision_2', name: 'Hikvision 2', vendor: 'hikvision', username: 'admin', camera_number: '2', access_protocol: 'rtsp', rtsp_transport: 'tcp', rtsp_main_channel: '201', rtsp_sub_channel: '202', snapshot_stream: 'sub', live_stream: 'sub', tile_stream: 'sub', record_stream: 'main', record: true, low_latency: true }
        ];
        const previousCards = Array.from(form.querySelectorAll('[data-camera-form]'));
        if (previousCards.length) expandedCameraIds = new Set(previousCards.filter((node) => node.open).map((node) => node.dataset.cameraId));
        if (!cameraExpansionInitialized && cameras[0]) { expandedCameraIds.add(cameras[0].id); cameraExpansionInitialized = true; }
        if (cameraToExpand) expandedCameraIds.add(cameraToExpand);
        config = { ...config, cameras };
        form.innerHTML = cameras.map(cameraForm).join('');
        if (cameraToExpand) {
          const card = Array.from(form.querySelectorAll('[data-camera-form]')).find((node) => node.dataset.cameraId === cameraToExpand);
          card?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          cameraToExpand = '';
        }
        renderNvrGrid({ reason: 'render_config' });
        renderPresetSlots(cameras);
        renderEdgeSettings();
        updateUnsavedState();
        updateCameraOverview();
      }

      function patchNvrCard(card, camera, index, options = {}) {
        const status = recordingStatus[index] || {};
        const video = card.querySelector('video[data-recording-player]');
        const badge = card.querySelector('.rec-badge');
        if (badge) {
          badge.textContent = status.recording ? 'RECORDING' : status.recording_status === 'blocked' ? 'BLOCKED' : status.desired_recording ? 'SCHEDULED' : 'OFF';
          badge.classList.toggle('off', !status.recording);
        }
        const action = card.querySelector('[data-record-action]');
        if (action) { action.dataset.recordAction = status.recording ? 'stop' : 'start'; action.textContent = status.recording ? 'Stop' : 'Record'; action.disabled = !status.recording && status.can_record === false; }
        const days = status.days || [];
        const shownDays = Array.from(card.querySelectorAll('[data-recording-day]')).map((node) => node.dataset.recordingDay).join(',');
        const wantedDays = days.map((day) => day.day).join(',');
        // Only the date strip needs rebuilding when a new date appears.
        if (shownDays !== wantedDays || options.reason === 'manual_refresh') {
          const template = document.createElement('template');
          template.innerHTML = nvrCard(camera, index);
          for (const selector of ['.recording-day-bar', ...(options.reason === 'manual_refresh' ? ['.recording-film-grid'] : [])]) {
            const next = template.content.querySelector(selector);
            const old = card.querySelector(selector);
            if (old && next) old.replaceWith(next);
          }
          scheduleRecordingThumbnailHydration();
        }
        card.querySelectorAll('[data-recording-day-summary]').forEach((node) => {
          const day = days.find((item) => item.day === node.dataset.recordingDaySummary);
          if (day) node.textContent = `${formatDuration(day.recorded_seconds || 0)} / ${formatDuration(day.day_total_seconds || 86400)} | 00:00–24:00`;
        });
        updateRecordingPlayerControls(video);
      }

      function renderNvrGrid(options = {}) {
        const cameras = config.cameras || [];
        const keep = new Set();
        cameras.forEach((camera, index) => {
          const status = recordingStatus[index] || {};
          const day = selectedRecordingDay[index] || status.selected_day || '';
          const existing = nvrGrid.querySelector(`[data-nvr-index="${index}"]`);
          keep.add(String(index));
          const same = existing && existing.dataset.nvrDay === day && existing.dataset.nvrKey === (status.key || '');
          const hasFiles = (status.files || []).length > 0;
          if (same && Boolean(existing.querySelector('video')) === hasFiles && options.reason !== 'render_config') {
            patchNvrCard(existing, camera, index, options);
            return;
          }
          clearRecordingContinuation(index);
          const template = document.createElement('template');
          template.innerHTML = nvrCard(camera, index);
          if (existing) { existing.querySelector('video')?.pause(); existing.replaceWith(template.content); }
          else nvrGrid.append(template.content);
        });
        nvrGrid.querySelectorAll('[data-nvr-index]').forEach((card) => {
          if (!keep.has(card.dataset.nvrIndex)) { card.querySelector('video')?.pause(); card.remove(); }
        });
        lastNvrRenderSignature = recordingStatusSignature();
        // Initialize only new players; existing nodes keep their source, position and pause state.
        applyRecordingSeek();
        scheduleRecordingThumbnailHydration();
      }

      function renderPresetSlots(cameras) {
        presetSlot.innerHTML = cameras.map((camera, index) =>
          `<option value="${index}">Camera ${index + 1}: ${escapeHtml(text(camera.name, camera.id || 'unnamed'))}</option>`
        ).join('');
      }

      function renderEdgeSettings() {
        const server = config.server || {};
        const storage = config.storage || {};
        const liveConfig = config.live || {};
        const nvrConfig = config.nvr || {};
        edgeForm.innerHTML = `
          <section class="field-section settings-main" id="recording-settings"><div class="section-heading"><span class="step-number">01</span><div><h2>Recording</h2><p>How long to keep footage and how it plays in the browser.</p></div></div><div class="form-grid"><label>Keep recordings (days)<input name="nvr-retention-days" type="number" min="1" max="365" value="${escapeHtml(text(nvrConfig.retention_days, storage.retention_days || 14))}"></label><label>Recording compatibility<select name="nvr-browser-playback">
                <option value="auto_h264" ${nvrConfig.browser_playback !== 'copy' && nvrConfig.browser_playback !== 'h264' ? 'selected' : ''}>Automatic (recommended)</option>
                <option value="copy" ${nvrConfig.browser_playback === 'copy' ? 'selected' : ''}>Keep original codec</option>
                <option value="h264" ${nvrConfig.browser_playback === 'h264' ? 'selected' : ''}>Convert to H264</option>
              </select></label></div><input type="hidden" name="storage-retention-days" value="${escapeHtml(text(nvrConfig.retention_days, storage.retention_days || 14))}"><details class="settings-group"><summary>Advanced recording <small>Segment length and playback cache</small></summary><div class="form-grid"><label>Segment seconds<input name="nvr-segment-seconds" type="number" min="2" max="300" value="${escapeHtml(text(nvrConfig.segment_seconds, 10))}"></label><label>Playback cache segments<input name="nvr-playback-cache-segments" type="number" min="12" max="50000" value="${escapeHtml(text(nvrConfig.playback_cache_segments, 10000))}"></label></div></details></section>
          <details class="settings-group" id="network-settings"><summary>Network & remote access <small>Optional for viewing outside your home network</small></summary><div class="form-grid"><label>Remote access<select name="live-remote-access-mode">
                <option value="local_only" ${text(liveConfig.remote_access_mode, 'local_only') === 'local_only' ? 'selected' : ''}>Local only / Nabu panel only</option>
                <option value="direct_public" ${liveConfig.remote_access_mode === 'direct_public' ? 'selected' : ''}>Direct public DDNS</option>
                <option value="vps_relay" ${liveConfig.remote_access_mode === 'vps_relay' ? 'selected' : ''}>VPS relay</option>
                <option value="turn_relay" ${liveConfig.remote_access_mode === 'turn_relay' ? 'selected' : ''}>TURN relay</option>
              </select></label><label>WebRTC public URL<input name="live-mobile-public-url" value="${escapeHtml(liveConfig.mobile_webrtc_public_url || '')}" placeholder="http://PUBLIC-IP:8889 or https://edge.example.com"></label><label>Mobile public hosts<input name="live-mobile-public-hosts" value="${escapeHtml(text(liveConfig.mobile_webrtc_public_hosts, 'homeassistant.local,192.168.33.17'))}" placeholder="homeassistant.local,public.example.com"></label></div><p class="field-help">For a Home Assistant dashboard card, Home Assistant handles remote access. These settings apply to the Edge live player.</p><details class="settings-group"><summary>Advanced WebRTC <small>STUN, TURN and ICE transport</small></summary><div class="form-grid"><label>STUN URL<input name="live-mobile-stun-url" value="${escapeHtml(text(liveConfig.mobile_webrtc_stun_url, 'stun:stun.l.google.com:19302'))}"></label><label>TURN URL<input name="live-mobile-turn-url" value="${escapeHtml(liveConfig.mobile_webrtc_turn_url || '')}" placeholder="turns:turn.example.com:443"></label><label>TURN username<input name="live-mobile-turn-username" value="${escapeHtml(liveConfig.mobile_webrtc_turn_username || '')}"></label><label>TURN password<input name="live-mobile-turn-password" type="password" value="${escapeHtml(liveConfig.mobile_webrtc_turn_password || '')}"></label><label>WebRTC ICE transport<select name="live-mobile-ice-transport">
                <option value="auto" ${text(liveConfig.mobile_webrtc_ice_transport, liveConfig.mobile_webrtc_tcp_only ? 'tcp' : 'auto') === 'auto' ? 'selected' : ''}>Auto UDP + TCP</option>
                <option value="udp" ${text(liveConfig.mobile_webrtc_ice_transport, liveConfig.mobile_webrtc_tcp_only ? 'tcp' : 'auto') === 'udp' ? 'selected' : ''}>UDP only</option>
                <option value="tcp" ${text(liveConfig.mobile_webrtc_ice_transport, liveConfig.mobile_webrtc_tcp_only ? 'tcp' : 'auto') === 'tcp' ? 'selected' : ''}>TCP only</option>
              </select></label></div></details></details>
          <details class="settings-group" id="storage-settings"><summary>Storage locations <small>Keep the defaults unless you use a custom disk layout</small></summary><div class="form-grid"><label>Recordings directory<input name="storage-recordings-dir" value="${escapeHtml(text(storage.recordings_dir, '/media/edge-of-infinity/recordings'))}"></label><label>Database path<input name="storage-database-path" value="${escapeHtml(text(storage.database_path, '/homeassistant/edge/edge.db'))}"></label></div></details>
          <details class="settings-group" id="advanced-settings"><summary>Advanced system settings <small>Playback performance, streaming engine and server</small></summary><h3>Performance</h3><div class="form-grid"><label>Tile FPS<input name="live-tile-fps" type="number" min="1" max="10" value="${escapeHtml(text(liveConfig.tile_fps, 5))}"></label><label>Tile max width<input name="live-tile-max-width" type="number" min="320" max="1920" value="${escapeHtml(text(liveConfig.tile_max_width, 960))}"></label><label class="check-row"><input name="live-prebuffer-enabled" type="checkbox" ${liveConfig.prebuffer_enabled !== false ? 'checked' : ''}> Keep selected low-latency streams warm</label><label class="check-row"><input name="live-always-on-enabled" type="checkbox" ${liveConfig.always_on_enabled !== false ? 'checked' : ''}> Keep live paths warm after boot</label><label>Always-on scope<select name="live-always-on-stream-scope">
                <option value="tile" ${text(liveConfig.always_on_stream_scope, 'tile') === 'tile' ? 'selected' : ''}>Tile preview only</option>
                <option value="live" ${liveConfig.always_on_stream_scope === 'live' ? 'selected' : ''}>Live stream only</option>
                <option value="tile_live" ${liveConfig.always_on_stream_scope === 'tile_live' ? 'selected' : ''}>Tile + live</option>
                <option value="all" ${liveConfig.always_on_stream_scope === 'all' ? 'selected' : ''}>All camera streams</option>
              </select></label><label>Local prebuffer ms<input name="live-prebuffer-local-ms" type="number" min="0" max="10000" value="${escapeHtml(text(liveConfig.prebuffer_local_ms, 4000))}"></label><label>Remote prebuffer ms<input name="live-prebuffer-remote-ms" type="number" min="0" max="10000" value="${escapeHtml(text(liveConfig.prebuffer_remote_ms, 2000))}"></label></div><h3>Engine & server</h3><div class="form-grid"><label>Engine<select name="live-engine">
                <option value="janus_webrtc" ${liveConfig.engine === 'janus_webrtc' ? 'selected' : ''}>Janus WebRTC core</option>
                <option value="mediamtx" ${liveConfig.engine === 'mediamtx' ? 'selected' : ''}>MediaMTX WHEP core</option>
                <option value="ll_hls" ${liveConfig.engine === 'll_hls' ? 'selected' : ''}>LL-HLS experimental</option>
                <option value="srt" ${liveConfig.engine === 'srt' ? 'selected' : ''}>SRT relay</option>
              </select></label><label>Listen address<input name="server-listen" value="${escapeHtml(text(server.listen, '0.0.0.0:8088'))}"></label><label>Public URL<input name="server-public-url" value="${escapeHtml(server.public_url || '')}" placeholder="Optional external URL"></label><label>Frame interval ms<input name="live-frame-interval-ms" type="number" min="250" max="10000" value="${escapeHtml(text(liveConfig.frame_interval_ms, 1200))}" disabled></label></div><p class="field-help">The add-on's API port is configured in Home Assistant. Engine and listener changes may require restarting the add-on.</p></details>
        `;
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

      function rtspWithHikvisionChannel(value, channel) {
        if (!value || !channel) return value || '';
        if (!value.includes('/Streaming/Channels/')) return value;
        return value.replace(/\/Streaming\/Channels\/\d+/, `/Streaming/Channels/${channel}`);
      }

      function rtspParts(value) {
        try {
          const parsed = new URL(value || '');
          const match = String(parsed.pathname || '').match(/\/Streaming\/Channels\/(\d+)/);
          return {
            host: parsed.hostname || '',
            username: decodeURIComponent(parsed.username || ''),
            password: decodeURIComponent(parsed.password || ''),
            channel: match ? match[1] : '',
            hikvision: String(parsed.pathname || value || '').includes('/Streaming/Channels/')
          };
        } catch (_) {
          return { host: '', username: '', password: '', channel: '', hikvision: false };
        }
      }

      function buildHikvisionRtsp(host, username, password, channel) {
        return host && username && password
          ? `rtsp://${encodeURIComponent(username)}:${encodeURIComponent(password)}@${host}:554/Streaming/Channels/${channel}`
          : '';
      }

      function refreshHikvisionRtsp(value, host, username, password, channel) {
        const rebuilt = buildHikvisionRtsp(host, username, password, channel);
        if (rebuilt) return rebuilt;
        const parts = rtspParts(value);
        if (!value) return '';
        if (!parts.hikvision) return value;
        const mismatch = (host && parts.host && parts.host !== host)
          || (username && parts.username && parts.username !== username)
          || (password && parts.password && parts.password !== password)
          || (channel && parts.channel && parts.channel !== channel);
        if (mismatch) return rebuilt || rtspWithHikvisionChannel(value, channel);
        return rtspWithHikvisionChannel(value, channel);
      }

      function refreshHikvisionOnvifUrl(value, host) {
        if (!host) return value || '';
        if (!value) return `http://${host}:80/onvif/device_service`;
        try {
          const parsed = new URL(value);
          if (!['http:', 'https:'].includes(parsed.protocol) || parsed.hostname !== host || parsed.pathname !== '/onvif/device_service') {
            return `http://${host}:80/onvif/device_service`;
          }
          return value;
        } catch (_) {
          return `http://${host}:80/onvif/device_service`;
        }
      }

      function refreshHikvisionIsapiBaseUrl(value, host) {
        if (!host) return value || '';
        if (!value) return `http://${host}`;
        try {
          const parsed = new URL(value);
          if (!['http:', 'https:'].includes(parsed.protocol) || parsed.hostname !== host || parsed.pathname.includes('/ISAPI/') || parsed.pathname.includes('/Streaming/')) {
            return `http://${host}`;
          }
          return value.replace(/\/+$/, '');
        } catch (_) {
          return `http://${host}`;
        }
      }

      function hikvisionChannelFromRtsp(value, fallback = '102') {
        const match = String(value || '').match(/\/Streaming\/Channels\/(\d+)/);
        return match ? match[1] : fallback;
      }

      function cameraField(section, index, name) {
        const prefix = `camera-${index}`;
        const element = section.querySelector(`[name="${prefix}-${name}"]`);
        if (!element) {
          throw new Error(`Missing camera field: ${prefix}-${name}`);
        }
        return element;
      }

      function refreshVendorRtsp(value, vendor, host, username, password, number, stream) {
        let path = CAMERA_TEMPLATES[vendor]?.[stream] || '';
        if (vendor === 'reolink') path = `/h264Preview_${String(Number(number) || 1).padStart(2, '0')}_${stream}`;
        if (['dahua', 'amcrest'].includes(vendor)) path = `/cam/realmonitor?channel=${Number(number) || 1}&subtype=${stream === 'sub' ? 1 : 0}`;
        if (!value && !path) return '';
        try {
          const parsed = new URL(value || `rtsp://camera${path}`);
          const target = new URL(`rtsp://${host}`);
          parsed.hostname = target.hostname;
          parsed.port = target.port || parsed.port || '554';
          parsed.username = username; parsed.password = password;
          return parsed.href;
        } catch (_) { return value || ''; }
      }

      function refreshGeneratedCameraFields(section, index) {
        const vendor = cameraField(section, index, 'vendor').value;
        if (vendor !== 'hikvision') {
          for (const stream of ['main', 'sub']) {
            const input = cameraField(section, index, `rtsp-${stream}`);
            input.value = refreshVendorRtsp(input.value.trim(), vendor, cameraField(section, index, 'host').value.trim(), cameraField(section, index, 'username').value.trim(), cameraField(section, index, 'password').value, cameraField(section, index, 'camera-number').value, stream);
          }
          return;
        }
        const host = cameraField(section, index, 'host').value.trim();
        const username = cameraField(section, index, 'username').value.trim();
        const password = cameraField(section, index, 'password').value;
        const cameraNumber = cameraField(section, index, 'camera-number').value.trim() || String(index + 1);
        const mainChannel = cameraField(section, index, 'rtsp-main-channel').value.trim() || `${cameraNumber}01`;
        const subChannel = cameraField(section, index, 'rtsp-sub-channel').value.trim() || `${cameraNumber}02`;
        const mainInput = cameraField(section, index, 'rtsp-main');
        const subInput = cameraField(section, index, 'rtsp-sub');
        mainInput.value = refreshHikvisionRtsp(mainInput.value.trim(), host, username, password, mainChannel);
        subInput.value = refreshHikvisionRtsp(subInput.value.trim(), host, username, password, subChannel);
        const onvifInput = cameraField(section, index, 'onvif');
        const isapiInput = cameraField(section, index, 'isapi');
        onvifInput.value = refreshHikvisionOnvifUrl(onvifInput.value.trim(), host);
        isapiInput.value = refreshHikvisionIsapiBaseUrl(isapiInput.value.trim(), host);
      }

      function cameraFormSnapshot() {
        return Array.from(form.querySelectorAll('[data-camera-form]')).map((section, index) => {
          const prefix = `camera-${index}`;
          const field = (name) => section.querySelector(`[name="${prefix}-${name}"]`);
          const value = (name) => {
            const element = field(name);
            if (!element) return '';
            if (element.type === 'checkbox') return element.checked;
            return element.value;
          };
          return {
            index,
            id: Array.isArray(config.cameras) ? config.cameras[index]?.id : '',
            name: value('name'),
            vendor: value('vendor'),
            host: value('host'),
            username: value('username'),
            password: value('password') ? 'set' : '',
            camera_number: value('camera-number'),
            access_protocol: value('access-protocol'),
            rtsp_transport: value('rtsp-transport'),
            rtsp_main_channel: value('rtsp-main-channel'),
            rtsp_sub_channel: value('rtsp-sub-channel'),
            rtsp_main: value('rtsp-main'),
            rtsp_sub: value('rtsp-sub'),
            enabled: value('enabled'),
            record: value('record'),
            low_latency: value('low-latency'),
            snapshot_stream: value('snapshot-stream'),
            live_stream: value('live-stream'),
            tile_stream: value('tile-stream'),
            record_stream: value('record-stream')
          };
        });
      }

      function edgeFormSnapshot() {
        const snapshot = {};
        Array.from(edgeForm.elements || []).forEach((element) => {
          if (!element.name) return;
          snapshot[element.name] = element.type === 'checkbox' ? element.checked : element.value;
        });
        return snapshot;
      }

      function collectConfig(options = {}) {
        const refreshGenerated = options.refreshGenerated !== false;
        const existingCameras = Array.isArray(config.cameras) ? config.cameras : [];
        const cameras = Array.from(form.querySelectorAll('[data-camera-form]')).map((section, index) => {
          if (refreshGenerated) refreshGeneratedCameraFields(section, index);
          const get = (name) => cameraField(section, index, name);
          const vendor = get('vendor').value;
          const cameraNumber = get('camera-number').value.trim() || '1';
          const defaultMainChannel = `${cameraNumber}01`;
          const defaultSubChannel = `${cameraNumber}02`;
          const mainInput = get('rtsp-main-channel').value.trim();
          const subInput = get('rtsp-sub-channel').value.trim();
          const rtspMainChannel = vendor === 'hikvision'
            ? (mainInput || defaultMainChannel)
            : '';
          const rtspSubChannel = vendor === 'hikvision'
            ? (subInput || defaultSubChannel)
            : '';
          const rtspMainRaw = get('rtsp-main').value.trim();
          const rtspSubRaw = get('rtsp-sub').value.trim();
          const host = get('host').value.trim();
          const username = get('username').value.trim();
          const password = get('password').value;
          const rtspMain = vendor === 'hikvision' ? refreshHikvisionRtsp(rtspMainRaw, host, username, password, rtspMainChannel) : refreshVendorRtsp(rtspMainRaw, vendor, host, username, password, cameraNumber, 'main');
          const rtspSub = vendor === 'hikvision' ? refreshHikvisionRtsp(rtspSubRaw, host, username, password, rtspSubChannel) : refreshVendorRtsp(rtspSubRaw, vendor, host, username, password, cameraNumber, 'sub');
          return {
            ...existingCameras[index],
            id: existingCameras[index]?.id || `${get('vendor').value}_${index + 1}`,
            name: get('name').value,
            vendor,
            host,
            username,
            password,
            rtsp_main: rtspMain,
            rtsp_sub: rtspSub,
            camera_number: cameraNumber,
            access_protocol: get('access-protocol').value,
            rtsp_transport: get('rtsp-transport').value,
            rtsp_main_channel: rtspMainChannel,
            rtsp_sub_channel: rtspSubChannel,
            onvif_url: get('onvif').value.trim(),
            isapi_base_url: get('isapi').value.trim(),
            enabled: get('enabled').checked,
            record: get('record').checked,
            low_latency: get('low-latency').checked,
            snapshot_stream: get('snapshot-stream').value,
            live_stream: get('live-stream').value,
            tile_stream: get('tile-stream').value,
            record_stream: get('record-stream').value
          };
        });
        return { ...config, cameras };
      }

      function syncConfigDraftFromForm() {
        try {
          config = collectEdgeSettings({ refreshGenerated: false });
          lastFormDraft = config;
          lastFormDraftAt = Date.now();
          configDirty = true;
          form.querySelectorAll('[data-camera-form]').forEach((node, index) => {
            const camera = config.cameras[index];
            if (!camera) return;
            node.querySelector('[data-camera-summary]').textContent = camera.name || `Camera ${index + 1}`;
            node.querySelector('.camera-summary small').textContent = `${CAMERA_VENDORS[camera.vendor] || 'Camera'} · ${camera.host || 'Not connected yet'}`;
            node.querySelector('.camera-summary-status').textContent = camera.enabled ? 'Enabled' : 'Disabled';
          });
          updateUnsavedState();
        } catch (error) {
          debugEvent('ui_config_draft_sync_error', { message: error.message });
        }
      }

      function newCamera(index) {
        const cameraNumber = String(index + 1);
        return {
          id: `hikvision_${index + 1}`,
          name: `Hikvision ${index + 1}`,
          vendor: 'hikvision',
          username: 'admin',
          camera_number: cameraNumber,
          access_protocol: 'rtsp',
          rtsp_transport: 'tcp',
          rtsp_main_channel: `${cameraNumber}01`,
          rtsp_sub_channel: `${cameraNumber}02`,
          snapshot_stream: 'sub',
          live_stream: 'sub',
          tile_stream: 'sub',
          record_stream: 'main',
          enabled: false,
          record: true,
          low_latency: true
        };
      }

      function addCamera() {
        config = collectConfig();
        if (config.cameras.length >= 8) {
          saveState.textContent = 'Maximum 8 cameras are supported in this panel for now.';
          return;
        }
        const added = newCamera(config.cameras.length);
        config.cameras = [...config.cameras, added];
        cameraToExpand = added.id;
        configDirty = true;
        renderConfig();
        saveState.textContent = 'Camera added. Fill connection details and click Save cameras.';
      }

      function removeCamera(index) {
        config = collectConfig();
        if (config.cameras.length <= 1) {
          saveState.textContent = 'At least one camera slot must remain.';
          return;
        }
        config.cameras = config.cameras.filter((_, currentIndex) => currentIndex !== index);
        configDirty = true;
        renderConfig();
        saveState.textContent = 'Camera removed from the form. Click Save cameras to write the change.';
      }

      function buildRtspForCamera(index) {
        const prefix = `camera-${index}`;
        const get = (name) => form.elements[`${prefix}-${name}`];
        const vendor = get('vendor').value;
        const host = get('host').value.trim();
        const username = get('username').value.trim();
        const password = get('password').value;
        const cameraNumber = get('camera-number').value.trim() || '1';
        const mainChannel = `${cameraNumber}01`;
        const subChannel = `${cameraNumber}02`;
        if (!host || !username || !password) {
          saveState.textContent = 'Host, username, and password are required to build RTSP URLs.';
          return;
        }
        configDirty = true;
        updateUnsavedState();
        if (vendor === 'hikvision') {
          get('rtsp-main-channel').value = mainChannel;
          get('rtsp-sub-channel').value = subChannel;
          get('rtsp-main').value = buildHikvisionRtsp(host, username, password, mainChannel);
          get('rtsp-sub').value = buildHikvisionRtsp(host, username, password, subChannel);
          get('onvif').value = get('onvif').value || `http://${host}:80/onvif/device_service`;
          get('isapi').value = get('isapi').value || `http://${host}`;
          saveState.textContent = 'Hikvision RTSP URLs prepared. Click Save cameras to write them.';
          return;
        }
        if (vendor === 'dahua') {
          get('rtsp-main').value = refreshVendorRtsp('', vendor, host, username, password, cameraNumber, 'main');
          get('rtsp-sub').value = refreshVendorRtsp('', vendor, host, username, password, cameraNumber, 'sub');
          get('onvif').value = get('onvif').value || `http://${host}:80/onvif/device_service`;
          saveState.textContent = 'Dahua RTSP URLs prepared. Click Save cameras to write them.';
          return;
        }
        for (const stream of ['main', 'sub']) get(`rtsp-${stream}`).value = refreshVendorRtsp('', vendor, host, username, password, cameraNumber, stream);
        get('onvif').value = get('onvif').value || `http://${host}:${vendor === 'reolink' ? 8000 : 80}/onvif/device_service`;
        saveState.textContent = CAMERA_TEMPLATES[vendor]?.main ? 'RTSP profile prepared. Save cameras to apply. Use ONVIF discovery for models with different paths.' : 'Use Read camera / ONVIF discovery or enter explicit RTSP URLs, then save.';
      }

      function collectEdgeSettings(options = {}) {
        const get = (name) => edgeForm.elements[name];
        const cameraConfig = collectConfig(options);
        return {
          ...cameraConfig,
          server: {
            listen: get('server-listen').value.trim() || '0.0.0.0:8088',
            public_url: get('server-public-url').value.trim()
          },
          storage: {
            recordings_dir: get('storage-recordings-dir').value.trim() || '/media/edge-of-infinity/recordings',
            database_path: get('storage-database-path').value.trim() || '/homeassistant/edge/edge.db',
            retention_days: Number(get('nvr-retention-days').value || 14)
          },
          live: {
            engine: get('live-engine').value,
            remote_access_mode: get('live-remote-access-mode').value,
            frame_interval_ms: Number(get('live-frame-interval-ms').value || 1200),
            tile_fps: Number(get('live-tile-fps').value || 5),
            tile_max_width: Number(get('live-tile-max-width').value || 960),
            prebuffer_enabled: get('live-prebuffer-enabled').checked,
            always_on_enabled: get('live-always-on-enabled').checked,
            always_on_stream_scope: get('live-always-on-stream-scope').value,
            prebuffer_local_ms: Number(get('live-prebuffer-local-ms').value || 4000),
            prebuffer_remote_ms: Number(get('live-prebuffer-remote-ms').value || 2000),
            mobile_webrtc_public_url: get('live-mobile-public-url').value.trim(),
            mobile_webrtc_public_hosts: get('live-mobile-public-hosts').value.trim(),
            mobile_webrtc_stun_url: get('live-mobile-stun-url').value.trim(),
            mobile_webrtc_turn_url: get('live-mobile-turn-url').value.trim(),
            mobile_webrtc_turn_username: get('live-mobile-turn-username').value.trim(),
            mobile_webrtc_turn_password: get('live-mobile-turn-password').value,
            mobile_webrtc_ice_transport: get('live-mobile-ice-transport').value,
            mobile_webrtc_tcp_only: get('live-mobile-ice-transport').value === 'tcp'
          },
          nvr: {
            segment_seconds: Number(get('nvr-segment-seconds').value || 10),
            retention_days: Number(get('nvr-retention-days').value || get('storage-retention-days').value || 14),
            playback_cache_segments: Number(get('nvr-playback-cache-segments').value || 10000),
            browser_playback: get('nvr-browser-playback').value
          }
        };
      }

      function applyPresetToSlot() {
        const preset = presets[Number(presetSelect.value)];
        const slot = Number(presetSlot.value);
        if (!preset || Number.isNaN(slot)) return;
        config = collectConfig();
        const current = config.cameras && config.cameras.length ? [...config.cameras] : [newCamera(0)];
        current[slot] = {
          ...preset,
          id: current[slot]?.id || `hikvision_${slot + 1}`,
          name: preset.name || current[slot]?.name || `Hikvision ${slot + 1}`
        };
        config = { ...config, cameras: current };
        cameraToExpand = current[slot].id;
        configDirty = true;
        renderConfig();
        saveState.textContent = 'Preset loaded into the form. Click Save cameras to write it.';
      }

      function hasMeaningfulCameras(payload) {
        return Array.isArray(payload.cameras) && payload.cameras.some((camera) =>
          camera && (camera.host || camera.rtsp_main)
        );
      }

      function saveSummary(payload) {
        const cameras = Array.isArray(payload.cameras) ? payload.cameras : [];
        const parts = cameras.map((camera) => {
          const name = text(camera.name, camera.id || 'camera');
          const host = text(camera.host, 'missing-host');
          const mainChannel = hikvisionChannelFromRtsp(camera.rtsp_main, '101');
          const subChannel = hikvisionChannelFromRtsp(camera.rtsp_sub, '102');
          return `${name}: host=${host}, tile=${text(camera.tile_stream, 'sub')}, live=${text(camera.live_stream, 'sub')}, record=${text(camera.record_stream, 'main')}, snapshot=${text(camera.snapshot_stream, 'sub')}, main ch=${mainChannel}, sub ch=${subChannel}`;
        });
        return parts.length ? parts.join(' | ') : 'no cameras';
      }

      async function loadConfig() {
        const response = await fetch(panelPath('api/config'), { cache: 'no-store' });
        if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Could not load configuration.'); }
        config = await response.json();
        debugEvent('ui_config_loaded', { summary: saveSummary(config) });
        configDirty = false;
        renderConfig();
      }

      async function fetchSavedConfig() {
        const response = await fetch(panelPath('api/config'), { cache: 'no-store' });
        if (!response.ok) {
          throw new Error(`Could not verify saved config: ${response.status}`);
        }
        return response.json();
      }

      async function loadCameras() {
        const response = await fetch(panelPath('cameras.json'), { cache: 'no-store' });
        const data = await response.json();
        const cameras = Array.isArray(data.cameras) ? data.cameras : [];
        overviewCameras = cameras;
        updateCameraOverview();
        debugEvent('ui_cameras_loaded', {
          count: cameras.length,
          cameras: cameras.map((camera) => ({
            id: camera.id,
            key: camera.key,
            status: camera.status,
            live_stream: camera.live_stream,
            tile_stream: camera.tile_stream,
            live_probe_status: camera.live_probe_status,
            video_codec: camera.video_codec || camera.codec,
            audio_codec: camera.audio_codec,
            width: camera.live_width || camera.width,
            height: camera.live_height || camera.height,
            fps: camera.live_fps || camera.fps,
            bitrate: camera.live_bitrate || camera.bitrate
          }))
        });
        cameras.forEach((camera) => {
          const liveKey = camera.key || `${camera.id || 'camera'}_${camera.index ?? 0}`;
          if (camera.status === 'online' && live[liveKey] === undefined) {
            live[liveKey] = true;
          }
        });
        grid.innerHTML = cameras.length
          ? `${remoteLiveNotice()}${cameras.map(cameraCard).join('')}${addCameraTile()}`
          : `${remoteLiveNotice()}${addCameraTile()}`;
        monitorLiveFrames();
      }

      let pendingNvrRefresh = null;
      async function loadRecordingStatus(options = {}) {
        if (nvrLoading) { if (options.forceRender) pendingNvrRefresh = options; return; }
        nvrLoading = true;
        try {
          const requestedDays = JSON.stringify(selectedRecordingDay);
          const dayQuery = Object.entries(selectedRecordingDay)
            .filter(([, day]) => day)
            .map(([index, day]) => `${encodeURIComponent(index)}:${encodeURIComponent(day)}`)
            .join(',');
          const statusPath = dayQuery ? `api/recording/status?days=${dayQuery}` : 'api/recording/status';
          const response = await fetch(panelPath(statusPath), { cache: 'no-store' });
          if (!response.ok) throw new Error(`Recording status failed: ${response.status}`);
          const data = await response.json();
          if (requestedDays !== JSON.stringify(selectedRecordingDay)) { pendingNvrRefresh = { forceRender: true, reason: 'day_selection_pending' }; return; }
          const shouldLogStatus = options.reason !== 'timer' || Date.now() - lastRecordingStatusLogAt > NVR_TIMER_LOG_THROTTLE_MS;
          if (shouldLogStatus) {
            lastRecordingStatusLogAt = Date.now();
            debugEvent('ui_recording_status_loaded', {
              reason: options.reason || 'recording_status',
              count: Array.isArray(data.cameras) ? data.cameras.length : 0,
              cameras: (Array.isArray(data.cameras) ? data.cameras : []).map((item) => ({
                index: item.index,
                id: item.id,
                recording: item.recording,
                desired_recording: item.desired_recording,
                recording_status: item.recording_status,
                segments: item.segments,
                record_stream: item.record_stream,
                record_error: item.record_error || item.last_error || '',
                selected_day: item.selected_day || '',
                playback_cache: item.playback_cache || {}
              }))
            });
          }
          recordingStatus = {};
          (Array.isArray(data.cameras) ? data.cameras : []).forEach((item) => {
            recordingStatus[item.index] = item;
            const knownDays = Array.isArray(item.days) ? item.days.map((day) => day.day).filter(Boolean) : [];
            const pinnedDay = selectedRecordingDay[item.index];
            if (item.selected_day && (!pinnedDay || (knownDays.length && !knownDays.includes(pinnedDay)))) {
              selectedRecordingDay[item.index] = item.selected_day;
            }
            const files = Array.isArray(item.files) ? item.files : [];
            const selected = selectedRecording[item.index];
            if (files.length && !files.some((file) => file.url === selected)) {
              const selection = recordingDefaultTarget(files, item);
              selectedRecording[item.index] = selection?.target?.file?.url || files[0].url;
              selectedRecordingSeek[item.index] = selection?.seek || 0;
              selectedRecordingTimeline[item.index] = selection?.requested || 0;
              if (!Number.isFinite(Number(recordingStreamStartOffset[item.index]))) {
                recordingStreamStartOffset[item.index] = selection?.requested || 0;
              }
            }
            if (!files.length) {
              delete selectedRecording[item.index];
              delete selectedRecordingSeek[item.index];
              delete selectedRecordingTimeline[item.index];
              delete recordingStreamStartOffset[item.index];
              delete recordingStreamNonce[item.index];
            }
          });
          updateCameraOverview();
          const nextSignature = recordingStatusSignature();
          const protectedPlayback = isNvrPlaybackProtected();
          const shouldRender = Boolean(options.forceRender)
            || activePage !== 'nvr'
            || !lastNvrRenderSignature
            || (nextSignature !== lastNvrRenderSignature && !protectedPlayback);
          if (shouldRender) {
            renderNvrGrid({ reason: options.reason || 'recording_status' });
          } else {
            renderNvrGrid({ reason: options.reason || 'recording_status' });
            if (options.reason !== 'timer' || Date.now() - lastNvrRenderSkipLogAt > NVR_TIMER_LOG_THROTTLE_MS) {
              lastNvrRenderSkipLogAt = Date.now();
              debugEvent('ui_recording_status_render_skipped', {
                reason: options.reason || 'recording_status',
                signature_changed: nextSignature !== lastNvrRenderSignature,
                active_playback: isNvrPlaybackBusy(),
                protected_playback: protectedPlayback,
              });
            }
          }
        } finally {
          nvrLoading = false;
          if (pendingNvrRefresh) { const pending = pendingNvrRefresh; pendingNvrRefresh = null; await loadRecordingStatus(pending); }
        }
      }

      function moveRecording(index, direction) {
        markNvrInteraction('move_recording', { index, direction });
        const files = Array.isArray(recordingStatus[index]?.files) ? recordingStatus[index].files : [];
        if (!files.length) return;
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        const wasPlaying = Boolean(video && !video.paused && !video.ended);
        const current = Math.max(0, files.findIndex((file) => file.url === selectedRecording[index]));
        const next = direction === 'older'
          ? Math.min(files.length - 1, current + 1)
          : Math.max(0, current - 1);
        recordingAutoplayAfterRender[index] = wasPlaying;
        selectRecordingAtOffset(index, offsetForRecordingUrl(index, files[next].url));
      }

      function streamSettingsFromEditor(editor) {
        const settings = { video: {}, audio: {} };
        editor.querySelectorAll('[data-stream-field]').forEach((field) => {
          const path = field.dataset.streamField.split('.');
          const value = field.value.trim();
          if (!value || value === (field.dataset.original || '')) return;
          settings[path[0]][path[1]] = value;
        });
        return settings;
      }

      async function loadCameraAutoconfig(index) {
        config = collectEdgeSettings();
        const camera = config.cameras[index];
        if (!camera) return;
        cameraAuto[index] = { loading: true };
        renderConfig();
        try {
          const response = await fetch(panelPath('api/camera-autoconfig'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index, camera })
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not read camera configuration.');
          if (data.camera) { config.cameras[index] = { ...camera, ...data.camera }; configDirty = true; }
          cameraAuto[index] = data.error
            ? { data, error: data.error }
            : { data, message: 'Camera configuration loaded.' };
        } catch (error) {
          cameraAuto[index] = { error: error.message };
        }
        renderConfig();
      }

      async function saveCameraStream(index, streamName, editor) {
        config = collectConfig();
        const camera = config.cameras[index];
        if (!camera) return;
        const previous = cameraAuto[index]?.data || null;
        cameraAuto[index] = { data: previous, message: `Saving ${streamName} stream...` };
        renderConfig();
        try {
          const response = await fetch(panelPath('api/camera-stream-config'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index, camera, stream: streamName, settings: streamSettingsFromEditor(editor) })
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not save stream configuration.');
          const next = previous || { streams: {}, device: {}, sections: {} };
          next.streams = { ...(next.streams || {}), [streamName]: data.stream };
          cameraAuto[index] = { data: next, message: `${streamName} stream saved.` };
          await fetch(panelPath('api/refresh'), { method: 'POST' });
          await loadCameras();
        } catch (error) {
          cameraAuto[index] = { data: previous, error: error.message };
        }
        renderConfig();
      }

      function showPage(pageName) {
        activePage = pageName;
        document.querySelectorAll('[data-page]').forEach((page) => {
          page.hidden = page.dataset.page !== pageName;
        });
        document.querySelectorAll('[data-page-target]').forEach((button) => {
          button.classList.toggle('active', button.dataset.pageTarget === pageName);
        });
        debugEvent('ui_page_change', { page: pageName });
        if (pageName === 'ha-dashboard') loadDashboardEntities();
        if (pageName === 'home') loadCameras().catch((error) => debugEvent('ui_cameras_refresh_error', { message: error.message }));
        if (pageName === 'logs') {
          loadLogs().catch((error) => {
            logsView.innerHTML = `<p class="notice">${escapeHtml(error.message)}</p>`;
          });
        }
        updateNvrTimer();
      }

      function updateLiveTimer() {
        if (liveTimer) {
          window.clearInterval(liveTimer);
          liveTimer = null;
        }
      }

      function updateNvrTimer() {
        if (nvrTimer) {
          window.clearInterval(nvrTimer);
          nvrTimer = null;
        }
        if (activePage !== 'nvr') return;
        nvrTimer = window.setInterval(() => {
          loadRecordingStatus({ reason: 'timer' }).catch((error) => {
            debugEvent('ui_recording_status_error', { message: error.message });
          });
        }, NVR_STATUS_REFRESH_MS);
      }

      document.querySelectorAll('[data-page-target]').forEach((button) => {
        button.addEventListener('click', () => {
          showPage(button.dataset.pageTarget);
          if (window.matchMedia('(max-width: 820px)').matches && !button.closest('.toolbar')) {
            setNavCollapsed(true);
          }
        });
      });

      menuToggle.addEventListener('click', () => {
        setNavCollapsed(!document.body.classList.contains('nav-collapsed'));
      });

      window.addEventListener('resize', () => {
        if (window.matchMedia('(max-width: 820px)').matches) return;
        let saved = null;
        try {
          saved = window.localStorage.getItem('edge-nav-collapsed');
        } catch (_) {}
        if (saved === null) {
          setNavCollapsed(false, false);
        }
      });

      document.getElementById('refresh').addEventListener('click', async () => {
        debugEvent('ui_refresh_status_click');
        await fetch(panelPath('api/refresh'), { method: 'POST' });
        await loadCameras();
      });

      document.getElementById('refresh-nvr').addEventListener('click', async () => {
        debugEvent('ui_refresh_nvr_click');
        await loadRecordingStatus({ forceRender: true, reason: 'manual_refresh' });
      });

      document.getElementById('refresh-logs').addEventListener('click', async () => {
        debugEvent('ui_refresh_logs_click');
        await loadLogs();
      });

      document.getElementById('add-camera').addEventListener('click', () => {
        debugEvent('ui_add_camera_click');
        addCamera();
      });

      let configSaveInFlight = false;
      function canonicalConfig(value) {
        if (Array.isArray(value)) return value.map(canonicalConfig);
        if (value && typeof value === 'object') {
          return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalConfig(value[key])]));
        }
        return value;
      }

      async function saveAllSettings(state, reason) {
        if (configSaveInFlight) return;
        const buttons = ['save-config', 'save-edge-settings'].map((id) => document.getElementById(id));
        configSaveInFlight = true;
        [form, edgeForm].forEach((element) => { element.inert = true; });
        buttons.forEach((button) => { button.disabled = true; });
        try {
          const payload = collectEdgeSettings({ refreshGenerated: true });
          if (!hasMeaningfulCameras(payload)) throw new Error('At least one camera needs a host/IP or RTSP URL.');
          state.textContent = 'Saving all current camera and Edge settings...';
          const response = await fetch(panelPath('api/config'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
          const data = await response.json();
          const addonSync = response.headers.get("X-Edge-Options-Sync");
          if (!response.ok) throw new Error(data.error || 'Could not save configuration.');
          const verified = await fetchSavedConfig();
          if (JSON.stringify(canonicalConfig(data)) !== JSON.stringify(canonicalConfig(verified))) {
            throw new Error('Saved settings changed during verification. Your form has been kept; reload before retrying.');
          }
          config = verified;
          lastFormDraft = verified;
          lastFormDraftAt = Date.now();
          configDirty = false;
          renderConfig();
          state.textContent = `Saved and verified. ${saveSummary(verified)} ${addonSync === 'synced' ? 'Home Assistant add-on options updated.' : 'Add-on options could not be synchronized; saved camera configuration is active.'}`;
          debugEvent(`ui_${reason}_done`, { savedSummary: saveSummary(verified) });
          if (liveTimer) { window.clearInterval(liveTimer); liveTimer = null; updateLiveTimer(); }
          // Auxiliary refresh errors must not be presented as a failed settings save.
          await Promise.allSettled([loadPresets(), loadCameras(), ...(panelLogs ? [loadLogs()] : [])]);
        } catch (error) {
          state.textContent = error.message || 'Could not save configuration. Your form has been kept.';
          debugEvent(`ui_${reason}_error`, { message: error.message });
        } finally {
          configSaveInFlight = false;
          [form, edgeForm].forEach((element) => { element.inert = false; });
          buttons.forEach((button) => { button.disabled = false; });
        }
      }

      document.getElementById('save-config').addEventListener('click', async () => {
        debugEvent('ui_save_config_click', { form: cameraFormSnapshot() });
        await saveAllSettings(saveState, 'save_config');
      });

      form.addEventListener('submit', (event) => {
        event.preventDefault();
        document.getElementById('save-config').click();
      });

      document.getElementById('save-edge-settings').addEventListener('click', async () => {
        debugEvent('ui_save_edge_settings_click', { form: edgeFormSnapshot() });
        await saveAllSettings(edgeSaveState, 'save_edge_settings');
      });

      document.getElementById('apply-preset').addEventListener('click', applyPresetToSlot);

      edgeForm.addEventListener('input', syncConfigDraftFromForm);
      edgeForm.addEventListener('change', syncConfigDraftFromForm);

      form.addEventListener('input', () => {
        syncConfigDraftFromForm();
      });
      form.addEventListener('change', (event) => {
        const match = event.target.name?.match(/^camera-(\d+)-vendor$/);
        if (match) {
          const index = Number(match[1]);
          for (const field of ['rtsp-main', 'rtsp-sub', 'onvif', 'isapi']) form.elements[`camera-${index}-${field}`].value = '';
          buildRtspForCamera(index);
        }
        syncConfigDraftFromForm();
      });

      form.addEventListener('click', async (event) => {
        const dashboard = event.target.closest('[data-camera-dashboard]');
        if (dashboard) { haRequestedCamera = config.cameras[Number(dashboard.dataset.cameraDashboard)]?.id || ''; showPage('ha-dashboard'); return; }
        const removeIndex = event.target?.dataset?.removeCamera;
        if (removeIndex !== undefined) {
          removeCamera(Number(removeIndex));
          return;
        }
        const buildRtspIndex = event.target?.dataset?.buildRtsp;
        if (buildRtspIndex !== undefined) {
          buildRtspForCamera(Number(buildRtspIndex));
          return;
        }
        const autoconfigIndex = event.target?.dataset?.autoconfig;
        if (autoconfigIndex !== undefined) {
          await loadCameraAutoconfig(Number(autoconfigIndex));
          return;
        }
        const streamName = event.target?.dataset?.saveStream;
        if (streamName) {
          const editor = event.target.closest('[data-stream-editor]');
          await saveCameraStream(Number(event.target.dataset.cameraIndex), streamName, editor);
        }
      });

      grid.addEventListener('click', async (event) => {
        const fullscreenTarget = event.target.closest('[data-fullscreen-live]');
        if (fullscreenTarget) {
          event.preventDefault();
          event.stopPropagation();
          const preview = fullscreenTarget.closest('.preview');
          requestEdgeFullscreen(preview, 'home_live');
          return;
        }
        const addTile = event.target.closest('[data-add-camera-tile]');
        if (addTile) {
          debugEvent('ui_add_camera_tile_click');
          showPage('camera-settings');
          addCamera();
          return;
        }
        const liveTarget = event.target.closest('[data-live-key]');
        const liveKey = liveTarget?.dataset?.liveKey;
        if (!liveKey) return;
        const nextLive = !live[liveKey];
        const plan = liveConnectionPlan(liveTarget?.dataset?.livePath || 'diagnostic');
        debugEvent('ui_live_toggle', {
          liveKey,
          cameraIndex: liveTarget?.dataset?.liveIndex,
          enabled: nextLive,
          can_embed: plan.canEmbed,
          reason: plan.reason,
          diagnostics: plan.diagnostics,
        });
        live[liveKey] = nextLive;
        updateLiveTimer();
        await loadCameras();
      });

      nvrGrid.addEventListener('input', (event) => {
        const playback = event.target.closest('[data-recording-playback-seek]');
        if (playback) {
          const index = playback.dataset.recordingPlaybackSeek;
          markNvrInteraction('playback_scrub', { index });
          const clock = nvrGrid.querySelector(`[data-recording-duration="${index}"]`);
          const total = recordingStatus[index]?.timeline?.playback_total_seconds || 0;
          if (clock) clock.textContent = `${recordingPlaybackClock(playback.value)} / ${recordingPlaybackClock(total)}`;
          return;
        }
        const slider = event.target.closest('[data-recording-day-seek]');
        if (!slider) return;
        const index = slider.dataset.recordingDaySeek;
        markNvrInteraction('day_timeline_preview', { index });
        const label = nvrGrid.querySelector(`[data-recording-timeline-label="${index}"]`);
        if (label) label.textContent = recordingTimelineLabel(index, Number(slider.value), Number(slider.max) + 1);
      });
      nvrGrid.addEventListener('change', (event) => {
        const playback = event.target.closest('[data-recording-playback-seek]');
        if (playback) { seekRecordingPlayback(playback.dataset.recordingPlaybackSeek, Number(playback.value)); return; }
        const speed = event.target.closest('[data-recording-speed]');
        if (speed) {
          const video = nvrGrid.querySelector(`video[data-recording-player="${speed.dataset.recordingSpeed}"]`);
          if (video) { video.playbackRate = Number(speed.value); video.defaultPlaybackRate = video.playbackRate; }
          return;
        }
        const slider = event.target.closest('[data-recording-day-seek]');
        if (slider) selectRecordingAtOffset(slider.dataset.recordingDaySeek, Number(slider.value));
      });

      nvrGrid.addEventListener('click', async (event) => {
        const toggle = event.target.closest('[data-recording-toggle]');
        if (toggle) {
          const video = nvrGrid.querySelector(`video[data-recording-player="${toggle.dataset.recordingToggle}"]`);
          if (video) {
            clearRecordingContinuation(toggle.dataset.recordingToggle);
            if (!video.paused) video.pause();
            else {
              const total = Number(recordingStatus[toggle.dataset.recordingToggle]?.timeline?.playback_total_seconds || 0);
              if (video.ended && recordingPlaybackPosition(video) >= total - 0.5) seekRecordingPlayback(toggle.dataset.recordingToggle, 0);
              try { await video.play(); } catch (error) { updateRecordingPlayerControls(video, error.message); }
            }
            updateRecordingPlayerControls(video);
          }
          return;
        }
        const mute = event.target.closest('[data-recording-mute]');
        if (mute) {
          const video = nvrGrid.querySelector(`video[data-recording-player="${mute.dataset.recordingMute}"]`);
          if (video) { video.muted = !video.muted; updateRecordingPlayerControls(video); }
          return;
        }
        const jump = event.target.closest('[data-recording-jump]');
        if (jump) {
          const video = nvrGrid.querySelector(`video[data-recording-player="${jump.dataset.recordIndex}"]`);
          if (video) seekRecordingPlayback(jump.dataset.recordIndex, recordingPlaybackPosition(video) + Number(jump.dataset.recordingJump));
          return;
        }
        const fullscreenTarget = event.target.closest('[data-recording-fullscreen]');
        if (fullscreenTarget) {
          event.preventDefault();
          event.stopPropagation();
          const wrap = fullscreenTarget.closest('[data-recording-wrap]');
          requestEdgeFullscreen(wrap, 'nvr_recording');
          return;
        }
        const dayTarget = event.target.closest('[data-recording-day]');
        if (dayTarget) {
          event.preventDefault();
          selectRecordingDay(dayTarget.dataset.recordIndex, dayTarget.dataset.recordingDay, 'tile');
          return;
        }
        const dayStepTarget = event.target.closest('[data-recording-day-step]');
        if (dayStepTarget) {
          event.preventDefault();
          moveRecordingDay(dayStepTarget.dataset.recordIndex, dayStepTarget.dataset.recordingDayStep, 'button');
          return;
        }
        const playTarget = event.target.closest('[data-play-recording]');
        const playRecording = playTarget?.dataset?.playRecording;
        let index = playTarget?.dataset?.recordIndex;
        if (index !== undefined && playRecording) {
          selectRecordingAtOffset(index, playTarget.dataset.recordingOffset || offsetForRecordingUrl(index, playRecording));
          return;
        }
        const stepTarget = event.target.closest('[data-playback-step]');
        const playbackStep = stepTarget?.dataset?.playbackStep;
        index = stepTarget?.dataset?.recordIndex;
        if (index !== undefined && playbackStep) {
          moveRecording(index, playbackStep);
          return;
        }
        const actionTarget = event.target.closest('[data-record-action]');
        const action = actionTarget?.dataset?.recordAction;
        index = actionTarget?.dataset?.recordIndex;
        if (index === undefined || !action) return;
        const response = await fetch(panelPath(`api/recording/${action}`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ index: Number(index) })
        });
        const data = await response.json();
        if (!response.ok) {
          saveState.textContent = data.error || 'Recording action failed.';
        }
        await loadRecordingStatus({ forceRender: true, reason: `recording_${action}` });
        await loadCameras();
      });

      nvrGrid.addEventListener('keydown', (event) => {
        const video = event.target.closest('video[data-recording-player]');
        if (!video || ![' ', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        const index = video.dataset.recordingPlayer;
        if (event.key === ' ') nvrGrid.querySelector(`[data-recording-toggle="${index}"]`)?.click();
        else seekRecordingPlayback(index, recordingPlaybackPosition(video) + (event.key === 'ArrowLeft' ? -10 : 10));
      });

      nvrGrid.addEventListener('pointerdown', (event) => {
        const video = event.target?.closest?.('video[data-recording-player], .recording-player-wrap');
        if (video) {
          const player = video.matches?.('video[data-recording-player]') ? video : video.querySelector?.('video[data-recording-player]');
          markNvrInteraction('recording_pointerdown', recordingVideoDiagnostics(player));
        }
        const swipe = event.target.closest('[data-recording-day-swipe]');
        if (!swipe) return;
        recordingSwipeStart[swipe.dataset.recordingDaySwipe] = { x: event.clientX, y: event.clientY };
      }, true);

      nvrGrid.addEventListener('pointerup', (event) => {
        const swipe = event.target.closest('[data-recording-day-swipe]');
        if (!swipe) return;
        const index = swipe.dataset.recordingDaySwipe;
        const start = recordingSwipeStart[index];
        delete recordingSwipeStart[index];
        if (!start) return;
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        if (Math.abs(dx) < 56 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
        moveRecordingDay(index, dx > 0 ? 'older' : 'newer', 'swipe');
      }, true);

      nvrGrid.addEventListener('timeupdate', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) {
          nvrInteractionUntil = Math.max(nvrInteractionUntil, Date.now() + 5000);
          syncRecordingVideoProgress(video);
        }
      }, true);

      nvrGrid.addEventListener('seeking', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) {
          markNvrInteraction('recording_video_seeking', recordingVideoDiagnostics(video));
          syncRecordingVideoProgress(video);
        }
      }, true);

      nvrGrid.addEventListener('seeked', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) {
          markNvrInteraction('recording_video_seeked', recordingVideoDiagnostics(video));
          syncRecordingVideoProgress(video);
        }
      }, true);

      for (const name of ['loadeddata', 'playing', 'seeked']) nvrGrid.addEventListener(name, (event) => {
        if (event.target.matches?.('video[data-recording-player]')) releaseRecordingFrame(event.target);
      }, true);

      nvrGrid.addEventListener('play', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) { markNvrInteraction('recording_video_play', recordingVideoDiagnostics(video)); updateRecordingPlayerControls(video, 'Playing recordings for selected day'); }
      }, true);

      nvrGrid.addEventListener('pause', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) { markNvrInteraction('recording_video_pause', recordingVideoDiagnostics(video)); updateRecordingPlayerControls(video); }
      }, true);

      nvrGrid.addEventListener('ratechange', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) markNvrInteraction('recording_video_ratechange', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('ended', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) playNextRecordingSegment(video.dataset.recordingPlayer, video);
      }, true);

      nvrGrid.addEventListener('loadedmetadata', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_loadedmetadata', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('canplay', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_canplay', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('waiting', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_waiting', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('stalled', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_stalled', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('error', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_error', recordingVideoDiagnostics(video));
        const image = event.target?.matches?.('img.recording-thumb, .recording-day-thumb img') ? event.target : null;
        if (image && !String(image.src || '').startsWith('data:image')) {
          debugEvent('ui_recording_thumbnail_error', { src: image.currentSrc || image.src || '' });
        }
      }, true);

      nvrGrid.addEventListener('load', (event) => {
        const image = event.target?.matches?.('img.recording-thumb, .recording-day-thumb img') ? event.target : null;
        if (image && !String(image.currentSrc || image.src).startsWith('data:image') && image.naturalWidth > 1) {
          thumbnailContainer(image)?.classList.remove('thumb-error');
          thumbnailContainer(image)?.classList.add('thumb-loaded');
        }
      }, true);

      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') exitSoftFullscreen('keyboard');
      });

      document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement) debugEvent('ui_fullscreen_change', { active: false });
      });

      document.addEventListener('webkitfullscreenchange', () => {
        if (!document.webkitFullscreenElement) debugEvent('ui_fullscreen_change', { active: false, webkit: true });
      });

      window.addEventListener('error', (event) => {
        debugEvent('ui_global_error', {
          message: event.message || '',
          source: event.filename || '',
          line: event.lineno || 0,
          column: event.colno || 0,
          stack: event.error?.stack || '',
        });
      });

      window.addEventListener('unhandledrejection', (event) => {
        const reason = event.reason || {};
        debugEvent('ui_unhandled_rejection', {
          message: reason.message || String(reason || ''),
          stack: reason.stack || '',
        });
      });

      restoreNavState();

      async function boot() {
        debugEvent('ui_boot', {
          panel_version: EDGE_PANEL_VERSION,
          ui_build: EDGE_UI_BUILD,
          path: window.location.pathname,
        });
        await loadConfig();
        await Promise.all([loadPresets(), loadCameras(), loadRecordingStatus({ forceRender: true, reason: 'boot' })]);
      }

      boot().catch((error) => {
        debugEvent('ui_boot_error', { message: error.message, stack: error.stack });
        grid.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      });
    </script>
  </body>
</html>
"""

INDEX_HTML = (
    INDEX_HTML
    .replace("__EDGE_PANEL_VERSION__", APP_VERSION)
    .replace("__EDGE_UI_BUILD__", UI_BUILD)
    .replace("__EDGE_NATIVE_RTC__", "true" if EDGE_RTC_ENABLED else "false")
    .replace("__EDGE_HA_LIVE_HELP__", "EdgeRTC sends H264 frames through authenticated Home Assistant WebSocket connections. Native live requires HTTPS and browser WebCodecs. A compatible RTSP G711 microphone uses the existing camera session. HA streaming needs no extra public media ports." if EDGE_RTC_ENABLED else 'Home Assistant streams the selected live feed through its own authenticated connection. Keep the camera reachable from Home Assistant and use H264 for broad browser support. Edge Live needs a Home Assistant WebRTC provider such as go2rtc. The microphone requires HTTPS, browser permission and camera/provider support for return audio.')
    .replace("__EDGE_CAMERA_VENDORS__", json.dumps(CAMERA_VENDORS))
    .replace("__EDGE_CAMERA_TEMPLATES__", json.dumps({vendor: {stream: camera_stream_template(vendor, "1", stream) for stream in ("main", "sub")} for vendor in CAMERA_VENDORS}))
)


class EdgeHandler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[edge-panel] {self.address_string()} {format % args}")

    def send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Edge-Version", APP_VERSION)
        self.send_header("X-Edge-UI-Build", UI_BUILD)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, str(value))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK, extra_headers: dict | None = None) -> None:
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json", status, extra_headers)

    def serve_edge_rtc_stream(self, stream_path: str) -> None:
        """Read-only binary tunnel; microphone is available only through HA ACLs."""
        import socket
        import select
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", stream_path) or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_json({"error": "invalid_stream"}, HTTPStatus.BAD_REQUEST)
            return
        backend = None
        upgraded = False
        try:
            token = (HOME_DIR / "edge-rtc-token").read_text().strip()
            backend = socket.create_connection(("127.0.0.1", MEDIAMTX_API_PORT), timeout=5)
            key = self.headers.get("Sec-WebSocket-Key", "")
            if not re.fullmatch(r"[A-Za-z0-9+/=]{20,32}", key):
                raise ValueError("invalid_websocket_key")
            header = f"GET /api/stream/{stream_path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {token}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            backend.sendall(header.encode())
            response = bytearray()
            while not response.endswith(b"\r\n\r\n") and len(response) < 16384:
                value = backend.recv(1)
                if not value:
                    raise OSError("backend_closed")
                response.extend(value)
            if not response.startswith(b"HTTP/1.1 101 "):
                raise OSError("backend_unavailable")
            self.connection.sendall(response)
            upgraded = True
            self.close_connection = True
            backend.settimeout(10)
            self.connection.settimeout(10)
            while True:
                readable, _, _ = select.select([backend, self.connection], [], [], 30)
                if not readable:
                    continue
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = self.connection if source is backend else backend
                    destination.sendall(data)
        except (OSError, ValueError):
            if not upgraded:
                self.send_json({"error": "edge_rtc_unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
        finally:
            if backend:
                backend.close()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = route_path(parsed.path)

        if path in ("/", "/index.html"):
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/health":
            payload = health_payload()
            self.send_json(payload, HTTPStatus.OK if payload["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/api/home-assistant/dashboard":
            self.send_json(home_assistant_dashboard_payload())
            return
        if path == "/api/edge-rtc/client.js":
            self.send_bytes((Path(__file__).parent / "custom_components/edge/www/edge-rtc-client.js").read_bytes(), "text/javascript")
            return
        if path.startswith("/api/edge-rtc/stream/") and EDGE_RTC_ENABLED:
            self.serve_edge_rtc_stream(path.removeprefix("/api/edge-rtc/stream/"))
            return
        if path.startswith("/api/edge-rtc/player/") and EDGE_RTC_ENABLED:
            self.send_bytes(EDGE_RTC_PLAYER_HTML.encode(), "text/html; charset=utf-8")
            return
        if path == "/api/version":
            self.send_json({
                "version": APP_VERSION,
                "server_version": SERVER_VERSION,
                "ui_build": UI_BUILD,
                "authoritative_config": str(CONFIG_PATH),
            })
            return
        if path == "/api/home-assistant/streams":
            self.send_json(home_assistant_restream_payload(load_config()))
            return
        if path == "/api/config":
            try:
                self.send_json(load_config())
            except (OSError, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if path == "/api/presets":
            self.send_json({"presets": load_presets()})
            return
        if path == "/api/recording/status":
            config = load_config()
            schedule_recording_ensure(config, "recording_status")
            self.send_json(recording_status_payload(config, day_selection=parse_recording_day_query(parse_qs(parsed.query))))
            return
        if path == "/api/logs":
            self.send_json(collect_panel_logs())
            return
        if path == "/api/stream/capabilities":
            self.send_json(stream_capabilities())
            return
        if path == "/api/core/status":
            self.send_json(engine_runtime_status())
            return
        if path == "/api/core/mediamtx.yml":
            if not MEDIAMTX_CONFIG_PATH.exists():
                self.send_json({"error": "mediamtx_config_not_found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_bytes(redact_rtsp(MEDIAMTX_CONFIG_PATH.read_text(encoding="utf-8", errors="replace")).encode("utf-8"), "text/plain; charset=utf-8")
            return
        if path == "/api/core/janus-streaming.jcfg":
            streaming_config = JANUS_CONFIG_DIR / "janus.plugin.streaming.jcfg"
            if not streaming_config.exists():
                self.send_json({"error": "janus_streaming_config_not_found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_bytes(redact_rtsp(streaming_config.read_text(encoding="utf-8", errors="replace")).encode("utf-8"), "text/plain; charset=utf-8")
            return
        if path == "/cameras.json":
            payload = read_json(DATA_DIR / "cameras.json", {"cameras": []})
            self.send_json(payload)
            return
        if path.startswith("/snapshots/"):
            self.serve_snapshot(path.removeprefix("/snapshots/"))
            return
        if path.startswith("/recordings-stream/") and path.endswith(".mp4"):
            self.serve_recording_stream(path, parsed.query)
            return
        if path.startswith("/recording-cache/") and path.endswith("/timeline.mp4"):
            self.serve_recording_cache(path)
            return
        if path.startswith("/recording-thumbs/") and path.endswith(".jpg"):
            self.serve_recording_thumbnail(path)
            return
        if path.startswith("/recordings/") and path.endswith(".mp4"):
            self.serve_recording(path)
            return

        self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = route_path(parsed.path)
        if path.startswith("/recording-cache/") and path.endswith("/timeline.mp4"):
            self.serve_recording_cache(path, send_body=False)
            return
        if path.startswith("/recordings/") and path.endswith(".mp4"):
            self.serve_recording(path, send_body=False)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        _post_path = route_path(parsed.path)
        parsed = parsed._replace(path=_post_path)
        write_debug_event("api_post", {"path": parsed.path, "query": parse_qs(parsed.query), "client": self.client_address[0]})
        if parsed.path == "/api/config":
            self.save_config()
            return
        if parsed.path == "/api/refresh":
            payload = refresh_status()
            write_debug_event("api_refresh_done", {"camera_summary": config_summary(payload)})
            self.send_json(payload)
            return
        if parsed.path == "/api/debug":
            self.ui_debug()
            return
        if parsed.path == "/api/camera-autoconfig":
            self.camera_autoconfig()
            return
        if parsed.path == "/api/camera-stream-config":
            self.camera_stream_config()
            return
        if parsed.path == "/api/recording/start":
            self.recording_action("start")
            return
        if parsed.path == "/api/recording/stop":
            self.recording_action("stop")
            return
        self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def read_raw_body(self) -> bytes:
        length_header = self.headers.get("Content-Length", "")
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        content_type = self.headers.get("Content-Type", "")
        info = {
            "content_length": length_header,
            "transfer_encoding": transfer_encoding,
            "content_type": content_type,
            "bytes_read": 0,
        }
        self._last_body_info = info
        if length_header:
            try:
                length = int(length_header)
            except ValueError as error:
                raise ValueError(f"Invalid Content-Length: {length_header}") from error
            if length > MAX_REQUEST_BODY_BYTES:
                raise ValueError("Request body is too large.")
            body = self.rfile.read(length)
        elif "chunked" in transfer_encoding.lower():
            body = read_chunked_body_from_stream(self.rfile)
        else:
            body = b""
        info["bytes_read"] = len(body)
        return body

    def read_body_json(self) -> dict:
        body = self.read_raw_body()
        return json.loads(body.decode("utf-8")) if body else {}

    def ui_debug(self) -> None:
        try:
            query = parse_qs(urlparse(self.path).query)
            payload = self.read_body_json()
            event = payload.get("event") if isinstance(payload, dict) else ""
            event = event or (query.get("event") or ["ui_debug"])[0]
            write_debug_event(str(event or "ui_debug"), {
                "client": self.client_address[0],
                "user_agent": self.headers.get("User-Agent", ""),
                "query": query,
                "request_body": getattr(self, "_last_body_info", {}),
                "ui": payload,
            })
            self.send_json({"ok": True})
        except (json.JSONDecodeError, OSError, ValueError) as error:
            write_debug_event("ui_debug_error", {"error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def save_config(self) -> None:
        with CONFIG_SAVE_LOCK:
            self._save_config()

    def _save_config(self) -> None:
        try:
            request_payload = self.read_body_json()
            body_info = getattr(self, "_last_body_info", {})
            if not request_payload:
                write_debug_event("config_save_empty_body", {
                    "body": body_info,
                    "client": self.client_address[0],
                    "headers": {
                        "content_length": self.headers.get("Content-Length", ""),
                        "transfer_encoding": self.headers.get("Transfer-Encoding", ""),
                        "content_type": self.headers.get("Content-Type", ""),
                    },
                })
                self.send_json({"error": "empty_request_body", "body": body_info}, HTTPStatus.BAD_REQUEST)
                return
            previous_config = load_config()
            raw_payload, merged_payload, normalized_payload, final_payload, saved_payload = persist_config_payload(request_payload)
            committed_payload = saved_payload
            save_debug_payload(raw_payload, merged_payload, normalized_payload, saved_payload)
            write_debug_event("config_save", {
                "raw_summary": config_summary(raw_payload),
                "merged_summary": config_summary(merged_payload),
                "normalized_summary": config_summary(normalized_payload),
                "final_summary": config_summary(final_payload),
                "committed_summary": config_summary(committed_payload),
                "saved_summary": config_summary(saved_payload),
                "verified": committed_payload == saved_payload,
                "changed_by": self.client_address[0],
                "request_body": body_info,
                "authoritative_path": str(CONFIG_PATH),
                "addon_config_mirror_path": str(ADDON_CONFIG_PATH),
                "panel_mirror_path": str(PANEL_CONFIG_PATH),
            })
            restart_changed_recordings(previous_config, saved_payload)
            stop_orphan_recordings(saved_payload)
            sync_runtime_engine_config(saved_payload, "config_save")
            schedule_recording_ensure(saved_payload, "config_save")
            remember_camera_presets(saved_payload.get("cameras", []))
            publish_pending_camera_status(saved_payload)
            options_sync = sync_supervisor_options(saved_payload)
            threading.Thread(target=refresh_status_after_save, daemon=True).start()
            self.send_json(saved_payload, extra_headers={"X-Edge-Options-Sync": options_sync["status"]})
        except (json.JSONDecodeError, OSError, ValueError) as error:
            write_debug_event("config_save_error", {"error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def camera_autoconfig(self) -> None:
        try:
            payload = self.read_body_json()
            camera = camera_from_payload(payload)
            write_debug_event("camera_autoconfig_start", {"camera": camera_debug_profile(camera, camera.get("live_stream") or "sub")})
            result = fetch_camera_autoconfig(camera)
            write_debug_event("camera_autoconfig_done", {
                "camera_id": camera.get("id"),
                "sections": result.get("sections") if isinstance(result, dict) else None,
                "effective_streams": result.get("effective_streams") if isinstance(result, dict) else None,
            })
            self.send_json(result)
        except (json.JSONDecodeError, ET.ParseError, RuntimeError, ValueError) as error:
            write_debug_event("camera_autoconfig_error", {"error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    def camera_stream_config(self) -> None:
        try:
            payload = self.read_body_json()
            camera = camera_from_payload(payload)
            stream_name = payload.get("stream") if payload.get("stream") in ("main", "sub") else "sub"
            settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            write_debug_event("camera_stream_config_start", {
                "camera": camera_debug_profile(camera, stream_name),
                "stream": stream_name,
                "settings": settings,
            })
            stream = update_stream_config(camera, stream_name, settings)
            write_debug_event("camera_stream_config_done", {"camera_id": camera.get("id"), "stream": stream_name, "result": stream})
            self.send_json({"stream": stream})
        except ValueError as error:
            write_debug_event("camera_stream_config_error", {"error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except (json.JSONDecodeError, ET.ParseError, RuntimeError) as error:
            write_debug_event("camera_stream_config_error", {"error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    def recording_action(self, action: str) -> None:
        try:
            payload = self.read_body_json()
            camera = camera_from_payload(payload)
            index = int(payload.get("index", 0))
            if action == "start":
                self.send_json(start_recording(camera, index))
            else:
                self.send_json(stop_recording(camera, index))
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except (json.JSONDecodeError, OSError, RuntimeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    def serve_snapshot(self, filename: str) -> None:
        safe_name = Path(filename).name
        path = DATA_SNAPSHOT_DIR / safe_name
        if not path.exists():
            path = SNAPSHOT_DIR / safe_name
        if not path.exists():
            self.send_json({"error": "snapshot_not_found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_bytes(path.read_bytes(), "image/jpeg")

    def serve_recording_stream(self, request_path: str, query: str) -> None:
        key = Path(request_path.removeprefix("/recordings-stream/")).stem
        params = parse_qs(query)
        start_seconds = safe_int((params.get("start") or ["0"])[0], 0)
        timeline_start_seconds = safe_int((params.get("timeline_start") or ["0"])[0], 0)
        start_mode = (params.get("mode") or ["playback"])[0] or "playback"
        day_key = (params.get("day") or [""])[0]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key or ""):
            day_key = ""
        if not key:
            self.send_json({"error": "recording_stream_missing_key"}, HTTPStatus.NOT_FOUND)
            return
        if shutil.which("ffmpeg") is None:
            self.send_json({"error": "ffmpeg_not_installed_in_addon"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        try:
            plan = recording_stream_plan(key, start_seconds, day_key)
        except FileNotFoundError as error:
            self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            return
        except OSError as error:
            write_debug_event("recording_stream_plan_error", {"key": key, "start_seconds": start_seconds, "error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        command = build_recording_stream_command(plan["concat_path"], plan["seek_seconds"])
        write_debug_event("recording_stream_start", {
            "client": self.client_address[0],
            "path": request_path,
            "query": params,
            "start_mode": start_mode,
            "timeline_start_seconds": timeline_start_seconds,
            "plan": {name: value for name, value in plan.items() if name != "concat_path"},
            "concat_path": str(plan["concat_path"]),
            "command": redact_command(command),
        })

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Edge-Version", APP_VERSION)
        self.send_header("X-Edge-UI-Build", UI_BUILD)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        bytes_written = 0
        chunks = 0
        process = None
        log_handle = None
        result = "completed"
        try:
            RECORDING_STREAM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            log_handle = RECORDING_STREAM_LOG_PATH.open("ab")
            log_handle.write(("\n=== Edge recording stream ===\n" + json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "key": key,
                "start_seconds": start_seconds,
                "start_mode": start_mode,
                "timeline_start_seconds": timeline_start_seconds,
                "plan": {name: value for name, value in plan.items() if name != "concat_path"},
                "command": redact_command(command),
            }, default=str) + "\n").encode("utf-8"))
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log_handle)
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(256 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    bytes_written += len(chunk)
                    chunks += 1
                except (BrokenPipeError, ConnectionResetError):
                    result = "client_closed"
                    break
        except OSError as error:
            result = f"os_error:{error}"
            write_debug_event("recording_stream_error", {"key": key, "error": str(error), "type": type(error).__name__})
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            exit_code = process.poll() if process else None
            if log_handle:
                log_handle.close()
            try:
                plan["concat_path"].unlink(missing_ok=True)
            except OSError:
                pass
            write_debug_event("recording_stream_end", {
                "key": key,
                "result": result,
                "bytes": bytes_written,
                "chunks": chunks,
                "exit_code": exit_code,
                "plan": {name: value for name, value in plan.items() if name != "concat_path"},
            })

    def serve_recording_thumbnail(self, request_path: str) -> None:
        parts = request_path.removeprefix("/recording-thumbs/").split("/", 1)
        if len(parts) != 2:
            self.send_json({"error": "recording_thumbnail_not_found"}, HTTPStatus.NOT_FOUND)
            return
        key, filename = parts
        source = recording_file_for_key(key, filename)
        if source is None:
            self.send_json({"error": "recording_not_found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            thumb = ensure_recording_thumbnail(key, source, filename)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            payload = {
                "key": key,
                "filename": Path(filename).name,
                "error": str(error),
                "type": type(error).__name__,
            }
            append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_request_error", payload)
            write_debug_event("recording_thumbnail_error", payload)
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        self.send_bytes(thumb.read_bytes(), "image/jpeg")

    def serve_mp4_file(
        self,
        target: Path,
        request_path: str,
        disposition_name: str,
        debug_event_name: str,
        debug_payload: dict,
        send_body: bool = True,
        cache_control: str = "public, max-age=86400, immutable",
    ) -> None:
        file_size = target.stat().st_size
        if file_size <= 0:
            self.send_json({"error": "recording_empty"}, HTTPStatus.NOT_FOUND)
            return

        range_header = self.headers.get("Range", "")
        start = 0
        end = file_size - 1
        status = HTTPStatus.OK

        if range_header.startswith("bytes="):
            requested = range_header.removeprefix("bytes=").split("-", 1)
            try:
                start = int(requested[0]) if requested[0] else 0
                end = int(requested[1]) if len(requested) > 1 and requested[1] else file_size - 1
                start = max(0, min(start, file_size - 1))
                end = max(start, min(end, file_size - 1))
                status = HTTPStatus.PARTIAL_CONTENT
            except ValueError:
                self.send_json({"error": "invalid_range"}, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return

        length = end - start + 1
        safe_disposition = disposition_name.replace('"', "_")
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Disposition", f'inline; filename="{safe_disposition}"')
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Edge-Version", APP_VERSION)
        self.send_header("X-Edge-UI-Build", UI_BUILD)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        write_debug_event(debug_event_name, {
            "method": self.command,
            "path": request_path,
            "range": range_header,
            "status": int(status),
            "start": start,
            "end": end,
            "length": length,
            "file_size": file_size,
            "send_body": send_body,
            **debug_payload,
        })
        if not send_body:
            return

        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)

    def serve_recording_cache(self, request_path: str, send_body: bool = True) -> None:
        parts = request_path.removeprefix("/recording-cache/").split("/", 1)
        if len(parts) != 2:
            self.send_json({"error": "recording_cache_not_found"}, HTTPStatus.NOT_FOUND)
            return
        key = safe_id(parts[0])
        cache_name = ""
        tail = parts[1]
        if tail == "timeline.mp4":
            cache_name = ""
        else:
            tail_parts = tail.split("/", 1)
            if len(tail_parts) != 2 or tail_parts[1] != "timeline.mp4":
                self.send_json({"error": "recording_cache_not_found"}, HTTPStatus.NOT_FOUND)
                return
            cache_name = safe_id(tail_parts[0])
        config = load_config()
        if camera_for_recording_key(config, key) is None:
            self.send_json({"error": "recording_camera_not_found"}, HTTPStatus.NOT_FOUND)
            return
        target = recording_cache_video_path(key, cache_name)
        if not target.exists():
            self.send_json({"error": "recording_cache_not_ready"}, HTTPStatus.NOT_FOUND)
            return
        meta_path = recording_cache_meta_path(key, cache_name)
        metadata = read_json(meta_path, {}) if meta_path.exists() else {}
        self.serve_mp4_file(
            target,
            request_path,
            f"{key}-{cache_name or 'timeline'}.mp4",
            "recording_cache_request",
            {"key": key, "cache_name": cache_name, "cache_id": metadata.get("cache_id") or "", "source_hash": metadata.get("source_hash") or ""},
            send_body=send_body,
        )

    def serve_recording(self, request_path: str, send_body: bool = True) -> None:
        parts = request_path.removeprefix("/recordings/").split("/", 1)
        if len(parts) != 2:
            self.send_json({"error": "recording_not_found"}, HTTPStatus.NOT_FOUND)
            return
        key, filename = parts
        safe_name = Path(filename).name
        if not safe_name.endswith(".mp4"):
            self.send_json({"error": "recording_not_found"}, HTTPStatus.NOT_FOUND)
            return

        config = load_config()
        target = None
        for index, camera in enumerate(config.get("cameras", [])):
            if recording_key(camera, index) == key:
                target = recording_base_dir(camera, index) / safe_name
                break

        if target is None or not target.exists():
            self.send_json({"error": "recording_not_found"}, HTTPStatus.NOT_FOUND)
            return

        self.serve_mp4_file(
            target,
            request_path,
            safe_name,
            "recording_file_request",
            {"key": key, "filename": safe_name},
            send_body=send_body,
        )

def main() -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    STREAM_LIST_DIR.mkdir(parents=True, exist_ok=True)
    RECORDING_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    RECORDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    clear_legacy_override_files()
    write_debug_event("boot_start", {
        "server_version": EdgeHandler.server_version,
        "home_dir": str(HOME_DIR),
        "data_dir": str(DATA_DIR),
        "config_path": str(CONFIG_PATH),
        "port": PORT,
    })
    write_json(DATA_DIR / "health", health_payload())
    write_json(HOME_DIR / "health.json", health_payload())
    try:
        config = load_config()
        sync_runtime_engine_config(config, "boot_panel")
        status_payload = refresh_status()
        write_debug_event("boot_refresh_status_done", {"camera_summary": config_summary(status_payload)})
        schedule_recording_ensure(config, "boot")
        start_recording_retention_loop()
        start_recording_cache_refresh_loop()
        start_recording_thumbnail_warmup_loop()
    except Exception as error:
        write_debug_event("boot_refresh_status_error", {"error": str(error), "type": type(error).__name__})
        raise
    server = ThreadingHTTPServer(("0.0.0.0", PORT), EdgeHandler)
    print(f"[edge-panel] listening on 0.0.0.0:{PORT}")
    write_debug_event("boot_server_listening", {"listen": f"0.0.0.0:{PORT}"})
    server.serve_forever()


if __name__ == "__main__":
    main()

