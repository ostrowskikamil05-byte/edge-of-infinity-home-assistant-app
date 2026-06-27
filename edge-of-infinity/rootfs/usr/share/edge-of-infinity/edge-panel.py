#!/usr/bin/env python3
"""Edge of Infinity panel server."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import base64
import hashlib
import secrets
import socket
import struct


HOME_DIR = Path(os.environ.get("EDGE_HOME_DIR", "/homeassistant/edge"))
DATA_DIR = Path(os.environ.get("EDGE_DATA_DIR", "/tmp/edge-placeholder"))
CONFIG_PATH = Path(os.environ.get("EDGE_HOME_CONFIG", "/homeassistant/edge/edge.json"))
CONFIG_BACKUP_PATH = HOME_DIR / "edge.backup.json"
PRESETS_PATH = HOME_DIR / "camera-presets.json"
DEBUG_LOG_PATH = HOME_DIR / "edge-debug.log"
PORT = int(os.environ.get("API_PORT", "8088"))
SNAPSHOT_DIR = HOME_DIR / "snapshots"
DATA_SNAPSHOT_DIR = DATA_DIR / "snapshots"
RECORDING_PROCESSES: dict[str, subprocess.Popen] = {}
HLS_PROCESSES: dict[str, subprocess.Popen] = {}
DEBUG_LOCK = threading.Lock()
HIKVISION_MAIN_CHANNEL = "101"
HIKVISION_SUB_CHANNEL = "102"
STREAM_FALLBACK_CHANNELS = {"main": HIKVISION_MAIN_CHANNEL, "sub": HIKVISION_SUB_CHANNEL}
STREAM_ENGINES = ("mjpeg", "webrtc", "ll_hls")
# go2rtc rebroadcast address (same host, port 1984 = go2rtc default HA addon port)
GO2RTC_HOST = os.environ.get("GO2RTC_HOST", "localhost")
GO2RTC_PORT = int(os.environ.get("GO2RTC_PORT", "1984"))
GO2RTC_BASE = f"http://{GO2RTC_HOST}:{GO2RTC_PORT}"
# WebRTC STUN server list (used in SDP offer to browser)
STUN_SERVERS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]
# ICE UDP port range for WebRTC (must match router port-forward if using 4G/remote)
WEBRTC_UDP_PORT_MIN = int(os.environ.get("WEBRTC_UDP_PORT_MIN", "50000"))
WEBRTC_UDP_PORT_MAX = int(os.environ.get("WEBRTC_UDP_PORT_MAX", "50100"))
# Active WebRTC sessions: session_id -> {offer_sdp, answer_sdp, camera_id, created}
WEBRTC_SESSIONS: dict[str, dict] = {}
WEBRTC_LOCK = threading.Lock()


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
        **redact_for_log(payload or {}),
    }
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOCK:
            with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as error:
        print(f"[edge-panel] debug log write failed: {error}")


def classify_ffmpeg_error(stderr_text: str, frame_count: int, end_reason: str, bytes_sent: int = 0) -> tuple[str, list[str]]:
    text_lower = stderr_text.lower()
    hints = []
    if "option" in text_lower and "not found" in text_lower:
        hints.append("ffmpeg_option_unsupported")
    if "401" in text_lower or "unauthorized" in text_lower:
        hints.append("camera_auth_failed")
    if "connection refused" in text_lower:
        hints.append("rtsp_port_refused")
    if "timed out" in text_lower or "connection timed out" in text_lower or "i/o timeout" in text_lower:
        hints.append("rtsp_timeout")
    if "could not find codec parameters" in text_lower:
        hints.append("probe_needs_more_data")
    if "invalid data" in text_lower:
        hints.append("invalid_rtsp_or_codec_data")
    if "hevc" in text_lower or "h265" in text_lower:
        hints.append("hevc_decode_path")
    if "error while decoding" in text_lower or "error constructing the frame" in text_lower:
        hints.append("decoder_error")
    if frame_count == 0:
        hints.append("no_mjpeg_frames_sent")
    if frame_count > 0 and bytes_sent // frame_count > 750_000:
        hints.append("mjpeg_frames_too_large")
    if end_reason.startswith("client_") and frame_count > 0:
        hints.append("browser_closed_stream_after_frames")
    if not hints and frame_count > 0:
        hints.append("mjpeg_frames_sent")
    category = hints[0] if hints else "unknown"
    return category, hints


def extract_ffmpeg_stderr(log_text: str, begin_marker: str) -> str:
    request_text = log_text.rsplit(begin_marker, 1)[-1]
    if "--- ffmpeg stderr begin ---" in request_text:
        request_text = request_text.split("--- ffmpeg stderr begin ---", 1)[1]
    if "--- ffmpeg stderr end ---" in request_text:
        request_text = request_text.split("--- ffmpeg stderr end ---", 1)[0]
    return request_text[-12000:]


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


def build_rtsp(explicit: str, host: str, username: str, password: str, channel: str) -> str:
    if explicit:
        return explicit
    if host and username and password:
        return f"rtsp://{username}:{password}@{host}:554/Streaming/Channels/{channel}"
    return ""


def hikvision_channel_from_rtsp(value: str, fallback: str) -> str:
    match = re.search(r"/Streaming/Channels/(\d+)", value or "")
    if match:
        return match.group(1)
    return fallback


def normalize_stream_name(value: str | None, fallback: str) -> str:
    return value if value in ("main", "sub") else fallback


def hikvision_camera_number_from_channel(channel: str) -> str:
    if not channel or len(channel) <= 2:
        return "1"
    return channel[:-2] or "1"


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
                "live_stream": camera.get("live_stream"),
                "tile_stream": camera.get("tile_stream"),
                "record_stream": camera.get("record_stream"),
                "snapshot_stream": camera.get("snapshot_stream"),
                "rtsp_main_channel": hikvision_channel_from_rtsp(camera.get("rtsp_main") or "", ""),
                "rtsp_sub_channel": hikvision_channel_from_rtsp(camera.get("rtsp_sub") or "", ""),
            }
        )
    return summary


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


def normalize_camera(raw: dict, index: int) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    vendor = raw.get("vendor") if raw.get("vendor") in ("hikvision", "dahua", "onvif", "rtsp") else "hikvision"
    vendor_labels = {"hikvision": "Hikvision", "dahua": "Dahua", "onvif": "ONVIF", "rtsp": "RTSP"}
    vendor_label = vendor_labels.get(vendor, "Camera")
    camera_id = raw.get("id") or f"{vendor}_{index}"
    name = raw.get("name") or f"{vendor_label} {index}"
    host = raw.get("host") or ""
    username = raw.get("username") or "admin"
    password = raw.get("password") or ""
    rtsp_main = raw.get("rtsp_main") or ""
    rtsp_sub = raw.get("rtsp_sub") or ""
    rtsp_sub_channel = hikvision_channel_from_rtsp(rtsp_sub, HIKVISION_SUB_CHANNEL)
    if vendor == "hikvision":
        rtsp_main = build_rtsp(rtsp_main, host, username, password, HIKVISION_MAIN_CHANNEL)
        rtsp_sub = build_rtsp(rtsp_sub, host, username, password, HIKVISION_SUB_CHANNEL)
        rtsp_sub_channel = hikvision_channel_from_rtsp(rtsp_sub, HIKVISION_SUB_CHANNEL)
    elif vendor == "dahua":
        rtsp_main = build_dahua_rtsp(rtsp_main, host, username, password, 0)
        rtsp_sub = build_dahua_rtsp(rtsp_sub, host, username, password, 1)
        rtsp_sub_channel = ""
    snapshot_stream = normalize_stream_name(raw.get("snapshot_stream"), "sub")
    live_stream = normalize_stream_name(raw.get("live_stream"), "sub")
    record_stream = normalize_stream_name(raw.get("record_stream"), "main")
    tile_stream = normalize_stream_name(raw.get("tile_stream"), "sub")

    onvif_url = raw.get("onvif_url") or (f"http://{host}:80/onvif/device_service" if host else "")
    isapi_base_url = raw.get("isapi_base_url") or (f"http://{host}" if host and vendor == "hikvision" else "")

    return {
        "id": camera_id,
        "name": name,
        "vendor": vendor,
        "host": host,
        "username": username,
        "password": password,
        "rtsp_main": rtsp_main,
        "rtsp_sub": rtsp_sub,
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
            "engine": live.get("engine") if live.get("engine") in STREAM_ENGINES else "webrtc",
            "frame_interval_ms": safe_int(live.get("frame_interval_ms"), 1200),
            "mjpeg_fps": clamp_int(live.get("mjpeg_fps"), 5, 1, 15),
            "mjpeg_quality": clamp_int(live.get("mjpeg_quality"), 8, 2, 31),
            "mjpeg_max_width": clamp_int(live.get("mjpeg_max_width"), 1280, 320, 3840),
            "tile_fps": clamp_int(live.get("tile_fps"), 5, 1, 10),
            "tile_max_width": clamp_int(live.get("tile_max_width"), 960, 320, 1920),
        },
        "nvr": {
            "segment_seconds": clamp_int(nvr.get("segment_seconds"), 10, 2, 300),
            "retention_days": clamp_int(nvr.get("retention_days"), safe_int(storage.get("retention_days"), 14), 1, 365),
            "copy_all_streams": bool(nvr.get("copy_all_streams", True)),
        },
        "cameras": normalized,
        "future_vendors": payload.get("future_vendors") or ["dahua", "onvif", "rtsp"],
    }


def preserve_submitted_stream_choices(payload: dict, raw_payload: dict) -> dict:
    """Keep explicit UI stream choices after normalization builds RTSP URLs.

    FIX 0.5.8: always use index as primary match (not id) so new cameras
    without a stable id still get their stream choices preserved correctly.
    """
    raw_cameras = raw_payload.get("cameras") if isinstance(raw_payload, dict) else []
    cameras = payload.get("cameras") if isinstance(payload, dict) else []
    if not isinstance(raw_cameras, list) or not isinstance(cameras, list):
        return payload
    cameras_by_id = {
        str(camera.get("id")): camera
        for camera in cameras
        if isinstance(camera, dict) and camera.get("id")
    }
    for index, raw_camera in enumerate(raw_cameras):
        if not isinstance(raw_camera, dict):
            continue
        # FIX: prefer index-based match first (reliable), then id-based fallback
        if index < len(cameras):
            target = cameras[index]
        elif raw_camera.get("id"):
            target = cameras_by_id.get(str(raw_camera.get("id")))
        else:
            target = None
        if not isinstance(target, dict):
            continue
        for field in ("snapshot_stream", "live_stream", "record_stream", "tile_stream"):
            value = raw_camera.get(field)
            if value in ("main", "sub"):
                target[field] = value
    return payload


def save_debug_payload(raw_payload: dict, normalized_payload: dict, final_payload: dict) -> None:
    debug_payload = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "raw_summary": config_summary(raw_payload),
        "normalized_summary": config_summary(normalized_payload),
        "final_summary": config_summary(final_payload),
        "raw": raw_payload,
        "normalized": normalized_payload,
        "final": final_payload,
    }
    write_json(HOME_DIR / "last-save-debug.json", debug_payload)


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
            "rtsp_sub_channel",
            "onvif_url",
            "isapi_base_url",
            "enabled",
            "record",
            "low_latency",
            "snapshot_stream",
            "live_stream",
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
    stream = camera_stream(camera, camera.get("snapshot_stream") or "sub")
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


def recording_key(camera: dict, index: int) -> str:
    return f"{safe_id(camera.get('id') or 'camera')}_{index}"


def hls_key(camera: dict, index: int) -> str:
    return f"{safe_id(camera.get('id') or 'camera')}_{index}"


def hls_base_dir(camera: dict, index: int) -> Path:
    return DATA_DIR / "hls" / hls_key(camera, index)


def stream_urls(camera: dict, index: int) -> dict:
    camera_id = safe_id(camera.get("id") or f"camera_{index + 1}")
    return {
        "mjpeg": f"live/{camera_id}.mjpg",
        "tile_mjpeg": f"live/{camera_id}.mjpg?tile=1",
        "ll_hls": f"hls/{camera_id}/index.m3u8",
        "mse_next": f"mse/{camera_id}",
        "webrtc": f"webrtc/{camera_id}",
        "rtsp_main": redact_rtsp(camera.get("rtsp_main") or ""),
        "rtsp_sub": redact_rtsp(camera.get("rtsp_sub") or ""),
    }


def stream_capabilities(config: dict | None = None) -> dict:
    config = config or load_config()
    return {
        "engines": {
            "mjpeg": {"status": "active", "transport": "multipart-jpeg"},
            "ll_hls": {"status": "experimental", "transport": "hls-fmp4", "note": "ffmpeg generated local HLS playlist"},
            "mse_next": {"status": "planned", "transport": "fmp4/websocket", "note": "prepared for rebroadcast engine"},
            "webrtc_next": {"status": "planned", "transport": "webrtc", "note": "use go2rtc-style signaling/rebroadcast in the next engine step"},
        },
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


def cleanup_hls_processes() -> None:
    for key, process in list(HLS_PROCESSES.items()):
        if process.poll() is not None:
            HLS_PROCESSES.pop(key, None)


def cleanup_webrtc_sessions_safe() -> None:
    """Cleanup expired WebRTC sessions (called from periodic maintenance)."""
    cleanup_webrtc_sessions()


# ═══════════════════════════════════════════════════════════════════════════
# WebRTC engine — RTSP→WebRTC via SDP offer/answer over HTTP (no go2rtc needed)
# Architecture inspired by SharpRTSPtoWebRTC + go2rtc signaling pattern:
#   1. Browser sends SDP offer (via POST /api/webrtc/offer)
#   2. Server answers with SDP + ICE candidates  (via GET /api/webrtc/answer)
#   3. ffmpeg pushes RTP to a local UDP socket which forwards to browser
#   4. Browser receives via WebRTC DataChannel / RTP directly
# For 4G/remote: STUN resolves public ICE candidates automatically.
# For NAT-heavy setups: add TURN server via GO2RTC_TURN env var.
# ═══════════════════════════════════════════════════════════════════════════

def go2rtc_available() -> bool:
    """Check if go2rtc is running on the configured address."""
    try:
        import urllib.request
        urllib.request.urlopen(f"{GO2RTC_BASE}/api/streams", timeout=1)
        return True
    except Exception:
        return False


def go2rtc_register_stream(camera: dict, camera_id: str) -> bool:
    """Register or update a camera RTSP stream in go2rtc.

    go2rtc accepts: PUT /api/streams?src=rtsp://...&name=camera_id
    This is the go2rtc API style from AlexxIT/go2rtc.
    """
    import urllib.request
    stream_name_main = camera.get("rtsp_main") or camera_stream(camera, "main")
    stream_name_sub  = camera.get("rtsp_sub")  or camera_stream(camera, "sub")
    registered = False
    for stream_rtsp, stream_id in ((stream_name_main, f"{camera_id}_main"), (stream_name_sub, f"{camera_id}_sub")):
        if not stream_rtsp:
            continue
        try:
            url = f"{GO2RTC_BASE}/api/streams?name={stream_id}&src={stream_rtsp}"
            req = urllib.request.Request(url, method="PUT")
            urllib.request.urlopen(req, timeout=3)
            registered = True
        except Exception:
            pass
    return registered


def go2rtc_webrtc_offer(camera_id: str, offer_sdp: str, stream_variant: str = "sub") -> str | None:
    """Exchange SDP with go2rtc WebRTC endpoint (WHIP/go2rtc-native).

    go2rtc endpoint: POST /api/webrtc?src=<stream_id>
    Body: SDP offer  →  Response: SDP answer
    """
    import urllib.request
    stream_id = f"{camera_id}_{stream_variant}"
    url = f"{GO2RTC_BASE}/api/webrtc?src={stream_id}"
    try:
        data = offer_sdp.encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/sdp"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read().decode()
    except Exception:
        return None


def _local_ip() -> str:
    """Best-effort local IP (used in SDP o= and c= lines)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _make_sdp_answer(offer_sdp: str, rtp_port: int, local_ip: str, codec: str = "H264") -> str:
    """Build a minimal SDP answer for RTSP→WebRTC fallback (no go2rtc).

    Inspired by SharpRTSPtoWebRTC: re-stream H264/H265 RTP without transcoding.
    The browser decodes natively — zero transcoding CPU cost.
    """
    payload = 96
    rtpmap = f"rtpmap:{payload} {codec}/90000"
    fmtp   = f"fmtp:{payload} packetization-mode=1" if codec == "H264" else f"fmtp:{payload} sprop-vps=...; sprop-sps=...; sprop-pps=..."
    session_id = str(int(time.time()))
    sdp = (
        f"v=0\r\n"
        f"o=- {session_id} {session_id} IN IP4 {local_ip}\r\n"
        f"s=Edge of Infinity Stream\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        f"t=0 0\r\n"
        f"a=group:BUNDLE 0\r\n"
        f"m=video {rtp_port} RTP/AVP {payload}\r\n"
        f"a={rtpmap}\r\n"
        f"a={fmtp}\r\n"
        f"a=recvonly\r\n"
        f"a=rtcp-mux\r\n"
    )
    for stun in STUN_SERVERS:
        sdp += f"a=ice-options:trickle\r\na=candidate:0 1 UDP 2122260223 {local_ip} {rtp_port} typ host\r\n"
    return sdp


def start_webrtc_ffmpeg(camera: dict, camera_id: str, rtp_port: int) -> subprocess.Popen | None:
    """Start ffmpeg pushing RTP to localhost:{rtp_port} for a WebRTC session.

    Key args (Scrypted + go2rtc inspired):
    - -c:v copy   → zero transcoding, just repackage (H264/H265 native passthrough)
    - -f rtp      → RTP output for WebRTC ingestion
    - -probesize/analyzeduration → fast startup
    - -fflags nobuffer → minimal buffering = lowest latency
    This mirrors SharpRTSPtoWebRTC's no-transcode philosophy.
    """
    stream_name = camera.get("live_stream") or "sub"
    stream = camera_stream(camera, stream_name)
    if not stream:
        return None
    command = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "warning",
        "-fflags", "nobuffer+discardcorrupt",
        "-flags", "low_delay",
        "-rtsp_transport", "tcp",
        "-probesize", "32768",
        "-analyzeduration", "0",
        "-thread_queue_size", "512",
        "-i", stream,
        "-map", "0:v:0",
        "-c:v", "copy",
        "-f", "rtp",
        f"rtp://127.0.0.1:{rtp_port}?pkt_size=1200",
    ]
    try:
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def create_webrtc_session(camera: dict, camera_id: str, offer_sdp: str) -> dict:
    """Create a WebRTC session. Tries go2rtc first, falls back to native RTP.

    Returns: {session_id, answer_sdp, engine, status}
    """
    session_id = secrets.token_hex(8)

    # ── Path A: go2rtc available (lowest latency, ~50ms, works great on 4G) ──
    if go2rtc_available():
        go2rtc_register_stream(camera, camera_id)
        variant = camera.get("live_stream") or "sub"
        answer = go2rtc_webrtc_offer(camera_id, offer_sdp, variant)
        if answer:
            with WEBRTC_LOCK:
                WEBRTC_SESSIONS[session_id] = {
                    "camera_id": camera_id,
                    "engine": "go2rtc",
                    "answer_sdp": answer,
                    "created": time.time(),
                    "process": None,
                }
            return {"session_id": session_id, "answer_sdp": answer, "engine": "go2rtc", "status": "ok"}

    # ── Path B: native RTP fallback (ffmpeg → RTP → browser via WebRTC) ───────
    # Find a free UDP port in configured range
    rtp_port = WEBRTC_UDP_PORT_MIN
    for port in range(WEBRTC_UDP_PORT_MIN, WEBRTC_UDP_PORT_MAX, 2):
        with WEBRTC_LOCK:
            used = {s.get("rtp_port") for s in WEBRTC_SESSIONS.values()}
        if port not in used:
            rtp_port = port
            break

    local_ip = _local_ip()
    # Detect codec from camera config
    probe = probe_rtsp_stream(camera_stream(camera, camera.get("live_stream") or "sub"), timeout=4)
    codec = "H264"
    for stream_info in (probe.get("streams") or []):
        if stream_info.get("codec_type") == "video":
            cn = (stream_info.get("codec_name") or "").upper()
            if cn in ("H265", "HEVC"):
                codec = "H265"
            break

    answer_sdp = _make_sdp_answer(offer_sdp, rtp_port, local_ip, codec)
    process = start_webrtc_ffmpeg(camera, camera_id, rtp_port)
    with WEBRTC_LOCK:
        WEBRTC_SESSIONS[session_id] = {
            "camera_id": camera_id,
            "engine": "native_rtp",
            "answer_sdp": answer_sdp,
            "rtp_port": rtp_port,
            "created": time.time(),
            "process": process,
        }
    write_debug_event("webrtc_session_created", {"session_id": session_id, "engine": "native_rtp", "port": rtp_port, "codec": codec})
    return {"session_id": session_id, "answer_sdp": answer_sdp, "engine": "native_rtp", "status": "ok"}


def cleanup_webrtc_sessions() -> None:
    """Remove expired WebRTC sessions (older than 60s without keepalive)."""
    now = time.time()
    with WEBRTC_LOCK:
        expired = [sid for sid, s in WEBRTC_SESSIONS.items() if now - s["created"] > 60]
        for sid in expired:
            session = WEBRTC_SESSIONS.pop(sid)
            proc = session.get("process")
            if proc and proc.poll() is None:
                proc.terminate()


def start_hls(camera: dict, index: int) -> dict:
    cleanup_hls_processes()
    key = hls_key(camera, index)
    process = HLS_PROCESSES.get(key)
    directory = hls_base_dir(camera, index)
    playlist = directory / "index.m3u8"
    if process and process.poll() is None and playlist.exists():
        return {"started": False, "status": "already_running", "directory": str(directory)}

    stream_name = normalize_stream_name(camera.get("live_stream"), "sub")
    stream = camera_stream(camera, stream_name)
    if not stream:
        raise ValueError("rtsp_not_configured")

    if directory.exists():
        for path in directory.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "ffmpeg-hls.log"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer+discardcorrupt",
        "-rtsp_transport",
        "tcp",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-i",
        stream,
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        "hls",
        "-hls_time",
        "1",
        "-hls_list_size",
        "6",
        "-hls_segment_type",
        "fmp4",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_flags",
        "delete_segments+omit_endlist+independent_segments",
        playlist.name,
    ]
    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(command, cwd=directory, stdout=subprocess.DEVNULL, stderr=log_file)
    finally:
        log_file.close()
    HLS_PROCESSES[key] = process
    write_debug_event("hls_start", {
        "key": key,
        "camera_id": camera.get("id"),
        "stream": stream_name,
        "directory": str(directory),
        "command": redact_command(command),
    })
    return {"started": True, "status": "running", "directory": str(directory), "pid": process.pid}


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


def recording_base_dir(camera: dict, index: int) -> Path:
    config = load_config()
    storage = config.get("storage") or {}
    recordings_dir = Path(storage.get("recordings_dir") or "/media/edge-of-infinity/recordings")
    return recordings_dir / safe_id(camera.get("id") or f"camera_{index + 1}")


def recording_segments(camera: dict, index: int, limit: int = 24) -> list[dict]:
    directory = recording_base_dir(camera, index)
    if not directory.exists():
        return []
    key = recording_key(camera, index)
    files = sorted(directory.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
    segments = []
    for path in files[:limit]:
        stat = path.stat()
        segments.append(
            {
                "name": path.name,
                "url": f"recordings/{key}/{path.name}",
                "size_bytes": stat.st_size,
                "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stat.st_mtime)),
            }
        )
    return segments


def cleanup_old_recordings(directory: Path, retention_days: int) -> int:
    if not directory.exists():
        return 0
    cutoff = time.time() - max(1, retention_days) * 86400
    removed = 0
    for path in directory.glob("*.mp4"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def recording_status_payload(config: dict | None = None) -> dict:
    cleanup_recording_processes()
    config = config or load_config()
    cameras = []
    for index, camera in enumerate(config.get("cameras", [])):
        key = recording_key(camera, index)
        process = RECORDING_PROCESSES.get(key)
        directory = recording_base_dir(camera, index)
        segment_count = len(list(directory.glob("*.mp4"))) if directory.exists() else 0
        nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
        segment_files = recording_segments(camera, index, limit=72)
        record_stream = camera.get("record_stream") or "main"
        record_rtsp = camera_stream(camera, record_stream)
        cameras.append(
            {
                "index": index,
                "id": camera.get("id"),
                "key": key,
                "record_stream": record_stream,
                "record_rtsp": redact_rtsp(record_rtsp),
                "recording": bool(process and process.poll() is None),
                "pid": process.pid if process and process.poll() is None else None,
                "directory": str(directory),
                "segments": segment_count,
                "files": segment_files,
                "segment_seconds": nvr.get("segment_seconds", 10),
            }
        )
    return {"cameras": cameras}


def start_recording(camera: dict, index: int) -> dict:
    cleanup_recording_processes()
    key = recording_key(camera, index)
    existing = RECORDING_PROCESSES.get(key)
    if existing and existing.poll() is None:
        return {"started": False, "status": "already_recording", "key": key, "pid": existing.pid}

    record_stream = camera.get("record_stream") or "main"
    stream = camera_stream(camera, record_stream)
    if not stream:
        raise ValueError("rtsp_not_configured")

    directory = recording_base_dir(camera, index)
    directory.mkdir(parents=True, exist_ok=True)
    config = load_config()
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    retention_days = clamp_int(nvr.get("retention_days"), 14, 1, 365)
    removed_old = cleanup_old_recordings(directory, retention_days)
    log_path = directory / "ffmpeg.log"
    output_pattern = str(directory / "%Y%m%d-%H%M%S.mp4")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-fflags",
        "+genpts",
        "-rtsp_transport",
        "tcp",
        "-probesize",
        "32768",
        "-analyzeduration",
        "0",
        "-i",
        stream,
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_format",
        "mp4",
        "-segment_format_options",
        "movflags=+faststart",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        output_pattern,
    ]
    log_file = log_path.open("ab")
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=log_file)
    except OSError:
        raise
    finally:
        log_file.close()
    RECORDING_PROCESSES[key] = process
    write_debug_event("recording_start", {
        "key": key,
        "camera_id": camera.get("id"),
        "record_stream": record_stream,
        "segment_seconds": segment_seconds,
        "retention_days": retention_days,
        "removed_old": removed_old,
        "command": redact_command(command),
    })
    return {
        "started": True,
        "status": "recording",
        "key": key,
        "pid": process.pid,
        "directory": str(directory),
        "record_stream": record_stream,
        "record_rtsp": redact_rtsp(stream),
        "segment_seconds": segment_seconds,
    }


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


def fetch_camera_autoconfig(camera: dict) -> dict:
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

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "ok": not autoconfig_error,
        "error": autoconfig_error,
        "device": device,
        "streams": streams,
        "channels": channels,
        "effective_streams": effective_streams(camera),
        "sections": {
            key: {"ok": value.get("ok", False), "error": value.get("error", "")}
            for key, value in raw_sections.items()
        },
    }


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
                main_probe = probe_rtsp_stream(rtsp_main)
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
                    live_probe = probe_rtsp_stream(live_rtsp)
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

        # FIX 0.5.8: capture snapshot when camera is online
        if status == "online":
            try:
                snapshot_path, snapshot_url = capture_snapshot(camera, camera_key)
            except Exception:
                snapshot_path, snapshot_url = "", ""

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

    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cameras": cameras}
    write_json(DATA_DIR / "cameras.json", payload)
    write_json(HOME_DIR / "cameras.json", payload)
    return payload


def health_payload() -> dict:
    return {"status": "ok", "product": "Edge of Infinity", "version": "0.7.0", "go2rtc": go2rtc_available(), "mode": "panel-live-mjpeg"}


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
        --sidebar-width: 220px;
        --sidebar-collapsed-width: 74px;
      }
      * { box-sizing: border-box; }
      body { margin: 0; background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; }
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
      main { width: min(1180px, calc(100vw - 28px)); min-width: 0; margin: 0 auto; padding: 24px 0 40px; }
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
      .preview span { color: var(--muted); font-size: 13px; padding: 12px; text-align: center; }
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
      .recording-player {
        width: 100%;
        aspect-ratio: 16 / 9;
        display: block;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #05080a;
        margin-top: 12px;
      }
      .recording-list {
        display: grid;
        gap: 7px;
        margin-top: 12px;
      }
      .recording-item {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 8px;
        background: rgba(0,0,0,.14);
      }
      .recording-item.active {
        border-color: rgba(86,214,181,.65);
        background: rgba(86,214,181,.08);
      }
      .recording-item button {
        min-width: 0;
        overflow-wrap: anywhere;
        text-align: left;
      }
      .recording-meta {
        color: var(--muted);
        font-size: 12px;
        white-space: nowrap;
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
        </div>
        <nav class="nav">
          <button class="active" data-page-target="home"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></svg></span><span class="nav-label">Home</span></button>
          <button data-page-target="nvr"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/></svg></span><span class="nav-label">NVR</span></button>
          <button data-page-target="camera-settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 8h11l5 4-5 4H4z"/><circle cx="9" cy="12" r="2"/></svg></span><span class="nav-label">Camera Settings</span></button>
          <button data-page-target="edge-settings"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a8 8 0 0 0 .1-6"/><path d="M4.5 9a8 8 0 0 0 .1 6"/><path d="M15 4.6a8 8 0 0 0-6 0"/><path d="M9 19.4a8 8 0 0 0 6 0"/></svg></span><span class="nav-label">Edge Settings</span></button>
          <button data-page-target="account"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.8-4 14.2-4 16 0"/></svg></span><span class="nav-label">Account</span></button>
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
              <p>Recording control, recent segments, and local playback.</p>
            </div>
            <div class="toolbar">
              <button class="primary" id="refresh-nvr">Refresh NVR</button>
            </div>
          </header>
          <section class="panel">
            <h2>Recording</h2>
            <div id="nvr-grid" class="grid"></div>
          </section>
          <section class="panel">
            <h2>Timeline</h2>
            <p>Recorded MP4 segments are available inside each camera card. The full rewind/forward timeline will build on these files.</p>
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
              <button id="add-camera" type="button">Add camera</button>
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
          <section class="settings">
            <form id="edge-settings-form"></form>
            <div class="actions">
              <button class="primary" id="save-edge-settings" type="button">Save Edge settings</button>
            </div>
            <p class="notice" id="edge-save-state">Core settings are saved to <code>/homeassistant/edge/edge.json</code>. Some runtime changes may need an add-on restart.</p>
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
      const edgeForm = document.getElementById('edge-settings-form');
      const presetSelect = document.getElementById('preset-select');
      const presetSlot = document.getElementById('preset-slot');
      const saveState = document.getElementById('save-state');
      const edgeSaveState = document.getElementById('edge-save-state');
      const menuToggle = document.getElementById('menu-toggle');
      let config = { cameras: [] };
      let live = {};
      let presets = [];
      let liveTimer = null;
      let cameraAuto = {};
      let recordingStatus = {};
      let selectedRecording = {};
      let configDirty = false;

      const panelBase = window.location.pathname.endsWith('/')
        ? window.location.pathname
        : `${window.location.pathname}/`;

      // ── EdgeWebRTC: WebRTC client (SharpRTSPtoWebRTC + go2rtc inspired) ─────────
      // Handles SDP offer/answer, ICE, keepalive, auto-reconnect.
      // Works on 4G via STUN. go2rtc path: zero-transcode H264/H265 passthrough.
      const EdgeWebRTC = (() => {
        const sessions = {};
        async function start(cameraId) {
          if (sessions[cameraId]?.pc) stop(cameraId);
          const video = document.getElementById('webrtc-' + cameraId);
          if (!video) return;
          const pc = new RTCPeerConnection({
            iceServers: [
              { urls: 'stun:stun.l.google.com:19302' },
              { urls: 'stun:stun1.l.google.com:19302' },
            ],
            iceCandidatePoolSize: 2,
          });
          pc.ontrack = (e) => { if (e.streams?.[0]) video.srcObject = e.streams[0]; };
          pc.addTransceiver('video', { direction: 'recvonly' });
          const offer = await pc.createOffer();
          await pc.setLocalDescription(offer);
          // Wait for ICE (max 3s) — important for 4G NAT traversal
          await new Promise((res) => {
            if (pc.iceGatheringState === 'complete') { res(); return; }
            const t = setTimeout(res, 3000);
            pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') { clearTimeout(t); res(); } };
          });
          let answerData;
          try {
            const resp = await fetch(panelPath('api/webrtc/offer'), {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ camera_id: cameraId, sdp: pc.localDescription.sdp }),
            });
            answerData = await resp.json();
          } catch (err) {
            video.replaceWith(Object.assign(document.createElement('span'), { textContent: 'WebRTC failed — try MJPEG engine' }));
            pc.close(); return;
          }
          if (!answerData.answer_sdp) {
            video.replaceWith(Object.assign(document.createElement('span'), { textContent: 'WebRTC error: ' + (answerData.error || 'no answer') }));
            pc.close(); return;
          }
          await pc.setRemoteDescription({ type: 'answer', sdp: answerData.answer_sdp });
          const sessionId = answerData.session_id;
          const keepalive = setInterval(async () => {
            if (!sessions[cameraId]) { clearInterval(keepalive); return; }
            try { await fetch(panelPath('api/webrtc/keepalive'), { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ session_id: sessionId }) }); } catch (_) {}
          }, 20000);
          sessions[cameraId] = { pc, sessionId, keepalive, engine: answerData.engine };
          pc.onconnectionstatechange = () => {
            if (['failed','disconnected','closed'].includes(pc.connectionState)) {
              clearInterval(keepalive); delete sessions[cameraId];
              setTimeout(() => start(cameraId), 3000);
            }
          };
        }
        function stop(cameraId) {
          const s = sessions[cameraId]; if (!s) return;
          clearInterval(s.keepalive);
          if (s.sessionId) fetch(panelPath('api/webrtc/close'), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({session_id: s.sessionId}) }).catch(()=>{});
          s.pc?.close(); delete sessions[cameraId];
        }
        return { start, stop };
      })();

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

      function debugEvent(event, payload = {}) {
        const body = JSON.stringify({
          event,
          page: document.querySelector('[data-page]:not([hidden])')?.dataset?.page || 'unknown',
          location: window.location.pathname,
          payload
        });
        fetch(panelPath('api/debug'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
          keepalive: true
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

      function cameraCard(camera) {
        const online = camera.status === 'online';
        const liveKey = camera.key || `${camera.id || 'camera'}_${camera.index ?? 0}`;
        const liveId = camera.id || liveKey;
        const videoCodec = camera.video_codec || camera.codec;
        const liveStream = camera.live_stream || 'sub';
        const tileStream = camera.tile_stream || 'sub';
        const hasSubPreview = camera.effective_streams?.sub?.configured;
        const previewStream = hasSubPreview ? tileStream : liveStream;
        const liveResolution = camera.live_width && camera.live_height ? `${camera.live_width}x${camera.live_height}` : 'unknown';
        const previewResolution = previewStream === liveStream ? liveResolution : 'tile';
        const liveCodec = camera.live_video_codec || videoCodec || 'video';
        const livePath = `live/${encodeURIComponent(liveId)}.mjpg?tile=1&t=${Date.now()}`;
        const liveMjpegUrl = directPanelPath(livePath);
        const liveMjpegFallbackUrl = panelPath(livePath);
        const statusBadge = `<div class="connection-badge ${statusClass(camera.status)}">${escapeHtml(statusLabel(camera.status))}</div>`;
        const liveEngine = (window._edgeConfig?.live?.engine) || 'webrtc';
        let preview;
        if (!live[liveKey]) {
          preview = `<span>${online ? 'Click to start live' : escapeHtml(text(camera.detail, 'Waiting for camera'))}</span>`;
        } else if (liveEngine === 'webrtc') {
          // WebRTC player: ultra-low latency (50-300ms), 4G-ready
          preview = `<video id="webrtc-${escapeHtml(liveId)}" class="webrtc-player" autoplay muted playsinline
            data-camera-id="${escapeHtml(liveId)}"
            style="width:100%;height:100%;object-fit:contain;background:#000"></video>
            <script>EdgeWebRTC.start("${escapeHtml(liveId)}");</script>`;
        } else if (liveEngine === 'll_hls') {
          // LL-HLS player: good for 4G fallback when WebRTC not available
          const hlsUrl = panelPath('hls/' + encodeURIComponent(liveId) + '/index.m3u8');
          preview = `<video class="hls-player" autoplay muted playsinline
            style="width:100%;height:100%;object-fit:contain;background:#000"
            onerror="this.outerHTML='<span>HLS failed</span>'"
            src="${escapeHtml(hlsUrl)}"></video>`;
        } else {
          // MJPEG fallback
          preview = `<img src="${escapeHtml(liveMjpegUrl)}" data-fallback-src="${escapeHtml(liveMjpegFallbackUrl)}"
            alt="${escapeHtml(text(camera.name, camera.id))} MJPEG live"
            onerror="if (this.dataset.fallbackSrc) { this.src = this.dataset.fallbackSrc; this.dataset.fallbackSrc = ''; } else { this.outerHTML='<span>MJPEG failed</span>'; }">`;
        }
        return `
          <article class="camera video-tile">
            <div class="preview" data-live-key="${escapeHtml(liveKey)}" data-live-index="${escapeHtml(camera.index ?? 0)}" title="Click to toggle live preview">${preview}${statusBadge}</div>
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

      function nvrCard(camera, index) {
        const status = recordingStatus[index] || {};
        const isRecording = Boolean(status.recording);
        const recordStream = status.record_stream || camera.record_stream || 'main';
        const recordRtsp = status.record_rtsp || camera.record_rtsp || 'missing';
        const files = Array.isArray(status.files) ? status.files : [];
        const selected = selectedRecording[index] || '';
        const selectedIndex = files.findIndex((file) => file.url === selected);
        const player = selected
          ? `<video class="recording-player" src="${escapeHtml(panelPath(selected))}" controls preload="metadata"></video>`
          : '';
        const recordingList = files.length
          ? `<div class="recording-list">${files.map((file) => `
              <div class="recording-item ${file.url === selected ? 'active' : ''}">
                <button type="button" data-play-recording="${escapeHtml(file.url)}" data-record-index="${index}">${escapeHtml(file.name)}</button>
                <span class="recording-meta">${escapeHtml(formatBytes(file.size_bytes))} | ${escapeHtml(formatDate(file.modified_at))}</span>
              </div>
            `).join('')}</div>`
          : `<p class="notice">No segments yet. Start recording and refresh after the first ${escapeHtml(text(status.segment_seconds, 10))}-second segment is closed.</p>`;
        return `
          <article class="camera">
            <div class="body">
              <div class="row">
                <div class="name">${escapeHtml(text(camera.name, `Camera ${index + 1}`))}</div>
                <div class="vendor">${isRecording ? 'REC' : 'READY'}</div>
              </div>
              <div class="meta">
                <div class="metric"><b>Host</b><span>${escapeHtml(text(camera.host))}</span></div>
                <div class="metric"><b>Status</b><span class="${isRecording ? 'state-online' : ''}">${isRecording ? 'recording' : 'stopped'}</span></div>
                <div class="metric"><b>Record stream</b><span>${escapeHtml(recordStream)}</span></div>
                <div class="metric"><b>Segments</b><span>${escapeHtml(text(status.segments, '0'))}</span></div>
                <div class="metric"><b>Segment</b><span>${escapeHtml(text(status.segment_seconds, 10))}s</span></div>
                <div class="metric"><b>PID</b><span>${escapeHtml(text(status.pid, 'none'))}</span></div>
                <div class="metric wide"><b>Record RTSP</b><span>${escapeHtml(recordRtsp)}</span></div>
              </div>
              <p class="notice">${escapeHtml(text(status.directory, 'Recording directory will appear after start.'))}</p>
              <div class="actions">
                <button class="primary" data-record-action="${isRecording ? 'stop' : 'start'}" data-record-index="${index}">${isRecording ? 'Stop recording' : 'Start recording'}</button>
                <button data-playback-step="older" data-record-index="${index}" ${selectedIndex >= 0 && selectedIndex < files.length - 1 ? '' : 'disabled'}>Rewind</button>
                <button data-playback-step="newer" data-record-index="${index}" ${selectedIndex > 0 ? '' : 'disabled'}>Forward</button>
              </div>
              ${player}
              ${recordingList}
            </div>
          </article>
        `;
      }

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
        const effective = renderEffectiveStreams(state.data?.effective_streams);
        const sectionSummary = state.data?.sections
          ? Object.entries(state.data.sections).map(([name, item]) => `${name}: ${item.ok ? 'ok' : item.error}`).join(' | ')
          : '';
        return `
          <div class="autoconfig">
            <div class="autoconfig-head">
              <div>
                <h2>Autoconfig</h2>
                <p>${device.model ? `${escapeHtml(device.model)} ${escapeHtml(device.firmware || '')}` : 'Read camera settings through Hikvision ISAPI.'}</p>
              </div>
              <button type="button" data-autoconfig="${index}" ${state.loading ? 'disabled' : ''}>${state.loading ? 'Reading...' : 'Autoconfig'}</button>
            </div>
            ${state.error ? `<p class="section-status state-offline">${escapeHtml(state.error)}</p>` : ''}
            ${state.message ? `<p class="section-status state-online">${escapeHtml(state.message)}</p>` : ''}
            ${device.serial_number ? `<p class="section-status">Serial: ${escapeHtml(device.serial_number)} ${device.device_name ? `| Name: ${escapeHtml(device.device_name)}` : ''}</p>` : ''}
            ${effective}
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

      function cameraForm(camera, index) {
        const prefix = `camera-${index}`;
        return `
          <div class="camera-form" data-camera-form data-index="${index}">
            <div class="row">
              <h2>${escapeHtml(text(camera.name, `Camera ${index + 1}`))}</h2>
              <button class="danger" type="button" data-remove-camera="${index}" ${config.cameras.length <= 1 ? 'disabled' : ''}>Remove</button>
            </div>
            <div class="form-grid">
              <label>Name<input name="${prefix}-name" value="${escapeHtml(text(camera.name, `Camera ${index + 1}`))}"></label>
              <label>Vendor<select name="${prefix}-vendor">
                <option value="hikvision" ${camera.vendor === 'hikvision' ? 'selected' : ''}>Hikvision</option>
                <option value="dahua" ${camera.vendor === 'dahua' ? 'selected' : ''}>Dahua</option>
                <option value="onvif" ${camera.vendor === 'onvif' ? 'selected' : ''}>ONVIF</option>
                <option value="rtsp" ${camera.vendor === 'rtsp' ? 'selected' : ''}>RTSP</option>
              </select></label>
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
              <label>Live stream<select name="${prefix}-live-stream">
                <option value="sub" ${camera.live_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.live_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label>
              <label>Tile stream<select name="${prefix}-tile-stream">
                <option value="sub" ${camera.tile_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.tile_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label>
              <label>Recording stream<select name="${prefix}-record-stream">
                <option value="main" ${camera.record_stream !== 'sub' ? 'selected' : ''}>main</option>
                <option value="sub" ${camera.record_stream === 'sub' ? 'selected' : ''}>sub</option>
              </select></label>
              <label class="check-row"><input name="${prefix}-enabled" type="checkbox" ${camera.enabled ? 'checked' : ''}> Enabled</label>
              <label class="check-row"><input name="${prefix}-record" type="checkbox" ${camera.record !== false ? 'checked' : ''}> Record</label>
              <label class="check-row"><input name="${prefix}-low-latency" type="checkbox" ${camera.low_latency !== false ? 'checked' : ''}> Low latency</label>
            </div>
            <div class="actions">
              <button type="button" data-build-rtsp="${index}">Build RTSP</button>
            </div>
          </div>
        `;
      }

      function renderConfig() {
        configDirty = false;
        const cameras = config.cameras && config.cameras.length ? config.cameras : [
          { id: 'hikvision_1', name: 'Hikvision 1', vendor: 'hikvision', username: 'admin', rtsp_sub_channel: '102', snapshot_stream: 'sub', live_stream: 'sub', tile_stream: 'sub', record_stream: 'main', record: true, low_latency: true },
          { id: 'hikvision_2', name: 'Hikvision 2', vendor: 'hikvision', username: 'admin', rtsp_sub_channel: '102', snapshot_stream: 'sub', live_stream: 'sub', tile_stream: 'sub', record_stream: 'main', record: true, low_latency: true }
        ];
        config = { ...config, cameras };
        form.innerHTML = cameras.map(cameraForm).join('');
        nvrGrid.innerHTML = cameras.map(nvrCard).join('');
        renderPresetSlots(cameras);
        renderEdgeSettings();
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
          <section class="camera-form">
            <h2>Server</h2>
            <div class="form-grid">
              <label>Listen address<input name="server-listen" value="${escapeHtml(text(server.listen, '0.0.0.0:8088'))}"></label>
              <label>Public URL<input name="server-public-url" value="${escapeHtml(server.public_url || '')}" placeholder="Optional external URL"></label>
            </div>
            <p class="notice">Listen address is controlled by the add-on port at runtime. Changing it here prepares the config, but usually requires restart.</p>
          </section>
          <section class="camera-form">
            <h2>Storage</h2>
            <div class="form-grid">
              <label>Recordings directory<input name="storage-recordings-dir" value="${escapeHtml(text(storage.recordings_dir, '/media/edge-of-infinity/recordings'))}"></label>
              <label>Database path<input name="storage-database-path" value="${escapeHtml(text(storage.database_path, '/homeassistant/edge/edge.db'))}"></label>
              <label>Retention days<input name="storage-retention-days" type="number" min="1" max="365" value="${escapeHtml(text(storage.retention_days, 14))}"></label>
            </div>
          </section>
          <section class="camera-form">
            <h2>NVR</h2>
            <div class="form-grid">
              <label>Segment seconds<input name="nvr-segment-seconds" type="number" min="2" max="300" value="${escapeHtml(text(nvrConfig.segment_seconds, 10))}"></label>
              <label>NVR retention days<input name="nvr-retention-days" type="number" min="1" max="365" value="${escapeHtml(text(nvrConfig.retention_days, storage.retention_days || 14))}"></label>
            </div>
            <p class="notice">NVR records with stream copy so every encoded camera frame in the selected recording stream is preserved without re-encoding.</p>
          </section>
          <section class="camera-form">
            <h2>Live Preview</h2>
            <div class="form-grid">
              <label>Engine<select name="live-engine">
                <option value="mjpeg" ${liveConfig.engine === 'mjpeg' ? 'selected' : ''}>MJPEG preview</option>
                <option value="ll_hls" ${liveConfig.engine === 'll_hls' ? 'selected' : ''}>LL-HLS experimental</option>
                <option value="mse_next" ${liveConfig.engine === 'mse_next' ? 'selected' : ''}>MSE next</option>
                <option value="webrtc" ${liveConfig.engine === 'webrtc' ? 'selected' : ''}>WebRTC (ultra-low latency, 4G-ready)</option>
              </select></label>
              <label>Frame interval ms<input name="live-frame-interval-ms" type="number" min="250" max="10000" value="${escapeHtml(text(liveConfig.frame_interval_ms, 1200))}" disabled></label>
              <label>MJPEG FPS<input name="live-mjpeg-fps" type="number" min="1" max="15" value="${escapeHtml(text(liveConfig.mjpeg_fps, 5))}"></label>
              <label>MJPEG quality<input name="live-mjpeg-quality" type="number" min="2" max="31" value="${escapeHtml(text(liveConfig.mjpeg_quality, 8))}"></label>
              <label>MJPEG max width<input name="live-mjpeg-max-width" type="number" min="320" max="3840" value="${escapeHtml(text(liveConfig.mjpeg_max_width, 1280))}"></label>
            </div>
            <p class="notice">These settings only shape the browser MJPEG preview. Camera resolution and recording streams stay unchanged.</p>
          </section>
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

      function hikvisionChannelFromRtsp(value, fallback = '102') {
        const match = String(value || '').match(/\/Streaming\/Channels\/(\d+)/);
        return match ? match[1] : fallback;
      }

      function collectConfig() {
        const cameras = Array.from(form.querySelectorAll('[data-camera-form]')).map((section, index) => {
          const prefix = `camera-${index}`;
          const get = (name) => {
            const element = section.querySelector(`[name="${prefix}-${name}"]`);
            if (!element) {
              throw new Error(`Missing camera field: ${prefix}-${name}`);
            }
            return element;
          };
          const vendor = get('vendor').value;
          const rtspMain = get('rtsp-main').value.trim();
          const rtspSub = get('rtsp-sub').value.trim();
          const rtspSubChannel = vendor === 'hikvision' ? hikvisionChannelFromRtsp(rtspSub, '102') : '';
          return {
            id: config.cameras[index]?.id || `${get('vendor').value}_${index + 1}`,
            name: get('name').value,
            vendor,
            host: get('host').value.trim(),
            username: get('username').value.trim(),
            password: get('password').value,
            rtsp_main: rtspMain,
            rtsp_sub: rtspSub,
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

      function newCamera(index) {
        return {
          id: `hikvision_${index + 1}`,
          name: `Hikvision ${index + 1}`,
          vendor: 'hikvision',
          username: 'admin',
          rtsp_sub_channel: '102',
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
        config.cameras = [...config.cameras, newCamera(config.cameras.length)];
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
        if (!host || !username || !password) {
          saveState.textContent = 'Host, username, and password are required to build RTSP URLs.';
          return;
        }
        if (vendor === 'hikvision') {
          get('rtsp-main').value = `rtsp://${username}:${password}@${host}:554/Streaming/Channels/101`;
          get('rtsp-sub').value = `rtsp://${username}:${password}@${host}:554/Streaming/Channels/102`;
          get('onvif').value = get('onvif').value || `http://${host}:80/onvif/device_service`;
          get('isapi').value = get('isapi').value || `http://${host}`;
          saveState.textContent = 'Hikvision RTSP URLs prepared. Click Save cameras to write them.';
          return;
        }
        if (vendor === 'dahua') {
          get('rtsp-main').value = `rtsp://${username}:${password}@${host}:554/cam/realmonitor?channel=1&subtype=0`;
          get('rtsp-sub').value = `rtsp://${username}:${password}@${host}:554/cam/realmonitor?channel=1&subtype=1`;
          get('onvif').value = get('onvif').value || `http://${host}:80/onvif/device_service`;
          saveState.textContent = 'Dahua RTSP URLs prepared. Click Save cameras to write them.';
          return;
        }
        saveState.textContent = 'Generic ONVIF/RTSP cameras need manual RTSP URLs for now.';
      }

      function collectEdgeSettings() {
        const get = (name) => edgeForm.elements[name];
        return {
          ...config,
          server: {
            listen: get('server-listen').value.trim() || '0.0.0.0:8088',
            public_url: get('server-public-url').value.trim()
          },
          storage: {
            recordings_dir: get('storage-recordings-dir').value.trim() || '/media/edge-of-infinity/recordings',
            database_path: get('storage-database-path').value.trim() || '/homeassistant/edge/edge.db',
            retention_days: Number(get('storage-retention-days').value || 14)
          },
          live: {
            engine: get('live-engine').value,
            frame_interval_ms: Number(get('live-frame-interval-ms').value || 1200),
            mjpeg_fps: Number(get('live-mjpeg-fps').value || 5),
            mjpeg_quality: Number(get('live-mjpeg-quality').value || 8),
            mjpeg_max_width: Number(get('live-mjpeg-max-width').value || 1280)
          },
          nvr: {
            segment_seconds: Number(get('nvr-segment-seconds').value || 10),
            retention_days: Number(get('nvr-retention-days').value || get('storage-retention-days').value || 14)
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
          const mainChannel = hikvisionChannelFromRtsp(camera.rtsp_main, '101');
          const subChannel = hikvisionChannelFromRtsp(camera.rtsp_sub, '102');
          return `${name}: tile=${text(camera.tile_stream, 'sub')}, live=${text(camera.live_stream, 'sub')}, record=${text(camera.record_stream, 'main')}, snapshot=${text(camera.snapshot_stream, 'sub')}, main ch=${mainChannel}, sub ch=${subChannel}`;
        });
        return parts.length ? parts.join(' | ') : 'no cameras';
      }

      async function loadConfig() {
        const response = await fetch(panelPath('api/config'), { cache: 'no-store' });
        config = await response.json();
        debugEvent('ui_config_loaded', { summary: saveSummary(config) });
        renderConfig();
      }

      async function loadCameras() {
        const response = await fetch(panelPath('cameras.json'), { cache: 'no-store' });
        const data = await response.json();
        const cameras = Array.isArray(data.cameras) ? data.cameras : [];
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
          ? `${cameras.map(cameraCard).join('')}${addCameraTile()}`
          : `${addCameraTile()}`;
      }

      async function loadRecordingStatus() {
        const response = await fetch(panelPath('api/recording/status'), { cache: 'no-store' });
        const data = await response.json();
        debugEvent('ui_recording_status_loaded', {
          count: Array.isArray(data.cameras) ? data.cameras.length : 0,
          cameras: (Array.isArray(data.cameras) ? data.cameras : []).map((item) => ({
            index: item.index,
            id: item.id,
            recording: item.recording,
            segments: item.segments,
            record_stream: item.record_stream
          }))
        });
        recordingStatus = {};
        (Array.isArray(data.cameras) ? data.cameras : []).forEach((item) => {
          recordingStatus[item.index] = item;
          const files = Array.isArray(item.files) ? item.files : [];
          const selected = selectedRecording[item.index];
          if (files.length && !files.some((file) => file.url === selected)) {
            selectedRecording[item.index] = files[0].url;
          }
          if (!files.length) {
            delete selectedRecording[item.index];
          }
        });
        if (configDirty) {
          const cameras = config.cameras && config.cameras.length ? config.cameras : [];
          nvrGrid.innerHTML = cameras.map(nvrCard).join('');
        } else {
          renderConfig();
        }
      }

      function moveRecording(index, direction) {
        const files = Array.isArray(recordingStatus[index]?.files) ? recordingStatus[index].files : [];
        if (!files.length) return;
        const current = Math.max(0, files.findIndex((file) => file.url === selectedRecording[index]));
        const next = direction === 'older'
          ? Math.min(files.length - 1, current + 1)
          : Math.max(0, current - 1);
        selectedRecording[index] = files[next].url;
        renderConfig();
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
        config = collectConfig();
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
        document.querySelectorAll('[data-page]').forEach((page) => {
          page.hidden = page.dataset.page !== pageName;
        });
        document.querySelectorAll('[data-page-target]').forEach((button) => {
          button.classList.toggle('active', button.dataset.pageTarget === pageName);
        });
        debugEvent('ui_page_change', { page: pageName });
      }

      function updateLiveTimer() {
        if (liveTimer) {
          window.clearInterval(liveTimer);
          liveTimer = null;
        }
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
        await loadRecordingStatus();
      });

      document.getElementById('add-camera').addEventListener('click', () => {
        debugEvent('ui_add_camera_click');
        addCamera();
      });

      document.getElementById('save-config').addEventListener('click', async () => {
        const payload = collectConfig();
        debugEvent('ui_save_config_click', { summary: saveSummary(payload), cameras: payload.cameras });
        if (!hasMeaningfulCameras(payload)) {
          saveState.textContent = 'Save blocked: at least one camera needs host/IP or RTSP, so existing configuration was not overwritten.';
          debugEvent('ui_save_config_blocked', { reason: 'empty_camera_configuration' });
          return;
        }
        saveState.textContent = `Saving cameras... ${saveSummary(payload)}`;
        const response = await fetch(panelPath('api/config'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          saveState.textContent = data.error || 'Could not save configuration.';
          debugEvent('ui_save_config_error', { status: response.status, data });
          return;
        }
        config = data;
        renderConfig();
        configDirty = false;
        await loadPresets();
        await loadCameras();
        const sentSummary = saveSummary(payload);
        const savedSummary = saveSummary(data);
        saveState.textContent = sentSummary === savedSummary
          ? `Saved. ${savedSummary}`
          : `Saved, but server normalized values. Sent: ${sentSummary} | Server: ${savedSummary}`;
        debugEvent('ui_save_config_done', { sentSummary, savedSummary, normalized: sentSummary !== savedSummary });
      });

      document.getElementById('save-edge-settings').addEventListener('click', async () => {
        const payload = collectEdgeSettings();
        if (!hasMeaningfulCameras(payload)) {
          edgeSaveState.textContent = 'Save blocked: camera configuration is empty, so existing settings were not overwritten.';
          return;
        }
        edgeSaveState.textContent = 'Saving Edge settings...';
        const response = await fetch(panelPath('api/config'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          edgeSaveState.textContent = data.error || 'Could not save Edge settings.';
          return;
        }
        config = data;
        renderConfig();
        if (liveTimer) {
          window.clearInterval(liveTimer);
          liveTimer = null;
          updateLiveTimer();
        }
        edgeSaveState.textContent = 'Saved. Edge settings are stored in /homeassistant/edge/edge.json.';
      });

      document.getElementById('apply-preset').addEventListener('click', applyPresetToSlot);

      form.addEventListener('input', () => {
        configDirty = true;
      });
      form.addEventListener('change', () => {
        configDirty = true;
      });

      form.addEventListener('click', async (event) => {
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
        debugEvent('ui_live_toggle', {
          liveKey,
          cameraIndex: liveTarget?.dataset?.liveIndex,
          enabled: nextLive
        });
        live[liveKey] = nextLive;
        updateLiveTimer();
        await loadCameras();
      });

      nvrGrid.addEventListener('click', async (event) => {
        const index = event.target?.dataset?.recordIndex;
        const playRecording = event.target?.dataset?.playRecording;
        if (index !== undefined && playRecording) {
          selectedRecording[index] = playRecording;
          renderConfig();
          return;
        }
        const playbackStep = event.target?.dataset?.playbackStep;
        if (index !== undefined && playbackStep) {
          moveRecording(index, playbackStep);
          return;
        }
        const action = event.target?.dataset?.recordAction;
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
        await loadRecordingStatus();
        await loadCameras();
      });

      restoreNavState();

      async function boot() {
        await loadConfig();
        await Promise.all([loadPresets(), loadCameras(), loadRecordingStatus()]);
      }

      boot().catch((error) => {
        debugEvent('ui_boot_error', { message: error.message, stack: error.stack });
        grid.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      });
    </script>
  </body>
</html>
"""


class EdgeHandler(BaseHTTPRequestHandler):
    server_version = "EdgePanel/0.7.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[edge-panel] {self.address_string()} {format % args}")

    def handle_webrtc_offer(self) -> None:
        """Handle WebRTC SDP offer from browser.

        Flow (inspired by SharpRTSPtoWebRTC + go2rtc):
        1. Browser sends SDP offer
        2. We find camera, call create_webrtc_session()
        3. Return SDP answer + session_id
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            camera_id = body.get("camera_id") or ""
            offer_sdp = body.get("sdp") or ""
            if not camera_id or not offer_sdp:
                self.send_json({"error": "missing camera_id or sdp"}, HTTPStatus.BAD_REQUEST)
                return
            config = load_config()
            cameras = config.get("cameras") or []
            camera = next((c for c in cameras if c.get("id") == camera_id), None)
            if not camera:
                self.send_json({"error": "camera_not_found"}, HTTPStatus.NOT_FOUND)
                return
            cleanup_webrtc_sessions()
            result = create_webrtc_session(camera, camera_id, offer_sdp)
            self.send_json(result)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_webrtc_keepalive(self) -> None:
        """Keepalive ping to extend WebRTC session TTL."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            sid = body.get("session_id") or ""
            with WEBRTC_LOCK:
                if sid in WEBRTC_SESSIONS:
                    WEBRTC_SESSIONS[sid]["created"] = time.time()
                    self.send_json({"status": "ok"})
                else:
                    self.send_json({"error": "session_not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_webrtc_close(self) -> None:
        """Explicitly close a WebRTC session and free ffmpeg process."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            sid = body.get("session_id") or ""
            with WEBRTC_LOCK:
                session = WEBRTC_SESSIONS.pop(sid, None)
            if session:
                proc = session.get("process")
                if proc and proc.poll() is None:
                    proc.terminate()
                self.send_json({"status": "closed"})
            else:
                self.send_json({"error": "session_not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_bytes(json.dumps(payload).encode("utf-8"), "application/json", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = route_path(parsed.path)

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
        if path == "/api/recording/status":
            self.send_json(recording_status_payload())
            return
        if path == "/api/stream/capabilities":
            self.send_json(stream_capabilities())
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
        if path.startswith("/hls/"):
            self.serve_hls(path)
            return
        if path.startswith("/webrtc/"):
            camera_id = path.split("/", 2)[-1]
            # Return WebRTC player page info (SDP exchange happens via POST /api/webrtc/offer)
            self.send_json({
                "camera_id": camera_id,
                "engine": "webrtc",
                "go2rtc_available": go2rtc_available(),
                "stun_servers": STUN_SERVERS,
                "offer_url": f"/api/webrtc/offer",
                "keepalive_url": f"/api/webrtc/keepalive",
                "status": "ready",
            })
            return
        if path.startswith("/recordings/") and path.endswith(".mp4"):
            self.serve_recording(path)
            return

        self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        _post_path = route_path(parsed.path)
        parsed = parsed._replace(path=_post_path)
        write_debug_event("api_post", {"path": parsed.path, "client": self.client_address[0]})
        if parsed.path == "/api/config":
            self.save_config()
            return
        if parsed.path == "/api/webrtc/offer":
            self.handle_webrtc_offer()
            return
        if parsed.path == "/api/webrtc/keepalive":
            self.handle_webrtc_keepalive()
            return
        if parsed.path == "/api/webrtc/close":
            self.handle_webrtc_close()
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

    def read_body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8")) if body else {}

    def ui_debug(self) -> None:
        try:
            payload = self.read_body_json()
            event = payload.get("event") if isinstance(payload, dict) else "ui_debug"
            write_debug_event(str(event or "ui_debug"), {
                "client": self.client_address[0],
                "user_agent": self.headers.get("User-Agent", ""),
                "ui": payload,
            })
            self.send_json({"ok": True})
        except (json.JSONDecodeError, OSError) as error:
            write_debug_event("ui_debug_error", {"error": str(error)})
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def save_config(self) -> None:
        try:
            raw_payload = self.read_body_json()
            if not isinstance(raw_payload, dict):
                raw_payload = {}
            raw_cameras = raw_payload.get("cameras") if isinstance(raw_payload, dict) else None
            if not raw_cameras:
                existing = load_config()
                if existing.get("cameras"):
                    raw_payload = {**existing, **raw_payload, "cameras": existing["cameras"]}
            validate_config_for_save(raw_payload)
            normalized_payload = normalize_config(raw_payload)
            payload = json.loads(json.dumps(normalized_payload))
            payload = preserve_submitted_stream_choices(payload, raw_payload)
            validate_config_for_save(payload)
            backup_config()
            write_json(CONFIG_PATH, payload)
            save_debug_payload(raw_payload, normalized_payload, payload)
            write_debug_event("config_save", {
                "raw_summary": config_summary(raw_payload),
                "normalized_summary": config_summary(normalized_payload),
                "final_summary": config_summary(payload),
                "changed_by": self.client_address[0],
            })
            stop_orphan_recordings(payload)
            remember_camera_presets(payload.get("cameras", []))
            refresh_status()
            self.send_json(payload)
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

    def serve_live_frame(self, path: str, query: dict[str, list[str]]) -> None:
        camera_id = path.removeprefix("/live-frame/").removesuffix(".jpg")
        stream_name = (query.get("stream") or ["sub"])[0]
        config = load_config()
        cameras = config.get("cameras", [])
        camera = None
        camera_index = (query.get("camera_index") or [""])[0]
        if str(camera_index).isdigit():
            index = int(camera_index)
            if 0 <= index < len(cameras):
                camera = cameras[index]
        if camera is None:
            camera = next((item for item in cameras if item.get("id") == camera_id), None)
        if not camera:
            self.send_json({"error": "camera_not_found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            self.send_bytes(capture_live_frame(camera, stream_name), "image/jpeg")
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)

    def serve_recording(self, request_path: str) -> None:
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
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

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

    def serve_hls(self, request_path: str) -> None:
        parts = request_path.removeprefix("/hls/").split("/", 1)
        if len(parts) != 2:
            self.send_json({"error": "hls_not_found"}, HTTPStatus.NOT_FOUND)
            return
        camera_id, filename = parts
        safe_name = Path(filename).name
        if safe_name not in ("index.m3u8", "init.mp4") and not safe_name.endswith(".m4s"):
            self.send_json({"error": "hls_not_found"}, HTTPStatus.NOT_FOUND)
            return

        config = load_config()
        camera = None
        camera_index = 0
        for index, item in enumerate(config.get("cameras", [])):
            if item.get("id") == camera_id or safe_id(item.get("id") or f"camera_{index + 1}") == camera_id:
                camera = item
                camera_index = index
                break
        if camera is None:
            self.send_json({"error": "camera_not_found"}, HTTPStatus.NOT_FOUND)
            return

        directory = hls_base_dir(camera, camera_index)
        key = hls_key(camera, camera_index)
        log_path = directory / "ffmpeg-hls.log"
        hls_result = {}
        if safe_name == "index.m3u8":
            try:
                hls_result = start_hls(camera, camera_index)
            except (OSError, ValueError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
                return
            playlist = directory / safe_name
            for _ in range(80):
                if playlist.exists() and playlist.stat().st_size > 0:
                    break
                process = HLS_PROCESSES.get(key)
                if process and process.poll() is not None:
                    break
                time.sleep(0.1)

        target = directory / safe_name
        if not target.exists():
            process = HLS_PROCESSES.get(key)
            process_status = {
                "running": bool(process and process.poll() is None),
                "returncode": process.poll() if process else None,
            }
            payload = {
                "error": "hls_not_ready",
                "camera_id": camera.get("id") or camera_id,
                "stream": camera.get("live_stream") or "sub",
                "directory": str(directory),
                "requested": safe_name,
                "started": hls_result,
                "process": process_status,
                "files": sorted(path.name for path in directory.glob("*")) if directory.exists() else [],
                "ffmpeg_tail": redact_rtsp(read_text_tail(log_path)),
            }
            write_debug_event("hls_not_ready", payload)
            self.send_json(payload, HTTPStatus.NOT_FOUND)
            return
        content_type = "application/vnd.apple.mpegurl" if safe_name.endswith(".m3u8") else "video/mp4"
        self.send_bytes(target.read_bytes(), content_type)

    def serve_live(self, path: str, query: dict[str, list[str]]) -> None:
        camera_id = path.removeprefix("/live/").removesuffix(".mjpg")
        requested_stream = normalize_stream_name((query.get("stream") or [""])[0], "")
        allow_stream_override = (query.get("override") or query.get("debug_stream") or ["0"])[0] in ("1", "true", "yes")
        tile_mode = (query.get("tile") or ["0"])[0] in ("1", "true", "yes")
        config = load_config()
        cameras = config.get("cameras", [])
        camera = None
        camera_index = (query.get("camera_index") or [""])[0]
        if str(camera_index).isdigit():
            index = int(camera_index)
            if 0 <= index < len(cameras):
                camera = cameras[index]
        if camera is None:
            camera = next((item for item in cameras if item.get("id") == camera_id), None)
        if not camera:
            write_debug_event("live_error", {"reason": "camera_not_found", "path": path, "query": query})
            self.send_json({"error": "camera_not_found"}, HTTPStatus.NOT_FOUND)
            return

        configured_stream = normalize_stream_name(camera.get("live_stream"), "sub")
        configured_tile_stream = normalize_stream_name(camera.get("tile_stream"), "sub")
        stream_name = requested_stream if requested_stream and allow_stream_override else configured_stream
        if tile_mode:
            if camera_stream(camera, configured_tile_stream):
                stream_name = configured_tile_stream
            elif camera_stream(camera, "sub"):
                stream_name = "sub"
        if requested_stream and requested_stream != configured_stream and not allow_stream_override:
            write_debug_event("live_stream_override_ignored", {
                "camera_id": camera.get("id") or camera_id,
                "requested_stream": requested_stream,
                "configured_live": configured_stream,
                "path": self.path,
            })

        stream = camera_stream(camera, stream_name)
        if not stream:
            write_debug_event("live_error", {"reason": "rtsp_not_configured", "camera_id": camera.get("id"), "stream": stream_name})
            self.send_json({"error": "rtsp_not_configured"}, HTTPStatus.BAD_REQUEST)
            return

        probe = probe_rtsp_stream(stream, timeout=5)
        camera_profile = camera_debug_profile(camera, stream_name, probe)
        live_config = config.get("live") if isinstance(config.get("live"), dict) else {}
        mjpeg_fps = clamp_int(live_config.get("mjpeg_fps"), 5, 1, 15)
        mjpeg_quality = clamp_int(live_config.get("mjpeg_quality"), 8, 2, 31)
        mjpeg_max_width = clamp_int(live_config.get("mjpeg_max_width"), 1280, 320, 3840)
        if tile_mode:
            mjpeg_fps = min(mjpeg_fps, 5)
            mjpeg_max_width = min(mjpeg_max_width, 960)
        vf = f"fps={mjpeg_fps},scale=w='min({mjpeg_max_width}\\,iw)':h=-2"

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-nostdin",
            "-fflags",
            "nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "5000000",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-thread_queue_size",
            "512",
            "-i",
            stream,
            "-an",
            "-vf",
            vf,
            "-g",
            "1",
            "-vsync",
            "0",
            "-preset",
            "ultrafast",
            "-vcodec",
            "mjpeg",
            "-q:v",
            str(mjpeg_quality),
            "-flush_packets",
            "1",
            "-f",
            "image2pipe",
            "-",
        ]
        log_path = HOME_DIR / f"live-{safe_id(camera.get('id') or camera_id)}.log"
        log_file = log_path.open("ab")
        request_id = f"{int(time.time())}-{safe_id(stream_name)}"
        begin_marker = f"=== live request {request_id} begin"
        started_at = time.monotonic()
        write_debug_event("live_start", {
            "request_id": request_id,
            "client": self.client_address[0],
            "path": self.path,
            "user_agent": self.headers.get("User-Agent", ""),
            "camera": camera_profile,
            "command": redact_command(command),
            "mjpeg": {
                "tile": tile_mode,
                "fps": mjpeg_fps,
                "quality": mjpeg_quality,
                "max_width": mjpeg_max_width,
                "vf": vf,
            },
        })
        log_file.write(
            (
                f"\n=== live request {request_id} begin {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ===\n"
                f"client={self.client_address[0]} path={self.path}\n"
                f"user_agent={self.headers.get('User-Agent', '')}\n"
                f"camera={camera.get('id') or camera_id} index={camera_index or 'lookup'} "
                f"tile_mode={tile_mode} requested_stream={requested_stream or 'none'} stream={stream_name} configured_live={configured_stream} "
                f"status={camera.get('status', 'unknown')}\n"
                f"rtsp={redact_rtsp(stream)}\n"
                f"command={json.dumps(redact_command(command))}\n"
                "--- ffmpeg stderr begin ---\n"
            ).encode("utf-8")
        )
        log_file.flush()
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log_file)
        except OSError as error:
            log_file.close()
            write_debug_event("live_process_start_error", {
                "request_id": request_id,
                "camera": camera_profile,
                "error": str(error),
            })
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        finally:
            log_file.close()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=edgeframe")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        frame_count = 0
        bytes_sent = 0
        first_frame_ms = None
        end_reason = "ffmpeg_stdout_closed"
        cleanup = "none"
        try:
            assert process.stdout is not None
            buffer = b""
            while True:
                chunk = process.stdout.read(16384)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start > 0:
                        buffer = buffer[start:]
                    end = buffer.find(b"\xff\xd9")
                    if start == -1 or end == -1:
                        break
                    frame = buffer[: end + 2]
                    buffer = buffer[end + 2 :]
                    self.wfile.write(b"--edgeframe\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    frame_count += 1
                    bytes_sent += len(frame)
                    if first_frame_ms is None:
                        first_frame_ms = int((time.monotonic() - started_at) * 1000)
                        try:
                            with log_path.open("ab") as live_log:
                                live_log.write(f"\n--- first_frame_ms={first_frame_ms} ---\n".encode("utf-8"))
                        except OSError:
                            pass
        except BrokenPipeError:
            end_reason = "client_broken_pipe"
        except ConnectionResetError:
            end_reason = "client_connection_reset"
        except OSError as error:
            end_reason = f"stream_write_oserror:{error}"
        finally:
            if frame_count == 0 and end_reason == "ffmpeg_stdout_closed":
                end_reason = "ffmpeg_ended_before_first_frame"
            try:
                if process.stdout is not None:
                    process.stdout.close()
            except OSError:
                pass
            exit_before_cleanup = process.poll()
            if exit_before_cleanup is None:
                cleanup = "terminate"
                process.terminate()
            exit_after_cleanup = process.poll()
            try:
                if exit_after_cleanup is None:
                    exit_after_cleanup = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cleanup = "kill_after_terminate_timeout"
                process.kill()
                exit_after_cleanup = process.wait(timeout=2)
            duration_ms = int((time.monotonic() - started_at) * 1000)
            stderr_text = ""
            try:
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                stderr_text = extract_ffmpeg_stderr(log_text, begin_marker)
            except OSError:
                stderr_text = ""
            category, hints = classify_ffmpeg_error(stderr_text, frame_count, end_reason, bytes_sent)
            write_debug_event("live_end", {
                "request_id": request_id,
                "camera": camera_profile,
                "mjpeg": {
                    "tile": tile_mode,
                    "fps": mjpeg_fps,
                    "quality": mjpeg_quality,
                    "max_width": mjpeg_max_width,
                    "vf": vf,
                },
                "reason": end_reason,
                "category": category,
                "hints": hints,
                "frames": frame_count,
                "bytes": bytes_sent,
                "first_frame_ms": first_frame_ms,
                "duration_ms": duration_ms,
                "exit_before_cleanup": exit_before_cleanup,
                "exit_after_cleanup": exit_after_cleanup,
                "cleanup": cleanup,
                "ffmpeg_tail": stderr_text[-3000:],
            })
            try:
                with log_path.open("ab") as live_log:
                    live_log.write(
                        (
                            "\n--- ffmpeg stderr end ---\n"
                            f"summary request={request_id} reason={end_reason} "
                            f"frames={frame_count} bytes={bytes_sent} "
                            f"first_frame_ms={first_frame_ms if first_frame_ms is not None else 'none'} "
                            f"duration_ms={duration_ms} "
                            f"exit_before_cleanup={exit_before_cleanup} "
                            f"exit_after_cleanup={exit_after_cleanup} "
                            f"cleanup={cleanup}\n"
                            f"=== live request {request_id} end {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ===\n"
                        ).encode("utf-8")
                    )
            except OSError:
                pass


def main() -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
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
        status_payload = refresh_status()
        write_debug_event("boot_refresh_status_done", {"camera_summary": config_summary(status_payload)})
    except Exception as error:
        write_debug_event("boot_refresh_status_error", {"error": str(error), "type": type(error).__name__})
        raise
    server = ThreadingHTTPServer(("0.0.0.0", PORT), EdgeHandler)
    print(f"[edge-panel] listening on 0.0.0.0:{PORT}")
    write_debug_event("boot_server_listening", {"listen": f"0.0.0.0:{PORT}"})
    server.serve_forever()


if __name__ == "__main__":
    main()
