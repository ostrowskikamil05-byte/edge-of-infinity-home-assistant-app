#!/usr/bin/env python3
"""Edge of Infinity panel server."""

from __future__ import annotations

import json
import base64
import signal
import functools
import gzip
import os
import platform
import re
import shutil
import subprocess
import sys
import hashlib
import hmac
import threading
import tempfile
import time
from datetime import date, timedelta, datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlunparse, quote, unquote
from xml.sax.saxutils import escape as xml_escape

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
from edge_detection import DetectionService, settings as detection_settings, zone_settings, ZONE_FIELDS
from edge_storage import MIB, SEGMENT, managed_files, prune_cache, trim_log
from edge_recording_media import AlertMediaService, video_timing, aac_encoding_options
from edge_recording_index import RecordingIndexCache
from edge_recording_compat import CompatibleRecordings
from edge_metrics import CpuSampler, cpu_load_severity, hardware_inventory
from edge_camera_keyframes import CameraKeyframes
from edge_camera_gop import set_onvif_gop
from edge_native_migration import native_options
sys.path.insert(0,str(Path(__file__).parent/'custom_components/edge'))
from _device_logs import DeviceLogs, safe_url

DETECTION_SERVICE = None
ALERT_SERVICE = None
CPU_SAMPLER=CpuSampler()


def alert_image_path(ident):
    if not 0<int(ident)<=9223372036854775807:raise ValueError('invalid_alert_id')
    return HOME_DIR/'alerts'/f'event-{int(ident)}.jpg'


def save_alert_image(event, frame):
    data=DETECTION_SERVICE.store.get(event['id']) or {}
    target=alert_image_path(event['id']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.parent.is_symlink():raise RuntimeError('invalid_alert_directory')
    payload=frame.jpeg(data.get('objects'))
    temporary=target.with_suffix('.tmp');temporary.write_bytes(payload);temporary.replace(target)
    target.chmod(0o600);os.utime(target,(time.time(),event['timestamp']))
    write_json(target.with_suffix('.json'),{key:event[key] for key in ('id','camera_id','kind','timestamp')})
    DETECTION_SERVICE.store.update_media(event['id'],has_image=True)


def save_alert_fallback_image(event, clip, offset):
    target=alert_image_path(event['id']);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.tmp')
    try:
        result=subprocess_run_low_priority(build_recording_thumbnail_command(clip,temporary,offset),capture_output=True,timeout=15)
        if result.returncode or not temporary.exists():raise RuntimeError('alert_thumbnail_failed')
        temporary.replace(target);target.chmod(0o600);os.utime(target,(time.time(),event['timestamp']))
        write_json(target.with_suffix('.json'),{key:event[key] for key in ('id','camera_id','kind','timestamp')})
        DETECTION_SERVICE.store.update_media(event['id'],has_image=True)
    finally:temporary.unlink(missing_ok=True)


def alert_source_entries(camera_id, start, end):
    config=load_config();match=next(((i,c) for i,c in enumerate(config.get('cameras',[])) if c['id']==camera_id),None)
    if not match:return []
    index,camera=match;rows=[]
    for path,stat,stamp in recording_file_candidates(recording_base_dir(camera,index))[0]:
        if stamp>end or stamp<start-3600:continue
        timing=video_timing(path);duration=timing['duration'] or mp4_duration_seconds(path)
        if duration and stamp+duration>start:rows.append({'path':path,'start_ts':stamp,'duration':duration})
    return sorted(rows,key=lambda row:row['start_ts'])


def enqueue_alert(event, frame):
    if frame is not None:
        try:save_alert_image(event,frame);frame=None
        except Exception:write_debug_event('alert_image_error',{'id':event['id']})
    if ALERT_SERVICE:ALERT_SERVICE.enqueue(event,frame)


def alert_payload(event):
    if not event:return None
    result=dict(event)
    result['image_url']=f"api/alerts/{event['id']}/image" if alert_image_path(event['id']).is_file() else ''
    result['clip_url']=f"api/alerts/{event['id']}/clip.mp4" if ALERT_SERVICE and ALERT_SERVICE.path(event['id']).is_file() else ''
    return result


APP_VERSION = "0.16.0"
APP_RELEASE_FEATURES=['Native-only DVR in panel and HA cards; MP4 retained as original downloads',
                      'Viewport-aware Live/DVR and smaller archive batches preserve original resolution/FPS',
                      'Vertical dashboard gestures, stable thumbnail DOM and bounded per-user JPEG cache',
                      'EdgeRTC-only Live, recording and DVR; retired engines and options removed with bounded migration',
                      'DVR AAC uses timestamp lookahead, bounded PCM and decoder backpressure; original Live policy retained',
                      'UNV malformed optional talk AAC compatibility; valid forward H265/AAC and G711 mic share one connection',
                      'EdgeRTC DVR: indexed original H264/H265/AAC, epoch seeks and one bounded persistent socket',
                      'RTSP connection phases, response codes and safe advertised codec diagnostics',
                      'Same-connection optional microphone fallback and explicit missing-recorder diagnostics',
                      'Deferred recorder metadata starts retry without UI polling or extra camera channels',
                      'Known Uniview ONVIF aliases and distinct metadata waiting diagnostics',
                      'Source-specific RTP payload diagnostics and bounded SDP refresh without parallel camera connections',
                      'Actual video previews, frame steps, gray/green alert track and same-socket file transitions',
                      'Device codec negotiation with explicit unsupported-profile diagnostics; original MP4 downloads and existing AAC retained',
                      'Native Live status and recording codec decisions use the existing source registry instead of RTSP probes',
                      'In-band camera parameters reach shared RTSP readers; bounded metadata startup wait',
                      'Verified explicit GOP 1–2 s actions and FFmpeg runtime diagnostics separate from command headers']
SERVER_VERSION = f"EdgePanel/{APP_VERSION}"
UI_BUILD = "native-dashboard-v160"
MAX_REQUEST_BODY_BYTES = 2_000_000
HOME_DIR = Path(os.environ.get("EDGE_HOME_DIR", "/homeassistant/edge"))
DATA_DIR = Path(os.environ.get("EDGE_DATA_DIR", "/tmp/edge-placeholder"))
RECORDING_COMPAT = CompatibleRecordings(DATA_DIR / 'recording-compat',log=lambda event,payload:append_jsonl_log(HOME_DIR/'playback.jsonl',event,payload))
DEVICE_LOGS=DeviceLogs(HOME_DIR/'device-logs')
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
RECORDING_SOURCE_WAIT = {}
RECORDING_SOURCE_RETRY_RUNNING = False
RECORDING_SOURCE_RETRY_STOP = threading.Event()
RECORDING_SOURCE_RETRY_SECONDS = 5
RECORDING_CACHE_WORKERS: set[str] = set()
RECORDING_CACHE_LOOP_STARTED = False
RECORDING_THUMBNAIL_WARMUP_LOOP_STARTED = False
LAST_RECORDING_ENSURE_AT = 0.0
DEBUG_LOCK = threading.Lock()
RECORDING_ENSURE_LOCK = threading.Lock()
CONFIG_LOCK = threading.RLock()
SUPERVISOR_OPTIONS_LOCK = threading.RLock()
CONFIG_SAVE_LOCK = threading.Lock()
RECORDING_CONTROL_LOCK = threading.RLock()
RECORDING_DURATION_CACHE: dict[str, tuple[int, int, int]] = {}
RECORDING_DURATION_LOCK = threading.Lock()
RECORDING_CACHE_LOCK = threading.Lock()
RECORDING_CACHE_BUILD_SEMAPHORE = threading.BoundedSemaphore(value=1)
RECORDING_THUMBNAIL_SEMAPHORE = threading.BoundedSemaphore(value=1)
STORAGE_CLEANUP_LOCK = threading.Lock()
STORAGE_USAGE_LOCK = threading.Lock()
STORAGE_USAGE_CACHE = {}
RECORDING_CACHE_ACCESS = {}
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
    "manufacturer",
    "model",
    "firmware",
    "enabled",
    "events_enabled",
    "local_detection_enabled",
    *ZONE_FIELDS,
    "record",
    "low_latency",
    *STREAM_ROLE_FIELDS,
)
EDGE_RTC_ENABLED = True
EDGE_RTC_CONFIG_PATH = Path(os.environ.get("EDGE_RTC_CONFIG", "/tmp/edge-runtime/edge-rtc.json"))
EDGE_RTC_RTSP_PORT = int(os.environ.get("EDGE_RTC_RTSP_PORT", "8556"))
EDGE_RTC_API_PORT = int(os.environ.get("EDGE_RTC_API_PORT", "9997"))


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value or "camera")


def redact_rtsp(value: str) -> str:
    return re.sub(r"(rtsps?://[^:/@\s\"']+:)[^@\s\"']+@", r"\1***@", value or "")


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
            if any(secret in lowered for secret in ("password", "token", "authorization", "secret", "authsig")):
                redacted[key] = "***"
            else:
                redacted[key] = redact_for_log(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    if isinstance(value, str):
        value=redact_rtsp(value)
        value=re.sub(r'(?i)((?:authsig|access_token|password|token|secret)=)[^&\s"#]+',r'\1***',value)
        value=re.sub(r'(?i)("(?:authsig|access_token|password|token|secret)"\s*:\s*")[^"]*(")',r'\1***\2',value)
        return re.sub(r'/api/(?:hassio_ingress|hassio/ingress)/[^/\s?]+','/ingress',value)
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
            trim_log(DEBUG_LOG_PATH)
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
        with DEBUG_LOCK:
            trim_log(path)
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
        "manufacturer":camera.get('manufacturer'),"model":camera.get('model'),"firmware":camera.get('firmware'),
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


def refresh_camera_http_endpoint(value: str, host: str, default_path: str = '/onvif/device_service', default_port: int = 80) -> str:
    """Rebind the configured camera host, retaining discovered HTTP ports/paths."""
    if not host:
        return value or ''
    target = urlparse(f'http://{host}')
    default_url = f'http://{target.netloc if target.port else target.netloc + ":" + str(default_port)}{default_path}'
    parsed = urlparse(value or default_url)
    if parsed.scheme not in ('http', 'https'):
        parsed = urlparse(default_url)
    hostname = target.hostname or host
    hostname = f'[{hostname}]' if ':' in hostname else hostname
    port = target.port or parsed.port
    return parsed._replace(netloc=f'{hostname}:{port}' if port else hostname, fragment='').geturl()


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
    else:
        onvif_url = refresh_camera_http_endpoint(raw.get('onvif_url') or '', host, default_port=onvif_port)

    return {
        "id": camera_id,
        "name": name,
        "vendor": vendor,
        "manufacturer": str(raw.get("manufacturer") or "")[:120],
        "model": str(raw.get("model") or "")[:120],
        "firmware": str(raw.get("firmware") or "")[:120],
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
        "events_enabled": safe_bool(raw.get("events_enabled"), False),
        "local_detection_enabled": safe_bool(raw.get("local_detection_enabled"), False),
        **dict(zip(ZONE_FIELDS, [zone_settings(raw)['enabled'], *zone_settings(raw)['bounds']])),
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
        "live": {"engine": "edge_rtc"},
        "nvr": {
            "segment_seconds": clamp_int(nvr.get("segment_seconds"), 10, 2, 300),
            "retention_days": clamp_int(nvr.get("retention_days"), safe_int(storage.get("retention_days"), 14), 1, 365),
            "playback_cache_segments": clamp_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS, 12, RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS),
            "playback_cache_mb": clamp_int(nvr.get("playback_cache_mb"), 0, 0, 2048),
            "thumbnail_cache_mb": clamp_int(nvr.get("thumbnail_cache_mb"), 32, 1, 256),
            "copy_all_streams": bool(nvr.get("copy_all_streams", True)),
            "browser_playback": "copy",
        },
        "cameras": normalized,
        "detection": detection_settings(payload.get("detection")),
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
                merged_camera["onvif_url"] = refresh_camera_http_endpoint(old_endpoint, merged_camera.get('host') or '')
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
        "inventory":hardware_inventory(),
        "cpu_usage": CPU_SAMPLER.sample(),
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
    if title.startswith('Recording FFmpeg'):
        runtime=ffmpeg_runtime_messages(tail)
        severity=diagnostic_severity_from_text(runtime)
        detail='Logged recorder command; no FFmpeg runtime error in this tail.' if not runtime else 'FFmpeg runtime messages evaluated separately from its startup command.'
        return diagnostic_item(severity,title,detail,{'tail':tail,'runtime_tail':runtime[-4000:]})
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
            "engines": {"edge_rtc": True, "rtsp_port": EDGE_RTC_RTSP_PORT, "api_port": EDGE_RTC_API_PORT},
        }
    )


def build_panel_diagnostics(config: dict, hardware: dict, tails: dict, recording_logs: list[dict], detection: dict | None = None, runtime=None) -> list[dict]:
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
        severity = cpu_load_severity(hardware)
        usage=hardware.get('cpu_usage') or {}
        detail=f"Host load per core: {load_per_core} (1 minute)."
        if usage:detail+=f" CPU busy {usage['busy_percent']}%; I/O wait {usage['io_wait_percent']}%."
        else:detail+=' Refresh diagnostics for measured CPU usage.'
        if (hardware.get('system_uptime_seconds') or 0)<600:detail+=' Host is still in its first 10 minutes after boot.'
        detail+=' Load includes all Home Assistant apps and I/O waits.'
        diagnostics.append(
            diagnostic_item(
                severity,
                "CPU load",
                detail,
                {"loadavg": hardware.get("loadavg"), "cpu_count": hardware.get("cpu_count"),'cpu_usage':usage},
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
    expected={recording_key(camera,index) for index,camera in enumerate(config.get('cameras',[])) if camera_should_record(camera)}
    missing=sorted(expected-{row.get('key') for row in active_recorders})
    diagnostics.append(diagnostic_item("warning" if missing else "ok", "NVR recorders", f"{len(active_recorders)} active recorder process(es); {len(expected)} configured."+(f" Missing: {', '.join(missing)}." if missing else ''), {"active_recorders": active_recorders,'missing_recorders':missing,'expected_recorders':len(expected)}))
    cameras={safe_id(camera.get('id') or f'camera_{index+1}'):camera for index,camera in enumerate(config.get('cameras',[]))}
    for source in ((runtime or {}).get('edge_rtc') or {}).get('sources',[]):
        camera=cameras.get(source.get('camera_id')) or {};connection=source.get('connection') or {};failure=connection.get('last_error') or {}
        title=f"RTSP · {camera.get('name') or source.get('camera_id')}"
        if not source.get('upstream_connections') and failure:
            detail=f"{failure.get('code','rtsp_negotiation_failed')}; etap {failure.get('phase','unknown')}; metoda {failure.get('rtsp_method') or '—'}; RTSP {failure.get('rtsp_status') or 'brak odpowiedzi'}."
            diagnostics.append(diagnostic_item('error',title,detail,{'camera_id':source.get('camera_id'),'connection':connection,'packets':source.get('packets',0)}))
        elif source.get('ready') and (source.get('video') or {}).get('parameters_ready',False):
            diagnostics.append(diagnostic_item('ok',title,'Wspólne źródło odbiera obraz.',{'camera_id':source.get('camera_id'),'connection':connection}))
        else:
            diagnostics.append(diagnostic_item('warning',title,'Oczekiwanie na połączenie lub parametry obrazu.',{'camera_id':source.get('camera_id'),'connection':connection,'packets':source.get('packets',0)}))
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

    if detection is not None:
        selected=[camera for camera in detection.get('cameras',[]) if camera.get('local_status')!='disabled']
        severity='error' if any(camera.get('local_status')=='error' for camera in selected) else 'warning' if any(camera.get('local_status')!='running' or (camera.get('decoder') or {}).get('backend')=='cpu_keyframes' for camera in selected) else 'ok'
        diagnostics.append(diagnostic_item(severity,'Local AI',f"{len(selected)} selected camera(s); model {'loaded' if detection.get('model_loaded') else 'not loaded'}.",detection))
    for title, tail in tails.items():
        if title=='Recording cache' and recording_cache_budget(config)<=0:
            diagnostics.append(diagnostic_item('ok',title,'Playback copy cache is disabled. Retained messages below are historical.',{'tail':tail[-2000:]}));continue
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
                "tail": current_recording_log_tail(log_path, 8000),
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
    detection=DETECTION_SERVICE.snapshot() if DETECTION_SERVICE else {}
    runtime=engine_runtime_status()

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "server_version": SERVER_VERSION,
        "ui_build": UI_BUILD,
        "authoritative_config": str(CONFIG_PATH),
        "release_features":APP_RELEASE_FEATURES,
        "addon_config_mirror": str(ADDON_CONFIG_PATH),
        "panel_mirror_config": str(PANEL_CONFIG_PATH),
        "config_summary": config_summary(config),
        "hardware": hardware,
        "playback":RECORDING_COMPAT.status(),
        "playback_log":redact_rtsp(read_text_tail(HOME_DIR/'playback.jsonl',20000)),
        "camera_media":safe_json_file(DATA_DIR/'cameras.json'),
        "camera_discovery_log":redact_rtsp(read_text_tail(HOME_DIR/'camera-discovery.jsonl',16000)),
        "runtime_parameters": runtime_parameters_payload(config, hardware, recordings_dir),
        "diagnostics": build_panel_diagnostics(config, hardware, tails, recording_logs,detection,runtime),
        "detection":detection,
        "edge_debug": tails["Edge debug"],
        "last_save_debug": safe_json_file(HOME_DIR / "last-save-debug.json"),
        "last_runtime_sync": safe_json_file(HOME_DIR / "edge.last-runtime-sync.json"),
        "last_saved_config": safe_json_file(HOME_DIR / "edge.last-saved.json"),
        "addon_config_file": safe_json_file(ADDON_CONFIG_PATH),
        "panel_config": safe_json_file(PANEL_CONFIG_PATH),
        "runtime_config_file": safe_json_file(CONFIG_PATH),
        "runtime_config": runtime,
        "native_migration": safe_json_file(HOME_DIR / "native-migration.json"),
        "recording_stream_log": tails["Recording stream"],
        "recording_cache_log": tails["Recording cache"],
        "recording_cache_enabled":recording_cache_budget(config)>0,
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
    for camera in cameras:
        if not isinstance(camera, dict):
            raise ValueError('Invalid camera settings.')
        zone = zone_settings(camera)
        if zone['enabled'] and not zone['valid']:
            raise ValueError("Strefa AI musi mieścić się w obrazie i mieć co najmniej 0,5% szerokości oraz wysokości.")
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


def edge_stream_path(camera: dict, index: int, stream_name: str) -> str:
    camera_id = safe_id(camera.get("id") or f"camera_{index + 1}")
    stream_name = normalize_stream_name(stream_name, "sub")
    return f"{camera_id}_{stream_name}"


def edge_rtsp_url(camera: dict, index: int, stream_name: str) -> str:
    return f"rtsp://127.0.0.1:{EDGE_RTC_RTSP_PORT}/{edge_stream_path(camera, index, stream_name)}"


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
    available = bool(_HA_RESTREAM_HOST)
    cameras = []
    for index, camera in enumerate(config.get("cameras", [])):
        streams = {}
        recording = camera.get("record_stream") or "main"
        if not camera.get("rtsp_" + recording):
            recording = "main"
        if available and camera.get("enabled"):
            for profile in ("main", "sub"):
                if profile != recording:
                    continue
                if camera.get("rtsp_" + profile):
                    streams[profile] = f"rtsp://{_HA_RESTREAM_HOST}:{EDGE_RTC_RTSP_PORT}/{edge_stream_path(camera, index, profile)}"
        streams["nvr"] = streams.get(recording) or streams.get("main") or ""
        row = {"camera_id": camera.get("id"), "streams": streams}
        if available:
            row["edge_rtc"] = {"api": f"http://{_HA_RESTREAM_HOST}:{EDGE_RTC_API_PORT}", "path": edge_stream_path(camera, index, recording), "profile": recording,"keyframe_api":f"http://{_HA_RESTREAM_HOST}:{PORT}/api/edge-rtc/keyframe/{safe_id(camera.get('id','camera'))}"}
        cameras.append(row)
    return {"available": available, "cameras": cameras, "source": "edge_rtc", "version": APP_VERSION}


def recording_source_stream(camera: dict, index: int, stream_name: str) -> tuple[str, str]:
    return edge_rtsp_url(camera, index, stream_name), ("edge_rtc_shared")










def append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        return [*values, value]
    return values




















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
    options = native_options(existing)
    cameras = [{key: camera.get(key) for key in ("id", "name", "vendor", "host", "username", "password", "rtsp_main", "rtsp_sub", "camera_number", "access_protocol", "rtsp_transport", "onvif_url", "isapi_base_url", "manufacturer", "model", "firmware", "enabled", "events_enabled", "local_detection_enabled", "record", "low_latency", "snapshot_stream", "live_stream", "tile_stream", "record_stream")} for camera in config.get("cameras", [])]
    for projected, camera in zip(cameras, config.get("cameras", [])):
        zone = zone_settings(camera)
        projected.update(dict(zip(ZONE_FIELDS, [zone['enabled'], *zone['bounds']])))
    options["cameras"] = cameras
    detect = detection_settings(config.get("detection"))
    for option, field in (("camera_events_enabled", "camera_events_enabled"), ("local_detection_enabled", "local_enabled"), ("detection_fps", "max_fps"), ("detection_decoder", "decoder"), ("detection_confidence", "confidence"), ("detection_hold_seconds", "hold_seconds"), ("detection_animal_confidence", "animal_confidence"), ("detection_person_enabled", "person_enabled"), ("detection_animal_enabled", "animal_enabled"), ("detection_vehicle_enabled", "vehicle_enabled"), ("detection_alert_media_mb", "alert_media_mb"), ("detection_pre_seconds", "pre_seconds"), ("detection_post_seconds", "post_seconds"), ("detection_notifications_enabled", "notifications_enabled"), ("detection_notify_service", "notify_service"), ("detection_notify_cooldown", "notify_cooldown"), ("detection_notify_path", "notify_path")):
        options[option] = detect[field]
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
    with SUPERVISOR_OPTIONS_LOCK:
        return _sync_supervisor_options(config)






def _sync_supervisor_options(config: dict) -> dict:
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
    from edge_rtc_config import write_config
    with CONFIG_LOCK:
        current = load_config()
        write_config(current, HOME_DIR, EDGE_RTC_CONFIG_PATH, EDGE_RTC_RTSP_PORT, EDGE_RTC_API_PORT)
        write_json(HOME_DIR / "restream.json", home_assistant_restream_payload(current))
    result = {"reason": reason, "edge_rtc": {"written": True, "cameras": len(current.get("cameras", []))}}
    write_json(HOME_DIR / "edge.last-runtime-sync.json", result)
    write_debug_event("native_runtime_config_sync", result)
    return result









def edge_rtc_status() -> dict:
    import urllib.request
    try:
        token = (HOME_DIR / "edge-rtc-token").read_text().strip()
        request = urllib.request.Request(f"http://127.0.0.1:{EDGE_RTC_API_PORT}/api/status", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=1) as response:
            return {"enabled": True, "api_reachable": True, **json.loads(response.read())}
    except (OSError, ValueError):
        return {"enabled": EDGE_RTC_ENABLED, "api_reachable": False}


def engine_runtime_status() -> dict:
    return {"architecture": "camera RTSP -> shared EdgeRTC source -> Live, recorder and analysis; indexed EdgeRTC DVR reads saved files",
            "edge_rtc": edge_rtc_status(),
            "codec_notes": {"video": "Original H264/H265, resolution and FPS; no video re-encoding.",
                            "audio": "Source AAC copy; G711 is converted once when required for saved playback.",
                            "microphone": "PCM over authenticated WS -> RTSP G711 backchannel on the existing camera session."}}



def stream_urls(camera: dict, index: int) -> dict:
    return {"edge_rtc_main": f"api/edge-rtc/player/{edge_stream_path(camera, index, 'main')}",
            "edge_rtc_sub": f"api/edge-rtc/player/{edge_stream_path(camera, index, 'sub')}",
            "shared_rtsp_main": edge_rtsp_url(camera, index, 'main'),
            "shared_rtsp_sub": edge_rtsp_url(camera, index, 'sub'),
            "rtsp_main": redact_rtsp(camera.get("rtsp_main") or ""),
            "rtsp_sub": redact_rtsp(camera.get("rtsp_sub") or "")}



def stream_capabilities(config: dict | None = None) -> dict:
    config = config or load_config()
    runtime = engine_runtime_status()
    return {"engines": {"edge_rtc": {"status": "active" if runtime["edge_rtc"].get("api_reachable") else "starting", "transport": "edgertc/1-websocket-webcodecs"}},
            "archive": {"transport": "edgertc/archive/1", "fallback": "native_mp4", "audio_buffer_limit_ms": 600},
            "codec_policy": {"live": "Original H264/H265 without re-encoding", "recording": "Original video/FPS and source AAC", "microphone": "PCM16LE 8kHz -> shared RTSP G711"},
            "runtime": runtime,
            "cameras": [{"id": c.get("id"), "record_stream": c.get("record_stream") or "main"} for c in config.get("cameras", [])]}



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


def recording_timezone_signature():
    return (time.tzname,time.timezone,time.altzone,time.daylight,os.environ.get('TZ',''))


@functools.lru_cache(maxsize=50000)
def recording_filename_timestamp(name,zone):
    for pattern in (r'^(\d{8})-(\d{6})',r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})'):
        match=re.search(pattern,name)
        if not match:continue
        stamp=''.join(match.groups())
        try:return int(datetime(int(stamp[:4]),int(stamp[4:6]),int(stamp[6:8]),int(stamp[8:10]),int(stamp[10:12]),int(stamp[12:14])).timestamp())
        except (OverflowError,ValueError,OSError):pass
    return None


def recording_segment_start_ts(path: Path, fallback_ts: float) -> int:
    parsed=recording_filename_timestamp(path.stem,recording_timezone_signature())
    return int(fallback_ts) if parsed is None else parsed


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


def recording_thumbnail_url(key: str, path: Path, stat=None) -> str:
    base = f"recording-thumbs/{key}/{path.name}.jpg"
    try:
        stat = stat or path.stat()
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
        probe = {}
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
        parsed=date.fromisoformat(day_key)
        return int(datetime(parsed.year,parsed.month,parsed.day).timestamp())
    except (OverflowError, OSError, ValueError):
        return 0


def recording_day_bounds(day_key: str) -> tuple[int, int]:
    return recording_day_bounds_cached(day_key,recording_timezone_signature())


@functools.lru_cache(maxsize=1024)
def recording_day_bounds_cached(day_key,zone):
    start = recording_day_start_ts(day_key)
    if not start:
        return 0, 0
    next_day = (date.fromisoformat(day_key) + timedelta(days=1)).isoformat()
    return start, recording_day_start_ts(next_day)


def recording_day_is_today(day_key: str) -> bool:
    return bool(day_key) and day_key == recording_day_key(time.time())


def recording_file_candidates(directory: Path) -> tuple[list, int]:
    """One metadata pass per request; never open video payloads for the day list."""
    candidates, total = [], 0
    now = time.time()
    opened=active_recording_files()
    try:
        with os.scandir(directory) as files:
            for item in files:
                if not item.name.endswith('.mp4') or not item.is_file(follow_symlinks=False):
                    continue
                total += 1
                try:
                    stat = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.st_size < RECORDING_FILE_MIN_PLAYABLE_BYTES or now - stat.st_mtime < MIN_RECORDING_FILE_READY_SECONDS:
                    continue
                path = Path(item.path)
                if str(path) in opened:continue
                candidates.append((path, stat, recording_segment_start_ts(path, stat.st_mtime)))
    except FileNotFoundError:
        pass
    return candidates, total


def active_recording_files() -> set[str]:
    """Find the actual writer's open MP4s, even during long buffered GOP gaps."""
    result=set()
    for process in list(RECORDING_PROCESSES.values()):
        pid=getattr(process,'pid',None)
        if not isinstance(pid,int):continue
        try:
            for descriptor in (Path('/proc')/str(pid)/'fd').iterdir():
                try:target=os.readlink(descriptor)
                except OSError:continue
                if target.endswith('.mp4'):result.add(target)
        except OSError:continue
    return result


def recording_file_entries(camera: dict, index: int, limit: int = 1000, segment_seconds: int = 10, day_key: str = "", candidates: list | None = None, measure_durations: bool = True) -> list[dict]:
    directory = recording_base_dir(camera, index)
    if not directory.exists():
        return []
    candidates = sorted(candidates if candidates is not None else recording_file_candidates(directory)[0], key=lambda item: (item[2], item[0].name))
    entries = []
    offset = 0
    selected_files = candidates if safe_int(limit, 0) <= 0 else candidates[-safe_int(limit, 0):]
    items = []
    for path, stat, start_ts in selected_files:
        calendar=recording_calendar_metadata(start_ts,recording_timezone_signature())
        items.append({"path": path, "stat": stat, "start_ts": start_ts, "day": calendar[0], "calendar":calendar})
    for position, item in enumerate(items):
        path = item["path"]
        start_ts = item["start_ts"]
        entry_day = item['day']
        if day_key and entry_day != day_key:
            continue
        next_item = items[position + 1] if position + 1 < len(items) else None
        next_start_ts = next_item.get("start_ts") if next_item and next_item.get("day") == entry_day else None
        duration_seconds = recording_inferred_duration_seconds(start_ts, next_start_ts, segment_seconds)
        if measure_durations:
            # Listing must never spawn a decoder/probe for damaged or pending
            # historical files. Read the MP4 index, otherwise retain the estimate.
            duration_seconds = recording_duration_seconds(path, item["stat"], duration_seconds, allow_probe=False)
        else:
            with RECORDING_DURATION_LOCK:
                cached_duration = RECORDING_DURATION_CACHE.get(str(path))
            if cached_duration and cached_duration[:2] == (item['stat'].st_mtime_ns, item['stat'].st_size):
                duration_seconds = cached_duration[2] or duration_seconds
        _,start_at,day_start_ts,_day_end_ts=item['calendar']
        day_offset = max(0, min(_day_end_ts - day_start_ts - 1, start_ts - day_start_ts)) if day_start_ts else offset
        entries.append(
            {
                "path": path,
                "stat":item['stat'],
                "name": path.name,
                "start_ts": start_ts,
                "start_at": start_at,
                "day": entry_day,
                "offset": offset,
                "playback_offset": offset,
                "day_offset": day_offset,
                "duration_seconds": duration_seconds,
            }
        )
        offset += duration_seconds
    return entries


@functools.lru_cache(maxsize=50000)
def recording_calendar_metadata(start_ts,zone):
    local=time.localtime(start_ts);day=time.strftime('%Y-%m-%d',local)
    start,end=recording_day_bounds(day)
    return day,time.strftime('%Y-%m-%dT%H:%M:%S%z',local),start,end


def recording_day_groups(entries: list[dict], segment_seconds: int, key: str = "") -> list[dict]:
    groups: dict[str, dict] = {}
    for entry in entries:
        day = entry.get("day") or recording_day_key(safe_int(entry.get("start_ts"), 0))
        if not day:
            continue
        group=groups.get(day)
        if group is None:
            start,end=recording_day_bounds(day)
            group=groups[day]={
                "day": day,
                "file_count": 0,
                "total_seconds": 0,
                "recorded_seconds": 0,
                "day_start_ts": start,
                "day_end_ts": end,
                "day_total_seconds": end-start,
                "oldest_at": entry.get("start_at") or "",
                "newest_at": entry.get("start_at") or "",
                "thumbnail_url": "",
            }
        group["file_count"] += 1
        group["recorded_seconds"] += safe_int(entry.get("duration_seconds"), segment_seconds)
        group["total_seconds"] = group["day_total_seconds"]
        group["newest_at"] = entry.get("start_at") or group["newest_at"]
        path = entry.get("path")
        if key and not group.get("thumbnail_url") and isinstance(path, Path):
            group["thumbnail_url"] = recording_thumbnail_url(key,path,entry.get('stat'))
    return sorted(groups.values(), key=lambda item: item["day"], reverse=True)


def recording_files_from_entries(key: str, entries: list[dict], segment_seconds: int) -> list[dict]:
    files = []
    for entry in sorted(entries, key=lambda item: (safe_int(item.get("start_ts"), 0), item.get("name") or ""), reverse=True):
        path = entry.get("path")
        if not isinstance(path, Path):
            continue
        try:
            stat = entry.get('stat') or path.stat()
        except OSError:
            continue
        start_ts = safe_int(entry.get("start_ts"), int(stat.st_mtime))
        duration_seconds = safe_int(entry.get("duration_seconds"), segment_seconds)
        files.append(
            {
                "name": path.name,
                "url": f"recordings/{key}/{path.name}",
                "thumbnail_url": recording_thumbnail_url(key, path,stat),
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


def recording_cache_budget(config: dict) -> int:
    return clamp_int((config.get('nvr') or {}).get('playback_cache_mb'), 0, 0, 2048) * MIB


def thumbnail_cache_budget(config: dict) -> int:
    return clamp_int((config.get('nvr') or {}).get('thumbnail_cache_mb'), 32, 1, 256) * MIB


def active_recording_caches() -> set:
    now = time.monotonic()
    with RECORDING_CACHE_LOCK:
        expired = [path for path, accessed in RECORDING_CACHE_ACCESS.items() if now - accessed > 600]
        for path in expired:
            RECORDING_CACHE_ACCESS.pop(path, None)
        active = set(RECORDING_CACHE_ACCESS)
    return {path for video in active for path in (video, video.with_suffix('.json'), video.with_suffix('.ffconcat'))}


def cleanup_disposable_storage(config: dict, wait_for_thumbnail: bool = False) -> dict:
    if not STORAGE_CLEANUP_LOCK.acquire(blocking=False):
        return {'skipped': 'maintenance_busy'}
    try:
        root = Path((config.get('storage') or {}).get('recordings_dir') or '/media/edge-of-infinity/recordings')
        source_roots = {recording_key(camera,index): root / safe_id(camera.get('id') or f'camera_{index+1}')
                        for index,camera in enumerate(config.get('cameras', []))}
        report = {}
        if RECORDING_CACHE_BUILD_SEMAPHORE.acquire(blocking=False):
            try:
                budget = recording_cache_budget(config)
                report['playback'] = prune_cache(RECORDING_CACHE_DIR, 'playback', budget, max_files=64,
                                                 protected=active_recording_caches() if budget else set())
            finally:
                RECORDING_CACHE_BUILD_SEMAPHORE.release()
        else:
            report['playback'] = {'skipped':'playback_worker_busy'}
        if RECORDING_THUMBNAIL_SEMAPHORE.acquire(timeout=15 if wait_for_thumbnail else 0):
            try:
                report['thumbnails'] = prune_cache(RECORDING_THUMB_DIR, 'thumbnail', thumbnail_cache_budget(config), source_roots=source_roots)
            finally:
                RECORDING_THUMBNAIL_SEMAPHORE.release()
        else:
            report['thumbnails'] = {'skipped':'thumbnail_worker_busy'}
        paths = [DEBUG_LOG_PATH, RECORDING_CACHE_LOG_PATH, RECORDING_THUMB_LOG_PATH, RECORDING_STREAM_LOG_PATH,HOME_DIR/'playback.jsonl',HOME_DIR/'camera-discovery.jsonl']
        if root.is_dir() and not root.is_symlink():
            paths += [path / 'ffmpeg.log' for path in root.iterdir() if path.is_dir() and not path.is_symlink()]
        with DEBUG_LOCK:
            report['log_freed_bytes'] = sum(trim_log(path) for path in paths)
        with STORAGE_USAGE_LOCK:
            STORAGE_USAGE_CACHE.clear()
        return report
    finally:
        STORAGE_CLEANUP_LOCK.release()


STORAGE_SCAN_LOCK=threading.Lock()


def storage_usage_snapshot(config,known_bytes=None,refresh=False):
    """A player request never walks storage or runs cache maintenance."""
    root=Path((config.get('storage') or {}).get('recordings_dir') or '/media/edge-of-infinity/recordings')
    signature=(str(root),recording_cache_budget(config),thumbnail_cache_budget(config),int((config.get('detection') or {}).get('alert_media_mb',256))*MIB)
    with STORAGE_USAGE_LOCK:cached=STORAGE_USAGE_CACHE.get(signature)
    fresh=bool(cached and time.monotonic()-cached[0]<60)
    if refresh and not fresh and STORAGE_SCAN_LOCK.acquire(blocking=False):
        def scan():
            started=time.monotonic()
            try:
                storage_usage_payload(config)
                write_debug_event('storage_scan_done',{'elapsed_ms':round((time.monotonic()-started)*1000)})
            except OSError as error:write_debug_event('storage_scan_error',{'error':str(error)})
            finally:STORAGE_SCAN_LOCK.release()
        threading.Thread(target=scan,daemon=True).start()
    if cached:return {**cached[1],'pending':not fresh,'stale':not fresh}
    def volume(path):
        data=disk_usage_payload(path)
        return {'free_bytes':data.get('free_bytes'),'total_bytes':data.get('total_bytes')}
    return {'pending':True,'recordings_bytes':known_bytes,'recordings_volume':volume(root),'cache_volume':volume(HOME_DIR),
            'playback_cache_limit_bytes':signature[1],'thumbnail_cache_limit_bytes':signature[2],
            'phone_cache_limit_bytes':RECORDING_COMPAT.budget,'alert_clip_limit_bytes':signature[3],'alert_image_limit_bytes':32*MIB}


def storage_usage_payload(config: dict) -> dict:
    root = Path((config.get('storage') or {}).get('recordings_dir') or '/media/edge-of-infinity/recordings')
    alert_limit=int((config.get('detection') or {}).get('alert_media_mb',256))*MIB
    signature = (str(root), recording_cache_budget(config), thumbnail_cache_budget(config),alert_limit)
    with STORAGE_USAGE_LOCK:
        cached = STORAGE_USAGE_CACHE.get(signature)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    total = 0
    if root.is_dir() and not root.is_symlink():
        for parent, directories, names in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if not (Path(parent)/name).is_symlink()]
            for name in names:
                if re.fullmatch(SEGMENT,name):
                    path = Path(parent)/name
                    try:
                        if not path.is_symlink() and path.is_file():
                            total += path.stat().st_size
                    except OSError:
                        pass
    def volume(path):
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            usage = shutil.disk_usage(path)
            return {'free_bytes':usage.free,'total_bytes':usage.total}
        except OSError:
            return {'free_bytes':None,'total_bytes':None}
    def alert_bytes(folder,suffix):
        total=0
        if folder.is_symlink():return total
        for path in folder.glob('event-*'+suffix):
            if not path.stem[6:].isdigit() or path.is_symlink():continue
            try:total+=path.stat().st_size
            except OSError:pass
        return total
    result = {'recordings_bytes':total,
              'phone_cache_bytes':RECORDING_COMPAT.prune(),
              'phone_cache_limit_bytes':RECORDING_COMPAT.budget,
              'alert_clip_bytes':alert_bytes(root/'.alerts','.mp4'),
              'alert_clip_limit_bytes':alert_limit,
              'alert_image_bytes':alert_bytes(HOME_DIR/'alerts','.jpg'),
              'alert_image_limit_bytes':32*MIB,
              'playback_cache_bytes':sum(stat.st_size for _,stat in managed_files(RECORDING_CACHE_DIR,'playback')),
              'thumbnail_cache_bytes':sum(stat.st_size for _,stat in managed_files(RECORDING_THUMB_DIR,'thumbnail')),
              'playback_cache_limit_bytes':recording_cache_budget(config),
              'thumbnail_cache_limit_bytes':thumbnail_cache_budget(config),
              'recordings_volume':volume(root),'cache_volume':volume(HOME_DIR),
              'measured_at':int(time.time())}
    with STORAGE_USAGE_LOCK:
        STORAGE_USAGE_CACHE[signature] = (time.monotonic(),result)
    return result


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
    budget = recording_cache_budget(load_config())
    if budget<=0:
        return {'enabled':False,'byte_limit':0,'disabled_reason':'disk_saving_file_sequence','ready':False,'raw_ready':False,'current':False,
                'active_day':recording_day_is_today(cache_name),'stale_active_day':False,'building':False,'stale':False,
                'rebuild_deferred':False,'rebuild_defer_reason':'disabled','new_source_count':0,'too_short_for_sources':False,
                'source_count':len(entries),'source_hash':'','cache_id':'disabled','cache_name':cache_name,'url':'','file_size':0,
                'file_count':len(entries),'total_seconds':sum(safe_int(e.get('duration_seconds'),segment_seconds) for e in entries),
                'modified_at':'','built_at':'','log_path':str(RECORDING_CACHE_LOG_PATH)}
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
    ready = current and budget > 0 and file_size <= budget
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
    if auto_refresh and budget > 0 and signature["items"] and not current and not defer_reason:
        schedule_recording_cache_refresh(key, entries, segment_seconds, signature, reason="recording_status", cache_name=cache_name)

    cache_id = metadata.get("cache_id") or metadata.get("source_hash") or str(int(time.time()))
    total_seconds = safe_int(
        metadata.get("total_seconds"),
        sum(safe_int(entry.get("duration_seconds"), segment_seconds) for entry in entries),
    )
    return {
        "enabled": budget > 0,
        "byte_limit": budget,
        "disabled_reason": "disk_saving_file_sequence" if budget <= 0 else "",
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
    budget = recording_cache_budget(load_config())
    if budget <= 0:
        return False
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
    if sum(entry['path'].stat().st_size for entry in worker_entries) * 1.1 + MIB > budget:
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
        budget = recording_cache_budget(load_config())
        estimate = int(sum(entry['path'].stat().st_size for entry in entries) * 1.1) + MIB
        if budget <= 0 or estimate > budget:
            return
        # Reserve the pending remux alongside all retained day copies.
        retained = prune_cache(RECORDING_CACHE_DIR, 'playback', budget - estimate,
                               protected=active_recording_caches(), max_files=64)
        if retained['bytes'] + estimate > budget:
            return
        RECORDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(RECORDING_CACHE_DIR).free < estimate + 16 * MIB:
            return
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
        if tmp_path.stat().st_size > estimate or recording_cache_budget(load_config()) <= 0:
            raise RuntimeError('recording_cache_budget_exceeded')
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
    if recording_cache_budget(config) <= 0:
        return
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    cache_limit = clamp_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS, 12, RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS)
    for index, camera in enumerate(config.get("cameras", [])):
        if not camera_should_record(camera):
            continue
        key = recording_key(camera, index)
        all_entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, measure_durations=False)
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


def build_recording_thumbnail_command(source: Path, target: Path, seek_seconds: float = 0.0) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        str(max(0, seek_seconds)),
        "-threads",
        "1",
        "-i",
        str(source),
        "-threads",
        "1",
        "-filter_threads",
        "1",
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


def ensure_recording_thumbnail(key: str, source: Path, filename: str, seek_seconds: int | None = None) -> Path:
    target = recording_thumbnail_path(key, filename)
    if seek_seconds is not None and seek_seconds>0:
        stat = source.stat()
        target = target.parent / 'scrub' / f'{source.name}.{stat.st_size}-{stat.st_mtime_ns}.{seek_seconds}.jpg'
    if recording_thumbnail_ready(target, source):
        return target
    if not recording_file_ready(source) or str(source) in active_recording_files():
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
    command = build_recording_thumbnail_command(source, tmp, seek_seconds=0 if seek_seconds is None else seek_seconds)
    acquired = RECORDING_THUMBNAIL_SEMAPHORE.acquire(timeout=0.2)
    if not acquired:
        append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_busy", {
            "key": key,
            "source": source.name,
            "target": str(target),
        })
        raise RuntimeError("thumbnail_generation_busy")
    started_at = time.monotonic()
    # A simultaneous poster/tile/hover request may have filled the same cache.
    if recording_thumbnail_ready(target,source):
        RECORDING_THUMBNAIL_SEMAPHORE.release()
        return target
    append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_generation_start", {
        "key": key,
        "source": source.name,
        "target": str(target),
        "command": redact_command(command),
    })
    try:
        try:
            result = subprocess_run_low_priority(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, check=False)
            if seek_seconds is None and result.returncode == 0 and not recording_thumbnail_ready(tmp, source):
                command = build_recording_thumbnail_command(source, tmp, seek_seconds=0)
                result = subprocess_run_low_priority(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, check=False)
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
        prune_cache(RECORDING_THUMB_DIR, 'thumbnail', thumbnail_cache_budget(load_config()))
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
        entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, measure_durations=False)
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
    return current_recording_log_tail(recording_log_path(camera, index),limit)


def current_recording_log_tail(path: Path, limit: int = 6000) -> str:
    tail=redact_rtsp(read_text_tail(path,limit))
    start=tail.rfind('=== Edge recording start ===')
    return tail[start:] if start>=0 else tail


def ffmpeg_runtime_messages(tail):
    marker='=== Edge recording start ==='
    if marker in tail:
        tail=tail.rsplit(marker,1)[1].lstrip()
        if tail.startswith('{'):
            try:_,end=json.JSONDecoder().raw_decode(tail);tail=tail[end:]
            except ValueError:return ''  # An incomplete command header is not an FFmpeg failure.
    return '\n'.join(line for line in tail.splitlines() if line.strip() and not line.lstrip().startswith(('"','{','}',']',',')) and not line.startswith('=== Edge recording'))


def recording_health(camera: dict, index: int) -> dict:
    tail = recording_log_tail(camera, index, 5000)
    runtime=ffmpeg_runtime_messages(tail)
    lower_tail = runtime.lower()
    error_lines = [
        line.strip()
        for line in runtime.splitlines()
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


def recording_codec_mode(stream: str, nvr: dict, metadata=None) -> tuple[str, str, str, dict]:
    probe = metadata if metadata is not None else probe_rtsp_stream(stream, timeout=6)
    video = probe.get("video") or {}
    audio = probe.get("audio") or {}
    video_codec = str(video.get("codec_name") or "").lower()
    audio_codec = str(audio.get("codec_name") or "").lower()
    mode = 'copy'
    return mode, video_codec, audio_codec, probe


class RecordingSourcePending(RuntimeError):
    def __init__(self, details):
        super().__init__('edge_rtc_source_metadata_pending')
        self.details=details


def recording_registry_metadata(camera,index,stream_name,wait_seconds=4):
    """Use the already connected camera's SDP; never open another probe channel."""
    path=edge_stream_path(camera,index,stream_name);deadline=time.monotonic()+wait_seconds
    while True:
        status=edge_rtc_status()
        sources=status.get('sources',[])
        source=next((row for row in sources if path in row.get('paths',[])),None)
        if source is None:source=next((row for row in sources if row.get('camera_id')==safe_id(camera.get('id') or f'camera_{index+1}')),None)
        if source and source.get('ready') and (source.get('video') or {}).get('codec_name') and source['video'].get('parameters_ready',True):
            video=dict(source['video']);video['codec_name']={'h265':'hevc'}.get(video['codec_name'],video['codec_name'])
            audio=source.get('audio') or {};codec=str(audio.get('codec') or '').lower()
            codec='aac' if codec.startswith('mp4a.40.') else {'pcma':'pcm_alaw','pcmu':'pcm_mulaw'}.get(codec,codec)
            return {'video':video,'audio':{'codec_name':codec,'sample_rate':audio.get('sampleRate'),'channels':audio.get('channels')},'metadata_source':'edge_rtc_registry','source_ready':True}
        if time.monotonic()>=deadline:
            video=(source or {}).get('video') or {}
            phase='core_unavailable' if not status.get('api_reachable',bool(sources)) else 'source_not_registered' if source is None else 'source_disconnected' if not source.get('ready') else 'video_metadata_missing' if not video.get('codec_name') else 'parameter_sets_pending'
            raise RecordingSourcePending({'source':'edge_rtc_registry','source_path':path,'phase':phase,
                'api_reachable':status.get('api_reachable',bool(sources)),'source_found':source is not None,
                'source_ready':bool((source or {}).get('ready')),'upstream_connections':(source or {}).get('upstream_connections',0),
                'packets':(source or {}).get('packets',0),'video_codec':video.get('codec_name',''),
                'parameters_ready':bool(video.get('parameters_ready',bool(video.get('codec_name')))),
                'width':video.get('width',0),'height':video.get('height',0),'onvif_required':False,
                'rtp':(source or {}).get('rtp',{}),'connection':(source or {}).get('connection',{})})
        time.sleep(.2)


def recording_mode_from_codec(video_codec: str, nvr: dict) -> str:
    return 'copy'


def build_recording_command(stream: str, output_pattern: str, segment_seconds: int, mode: str, video_codec: str = '', audio_codec: str = '', audio_rate=0, audio_channels=1) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
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
        "-user_agent",
        "EdgeRecorder/1",
        "-threads",
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
        ]
    else:
        command += ['-c:v','copy']
    if video_codec.lower() in ('h264','hevc','h265'):
        command[command.index('-probesize')+1]='1048576'
        command[command.index('-analyzeduration')+1]='2000000'
    if audio_codec in ('aac','mp4a'):
        command+=['-c:a','copy']
    else:
        command+=aac_encoding_options(audio_codec,audio_rate,audio_channels)
        command+=['-filter_threads','1','-af','aresample=async=1:first_pts=0,asetpts=N/SR/TB']
    if mode=='copy' and video_codec.lower() in ('hevc','h265'):
        command+=['-tag:v','hvc1']
    # Camera audio clocks can jump backwards while RTP video remains valid.
    # Keep original video timestamps; repair only the AAC sample clock.
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
            # Only known recorder output names qualify; preserve unrelated media.
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
    cache_cleanup = cleanup_disposable_storage(config)
    RECORDING_COMPAT.prune()
    write_debug_event("recording_retention_done", {
        "directory": str(directory), "retention_days": retention_days, "removed": removed,
        "cache_cleanup": cache_cleanup,
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
        candidates, total_segment_count = recording_file_candidates(directory)
        segment_count = len(candidates)
        nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
        segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
        cache_limit = clamp_int(nvr.get("playback_cache_segments"), RECORDING_CACHE_MAX_SEGMENTS, 12, RECORDING_CACHE_ABSOLUTE_MAX_SEGMENTS)
        all_entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, candidates=candidates, measure_durations=False)
        days = recording_day_groups(all_entries, segment_seconds, key)
        selected_day = day_selection.get(str(index)) or day_selection.get(str(camera.get("id") or "")) or (days[0]["day"] if days else "")
        # Keep an explicitly requested empty/deleted day visible rather than opening today.
        day_entries = recording_file_entries(camera, index, limit=0, segment_seconds=segment_seconds, day_key=selected_day, candidates=candidates) if selected_day else all_entries
        segment_files = recording_files_from_entries(key, day_entries, segment_seconds)
        timeline_files = sorted(segment_files, key=lambda item: (item.get("start_ts") or 0, item.get("name") or ""))
        day_start_ts, day_end_ts = recording_day_bounds(selected_day)
        recorded_seconds = sum(safe_int(item.get("duration_seconds"), segment_seconds) for item in timeline_files)
        for day in days:
            day['duration_estimated'] = day['day'] != selected_day
            if day['day'] == selected_day:
                day['recorded_seconds'] = recorded_seconds
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
            recording_status = "waiting_source" if key in RECORDING_SOURCE_WAIT else "scheduled_stopped"
        else:
            recording_status = "disabled"
        video_codec = camera.get("record_video_codec") or camera.get("live_video_codec") or camera.get("video_codec") or camera.get("codec") or ""
        nvr_mode = recording_mode_from_codec(video_codec, nvr)
        recent = sorted(candidates, key=lambda item: item[2])[-360:]
        sample_duration = 0
        for position, (path, stat, start) in enumerate(recent):
            following = recent[position+1][2] if position+1 < len(recent) else None
            fallback = recording_inferred_duration_seconds(start, following, segment_seconds)
            with RECORDING_DURATION_LOCK:
                duration = RECORDING_DURATION_CACHE.get(str(path))
            sample_duration += (duration[2] or fallback) if duration and duration[:2] == (stat.st_mtime_ns,stat.st_size) else fallback
        daily_estimate = int(sum(stat.st_size for _,stat,_ in recent) * 86400 / sample_duration) if sample_duration else 0
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
                "source_wait":{field:value for field,value in RECORDING_SOURCE_WAIT.get(key,{}).items() if not field.startswith('_')},
                "pid": process.pid if is_recording else None,
                "directory": str(directory),
                "directory_exists": directory.exists(),
                "segments": segment_count,
                "segments_total": total_segment_count,
                "recording_bytes": sum(stat.st_size for _,stat,_ in candidates),
                "bytes_per_day_estimate": daily_estimate,
                "segments_pending": max(0, total_segment_count - segment_count),
                "files": segment_files,
                "files_revision":recording_files_revision(key,selected_day,segment_files),
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
    return {"cameras": cameras, "storage": storage_usage_snapshot(config,known_bytes=sum(c['recording_bytes'] for c in cameras))}


def recording_files_revision(key,day,files):
    digest=hashlib.sha256(f'{key}:{day}'.encode())
    for item in files:
        digest.update(json.dumps([item.get(field) for field in ('name','size_bytes','modified_at','duration_seconds','timeline_offset','playback_offset')],separators=(',',':')).encode())
    return digest.hexdigest()[:32]


RECORDING_INDEX=RecordingIndexCache(lambda config,selection:recording_status_payload(config,selection),
    lambda event,data:append_jsonl_log(HOME_DIR/'playback.jsonl',event,data))


def recording_status_response(config,query):
    if query.get('summary')==['1']:
        cleanup_recording_processes();rows=[]
        for index,camera in enumerate(config.get('cameras',[])):
            key=recording_key(camera,index);process=RECORDING_PROCESSES.get(key);recording=bool(process and process.poll() is None)
            desired=camera_should_record(camera);error=recording_preflight_error(camera,camera.get('record_stream') or 'main')
            rtsp,source=recording_source_stream(camera,index,camera.get('record_stream') or 'main')
            rows.append({'index':index,'id':camera.get('id'),'key':key,'record_stream':camera.get('record_stream') or 'main',
                         'record_rtsp':redact_rtsp(rtsp),'record_source':source,'record_mode':'copy',
                         'recording':recording,'desired_recording':desired,'record_error':error,'can_record':not error,
                         'recording_status':'recording' if recording else 'blocked' if error else 'waiting_source' if desired and key in RECORDING_SOURCE_WAIT else 'scheduled_stopped' if desired else 'disabled',
                         'source_wait':{field:value for field,value in RECORDING_SOURCE_WAIT.get(key,{}).items() if not field.startswith('_')}})
        return {'summary':True,'cameras':rows}
    selection=parse_recording_day_query(query)
    if query.get('catalog')==['1']:
        data=RECORDING_INDEX.get(config,selection,force=query.get('fresh')==['1'])
        states=recording_status_response(config,{'summary':['1']})
        for row,state in zip(data['cameras'],states['cameras']):row.update(state)
        data['storage']=storage_usage_snapshot(config)
    else:data=recording_status_payload(config,day_selection=selection)
    known={}
    for value in str((query.get('known') or [''])[0]).split(','):
        if re.fullmatch(r'[0-9]+:[a-f0-9]{32}',value):
            index,revision=value.split(':');known[index]=revision
    for camera in data['cameras']:
        if known.get(str(camera['index']))==camera['files_revision']:
            camera.pop('files',None);camera['files_unchanged']=True
    return data


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
def ensure_configured_recordings(config: dict, reason: str, only_keys=None) -> list[dict]:
    cleanup_recording_processes()
    configured={recording_key(camera,index) for index,camera in enumerate(config.get('cameras',[]))}
    for key in list(RECORDING_SOURCE_WAIT):
        if key not in configured:RECORDING_SOURCE_WAIT.pop(key,None)
    results = []
    for index, camera in enumerate(config.get("cameras", [])):
        key = recording_key(camera, index)
        if only_keys is not None and key not in only_keys:continue
        process = RECORDING_PROCESSES.get(key)
        running = bool(process and process.poll() is None)
        should_record = camera_should_record(camera)
        if should_record and not running:
            try:
                result = start_recording(camera, index,metadata_wait_seconds=0) if reason=='source_retry' else start_recording(camera, index)
                RECORDING_SOURCE_WAIT.pop(key,None)
                results.append({"index": index, "id": camera.get("id"), "action": "started", "result": result})
            except RecordingSourcePending as error:
                now=time.monotonic();previous=RECORDING_SOURCE_WAIT.get(key,{})
                state={'index':index,'id':camera.get('id'),'action':'waiting_source','status':'waiting_source',
                       'retry_seconds':RECORDING_SOURCE_RETRY_SECONDS,**error.details}
                state['waiting_seconds']=round(now-previous.get('_since',now),1)
                state['severity']='warning' if state['waiting_seconds']>=30 else 'info'
                changed=any(previous.get(field)!=state.get(field) for field in ('phase','source_ready','parameters_ready','api_reachable','severity'))
                log=not previous or changed or now-previous.get('_logged_at',0)>=60
                RECORDING_SOURCE_WAIT[key]={**state,'_since':previous.get('_since',now),'_logged_at':now if log else previous['_logged_at']}
                results.append(state)
                if log:write_debug_event('recording_autostart_waiting',{**state,'reason':reason})
            except Exception as error:  # noqa: BLE001
                RECORDING_SOURCE_WAIT.pop(key,None)
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
            RECORDING_SOURCE_WAIT.pop(key,None)
            result = stop_recording(camera, index)
            results.append({"index": index, "id": camera.get("id"), "action": "stopped_disabled", "result": result})
        else:
            RECORDING_SOURCE_WAIT.pop(key,None)
            results.append({
                "index": index,
                "id": camera.get("id"),
                "action": "kept_running" if running else "kept_stopped",
                "desired_recording": should_record,
            })
    if RECORDING_SOURCE_WAIT:start_recording_source_retry()
    if reason!='source_retry' or any(row['action']!='waiting_source' for row in results):
        write_debug_event("recording_ensure_done", {"reason": reason, "results": results})
    return results


def retry_pending_recordings_once():
    with RECORDING_CONTROL_LOCK:
        return ensure_configured_recordings(load_config(),'source_retry',only_keys=set(RECORDING_SOURCE_WAIT))


def recording_source_retry_loop():
    global RECORDING_SOURCE_RETRY_RUNNING
    while True:
        stopped=RECORDING_SOURCE_RETRY_STOP.wait(RECORDING_SOURCE_RETRY_SECONDS)
        with RECORDING_CONTROL_LOCK:
            if stopped or not RECORDING_SOURCE_WAIT:
                RECORDING_SOURCE_RETRY_RUNNING=False
                return
            try:retry_pending_recordings_once()
            except Exception as error:
                write_debug_event('recording_source_retry_error',{'type':type(error).__name__})


def start_recording_source_retry():
    global RECORDING_SOURCE_RETRY_RUNNING
    with RECORDING_CONTROL_LOCK:
        if RECORDING_SOURCE_RETRY_RUNNING or RECORDING_SOURCE_RETRY_STOP.is_set():return
        RECORDING_SOURCE_RETRY_RUNNING=True
        try:threading.Thread(target=recording_source_retry_loop,daemon=True).start()
        except RuntimeError:
            RECORDING_SOURCE_RETRY_RUNNING=False
            raise


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
def start_recording(camera: dict, index: int, metadata_wait_seconds=4) -> dict:
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

    config = load_config()
    nvr = config.get("nvr") if isinstance(config.get("nvr"), dict) else {}
    metadata=recording_registry_metadata(camera,index,record_stream,wait_seconds=metadata_wait_seconds)
    mode, video_codec, audio_codec, probe = recording_codec_mode(stream, nvr,metadata)
    audio_info=probe.get('audio') or {}

    directory = recording_base_dir(camera, index)
    directory.mkdir(parents=True, exist_ok=True)
    probe_file = directory / ".write-test"
    try:
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink(missing_ok=True)
    except OSError as error:
        raise RuntimeError(f"recording_directory_not_writable: {directory}: {error}") from error

    segment_seconds = clamp_int(nvr.get("segment_seconds"), 10, 2, 300)
    retention_days = recording_retention_days(config)
    removed_old = cleanup_old_recordings(directory, retention_days)
    log_path = directory / "ffmpeg.log"
    output_pattern = str(directory / "%Y%m%d-%H%M%S.mp4")
    command = build_recording_command(stream, output_pattern, segment_seconds, mode,video_codec,audio_codec,audio_info.get('sample_rate'),audio_info.get('channels'))
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
        "metadata_source":probe.get('metadata_source','ffprobe_shared_stream' if record_source!='camera_direct' else 'ffprobe_camera'),
        "audio_output": 'copy_aac' if audio_codec in ('aac','mp4a') else 'aac_voice_16k_32k' if audio_codec in ('pcm_alaw','pcm_mulaw') and safe_int(audio_info.get('sample_rate'),0)<=16000 else 'aac',
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
    RECORDING_SOURCE_WAIT.pop(key,None)
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


def keyframe_interval_from_fps(value: str, seconds=2) -> str:
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
    return str(max(1, int(round(fps * seconds))))


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


def onvif_request(camera: dict, endpoint: str, action: str, body: str, reference_headers: str = "", timeout: float = 8) -> ET.Element:
    import urllib.request
    import uuid
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
    addressing = f'<wsa:Action>{xml_escape(action)}</wsa:Action><wsa:To>{xml_escape(endpoint)}</wsa:To><wsa:MessageID>urn:uuid:{uuid.uuid4()}</wsa:MessageID>'
    envelope = f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:tev="http://www.onvif.org/ver10/events/wsdl" xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:wsa="http://www.w3.org/2005/08/addressing"><s:Header>{security}{addressing}{reference_headers}</s:Header><s:Body>{body}</s:Body></s:Envelope>'
    request = urllib.request.Request(endpoint, data=envelope.encode(), headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'}, method="POST")
    passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, endpoint, username, password)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ValueError("ONVIF redirects are not allowed.")
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(passwords), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
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


CAMERA_KEYFRAMES=CameraKeyframes(load_config,lambda *args:onvif_request(*args,timeout=2),write_debug_event)


def fetch_onvif_camera_config(camera: dict) -> dict:
    endpoint = camera.get("onvif_url") or ""
    if not endpoint:
        raise ValueError("Enter the camera ONVIF device-service URL first.")
    capabilities = onvif_request(camera, endpoint, "http://www.onvif.org/ver10/device/wsdl/GetCapabilities", '<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>')
    media = next((node for node in capabilities.iter() if node.tag.rsplit("}", 1)[-1] == "Media"), None)
    media_url = (media.get("XAddr") or onvif_value(media, "XAddr")) if media is not None else ""
    if not media_url:
        raise RuntimeError("Camera did not expose an ONVIF Media service; use manual RTSP URLs.")
    profiles_xml = onvif_request(camera, media_url, "http://www.onvif.org/ver10/media/wsdl/GetProfiles", '<trt:GetProfiles/>')
    profiles = []
    for node in profiles_xml.iter():
        if node.tag.rsplit("}", 1)[-1] != "Profiles" or not node.get("token"):
            continue
        video=next((p for p in node if local_name(p.tag)=='VideoEncoderConfiguration'),None)
        audio=next((p for p in node if local_name(p.tag)=='AudioEncoderConfiguration'),None)
        profiles.append({"token": node.get("token"), "area": safe_int(onvif_value(node, "Width"), 0) * safe_int(onvif_value(node, "Height"), 0),
                         'video':{field:onvif_value(video,field) if video is not None else '' for field in ('Encoding','Width','Height','FrameRateLimit','BitrateLimit','GovLength','H264Profile')},
                         'audio':{field:onvif_value(audio,field) if audio is not None else '' for field in ('Encoding','SampleRate','Bitrate')}})
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
    device = {"manufacturer": CAMERA_VENDORS.get(camera.get("vendor"), "ONVIF")}
    try:
        identity = onvif_request(camera, endpoint, "http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation", '<tds:GetDeviceInformation/>')
        for key, field in (("manufacturer", "Manufacturer"), ("model", "Model"), ("firmware", "FirmwareVersion")):
            value = onvif_value(identity, field)[:120]
            if value:
                device[key] = value
                discovered[key] = value
    except (RuntimeError, ValueError):
        pass
    return {"ok": True, "camera": discovered, "device": device, "streams": {}, "profiles":profiles,"channels": [], "sections": {"onvif": {"ok": True}}, "effective_streams": effective_streams(discovered), "recommendations": [{"severity": "notice", "stream": "all", "message": "ONVIF main/sub streams loaded into the form. Save cameras to apply them."}]}


def fetch_camera_autoconfig(camera: dict) -> dict:
    protocol = normalize_access_protocol(camera.get('access_protocol'))
    if protocol == 'isapi' and camera.get('vendor') != 'hikvision':
        raise ValueError('ISAPI requires a Hikvision-compatible camera. Choose ONVIF for this camera.')
    if protocol == 'onvif' or (protocol != 'isapi' and camera.get("vendor") != "hikvision"):
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
        target_keyframe = fps * 2 if fps else 0
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
        if target_keyframe and keyframe and keyframe > target_keyframe + max(1,round(fps*.1)):
            recommendations.append({
                "stream": name,
                "severity": "notice",
                "message": f"{name}: GOP 1–2 s improves live/archive seeks. At {fps} FPS use {fps}–{target_keyframe} frames; camera reports {keyframe}. Shorter GOP may increase bitrate or use more of a fixed bitrate budget.",
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
        report = cleanup_disposable_storage(load_config())
        write_debug_event('recording_storage_maintenance', report)
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
    registry=edge_rtc_status()

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

        if camera.get('enabled'):
            path=edge_stream_path(camera,index,record_stream_name)
            source=next((row for row in registry.get('sources',[]) if path in row.get('paths',[]) or row.get('camera_id')==camera_id),None)
            observed=(source or {}).get('video') or {};audio=(source or {}).get('audio') or {}
            connected=bool(source and source.get('ready') and source.get('upstream_connections',0))
            status='online' if connected and observed.get('codec_name') and observed.get('parameters_ready',True) else 'checking'
            detail='EdgeRTC odbiera wspólne źródło kamery; stan potwierdzony bez dodatkowej sondy RTSP.' if status=='online' else 'EdgeRTC oczekuje na połączenie lub parametry obrazu kamery; źródło ponawia połączenie automatycznie.'
            if not registry.get('api_reachable'):detail='Oczekiwanie na API rdzenia EdgeRTC. Sprawdź stan silnika w diagnostyce.'
            video_codec={'h265':'hevc'}.get(observed.get('codec_name'),observed.get('codec_name',''))
            width=str(observed.get('width') or previous.get('width') or '');height=str(observed.get('height') or previous.get('height') or '');fps=str(observed.get('fps') or previous.get('fps') or '');bitrate=str(previous.get('bitrate') or '')
            audio_codec=str(audio.get('codec') or '');audio_codec='aac' if audio_codec.startswith('mp4a.40.') else {'pcma':'pcm_alaw','pcmu':'pcm_mulaw'}.get(audio_codec,audio_codec)
            audio_sample_rate=str(audio.get('sampleRate') or '');audio_channels=str(audio.get('channels') or '')
            live_probe_status='online' if status=='online' else 'warming';live_video_codec=video_codec;live_width=width;live_height=height;live_fps=fps;live_bitrate=bitrate

        # Live uses the shared EdgeRTC registry. Status refresh intentionally does
        # not capture JPEG snapshots, so old fallback work cannot block live.

        camera_status = {
            "id": camera.get("id"),
            "index": index,
            "key": camera_key,
            "name": camera.get("name"),
            "vendor": camera.get("vendor"),
            "host": camera.get("host"),
            "manufacturer":camera.get('manufacturer'),"model":camera.get('model'),"firmware":camera.get('firmware'),
            "media_observation":"Current configured stream, not a complete list of camera codec capabilities.",
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
            "status_source":'edge_rtc_registry',
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
        write_debug_event('camera_media_detected',camera_status)

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
        "mode": "edge-rtc-native",
        "authoritative_config": str(CONFIG_PATH),
        "addon_config_mirror": str(ADDON_CONFIG_PATH),
        "core": core,
    }


EDGE_RTC_PLAYER_HTML = r"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;height:100%;background:#080e15;color:#d9e7ee;font:13px system-ui}canvas{width:100%;height:100%;object-fit:contain}button{position:absolute;bottom:12px;right:12px;padding:10px;border:0;border-radius:8px;background:#20384b;color:white}p{position:absolute;top:8px;left:10px}</style><canvas></canvas><p>EdgeRTC · connecting…</p><button>Sound</button><script type="module">
import{EdgeRTCClient}from'../edge-rtc-client.js?v=__EDGE_PANEL_VERSION__';
import{getDeviceReporter}from'../device-diagnostics.js?v=__EDGE_PANEL_VERSION__';
try{window.EdgeI18n=parent.EdgeI18n;}catch(_){}const languageUI=window.EdgeI18n?.attach(document.body);window.addEventListener('pagehide',()=>languageUI?.close(),{once:true});
const playerBase=new URL(document.baseURI),path=playerBase.pathname.split('/').pop(),url=new URL('../stream/'+path,playerBase);let client,timer,retries=0,muted=true;
const diagnostics=getDeviceReporter({context:{surface:'edge_panel_live',source:path},send:async payload=>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);try{const response=await fetch(new URL('../../client-diagnostics',playerBase),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});if([401,403].includes(response.status)){diagnostics.setPaused?.(true);window.edgeStopLive?.();parent.postMessage({type:'edge_ingress_expired',path},playerBase.origin);}return response.ok;}finally{clearTimeout(timer);}}});
function connect(){clearTimeout(timer);client?.close();if(document.hidden)return;client=new EdgeRTCClient({url:url.href,diagnostics,canvas:document.querySelector('canvas'),onError:(m,detail)=>{document.querySelector('p').textContent=m;if(detail?.retryable!==false&&!document.hidden&&retries<5)timer=setTimeout(connect,Math.min(30000,2000*2**retries++));},onFrame:f=>{retries=0;if(f.frames===1)parent.postMessage({type:'edge_live_ready',path},playerBase.origin);const label='EdgeRTC · '+f.width+' × '+f.height+' · '+(client.videoCodecName==='h265'?'H.265':'H.264'),note=document.querySelector('p');if(note.textContent!==label)note.textContent=label;}});client.setAudio(.6,muted);client.start().catch(e=>{document.querySelector('p').textContent=e.message;});}
window.edgeStopLive=()=>{clearTimeout(timer);client?.close();};
connect();document.querySelector('button').onclick=()=>{muted=!muted;client?.setAudio(.6,muted);document.querySelector('button').textContent=muted?'Sound':'Mute';};document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(document.hidden)window.edgeStopLive();else connect();});window.addEventListener('pagehide',()=>window.edgeStopLive());
</script>"""


INDEX_HTML = r"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="edge-panel-version" content="__EDGE_PANEL_VERSION__">
    <meta name="edge-ui-build" content="__EDGE_UI_BUILD__">
    <script>{const p=location.pathname.replace(/\/{2,}/g,'/').replace(/\/index\.html$/,'/'),b=document.createElement('base');b.href=location.origin+(p.endsWith('/')?p:p+'/');document.head.prepend(b);}</script>
    <script>__EDGE_INTERFACE_LANGUAGE__</script>
    <script>document.addEventListener('DOMContentLoaded',()=>{const select=document.getElementById('edge-language');for(const [value,label] of [['auto','Automatic (Home Assistant / browser)'],...Object.entries(EdgeI18n.languages)]){const option=document.createElement('option');option.value=value;option.textContent=label;if(value!=='auto')option.translate=false;select.append(option);}EdgeI18n.attach(document.body,{onChange:(language,preference)=>{select.value=preference;document.documentElement.dataset.interfaceLanguage=language;}});select.addEventListener('change',event=>{event.stopPropagation();EdgeI18n.setPreference(select.value);debugEvent('ui_language_changed',{language:EdgeI18n.resolve(),preference:select.value});});debugEvent('ui_language_selected',{language:EdgeI18n.resolve(),preference:EdgeI18n.preference()});},{once:true});</script>
    __EDGE_LIVE_PRELOAD__
    <title>Edge of Infinity</title>
    <script>{const url=new URL('api/brand/icon.png?v=__EDGE_PANEL_VERSION__',document.baseURI).href,icon=document.createElement('link');icon.rel='icon';icon.type='image/png';icon.href=url;document.head.append(icon);document.addEventListener('DOMContentLoaded',()=>{for(const image of document.querySelectorAll('img[data-edge-brand]'))image.src=url;},{once:true});}</script>
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
        height: 100dvh !important;
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
      .form-grid label[hidden] { display: none; }
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
      .recording-transition-frame { position: absolute; inset: 0 0 auto; width: 100%; aspect-ratio: 16 / 9; object-fit: contain; background: #05080a; pointer-events: none; opacity: .999; }
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
      .recording-player-wrap { aspect-ratio:16/9; container-type:inline-size; container-name:nvrplayer; background:#05080a; }
      .recording-display { position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#05080a;pointer-events:none; }
      .recording-player-wrap .recording-player[data-recording-canvas="true"] { opacity:.001;position:absolute;inset:0;z-index:1; }
      .recording-player-wrap .recording-player { height:100%; aspect-ratio:auto; border:0; }
      .recording-player-wrap .nvr-playback-controls { position:absolute; inset:auto 0 0; z-index:3; width:100%; box-sizing:border-box; padding:20px 10px max(6px,env(safe-area-inset-bottom)); gap:2px; background:linear-gradient(transparent,rgba(0,0,0,.92)); transition:opacity .16s; }
      .recording-player-wrap[data-controls-visible="false"] .nvr-playback-controls { opacity:0; pointer-events:none; }
      .recording-player-wrap .nvr-playback-toolbar { flex-wrap:nowrap; gap:5px; }
      .recording-player-wrap .nvr-playback-toolbar button { display:grid; place-items:center; min-height:34px; min-width:34px; padding:5px; border:0; background:transparent; color:#fff; }
      .recording-player-wrap .nvr-playback-toolbar svg { width:18px; height:18px; }
      .recording-player-wrap .nvr-playback-toolbar select { min-height:34px; padding:3px; width:54px; background:#14202be0; color:#fff; }
      .recording-player-wrap .nvr-playback-clock { width:auto; margin-left:auto; margin-right:auto; font-size:12px; color:#fff; }
      .recording-player-wrap .nvr-playback-seek { margin:0; padding:0; border:0; appearance:none; min-height:24px; height:24px; background:transparent; cursor:pointer; touch-action:none; }
      .nvr-seek-shell { position:relative; height:32px; min-width:0; }
      .recording-player-wrap .nvr-seek-shell .nvr-playback-seek { position:absolute; left:0; bottom:0; }
      .nvr-alert-dots { position:absolute; left:8px; right:8px; bottom:12px; height:0; z-index:2; pointer-events:none; }
      .nvr-alert-dots button { position:absolute; top:0; transform:translate(-50%,-50%); width:12px; height:12px; min-height:12px; min-width:12px; padding:0; border:0; background:transparent; pointer-events:auto; touch-action:manipulation; }
      .nvr-alert-dots button::after { content:''; display:block; width:7px; height:7px; border-radius:50%; margin:auto; background:#a3aab3; box-shadow:0 0 0 1px #091018; }
      .nvr-alert-dots button[data-passed="true"]::after { background:var(--accent); }
      .nvr-alert-detail { display:flex; gap:8px; overflow:auto; padding:8px; background:var(--panel); border-radius:8px; }
      .nvr-alert-detail[hidden], .nvr-alert-popup[hidden] { display:none; }
      .nvr-alert-card { display:grid; flex:0 0 145px; gap:4px; color:var(--text); text-align:left; }
      .nvr-alert-card > button { text-align:left; display:grid; gap:4px; }
      .nvr-alert-card img { width:100%; height:95px; object-fit:contain; background:#080e15; border-radius:5px; }
      .nvr-alert-popup { position:absolute; z-index:6; bottom:100px; left:8px; right:8px; display:flex; gap:8px; max-height:calc(100% - 115px); overflow:auto; padding:8px; background:#101c29ee; border-radius:8px; }
      .alert-history-image { width:160px; max-width:100%; height:110px; object-fit:contain; display:block; margin:5px 0; }
      @container nvrplayer (max-width:420px) { .recording-player-wrap .nvr-playback-controls { padding-top:14px; } }
      .nvr-playback-seek::-webkit-slider-runnable-track { height:4px; border-radius:2px; background:linear-gradient(to right,var(--accent) var(--seek-percent,0%),#ffffff66 var(--seek-percent,0%)); }
      .nvr-playback-seek::-webkit-slider-thumb { appearance:none; width:12px; height:12px; margin-top:-4px; border-radius:50%; background:var(--accent); }
      .nvr-playback-seek::-moz-range-track { height:4px; border-radius:2px; background:#ffffff66; }
      .nvr-playback-seek::-moz-range-progress { height:4px; background:var(--accent); }
      .nvr-playback-seek::-moz-range-thumb { width:12px; height:12px; border:0; border-radius:50%; background:var(--accent); }
      .recording-player-wrap .nvr-player-status { position:absolute; z-index:2; top:8px; left:8px; right:8px; margin:0; padding:5px 8px; background:#061018dc; color:white; font-size:12px; pointer-events:none; }
      .recording-player-wrap .nvr-player-status[hidden] { display:none; }
      .recording-player-wrap .recording-transition-frame { z-index:1; }
      .recording-player-wrap:fullscreen, .recording-player-wrap.edge-soft-fullscreen { display:block !important; aspect-ratio:auto; }
      .recording-player-wrap .nvr-playback-extra { display:flex; align-items:center; gap:5px; }
      .recording-player-wrap .nvr-playback-toolbar .nvr-player-more { display:none; }
      @container nvrplayer (max-width:420px) {
        .recording-player-wrap .nvr-playback-toolbar { justify-content:space-between; gap:2px; }
        .recording-player-wrap .nvr-playback-toolbar button { min-width:44px; min-height:44px; }
        .recording-player-wrap .nvr-playback-clock { position:absolute; right:12px; top:1px; margin:0; font-size:11px; }
        .recording-player-wrap .nvr-playback-toolbar .nvr-player-more { display:grid; }
        .recording-player-wrap .nvr-playback-extra { display:none; position:absolute; bottom:54px; right:8px; z-index:5; max-width:calc(100% - 16px); padding:8px; border-radius:8px; background:#101c29; box-shadow:0 2px 8px #0008; }
        .recording-player-wrap[data-extra-open="true"] .nvr-playback-extra { display:flex; }
      }

      .event-history-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
      .event-history-row button { min-height: 44px; }
      #event-pagination { justify-content: space-between; }
      #event-list .field-section { margin-top: 10px; overflow-wrap: anywhere; }
      #event-notification-yaml { width: 100%; min-height: 250px; font: 12px/1.5 ui-monospace, monospace; white-space: pre; padding: 12px; color: var(--text); background: #0c1419; border: 1px solid var(--line); border-radius: 8px; }
      .recording-events { min-width: 0; padding: 10px; border: 1px solid var(--line); border-radius: 8px; }
      .recording-event-track { position: relative; height: 44px; margin: 8px 0; border-bottom: 1px solid var(--line); background: linear-gradient(90deg, transparent 24.8%, var(--line) 25%, transparent 25.2%, transparent 49.8%, var(--line) 50%, transparent 50.2%, transparent 74.8%, var(--line) 75%, transparent 75.2%); }
      .recording-event-marker { display: block; position: absolute; bottom: 0; height: 32px; min-width: 0; padding: 0; border: 0; border-radius: 2px; background: #22c55e; }
      .recording-event-marker[data-kind="person"] { background: #60a5fa; }
      .recording-event-marker[data-kind="vehicle"] { background: #fbbf24; }
      .recording-events select { width: 100%; min-width: 0; min-height: 44px; }
      .zone-editor { margin-top: 12px; min-width: 0; }
      .zone-editor summary { cursor: pointer; min-height: 44px; padding: 10px 0; }
      .zone-editor .event-history-row { margin: 10px 0; }
      .zone-stage { position: relative; width: 100%; max-width: 640px; aspect-ratio: 16 / 9; background: #05080a; overflow: hidden; border: 1px solid var(--line); border-radius: 8px; touch-action: pan-y; }
      .zone-stage[data-drawing="true"] { touch-action: none; cursor: crosshair; outline: 2px solid var(--accent); }
      .zone-stage img, .zone-stage svg { position: absolute; inset: 0; width: 100%; height: 100%; }
      .zone-stage img { pointer-events: none; object-fit: fill; }
      .zone-stage [hidden] { display: none !important; }
      .zone-stage rect { fill: rgba(34,197,94,.18); stroke: #22c55e; stroke-width: 2; vector-effect: non-scaling-stroke; }
      [data-zone-placeholder] { position: absolute; inset: 0; display: grid; place-items: center; padding: 20px; text-align: center; pointer-events: none; color: var(--muted); }

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
      .log-raw, .log-event {border:1px solid var(--line);border-radius:8px;overflow:hidden;min-width:0;}
      .log-raw>summary, .log-event>summary {padding:10px 12px;cursor:pointer;display:flex;align-items:center;flex-wrap:wrap;gap:8px;overflow-wrap:anywhere;}
      .log-event time {font-size:12px;opacity:.75;}
      .log-event.log-error>summary {color:#fa6e7e;}
      .log-event.log-warning>summary {color:#e4b45d;}
      .log-event .log-output {max-height:220px;}
      .log-data {border-color:var(--line);}
      .log-data h2 {color:var(--muted);}
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
      .brand { font-weight: 700; letter-spacing: -.035em; display:flex;align-items:center;gap:4px;font-size:12px;line-height:1.15;white-space:normal; }
      .brand img { width:28px;height:22px;object-fit:cover;flex:0 0 28px; }
      .brand span { max-width:56px; }
      @media(max-width:820px) { .brand,body.nav-collapsed .brand { display:flex;font-size:16px;gap:8px; } .brand span { max-width:none;white-space:nowrap; } .brand img { width:44px;height:28px;flex-basis:44px; } }
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
      .field-section legend + .field-help, .field-section legend + .form-grid { clear: both; }
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
          <div class="brand"><img data-edge-brand alt="" decoding="async" fetchpriority="low"><span>Edge of Infinity</span></div>
          <div class="build-pill" title="Panel build">v__EDGE_PANEL_VERSION__</div>
        </div>
        <nav class="nav" aria-label="Main navigation">
          <div class="nav-section-label">MONITOR</div>
          <button class="active" data-page-target="home"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></svg></span><span class="nav-label">Live view</span></button>
          <button data-page-target="nvr"><span class="nav-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/></svg></span><span class="nav-label">Recordings</span></button>
          <div class="nav-section-label">SETUP</div>
          <button data-page-target="events"><span class="nav-icon">◉</span><span class="nav-label">Zdarzenia / Events</span></button>
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
            <section class="notice" data-recording-disk-usage data-storage-compact role="status" hidden></section><p id="nvr-load-state" class="notice" role="status" hidden></p><div id="nvr-grid" class="grid"></div>
          </section>
        </section>

        <section class="page" data-page="events" hidden>
          <header><div><p class="eyebrow">DETECTION</p><h1>Zdarzenia / Events</h1><p>Ruch, osoby i pojazdy z kamery oraz lokalnej analizy.</p></div><div class="toolbar"><button id="refresh-events" type="button">Odśwież / Refresh</button><button data-page-target="edge-settings">Ustawienia detekcji</button></div></header>
          <section class="panel">
            <p id="detection-runtime" role="status"></p><div id="detection-cameras" class="grid"></div>
            <div class="form-grid"><label>Kamera<select id="event-camera-filter"><option value="">Wszystkie / All</option></select></label><label>Typ<select id="event-kind-filter"><option value="">Wszystkie / All</option><option value="motion">Ruch / Motion</option><option value="person">Osoba / Person</option><option value="vehicle">Pojazd / Vehicle</option><option value="animal">Zwierzę / Animal</option></select></label><label>Dzień rejestratora<input type="date" id="event-day-filter"></label></div>
            <div class="event-history-row"><button type="button" id="event-clear-day">Wszystkie dni / All days</button><span class="field-help">Historia pozostaje na wybranej stronie. Przycisk Odśwież pobiera najnowsze zdarzenia.</span></div>
            <p id="event-history-status" role="status"></p><div id="event-list"></div>
            <div class="event-history-row" id="event-pagination"><button type="button" id="events-newer" disabled>Nowsze / Newer</button><span id="event-page-label"></span><button type="button" id="events-older" disabled>Starsze / Older</button></div>
            <details class="field-section" id="event-notifications"><summary>Powiadomienia na telefon / Home Assistant</summary><p class="field-help">Wybierz kamerę i usługę powiadomień aplikacji Companion. Skopiuj YAML do nowej automatyzacji HA i zapisz ją tam. Edge przygotowuje kod; automatyzację uruchamiasz w HA.</p><div class="form-grid"><label>Kamera<select id="event-notify-camera"></select></label><label>Źródło zdarzeń<select id="event-notify-source"><option value="">Kamera i lokalne AI</option><option value="camera">Kamera / ONVIF</option><option value="local">Lokalne AI</option><option value="zone">Tylko strefa Edge</option></select></label><label>Usługa powiadomień<input id="event-notify-service" placeholder="notify.mobile_app_twoj_telefon" autocomplete="off"></label><label>Przerwa między powiadomieniami (s)<input id="event-notify-cooldown" type="number" min="0" max="3600" value="60"></label></div><textarea id="event-notification-yaml" readonly spellcheck="false" aria-label="YAML automatyzacji powiadomień"></textarea><div class="event-history-row"><button type="button" id="event-copy-notification" disabled>Kopiuj YAML</button><span id="event-notification-status" role="status"></span></div></details>
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
            <section class="field-section" id="language-settings"><h2>Language</h2><label>Interface language<select id="edge-language"></select></label><p class="field-help">Saved in this browser. Automatic uses Home Assistant, then your browser language.</p></section>
            <section class="field-section" id="edge-rtc-engine-info"><h2>EdgeRTC · Live / DVR</h2><p class="notice">Wspólne źródło kamery dla podglądu, nagrywarki i analizy. Archiwum jest odczytywane z serwera przez EdgeRTC DVR. / One camera source; indexed archive playback.</p></section>
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
          <section class="panel"><div class="form-grid"><label>Moduł<select id="log-group-filter"><option value="overview">Przegląd</option><option value="live">Podgląd na żywo · EdgeRTC</option><option value="recordings">Nagrania i galeria</option><option value="network">Urządzenia i sieć</option><option value="hardware">Sprzęt i dysk</option><option value="config">Kamery i konfiguracja</option><option value="ai">Detekcja</option><option value="all">Wszystkie moduły</option></select></label><label>Poziom zdarzeń<select id="log-level-filter"><option value="problems">Błędy i ostrzeżenia</option><option value="all">Wszystkie zdarzenia</option><option value="error">Błędy</option><option value="warning">Ostrzeżenia</option><option value="ok">Informacje</option></select></label><label>Kamera<select id="log-camera-filter"><option value="all">Wszystkie kamery</option></select></label><label>Okres<select id="log-time-filter"><option value="boot">Od uruchomienia aplikacji</option><option value="day">Ostatnie 24 godziny</option><option value="all">Cała zachowana historia</option></select></label><label>Źródło<select id="log-origin-filter"><option value="all">Serwer i urządzenie</option><option value="server">Serwer Edge</option><option value="device">Wybrane urządzenie</option></select></label><label>Szukaj kamery lub zdarzenia<input id="log-search" type="search" placeholder="np. UNV, bufor, AAC"></label></div><p class="field-help">Zdarzenia są uporządkowane według modułu i czasu. Zwykłe buforowanie oraz brak opcjonalnych pomiarów przeglądarki nie oznaczają błędu. Surowe dane są w rozwijanych sekcjach i w eksporcie.</p></section>
          <section class="panel"><h2>Urządzenia i połączenia</h2>
            <div class="form-grid"><label>Nazwa tego urządzenia<input id="device-label" maxlength="64" placeholder="np. iPhone Kamila"></label>
              <label>Logi urządzenia<select id="device-log-select"></select></label></div>
            <button type="button" id="device-log-download">Pobierz logi urządzenia i mapę połączeń</button>
            <p class="notice">Pomiar co 20 s i przy błędach, z podziałem na urządzenie i sesję. Safari może nie udostępniać RTT, RAM lub szczegółów żądania. Limit: 24 urządzenia × 256 KiB, do 7 dni.</p>
            <div id="device-log-view" class="log-grid"></div>
          </section>
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
              <label>Card type<select id="ha-card-type"><option value="edge">Edge Live · EdgeRTC, sound & microphone</option><option value="native">Standard Home Assistant camera</option></select></label>
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
      __EDGE_GALLERY_CACHE__
      __EDGE_LOG_VIEW__
      __EDGE_RECORDING_SURFACE__
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
      const EDGE_NATIVE_PLAYER_DOCUMENT = __EDGE_NATIVE_PLAYER_DOCUMENT__;
      function inlineLiveDocument(url){return `<base href="${escapeHtml(new URL(url,location.href).href)}">`+EDGE_NATIVE_PLAYER_DOCUMENT;}
      const CAMERA_VENDORS = __EDGE_CAMERA_VENDORS__;
      const CAMERA_TEMPLATES = __EDGE_CAMERA_TEMPLATES__;
      let config = { cameras: [] };
      let live = {};
      let presets = [];
      let liveTimer = null;
      let nvrTimer = null;
      let cameraAuto = {};
      let recordingStatus = {};
      let recordingStorage = null;
      const galleryStore=new EdgeRecordingGallery((event,data)=>debugEvent(event,data));
      let galleryScope='',gallerySaveTimer=null,gallerySavedAt=0,gallerySaveSignature='';
      function recordingGalleryScope(){return JSON.stringify([location.origin,config.storage?.recordings_dir||'',(config.cameras||[]).map(c=>[c.id,c.record_stream||'main'])]);}
      async function restoreRecordingGallery(){
        const scope=galleryScope=recordingGalleryScope(),saved=await galleryStore.loadSnapshot(scope);
        if(!saved||scope!==recordingGalleryScope()||Object.values(recordingStatus).some(row=>Array.isArray(row.files)))return;
        let count=0;
        for(const row of saved.cameras){if(config.cameras?.[row.index]?.id!==row.id||!Array.isArray(row.files))continue;recordingStatus[row.index]={...row,...recordingStatus[row.index]};rememberRecordingDay(recordingStatus[row.index]);count+=row.files.length;}
        if(count){renderNvrGrid({reason:'gallery_restore'});void restoreGalleryThumbnails();debugEvent('ui_gallery_restored',{files:count,age_ms:Date.now()-saved.saved_at});}
      }
      function persistRecordingGallery(){
        clearTimeout(gallerySaveTimer);
        gallerySaveTimer=setTimeout(()=>{
          const scope=recordingGalleryScope(),rows=Object.values(recordingStatus).filter(row=>Array.isArray(row.files));
          const signature=scope+'|'+rows.map(row=>row.key+':'+row.selected_day+':'+row.files_revision).join('|');
          if(!rows.length||signature===gallerySaveSignature)return;
          const save=()=>{galleryScope=scope;gallerySaveSignature=signature;gallerySavedAt=Date.now();void galleryStore.saveSnapshot(scope,rows).then(saved=>{if(saved)debugEvent('ui_gallery_saved',{files:rows.reduce((n,row)=>n+row.files.length,0)});});};
          if(window.requestIdleCallback)requestIdleCallback(save,{timeout:2000});else save();
        },Math.max(500,30000-(Date.now()-gallerySavedAt)));
      }
      async function restoreGalleryThumbnails(){
        if(!galleryScope)return;
        await Promise.allSettled([...nvrGrid.querySelectorAll('img[data-recording-thumb-src]')].slice(0,12).map(async image=>{
          if(image.dataset.recordingThumbQueued)return;
          const source=image.dataset.recordingThumbSrc;image.dataset.recordingThumbQueued='true';
          let finished=false;
          const finish=(ok,detached=false)=>{
            if(finished)return;finished=true;recordingThumbnailFinishes.delete(image);
            image.onload=null;image.onerror=null;delete image.dataset.recordingThumbQueued;
            if(ok){thumbnailContainer(image)?.classList.add('thumb-loaded');image.removeAttribute('data-recording-thumb-src');}
            else{releaseRecordingThumbnail(image);if(!detached&&image.isConnected&&activePage==='nvr')loadRecordingThumbnail(image);}
          };
          recordingThumbnailFinishes.set(image,finish);
          try{
            const blob=await galleryStore.loadThumbnail(galleryScope,source);
            if(finished)return;
            if(!blob||!image.isConnected||image.dataset.recordingThumbSrc!==source){finish(false,!image.isConnected);return;}
            const url=recordingThumbnailBlob(image,blob);image.loading='eager';image.dataset.recordingThumbBackend='jpeg';
            image.onload=()=>finish(true);image.onerror=()=>finish(false);image.src=url;
          }catch(_){finish(false);}
        }));
      }
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

      const panelBase = new URL(document.baseURI).pathname;
      let ingressSessionExpired=false,liveSessionReady=true;
      const usesIngress=/\/api\/(?:hassio_ingress|hassio\/ingress)\//.test(panelBase);
      function panelPath(path){return `${panelBase}${String(path).replace(/^\/+/,'')}`;}
      function expireIngressSession(){
        if(ingressSessionExpired)return;ingressSessionExpired=true;liveSessionReady=false;
        deviceReporter?.setPaused?.(true);cancelGalleryPrefetch();updateLivePlaybackVisibility();
        suspendArchivePlayers();
        nvrGrid.querySelectorAll('video[data-recording-player]').forEach(pauseRecording);
        releaseInactiveRecordingPlayers(null,true);
        const notice=document.createElement('aside');notice.dataset.ingressExpired='';notice.setAttribute('role','alert');
        notice.style.cssText='position:fixed;inset:auto 12px 12px;z-index:2147483647;background:#142536;color:white;padding:16px;border-radius:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap';
        notice.append(document.createTextNode('Sesja Home Assistanta wygasła. Otwórz ponownie panel dodatku.'));
        const button=document.createElement('button');button.textContent='Odśwież Home Assistant';button.onclick=()=>{try{window.top.location.reload();}catch(_){location.reload();}};notice.append(button);document.body.append(notice);
      }
      async function panelFetch(url,options){
        if(ingressSessionExpired)throw Error('ingress_session_expired');
        const response=await window.fetch(url,options);
        if(usesIngress&&[401,403].includes(response.status))expireIngressSession();
        return response;
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
        const keepPlaybackControls = target.matches('[data-recording-wrap]');
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
          if (!keepPlaybackControls && media.tagName === 'VIDEO' && typeof media.webkitEnterFullscreen === 'function') {
            media.webkitEnterFullscreen();
            debugEvent('ui_fullscreen_open', { context, mode: 'webkit_video' });
            return;
          }
          const mediaRequest = !keepPlaybackControls && (media.requestFullscreen
            || media.webkitRequestFullscreen
            || media.msRequestFullscreen);
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





      function liveConnectionPlan(path) {
        const url=panelPath(`api/edge-rtc/player/${path}`);
        return {url,canEmbed:true,reason:'edge_rtc',message:'EdgeRTC through the authenticated Home Assistant connection.',
          diagnostics:{engine:'edge_rtc',page_protocol:location.protocol,page_host:location.hostname,
            media_url:new URL(url,location.href).href,transport:location.protocol==='https:'?'wss':'ws'}};
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

      function debugEvent(event,payload={}){
        const data={...payload,page:payload.page||document.querySelector('[data-page]:not([hidden])')?.dataset?.page||'unknown'};
        if(deviceReporter)deviceReporter.note(event,data);
        else{pendingDeviceEvents.push([event,data]);if(pendingDeviceEvents.length>128)pendingDeviceEvents.shift();}
      }
      let deviceReporter=null,deviceReport=null;
      const pendingDeviceEvents=[];
      import(panelPath('api/client-diagnostics.js')).then(({getDeviceReporter})=>{
        deviceReporter=getDeviceReporter({context:{surface:'edge_panel',panel_version:EDGE_PANEL_VERSION,ui_build:EDGE_UI_BUILD},send:async payload=>{
          const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
          try{return (await panelFetch(panelPath('api/client-diagnostics'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal})).ok;}finally{clearTimeout(timer);}
        }});
        document.getElementById('device-label').value=deviceReporter.label||'';
        deviceReporter.registerSource('recordings',()=>({state:activePage==='nvr'?'visible':'closed',
          players:activePage==='nvr'?[...nvrGrid.querySelectorAll('video[data-recording-player]')].filter(v=>v.getAttribute('src')).slice(0,8).map(recordingVideoDiagnostics):[]}));
        for(const [event,data] of pendingDeviceEvents.splice(0))deviceReporter.note(event,data);
      }).catch(error=>{console.warn('Edge device diagnostics module:',error.message);});

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
        if(status==='checking')return 'connecting';
        if (status === 'lost_connection') return 'lost connection';
        return 'offline';
      }

      function statusClass(status) {
        if (status === 'online') return 'badge-online';
        if(status==='checking')return 'badge-lost-connection';
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
        return date.toLocaleString(EdgeI18n.resolve());
      }

      function formatTimestampSeconds(value) {
        if (!value) return 'unknown time';
        const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString(EdgeI18n.resolve(), {
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

      const recordingTimelines=new WeakMap();
      function recordingTimeline(files, status = {}) {
        let playbackOffset = 0;
        const timelineMeta = status?.timeline || {};
        const dayStart = Number(timelineMeta.day_start_ts || 0);
        const requestedTotal = Number(timelineMeta.day_total_seconds || timelineMeta.total_seconds || 0);
        const cached=Array.isArray(files)&&recordingTimelines.get(files);
        if(cached&&cached.dayStart===dayStart&&cached.requestedTotal===requestedTotal&&cached.length===files.length)return cached.timeline;
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
        const timeline={ entries, total, playbackTotal: playbackOffset, dayStart, byUrl:new Map(entries.map(entry=>[entry.file.url,entry])) };
        if(Array.isArray(files))recordingTimelines.set(files,{dayStart,requestedTotal,length:files.length,timeline});
        return timeline;
      }

      function recordingEntryAt(entries,value,field) {
        let low=0,high=entries.length;
        while(low<high){const middle=(low+high)>>1;if(entries[middle][field]<=value)low=middle+1;else high=middle;}
        const previous=entries[Math.max(0,low-1)];
        return previous&&value<previous[field]+previous.duration?previous:entries[low]||entries[entries.length-1];
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
        const input = Math.max(0, Math.min(Number(offset || 0), timelineMax));
        const target=recordingEntryAt(entries,input,'offset');
        const seek = Math.max(0, Math.min(input - target.offset, Math.max(0, target.duration - 0.1)));
        const requested = target.offset + seek;
        const playbackSeek = Math.max(0, Number(target.playbackOffset || 0) + seek);
        return { timeline, target, requested, input, seek, playbackSeek };
      }

      function recordingOffsetForSelection(files, selectedUrl, seekSeconds = 0, status = {}) {
        const timeline = recordingTimeline(files, status);
        const entry = timeline.byUrl.get(selectedUrl) || timeline.entries[timeline.entries.length - 1];
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
        return new Date(year, month - 1, date).toLocaleDateString(EdgeI18n.resolve(), {
          weekday: 'short',
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
        });
      }

      const recordingDayCache=new Map();
      function recordingDayCacheKey(status,day){return `${config.storage?.recordings_dir||''}|${status.key}|${day}`;}
      function rememberRecordingDay(status){
        if(!status.selected_day||!Array.isArray(status.files)||status.files.length>24000)return;
        const key=recordingDayCacheKey(status,status.selected_day);
        const saved=Object.fromEntries(['files','files_revision','selected_day','timeline','playback_cache'].map(name=>[name,status[name]]));
        recordingDayCache.delete(key);recordingDayCache.set(key,saved);
        let count=[...recordingDayCache.values()].reduce((sum,row)=>sum+row.files.length,0);
        while(recordingDayCache.size>4||count>24000){const first=recordingDayCache.keys().next().value;count-=recordingDayCache.get(first).files.length;recordingDayCache.delete(first);}
      }
      function selectRecordingDay(index, day, reason = 'select') {
        if (!day) return;
        const status=recordingStatus[index]||{};
        if(day===status.selected_day&&Array.isArray(status.files)&&day===selectedRecordingDay[index]){debugEvent('ui_recording_day_retained',{index,day,reason});return;}
        discardRecordingScrub(index);

        selectedRecordingDay[index] = day;
        delete selectedRecording[index];
        delete selectedRecordingSeek[index];
        delete selectedRecordingTimeline[index];
        delete recordingStreamStartOffset[index];
        delete recordingStreamNonce[index];
        recordingAutoplayAfterRender[index] = true;
        debugEvent('ui_recording_day_select', { index, day, reason });
        const cached=recordingDayCache.get(recordingDayCacheKey(status,day));
        if(cached){recordingStatus[index]={...status,...cached};renderNvrGrid({reason:'day_cache'});recordingDayLoadMessage('');debugEvent('ui_recording_day_cache',{index,day,files:cached.files.length});}
        loadRecordingStatus({ forceRender: !cached, restartRequest:true, reason: cached?'day_refresh':`day_${reason}` }).catch((error) => {
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
        if (!url) return url;
        let clean = String(url).split('#', 1)[0];
        // Version the representation: a browser must not combine byte ranges
        // from the former remux-only URL with a directly served original.
        {
          const parsed=new URL(clean,location.href);
          if (/\/recordings\/|\/api\/alerts\/\d+\/clip\.mp4$/.test(parsed.pathname)) {
            parsed.searchParams.set('compat','1');parsed.searchParams.set('container','aac-clock-v6');clean=parsed.href;
          }
        }
        return seek?`${clean}#t=${seek.toFixed(3)}`:clean;
      }

      function phonePlayback() {
        return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
      }

      const archivePlayers=new Map();let archiveModule;
      function archiveAvailable(){return EDGE_NATIVE_RTC&&isSecureContext&&typeof VideoDecoder==='function';}
      function suspendArchivePlayers(){for(const player of archivePlayers.values())player.suspend();}
      function disposeArchivePlayer(index){const player=archivePlayers.get(String(index));if(player){player.destroy();archivePlayers.delete(String(index));}}
      function archivePlayerMarkup(index,total,position,poster){return `<div class="recording-player-wrap" data-recording-wrap="${index}" data-dvr-host="${index}">
        <canvas class="recording-player" width="640" height="360" aria-label="Nagrania EdgeRTC DVR" style="display:block;width:100%;height:auto;aspect-ratio:16/9;background:#080e15"></canvas>
        ${poster?`<img data-dvr-poster src="${escapeHtml(poster)}" alt="Podgląd wybranego nagrania" style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain;pointer-events:none">`:''}
        <div class="nvr-playback-controls"><div class="nvr-seek-shell"><div class="nvr-alert-dots" data-alert-axis="${index}" role="group" aria-label="Wykrycia osób i zwierząt na nagraniu"></div><input class="nvr-playback-seek" type="range" min="0" max="${Math.max(0,total-.05)}" step="0.04" value="${position}" data-dvr-seek aria-label="Przewiń nagrania EdgeRTC DVR"></div>
        <div class="nvr-playback-toolbar"><button type="button" data-dvr-action="toggle" aria-label="Odtwórz">▶</button><button type="button" data-dvr-action="back" aria-label="Poprzednia klatka">‹|</button><button type="button" data-dvr-action="next" aria-label="Następna klatka">|›</button><output class="nvr-playback-clock" data-dvr-clock></output><button type="button" data-dvr-action="sound">Dźwięk</button><select data-dvr-rate aria-label="Szybkość nagrania"><option value=".5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select><button type="button" data-dvr-action="fullscreen" aria-label="Pełny ekran">${fullscreenIcon()}</button></div></div>
        <section class="nvr-alert-popup" data-alert-popup="${index}" hidden></section><p class="nvr-player-status" data-dvr-message role="status">Naciśnij ▶, aby odtworzyć przez EdgeRTC DVR.</p></div>`;}
      function mountArchivePlayers(){
        if(!archiveAvailable()||activePage!=='nvr'||ingressSessionExpired)return;
        archiveModule??=import(panelPath('api/edge-rtc/edge-dvr-panel.js?v='+EDGE_PANEL_VERSION));
        archiveModule.then(({EdgeDVRPanel})=>{
          if(!archiveAvailable()||activePage!=='nvr'||ingressSessionExpired)return;
          for(const host of nvrGrid.querySelectorAll('[data-dvr-host]')){
            const index=host.dataset.dvrHost;if(archivePlayers.has(index))continue;
            const status=recordingStatus[index]||{},target=recordingTargetForOffset(index,selectedRecordingTimeline[index]||0)||recordingDefaultTarget(status.files||[],status);
            const player=new EdgeDVRPanel(host,{url:panelPath('api/edge-rtc/archive/'+encodeURIComponent(status.key)),diagnostics:deviceReporter,
              total:()=>recordingTimeline(recordingStatus[index]?.files||[],recordingStatus[index]||{}).playbackTotal,
              position:target?.playbackSeek||0,format:recordingPlaybackClock,targetAt:position=>recordingTargetForPlaybackOffset(index,position),fullscreen:wrap=>requestEdgeFullscreen(wrap,'nvr_edge_dvr'),
              activate:selected=>{if(phonePlayback()){for(const other of archivePlayers.values())if(other!==selected)other.suspend();nvrGrid.querySelectorAll('video[data-recording-player]').forEach(pauseRecording);releaseInactiveRecordingPlayers(null,true);}},
              onTarget:target=>{const download=nvrGrid.querySelector(`[data-recording-download="${index}"]`);if(download)download.href=panelPath(target.target.file.url)+'?download=1';selectedRecording[index]=target.target.file.url;selectedRecordingSeek[index]=target.seek;selectedRecordingTimeline[index]=target.requested;updateRecordingTileSelection(index,target.target.file.url);},

              onPosition:(entry,seconds,fps)=>{selectedRecording[index]=entry.file.url;selectedRecordingSeek[index]=seconds;selectedRecordingTimeline[index]=entry.offset+seconds;updateAlertProgress(index,entry.playbackOffset+seconds);markNvrInteraction('timeupdate');}
            });archivePlayers.set(index,player);
            const autoplay=Boolean(recordingAutoplayAfterRender[index]);delete recordingAutoplayAfterRender[index];
            if(target&&(autoplay||Number(index)===0))void player.seekTarget(target,{play:autoplay});
          }
          repaintAlertAxes();
        }).catch(error=>{archiveModule=null;debugEvent('ui_dvr_module_error',{message:error.message});});
      }


      function releaseInactiveRecordingPlayers(selected,force=false) {
        if(!force&&!phonePlayback())return;
        nvrGrid.querySelectorAll('video[data-recording-player]').forEach(other=>{
          if(other===selected)return;
          pauseRecording(other);recordingSurface.stop(other,true);
          if(other.getAttribute('src')){
            if(other.readyState>=1)other.dataset.recordingSeek=String(other.currentTime);
            other.dataset.recordingSuspendedSrc=mediaUrlWithTimeFragment(other.dataset.recordingSrc||other.getAttribute('src'),other.dataset.recordingSeek);
            if(other.dataset.recordingPoster)other.poster=other.dataset.recordingPoster;
            other.removeAttribute('src');other.load();
          }
          const held=other.closest('[data-recording-wrap]')?.querySelector('[data-recording-transition]');
          if(held){held.hidden=true;held.width=0;held.height=0;delete held.dataset.valid;}
        });
      }

      function restoreRecordingPlayer(video) {
        const source=video.dataset.recordingSuspendedSrc;
        if(!source)return;
        if(video.dataset.recordingPoster&&!video.getAttribute('poster'))video.poster=video.dataset.recordingPoster;
        delete video.dataset.recordingSuspendedSrc;video.preload='auto';video.src=source.split('#')[0];video.load();recordingSurface.begin(video);
      }
      function disposeRecordingPlayer(video){
        if(!video)return;pauseRecording(video);recordingSurface.stop(video,true);
        video.removeAttribute('src');video.load();
      }

      function recordingDefaultTarget(files, status = {}) {
        const timeline = recordingTimeline(files, status);
        const first = timeline.entries[0];
        if (!first) return null;
        return { timeline, target: first, requested: first.offset, input: first.offset, seek: 0, playbackSeek: Number(first.playbackOffset || 0) };
      }

      // One browser decoder shared by hover and tile previews. No camera reads,
      // server transcoding, autoplay or persistent preview files.
      const recordedPreviewCache=new Map(),recordedPreviewJobs=[];
      let recordedPreviewVideo=null,recordedPreviewBusy=false,recordedPreviewReleaseTimer=null;
      function releaseRecordedPreviewDecoder() {
        if(recordedPreviewBusy||!recordedPreviewVideo)return;
        recordedPreviewVideo.pause();recordedPreviewVideo.removeAttribute('src');recordedPreviewVideo.load();
      }
      function previewFileUrl(thumbnail) {
        const url=new URL(thumbnail,location.href);
        url.pathname=url.pathname.replace('/recording-thumbs/','/recordings/').replace(/\.jpg$/,'');
        url.searchParams.delete('at');return url.href;
      }
      function recordedPreview(url,second=0,priority=false) {
        // On phones keep the hardware decoder for the watched recording.
        // Hover falls back to a small server JPEG; tiles already prefer JPEG.
        if(phonePlayback())return Promise.reject(new Error('phone_preview_uses_jpeg'));
        if(activePage!=='nvr')return Promise.reject(new Error('preview_not_visible'));
        const key=`${url}|${Math.round(Number(second)*10)/10}`;
        if(recordedPreviewCache.has(key))return Promise.resolve(recordedPreviewCache.get(key));
        return new Promise((resolve,reject)=>{
          const job={url,second,key,resolve,reject};
          if(priority)recordedPreviewJobs.unshift(job);else recordedPreviewJobs.push(job);
          pumpRecordedPreviews();
        });
      }
      async function pumpRecordedPreviews() {
        if(recordedPreviewBusy||!recordedPreviewJobs.length)return;
        clearTimeout(recordedPreviewReleaseTimer);
        recordedPreviewBusy=true;const job=recordedPreviewJobs.shift();
        try {
          if(recordedPreviewCache.has(job.key)){job.resolve(recordedPreviewCache.get(job.key));return;}
          const video=recordedPreviewVideo||(recordedPreviewVideo=document.createElement('video'));
          video.muted=true;video.playsInline=true;video.preload='auto';
          await new Promise((resolve,reject)=>{
            let seeked=false;
            const cleanup=()=>{clearTimeout(timeout);for(const type of ['loadedmetadata','loadeddata','seeked','error'])video.removeEventListener(type,progress);};
            const fail=()=>{cleanup();reject(new Error('browser_preview_unavailable'));};
            const progress=event=>{
              if(event?.type==='error'){fail();return;}
              if(video.readyState<1)return;
              const target=Math.min(Math.max(0,job.second),Math.max(0,video.duration-.05));
              if(!seeked){seeked=true;video.currentTime=target;}
              if(video.readyState>=2&&!video.seeking&&Math.abs(video.currentTime-target)<.11){cleanup();resolve();}
            };
            const timeout=setTimeout(fail,5000);
            for(const type of ['loadedmetadata','loadeddata','seeked','error'])video.addEventListener(type,progress);
            if(video.src!==job.url){video.src=job.url;video.load();}else progress();
          });
          const canvas=document.createElement('canvas');canvas.width=480;canvas.height=Math.max(2,Math.round(480*video.videoHeight/video.videoWidth));
          canvas.getContext('2d',{alpha:false}).drawImage(video,0,0,canvas.width,canvas.height);
          const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.85));if(!blob)throw new Error('preview_encoding_failed');
          const image=URL.createObjectURL(blob);recordedPreviewCache.set(job.key,image);
          while(recordedPreviewCache.size>64){const oldest=recordedPreviewCache.keys().next().value,url=recordedPreviewCache.get(oldest);URL.revokeObjectURL(url);recordedPreviewCache.delete(oldest);}
          job.resolve(image);
        }catch(error){job.reject(error);}finally{
          recordedPreviewBusy=false;pumpRecordedPreviews();
          if(!recordedPreviewBusy)recordedPreviewReleaseTimer=setTimeout(releaseRecordedPreviewDecoder,500);
        }
      }
      window.addEventListener('pagehide',()=>{recordedPreviewVideo?.pause();if(recordedPreviewVideo){recordedPreviewVideo.removeAttribute('src');recordedPreviewVideo.load();}for(const job of recordedPreviewJobs.splice(0))job.reject(new Error('page_closed'));for(const url of recordedPreviewCache.values())URL.revokeObjectURL(url);recordedPreviewCache.clear();});

      const recordingThumbnailQueue = [];
      let recordingThumbnailActive = 0;
      const recordingThumbnailUrls = new Map();
      const recordingThumbnailFinishes = new WeakMap();
      function releaseRecordingThumbnail(image,detach=false){
        // Cancel the resource before revoking it: WebKit can otherwise retry
        // a detached/unloading image against a Blob URL that no longer exists.
        if(detach){image.onload=null;image.onerror=null;image.removeAttribute('srcset');image.removeAttribute('src');recordingThumbnailFinishes.get(image)?.(false,true);}
        const url=recordingThumbnailUrls.get(image);if(url){if(image.getAttribute('src')===url)image.removeAttribute('src');recordingThumbnailUrls.delete(image);URL.revokeObjectURL(url);}
      }
      function recordingThumbnailBlob(image,blob){releaseRecordingThumbnail(image);const url=URL.createObjectURL(blob);recordingThumbnailUrls.set(image,url);return url;}
      // WebKit may reload a decoded image after layout/scroll. Keep its Blob
      // readable for the displayed node's lifetime, rather than revoking on load.
      new MutationObserver(()=>{for(const image of recordingThumbnailUrls.keys())if(!image.isConnected)releaseRecordingThumbnail(image,true);}).observe(nvrGrid,{childList:true,subtree:true});
      window.addEventListener('pagehide',event=>{if(!event.persisted)for(const image of recordingThumbnailUrls.keys())releaseRecordingThumbnail(image,true);});
      function thumbnailContainer(image) { return image.closest('.recording-thumb-wrap, .recording-day-thumb'); }
      function markRecordingThumbnailFailed(image) {
        image.onload = null;
        image.onerror = null;
        thumbnailContainer(image)?.classList.add('thumb-error');
        image.removeAttribute('data-recording-thumb-src');
        image.src = THUMB_PLACEHOLDER;
      }
      function pumpRecordingThumbnails() {
        if(activePage!=='nvr')return;
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
            recordingThumbnailFinishes.delete(image);
            image.onload = null; image.onerror = null;
            if(!ok)releaseRecordingThumbnail(image);
            delete image.dataset.recordingThumbQueued;
            if (ok) { thumbnailContainer(image)?.classList.remove('thumb-error'); thumbnailContainer(image)?.classList.add('thumb-loaded'); image.removeAttribute('data-recording-thumb-src'); }
            else markRecordingThumbnailFailed(image);
            recordingThumbnailActive -= 1;
            pumpRecordingThumbnails();
          };
          recordingThumbnailFinishes.set(image,finish);
          let expectedUrl = new URL(source, window.location.href).href;
          image.onload = () => { if (image.currentSrc === expectedUrl && image.naturalWidth > 1) finish(true); };
          let fallback=false;
          image.onerror = () => { if (image.currentSrc !== expectedUrl)return;releaseRecordingThumbnail(image);if(fallback){finish(false);return;}fallback=true;image.dataset.recordingThumbBackend='browser';recordedPreview(previewFileUrl(source),0).then(url=>{expectedUrl=url;image.src=url;}).catch(()=>finish(false)); };
          image.loading = 'eager';
          // A small cached JPEG avoids downloading/decoding full 4K clips for
          // every film tile, especially through Nabu Casa.
          Promise.resolve(galleryScope?galleryStore.loadThumbnail(galleryScope,source):null).then(async cached=>{
            if(cached)return {ok:true,blob:async()=>cached};
            return fetch(source,{signal:AbortSignal.timeout(15000)});
          }).then(async response=>{
            if([429,425,503].includes(response.status)){
              const attempt=Number(image.dataset.thumbAttempts||0)+1;image.dataset.thumbAttempts=String(attempt);
              if(attempt>6){finish(false);return;}
              finished=true;recordingThumbnailFinishes.delete(image);image.onload=null;image.onerror=null;recordingThumbnailActive--;
              const wait=Math.max(Math.min(8000,500*2**(attempt-1)),Math.min(60000,Math.max(0,Number(response.headers.get('Retry-After'))||0)*1000));
              setTimeout(()=>{delete image.dataset.recordingThumbQueued;if(image.isConnected)loadRecordingThumbnail(image);},wait);
              pumpRecordingThumbnails();return;
            }
            if(!response.ok)throw new Error('thumbnail_unavailable');
            const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('thumbnail_unavailable');
            if(!image.isConnected){finish(false);return;}
            if(galleryScope)void galleryStore.saveThumbnail(galleryScope,source,blob);
            expectedUrl=recordingThumbnailBlob(image,blob);image.dataset.recordingThumbBackend='jpeg';image.src=expectedUrl;
          }).catch(()=>{
            if(finished)return;fallback=true;image.dataset.recordingThumbBackend='browser';recordedPreview(previewFileUrl(source),0).then(url=>{expectedUrl=url;image.src=url;}).catch(()=>finish(false));
          });
        }
      }
      function loadRecordingThumbnail(image) {
        if (!image?.dataset?.recordingThumbSrc || image.dataset.recordingThumbQueued) return;
        image.dataset.recordingThumbQueued = 'true';
        recordingThumbnailQueue.push(image);
        pumpRecordingThumbnails();
      }

      function hydrateRecordingThumbnails() {
        if(activePage!=='nvr')return;
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

      let galleryPrefetchTimer=null,galleryPrefetchEpoch=0;
      function cancelGalleryPrefetch(){clearTimeout(galleryPrefetchTimer);galleryPrefetchTimer=null;galleryPrefetchEpoch++;}
      function scheduleGalleryPrefetch(){
        cancelGalleryPrefetch();if(activePage!=='home'||document.hidden)return;
        const epoch=galleryPrefetchEpoch;
        galleryPrefetchTimer=setTimeout(async()=>{
          galleryPrefetchTimer=null;
          if(epoch!==galleryPrefetchEpoch||activePage!=='home'||document.hidden)return;
          const frames=[...grid.querySelectorAll('iframe.live-frame')].filter(f=>f.dataset.liveSuspended!=='true');
          if(EDGE_NATIVE_RTC&&frames.some(f=>f.dataset.liveReady!=='true'&&overviewCameras.find(c=>(c.key||`${c.id}_${c.index??0}`)===f.dataset.liveKey)?.status==='online'))return;
          if(!(config.cameras||[]).every((_,i)=>Array.isArray(recordingStatus[i]?.files)))await loadRecordingStatus({history:true,reason:'gallery_prefetch'});
        },2000);
      }
      const recordingSurface=new EdgeRecordingSurface((event,data)=>debugEvent(event,data),video=>{
        if(!video.paused&&(video.dataset.recordingLoading==='true'||video.dataset.recordingBuffering==='true')){
          video.dataset.recordingLoading='false';video.dataset.recordingBuffering='false';updateRecordingPlayerControls(video,'');
        }
      });
      function holdRecordingFrame(video) {
        const wrap=video.closest('[data-recording-wrap]'),frame=wrap?.querySelector('[data-recording-transition]');
        const preview=video.closest('[data-nvr-index]')?.querySelector('.recording-tile.active img');
        recordingSurface.hold(video,frame,preview);
      }
      function releaseRecordingFrame(video) {recordingSurface.release(video,video.closest('[data-recording-wrap]')?.querySelector('[data-recording-transition]'));}

      function pauseRecording(video) {
        const scrub=recordingScrubs.get(video.dataset.recordingPlayer);if(scrub)scrub.resume=false;
        delete video.dataset.recordingBuffering;
        video.dataset.recordingWantsPlay='false';video.dataset.recordingPlayPending='false';video.dataset.recordingPlayEpoch=String(Number(video.dataset.recordingPlayEpoch||0)+1);video.pause();
      }
      function recordingPlaybackRequested(video) {
        return Boolean(video&&!video.ended&&(!video.paused||video.dataset.recordingPlayPending==='true'||video.dataset.recordingWantsPlay==='true'));
      }
      const recordingPauseRecovery=new WeakMap();
      function recoverUnexpectedRecordingPause(video){
        if(!video||!video.paused||video.ended||video.dataset.recordingWantsPlay!=='true'||recordingScrubs.has(video.dataset.recordingPlayer))return;
        debugEvent('ui_recording_native_pause',recordingVideoDiagnostics(video));
        if(activePage!=='nvr'||document.hidden)return;
        if(video.readyState<3||video.seeking||video.dataset.recordingLoading==='true'||video.dataset.recordingBuffering==='true')return;
        const now=performance.now(),previous=recordingPauseRecovery.get(video),state=previous&&now-previous.at<5000?previous:{at:now,count:0};
        if(state.count>=2)return;state.count++;recordingPauseRecovery.set(video,state);
        const epoch=video.dataset.recordingPlayEpoch,source=video.dataset.recordingSrc;
        setTimeout(()=>{if(video.isConnected&&activePage==='nvr'&&!document.hidden&&!video.ended&&video.paused&&video.dataset.recordingWantsPlay==='true'&&video.dataset.recordingPlayEpoch===epoch&&video.dataset.recordingSrc===source&&!recordingScrubs.has(video.dataset.recordingPlayer))playRecording(video);},150);
      }
      const recordingProgressWatch=new WeakMap();
      function watchRecordingProgress(video) {
        if(activePage!=='nvr'||document.hidden||!video.isConnected||video.dataset.recordingWantsPlay!=='true'||video.ended||recordingScrubs.has(video.dataset.recordingPlayer)){recordingProgressWatch.delete(video);return;}
        const now=performance.now(),src=video.dataset.recordingSrc||video.src,time=video.currentTime;
        let state=recordingProgressWatch.get(video);
        if(!state||state.src!==src){state={src,time,at:now,retries:0};recordingProgressWatch.set(video,state);return;}
        if(Math.abs(state.time-time)>.04){state.time=time;state.at=now;return;}
        if(now-state.at<8000)return;
        // Try Play before a reload. A long initial download must not be
        // repeatedly aborted; only one reload per selected segment is allowed.
        if(state.retries===0){state.retries=1;state.at=now;debugEvent('ui_recording_progress_retry',recordingVideoDiagnostics(video));playRecording(video);return;}
        if(now-state.at<12000)return;
        if(state.retries===1&&Number(video.dataset.recordingRecoveryReloads||0)<1){
          state.retries=2;state.at=now;video.dataset.recordingRecoveryReloads='1';
          const position=video.seeking?Number(video.dataset.recordingSeek||0):time;
          holdRecordingFrame(video);video.dataset.recordingLoading='true';video.dataset.recordingSeek=String(position);
          debugEvent('ui_recording_progress_reload',recordingVideoDiagnostics(video));
          video.src=mediaUrlWithTimeFragment(src,position).split('#')[0];video.load();recordingSurface.begin(video);playRecording(video);return;
        }
        if(now-state.at>=12000){debugEvent('ui_recording_progress_timeout',recordingVideoDiagnostics(video));pauseRecording(video);updateRecordingPlayerControls(video,'Nagranie nie dostarcza klatek. Naciśnij Play lub przetestuj kodeki w ustawieniach odtwarzacza.');}
      }
      setInterval(()=>nvrGrid.querySelectorAll('video[data-recording-player]').forEach(watchRecordingProgress),2000);

      function playRecording(video) {
        if(!video?.isConnected||!nvrGrid.contains(video))return;
        if(phonePlayback())suspendArchivePlayers();
        const status=recordingStatus[video.dataset.recordingPlayer];if(!selectedRecordingDay[video.dataset.recordingPlayer]&&status?.selected_day)selectedRecordingDay[video.dataset.recordingPlayer]=status.selected_day;
        releaseInactiveRecordingPlayers(video);restoreRecordingPlayer(video);
        if(recordingPictureUnavailable(video))return;
        if(video.error){
          const position=video.currentTime||Number(video.dataset.recordingSeek||0);
          video.dataset.recordingSeek=String(position);video.src=mediaUrlWithTimeFragment(video.dataset.recordingSrc||video.src,position).split('#')[0];video.load();recordingSurface.begin(video);
        }
        video.dataset.recordingWantsPlay='true';video.dataset.recordingPlayPending='true';
        const epoch=String(Number(video.dataset.recordingPlayEpoch||0)+1);video.dataset.recordingPlayEpoch=epoch;
        const scrub=recordingScrubs.get(video.dataset.recordingPlayer);
        if(scrub){scrub.resume=true;if(scrub.active){updateRecordingPlayerControls(video,'');return;}}
        const request=video.play();
        if(!video.paused)video.dataset.recordingPlayPending='false';
        request.catch(error=>{
          if(video.dataset.recordingPlayEpoch!==epoch||video.dataset.recordingWantsPlay!=='true'||error.name==='AbortError')return;
          video.dataset.recordingWantsPlay='false';video.dataset.recordingPlayPending='false';updateRecordingPlayerControls(video,error.name==='NotAllowedError'?'Przeglądarka wymaga kliknięcia Play.':'Nie udało się odtworzyć nagrania. Spróbuj ponownie.');
        });
      }
      function switchRecordingVideoToFile(index, target, autoplay = false, reason = 'server_file_seek') {
        if (!target?.target?.file?.url) return false;
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        if (!video) return false;

        releaseInactiveRecordingPlayers(video);delete video.dataset.recordingSuspendedSrc;
        const fileUrl = target.target.file.url;
        const nextPath = panelPath(fileUrl);
        const nextSrc = mediaUrlWithTimeFragment(nextPath, target.seek);
        const fileSeek=video.dataset.recordingLoadedFile===fileUrl&&Number.isFinite(video.duration)
          ? Math.min(target.seek,Math.max(0,video.duration-.05)) : target.seek;
        video.preload='auto';
        holdRecordingFrame(video);
        selectedRecording[index] = fileUrl;
        selectedRecordingSeek[index] = target.seek;
        selectedRecordingTimeline[index] = target.requested;
        recordingStreamStartOffset[index] = target.target.offset;
        video.dataset.recordingSeek = String(fileSeek);
        video.dataset.recordingSeekStarted=String(performance.now());
        delete video.dataset.recordingStableAt;delete video.dataset.recordingStableClock;
        video.dataset.recordingLoading='true';video.dataset.recordingBuffering='false';video.dataset.recordingWantsPlay=String(autoplay);
        video.dataset.recordingRecoveryReloads='0';recordingProgressWatch.delete(video);
        video.dataset.recordingStreamStart = String(target.target.offset);
        video.dataset.recordingPlaybackStart = String(target.target.playbackOffset || 0);
        video.dataset.recordingPlaybackMode = 'server_file_sequence';
        video.dataset.recordingFileUrl = fileUrl;
        video.defaultPlaybackRate = video.playbackRate;
        updateRecordingTimelineUi(index, target.requested, target.timeline.total);
        updateRecordingTileSelection(index, fileUrl);
        if (video.dataset.recordingLoadedFile === fileUrl && video.readyState >= 1) {
          if(!video.seeking&&video.readyState>=2&&Math.abs(video.currentTime-fileSeek)<.001){
            video.dataset.recordingLoading='false';
            if(autoplay)playRecording(video);releaseRecordingFrame(video);return true;
          }
          try {
            video.currentTime = fileSeek;recordingSurface.begin(video);
          } catch (error) {
            debugEvent('ui_recording_server_file_seek_error', { index, reason, message: error.message });
          }
          if (autoplay) playRecording(video);
          if(!recordingScrubs.has(String(index)))debugEvent('ui_recording_server_file_fast_seek', { index, reason, recording: target.target.file.name, timeline_second: target.requested, playback_second: target.playbackSeek, segment_second: target.seek });
          return true;
        }
        video.removeAttribute("poster");
        video.dataset.recordingLoading='true';video.dataset.recordingNetworkRetries='0';video.dataset.recordingWantsPlay=String(autoplay);video.dataset.recordingPlayPending='false';
        video.dataset.recordingLoadedFile = fileUrl;
        delete video.dataset.recordingStableAt;
        video.src = nextSrc.split('#')[0];
        video.dataset.recordingSrc = nextSrc;
        video.load();recordingSurface.begin(video);
        if (autoplay) {
          playRecording(video);
        }
        if(!recordingScrubs.has(String(index)))debugEvent('ui_recording_server_file_switch', { index, reason, recording: target.target.file.name, timeline_second: target.requested, playback_second: target.playbackSeek, segment_second: target.seek });
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
          playRecording(video);
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
        if([...archivePlayers.values()].some(player=>player.wantPlay||player.client?.picture))return true;
        return Array.from(nvrGrid.querySelectorAll('video[data-recording-player]')).some((video) => {
          return video.readyState >= 1 && !video.ended;
        });
      }

      function updateRecordingTimelineUi(index, value, total) {
        const safeValue = Math.max(0, Math.floor(Number(value || 0)));
        const safeTotal = Math.max(0, Math.floor(Number(total || 0)));
        selectedRecordingTimeline[index] = Math.min(safeValue, Math.max(0, safeTotal - 1));
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
        const playbackMax = Math.max(0, Number(timeline.playbackTotal || 0) - 0.1);
        const input = Math.max(0, Math.min(Number(playbackOffset || 0), playbackMax));
        const target = recordingEntryAt(entries,input,'playbackOffset');
        const seek = Math.max(0, Math.min(input - target.playbackOffset, Math.max(0, target.duration - 0.1)));
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

      function selectRecordingAtOffset(index, offset, options = {}) {
        const archive=archivePlayers.get(String(index));if(archive){const target=recordingTargetForOffset(index,offset);if(target)void archive.seekTarget(target,{play:options.play??archive.wantPlay});return;}
        if(!selectedRecordingDay[index]&&recordingStatus[index]?.selected_day)selectedRecordingDay[index]=recordingStatus[index].selected_day;
        if(!options.scrubbing)discardRecordingScrub(index);
        clearRecordingContinuation(index);
        markNvrInteraction(options.scrubbing?'timeupdate':'select_recording_offset', { index, offset });
        const target = recordingTargetForOffset(index, offset);
        if (!target) return;
        const video = nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        const wasPlaying = recordingPlaybackRequested(video);
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
          recordingSurface.begin(video);
          const autoplay=Boolean(recordingAutoplayAfterRender[index]);
          if(autoplay)delete recordingAutoplayAfterRender[index];
          const applySeek = () => {
            if(!video.isConnected||nvrGrid.querySelector(`video[data-recording-player="${index}"]`)!==video)return;
            const seek=Math.max(0,Number(video.dataset.recordingSeek||0));
            if (seek&&Math.abs(video.currentTime-seek)>.01) {
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
          };
          updateRecordingPlayerControls(video);
          if (video.readyState >= 1) {
            applySeek();
          } else {
            video.addEventListener('loadedmetadata', applySeek, { once: true });
          }
          if(autoplay)playRecording(video);
        });
      }

      function syncRecordingVideoProgress(video) {
        const index = video?.dataset?.recordingPlayer;
        if (index === undefined) return;
        if (recordingScrubs.has(String(index))) {updateRecordingPlayerControls(video);return;}
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

      async function loadRecordingTiming(video) {
        const index=video.dataset.recordingPlayer,file=recordingTargetForPlaybackOffset(index,recordingPlaybackPosition(video))?.target?.file;
        if(!file)return;const source=video.dataset.recordingLoadedFile;
        try{
          const response=await panelFetch(panelPath(`api/recording/timing?${new URLSearchParams({key:recordingStatus[index].key,name:file.name})}`));
          if(!response.ok)return;const data=await response.json();if(!video.isConnected||video.dataset.recordingLoadedFile!==source)return;
          video.dataset.recordingFps=String(data.fps||0);video.dataset.recordingCodec=data.codec_name||'';
          video.dataset.recordingKeyframeInterval=String(data.keyframe_interval_seconds||0);
          const slider=nvrGrid.querySelector(`[data-recording-playback-seek="${index}"]`);if(slider&&data.fps>0)slider.step=String(1/data.fps);
          video.dataset.recordingTimingSource=source||'';
          video.dataset.recordingAudioCodec=data.audio_codec||'';video.dataset.recordingAudioRate=String(data.audio_sample_rate||0);
          video.dataset.recordingAudioDuration=String(data.audio_duration_seconds||0);video.dataset.recordingAudioRegular=String(data.audio_clock_regular!==false);
          debugEvent('ui_recording_media_timing',{index,recording:file.name,...data});
          if(!recordingPictureUnavailable(video))updateRecordingPlayerControls(video);
        }catch(_){}
      }
      const recordingScrubs = new Map(), recordingControlsTimers = new WeakMap();
      function showRecordingControls(wrap) {
        if (!wrap) return;
        wrap.dataset.controlsVisible = 'true';
        clearTimeout(recordingControlsTimers.get(wrap));
        recordingControlsTimers.set(wrap,setTimeout(()=>{
          const video=wrap.querySelector('video'), focus=wrap.querySelector(':focus-visible');
          if (video&&!video.paused&&!recordingScrubs.has(video.dataset.recordingPlayer)&&!focus&&wrap.dataset.extraOpen!=='true') wrap.dataset.controlsVisible='false';
        },2500));
      }
      function discardRecordingScrub(index) {
        const state=recordingScrubs.get(String(index));if(!state)return;
        clearTimeout(state.timer);clearTimeout(state.deadline);
        state.video.closest('[data-recording-wrap]')?.removeAttribute('data-scrubbing');
        recordingScrubs.delete(String(index));
      }
      function beginRecordingScrub(slider) {
        const index=slider.dataset.recordingPlaybackSeek;
        let state=recordingScrubs.get(index);
        if(state){state.active=true;state.finishing=false;clearTimeout(state.deadline);return state;}
        const video=nvrGrid.querySelector(`video[data-recording-player="${index}"]`);if(!video)return null;
        state={index,video,slider,active:true,finishing:false,resume:recordingPlaybackRequested(video),original:recordingPlaybackPosition(video),desired:Number(slider.value),applied:null,lastSeek:0};
        recordingScrubs.set(index,state);clearRecordingContinuation(index);video.pause();
        markNvrInteraction('recording_scrub_start',{index});
        const wrap=video.closest('[data-recording-wrap]');wrap.dataset.scrubbing='true';showRecordingControls(wrap);
        return state;
      }
      function queueRecordingScrub(slider) {
        const state=beginRecordingScrub(slider);if(!state)return;
        state.desired=Number(slider.value);state.changedAt=performance.now();updateRecordingPlayerControls(state.video);
        markNvrInteraction('timeupdate');
        flushRecordingScrub(state);
      }
      function flushRecordingScrub(state) {
        if(recordingScrubs.get(state.index)!==state)return;
        clearTimeout(state.timer);
        const video=state.video;
        if(!video.isConnected){discardRecordingScrub(state.index);return;}
        const delay=state.active?Math.max(0,80-(performance.now()-state.lastSeek)):0;
        const desiredTarget=recordingTargetForPlaybackOffset(state.index,state.desired);
        const crossFile=desiredTarget?.target?.file?.url!==video.dataset.recordingLoadedFile;
        const settle=state.active&&crossFile?Math.max(0,100-(performance.now()-(state.changedAt||0))):0;
        if(settle){state.timer=setTimeout(()=>flushRecordingScrub(state),settle);return;}
        const supersede=state.applied!==state.desired&&(video.error||state.finishing||performance.now()-state.lastSeek>160);
        if(((video.seeking||video.readyState<1)&&!supersede)||delay){state.timer=setTimeout(()=>flushRecordingScrub(state),Math.max(40,delay));return;}
        if(state.applied!==state.desired){
          state.applied=state.desired;state.lastSeek=performance.now();
          seekRecordingPlayback(state.index,state.desired,true);
          if(state.finishing&&state.resume)playRecording(video);
        }
        const expected=Number.isFinite(video.duration)?Math.min(state.desired,Number(video.dataset.recordingPlaybackStart||0)+Math.max(0,video.duration-.05)):state.desired;
        const advance=state.playStarted?Math.max(0,(performance.now()-state.playStarted)/1000)*video.playbackRate:0;
        if(state.finishing&&!video.seeking&&video.readyState>=2&&Math.abs(recordingPlaybackPosition(video)-expected)<.2+advance){
          const resume=state.resume;discardRecordingScrub(state.index);
          updateRecordingPlayerControls(video,'');showRecordingControls(video.closest('[data-recording-wrap]'));
          if(resume)playRecording(video);else pauseRecording(video);
          return;
        }
        state.timer=setTimeout(()=>flushRecordingScrub(state),80);
      }
      function finishRecordingScrub(slider,cancel=false) {
        const state=recordingScrubs.get(slider.dataset.recordingPlaybackSeek);if(!state)return;
        state.desired=cancel?state.original:Number(slider.value);state.slider.value=String(state.desired);
        state.active=false;state.finishing=true;
        debugEvent('ui_recording_scrub_commit',{index:state.index,position:state.desired,cancel,...recordingVideoDiagnostics(state.video),keyframe_interval_seconds:Number(state.video.dataset.recordingKeyframeInterval||0),prefetch:false});
        clearTimeout(state.deadline);state.deadline=setTimeout(()=>{
          if(recordingScrubs.get(state.index)!==state)return;
          discardRecordingScrub(state.index);pauseRecording(state.video);
          updateRecordingPlayerControls(state.video,'Nie udało się wczytać wybranego momentu. Spróbuj ponownie.');
        },15000);
        flushRecordingScrub(state);
        if(state.resume&&recordingScrubs.get(state.index)===state)playRecording(state.video);
      }
      window.addEventListener('pagehide',()=>{for(const index of recordingScrubs.keys())discardRecordingScrub(index);});
      window.addEventListener('blur',()=>{for(const state of recordingScrubs.values()){state.resume=false;if(state.active)finishRecordingScrub(state.slider);}});

      function recordingControlIcon(name) {
        const shapes={play:'<path d="m8 5 11 7-11 7z"/>',pause:'<path d="M8 5v14M16 5v14"/>',settings:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',sound:'<path d="m11 5-6 5H2v4h3l6 5zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',muted:'<path d="m11 5-6 5H2v4h3l6 5zM16 9l5 6M21 9l-5 6"/>'};
        return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true">${shapes[name]||shapes.play}</svg>`;
      }
      function recordingPlayerControls(index, timeline, position = 0) {
        const total = Math.max(0, Number(timeline.playbackTotal || 0));
        return `<div class="nvr-playback-controls">
          <div class="nvr-seek-shell"><div class="nvr-alert-dots" data-alert-axis="${index}" role="group" aria-label="Wykrycia osób i zwierząt na nagraniu"></div>          <input class="nvr-playback-seek" type="range" min="0" max="${Math.max(0,total-0.1)}" step="0.1" value="${position}" data-recording-playback-seek="${index}" aria-label="Seek through all recorded video in selected day" aria-valuetext="${recordingPlaybackClock(position)} / ${recordingPlaybackClock(total)}"></div>
          <div class="nvr-playback-toolbar">
            <button type="button" data-recording-toggle="${index}" aria-label="Play selected day's recordings" title="Play">${recordingControlIcon('play')}</button>

<button type="button" data-recording-frame="-1" data-record-index="${index}" aria-label="Poprzednia klatka" title="Poprzednia klatka" disabled>‹|</button><button type="button" data-recording-frame="1" data-record-index="${index}" aria-label="Następna klatka" title="Następna klatka" disabled>|›</button>
            <output class="nvr-playback-clock" data-recording-duration="${index}">${recordingPlaybackClock(position)} / ${recordingPlaybackClock(total)}</output>


<div class="nvr-playback-extra" id="recording-extra-${index}"><button type="button" data-recording-codecs="${index}" title="Test H.264 / H.265 i dźwięku">Kodeki</button>            <button type="button" data-recording-mute="${index}" aria-label="Mute or unmute" title="Mute">${recordingControlIcon('sound')}</button>            <button class="nvr-skip" type="button" data-recording-jump="-1" data-record-index="${index}" aria-label="Back 1 second" title="Back 1 second">−1 s</button>
            <button class="nvr-skip" type="button" data-recording-jump="1" data-record-index="${index}" aria-label="Forward 1 second" title="Forward 1 second">+1 s</button>            <label class="nvr-speed-label"><select data-recording-speed="${index}" aria-label="Playback speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label></div>
            <button class="nvr-player-more" type="button" data-recording-more="${index}" aria-label="Playback settings" title="Playback settings" aria-controls="recording-extra-${index}" aria-expanded="false">${recordingControlIcon('settings')}</button>
            <button type="button" data-recording-fullscreen="${index}" aria-label="Fullscreen" title="Fullscreen">${fullscreenIcon()}</button>
          </div>
        </div>`;
      }

      function recordingPlaybackPosition(video) {
        return Math.max(0, Number(video?.dataset?.recordingPlaybackStart || 0) + Number(video?.currentTime || 0));
      }

      function recordingPictureUnavailable(video) {
        // A browser can report canplay and advance audio while silently
        // ignoring an unsupported HEVC video track. Do not show black Play.
        if(video.readyState<2||video.videoWidth||!video.dataset.recordingCodec)return false;
        const hevc=video.dataset.recordingCodec==='hevc';
        discardRecordingScrub(video.dataset.recordingPlayer);pauseRecording(video);
        updateRecordingPlayerControls(video,hevc?'Ta przeglądarka nie odtwarza obrazu H.265 z tego pliku. Otwórz nagranie w przeglądarce z obsługą H.265.':'Ta przeglądarka nie odtwarza obrazu z tego fragmentu nagrania.');
        return true;
      }

      function updateRecordingPlayerControls(video, message = null) {
        if (!video) return;
        const index = video.dataset.recordingPlayer;
        const status = recordingStatus[index] || {};
        const total = Number(status.timeline?.playback_total_seconds || status.timeline?.recorded_seconds || 0);
        const scrubbing = recordingScrubs.get(String(index));
        const position = Math.min(total, scrubbing ? scrubbing.desired : recordingPlaybackPosition(video));
        const clock = nvrGrid.querySelector(`[data-recording-duration="${index}"]`);
        if(clock)clock.textContent=`${recordingPlaybackClock(position)}${video.paused?'.'+String(Math.floor((position%1)*1000)).padStart(3,'0'):''} / ${recordingPlaybackClock(total)}`;
        const slider = nvrGrid.querySelector(`[data-recording-playback-seek="${index}"]`);
        if (slider) {
          slider.max = String(Math.max(0, total - 0.1));
          if (!scrubbing) slider.value = String(position);
          slider.setAttribute('aria-valuetext',`${recordingPlaybackClock(position)} / ${recordingPlaybackClock(total)}`);
          slider.style.setProperty('--seek-percent',`${Math.min(100,position/Math.max(.1,total-.1)*100)}%`);
        }
        updateAlertProgress(index,position);
        const fps=Number(video.dataset.recordingFps||0);
        video.closest('[data-recording-wrap]')?.querySelectorAll('[data-recording-frame]').forEach(button=>button.disabled=!(fps>0));
        const toggle = nvrGrid.querySelector(`[data-recording-toggle="${index}"]`);
        if (toggle) { const playing=scrubbing?scrubbing.resume:recordingPlaybackRequested(video);const label=playing?'Pause':'Play';toggle.innerHTML=recordingControlIcon(playing?'pause':'play');toggle.title=label;toggle.setAttribute('aria-label',label);toggle.setAttribute('aria-pressed',String(playing)); }
        const mute = nvrGrid.querySelector(`[data-recording-mute="${index}"]`);
        if (mute) {mute.innerHTML=recordingControlIcon(video.muted?'muted':'sound');mute.title=video.muted?'Unmute':'Mute';mute.setAttribute('aria-label',mute.title);}
        const note = nvrGrid.querySelector(`[data-recording-playback-message="${index}"]`);
        if (note && message !== null) {note.textContent=message;note.hidden=!message||message==='Playing recordings for selected day';}
      }

      function seekRecordingPlayback(index, position, scrubbing = false) {
        const target = recordingTargetForPlaybackOffset(index, position);
        if (target) selectRecordingAtOffset(index, target.requested, {scrubbing});
      }

      function recordingVideoDiagnostics(video) {
        if(!video)return {};
        const buffered=[];
        try{for(let i=0;i<Math.min(4,video.buffered.length);i++)buffered.push([video.buffered.start(i),video.buffered.end(i)]);}catch(_){}
        const current=Number(video?.currentTime||0),range=buffered.find(([a,b])=>a<=current&&current<=b),quality=video.getVideoPlaybackQuality?.();
        const errorNames = {
          1: 'MEDIA_ERR_ABORTED',
          2: 'MEDIA_ERR_NETWORK',
          3: 'MEDIA_ERR_DECODE',
          4: 'MEDIA_ERR_SRC_NOT_SUPPORTED',
        };
        return {
          ...recordingSurface.diagnostic(video),
          media_source:'server',speculative_prefetch:false,
          logical_src:video?.dataset.recordingSrc||'',
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
          muted:Boolean(video?.muted),volume:video?.volume,
          media_timing_current:video?.dataset.recordingTimingSource===video?.dataset.recordingLoadedFile,
          audio_sample_entry:video?.dataset.recordingAudioCodec||'',audio_sample_rate:Number(video?.dataset.recordingAudioRate||0),
          audio_duration_seconds:Number(video?.dataset.recordingAudioDuration||0),audio_clock_regular:video?.dataset.recordingAudioRegular,
          wants_play:video?.dataset.recordingWantsPlay==='true',
          loading:video?.dataset.recordingLoading==='true',
          buffering:video?.dataset.recordingBuffering==='true',
          buffered_ranges:buffered,buffered_ahead_seconds:range?Math.max(0,range[1]-current):0,
          seeking:Boolean(video.seeking),codec:video.dataset.recordingCodec||'',fps:Number(video.dataset.recordingFps||0),
          width:video.videoWidth,height:video.videoHeight,
          quality:quality?{total_frames:quality.totalVideoFrames,dropped_frames:quality.droppedVideoFrames,corrupted_frames:quality.corruptedVideoFrames}:null,
          connection:navigator.connection?{type:navigator.connection.effectiveType,downlink:navigator.connection.downlink,rtt:navigator.connection.rtt}:null,
          seek_elapsed_ms:video.dataset.recordingSeekStarted?Math.round(performance.now()-Number(video.dataset.recordingSeekStarted)):null,
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
        if(value&&typeof value==='object')return EdgeLogView.valueSeverity(value);
        const events=EdgeLogView.parse(value);if(events.length)return events.reduce((level,row)=>severityRank(row.severity)>severityRank(level)?row.severity:level,'ok');
        return logSeverityFromText(value);
      }

      function logBlock(title, value, severity = '') {
        const resolvedSeverity = ['ok', 'warning', 'error','data'].includes(severity) ? severity : logSeverityFromValue(value);
        return `
          <div class="log-block log-${resolvedSeverity}">
            <h2><span>${escapeHtml(title)}</span><span class="log-severity">${escapeHtml(resolvedSeverity==='data'?'DANE':resolvedSeverity.toUpperCase())}</span></h2>
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

      function logRaw(title,value,severity='data'){return `<details class="log-raw"><summary>${escapeHtml(title)}</summary>${logBlock(title,value,severity)}</details>`;}
      function logCameraId(row){
        const data=row.data||{};if(data.index!=null&&config.cameras?.[Number(data.index)])return config.cameras[Number(data.index)].id;
        if(data.camera_id&&(config.cameras||[]).some(c=>c.id===data.camera_id))return data.camera_id;
        const normalize=value=>String(value||'').toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_|_$/g,'');
        const source=normalize(data.source||data.liveKey||data.key||data.camera_id||data.path||'');
        return (config.cameras||[]).find(c=>[c.id,c.name].some(value=>{const token=normalize(value);return token&&(source===token||source.startsWith(token+'_')||source.endsWith('_'+token)||source.includes('_'+token+'_'));}))?.id||'';
      }
      function logCamera(row){const id=logCameraId(row);return(config.cameras||[]).find(c=>c.id===id)?.name||row.data?.source||row.data?.key||'';}
      function logRows(){
        const sources=[['playback_log','playback'],['edge_debug','server'],['recording_thumbnail_log','thumbnail'],['recording_stream_log','stream'],['recording_cache_log','archive'],['camera_discovery_log','discovery']];
        const rows=sources.flatMap(([field,origin])=>EdgeLogView.parse(panelLogs?.[field]||'',{origin,legacyEvent:origin==='archive'?'recording_cache':origin==='stream'?'recording_stream':null}));
        return [...new Map(rows.map(row=>[JSON.stringify([row.event,row.time,row.data]),row])).values()];
      }
      function configureLogCameras(){
        const select=document.getElementById('log-camera-filter'),signature=JSON.stringify((config.cameras||[]).map(c=>[c.id,c.name]));
        if(select.dataset.signature===signature)return;const old=select.value;
        select.innerHTML='<option value="all">Wszystkie kamery</option>'+(config.cameras||[]).map(c=>`<option value="${escapeHtml(c.id)}">${escapeHtml(c.name||c.id)}</option>`).join('');
        if([...select.options].some(o=>o.value===old))select.value=old;select.dataset.signature=signature;
      }
      function renderLogEvents(value,group='all'){
        const rows=Array.isArray(value)?value:EdgeLogView.parse(value),level=document.getElementById('log-level-filter').value,search=document.getElementById('log-search').value.trim().toLowerCase();
        const camera=document.getElementById('log-camera-filter').value,origin=document.getElementById('log-origin-filter').value,period=document.getElementById('log-time-filter').value;
        const now=EdgeLogView.stamp(panelLogs?.generated_at)||Date.now(),uptime=Number(panelLogs?.hardware?.process?.uptime_seconds);
        const cutoff=period==='all'?0:now-(period==='boot'&&uptime>0?uptime*1000:86400000);
        const labels={server:'Serwer',playback:'Odtwarzanie',thumbnail:'Miniatury',stream:'Strumień nagrań',archive:'Archiwum kopii',discovery:'Wykrywanie kamery',device:'Urządzenie'};
        const selected=rows.filter(row=>(group==='all'||row.category===group)&&(level==='all'||level==='problems'&&row.severity!=='ok'||row.severity===level)&&(period==='all'||EdgeLogView.stamp(row.time)>=cutoff)&&(camera==='all'||logCameraId(row)===camera)&&(origin==='all'||(row.origin==='device'?'device':'server')===origin)&&(!search||`${EdgeLogView.label(row)} ${row.event} ${logCamera(row)} ${JSON.stringify(row.data)}`.toLowerCase().includes(search))).sort((a,b)=>EdgeLogView.stamp(b.time)-EdgeLogView.stamp(a.time)).slice(0,150);
        if(!selected.length)return '<p class="notice" data-log-empty>Brak zdarzeń dla wybranych filtrów. Wybierz „Wszystkie zdarzenia” lub szerszy okres, aby zobaczyć historię.</p>';
        return `<div class="log-grid" data-log-events>${selected.map(row=>`<details class="log-event log-${row.severity}" data-log-event="${escapeHtml(row.event)}" data-log-category="${row.category}" data-log-level="${row.severity}" data-log-origin="${escapeHtml(row.origin||'server')}"><summary><time>${escapeHtml(EdgeLogView.stamp(row.time)?new Date(EdgeLogView.stamp(row.time)).toLocaleString(EdgeI18n.resolve()):'—')}</time><b>${escapeHtml(EdgeLogView.label(row))}</b><span>${escapeHtml(logCamera(row))}</span><small>${escapeHtml(labels[row.origin]||'Serwer')}</small><span class="log-severity">${row.severity==='ok'?'INFO':row.severity.toUpperCase()}</span></summary><pre class="log-output">${escapeHtml(pretty({event:row.event,...row.data}))}</pre></details>`).join('')}</div>`;
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
        const group=document.getElementById('log-group-filter').value;
        configureLogCameras();const events=logRows();
        const sections=[['overview','Stan systemu',()=>renderDiagnostics(panelLogs.diagnostics || [])+logRaw('Runtime summary', {
            generated_at: panelLogs.generated_at,
            server_version: panelLogs.server_version,
            ui_build: panelLogs.ui_build,
            authoritative_config: panelLogs.authoritative_config,
            runtime_config: panelLogs.runtime_config,
            config_summary: panelLogs.config_summary
          })+logRaw('Zmiany w tej wersji',panelLogs.release_features||[])+renderLogEvents(events)],
          ['live','Podgląd na żywo · EdgeRTC',()=>renderLogEvents(events,'live')+logRaw('EdgeRTC source connections',panelLogs.runtime_config?.edge_rtc||{})],
          ['recordings','Nagrania · odtwarzanie, zapis i galeria',()=>renderLogEvents(events,'recordings')+logRaw('Playback performance',panelLogs.playback||{})+recordingLogs.map(item=>logRaw(`Dziennik FFmpeg · historia: ${item.path}`,item.tail||'empty')).join('')+logRaw('Miniatury',panelLogs.recording_thumbnail_log||'empty')+logRaw('Archiwum dziennych kopii · pełna historia',panelLogs.recording_cache_log||'empty')],
          ['network','Urządzenia i sieć',()=>renderLogEvents(events,'network')+logRaw('Runtime parameters',panelLogs.runtime_parameters||{})],
          ['hardware','Sprzęt i dysk',()=>logBlock('Hardware diagnostics',panelLogs.hardware||{},hardwareSeverity)+renderDiagnostics(panelLogs.diagnostics||[])],
          ['config','Kamery i konfiguracja',()=>renderLogEvents(events,'config')+logRaw('Camera media · observed streams',panelLogs.camera_media||{})+logRaw('Camera discovery · ONVIF / ISAPI',panelLogs.camera_discovery_log||'Brak odczytu profili.')+logRaw('Last save debug',panelLogs.last_save_debug||{})+logRaw('Last runtime sync',panelLogs.last_runtime_sync||{})+logRaw('Panel config',panelLogs.panel_config||{})+logRaw('Runtime edge.json',panelLogs.runtime_config_file||{})+logRaw('Last saved response',panelLogs.last_saved_config||{})],
          ['ai','Detekcja',()=>renderLogEvents(events,'ai')+logRaw('Local AI',panelLogs.detection||{},EdgeLogView.valueSeverity(panelLogs.detection))]];
        logsView.innerHTML=sections.filter(([id])=>group==='all'||group===id).map(([id,title,body])=>`<section data-log-group="${id}"><h2>${title}</h2>${body()}</section>`).join('');
      }
      function renderDeviceLogs(){
        if(!deviceReport)return;
        const selected=deviceReport.selected?.latest||{},label=selected.device?.label||'Urządzenie',endpoint=selected.browser_endpoint||'Adres jeszcze niezgłoszony';
        const group=document.getElementById('log-group-filter').value;
        document.getElementById('device-log-view').innerHTML=`<div class="notice" style="overflow-wrap:anywhere">${escapeHtml(label)} (${escapeHtml(endpoint)}) → Home Assistant → Edge → kamera. Bieżące pomiary i historia zdarzeń są pokazane oddzielnie.</div>`+
          renderLogEvents(EdgeLogView.parse(deviceReport.selected?.log||'',{origin:'device'}),group==='all'||group==='overview'?'all':group)+
          logRaw('Device / session / browser measurements',selected)+logRaw('Connection map · configured and observed',deviceReport.network_map||{})+logRaw('Surowy dziennik urządzenia · historia',deviceReport.selected?.log||'Brak raportu.');
      }
      for(const id of ['log-group-filter','log-level-filter','log-search','log-camera-filter','log-time-filter','log-origin-filter'])document.getElementById(id).addEventListener(id==='log-search'?'input':'change',()=>{renderLogs();renderDeviceLogs();});

      async function loadLogs() {
        logsView.innerHTML = '<p class="notice">Loading logs...</p>';
        const response = await panelFetch(panelPath('api/logs'), { cache: 'no-store' });
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
        await loadDeviceLogs();
      }
      async function loadDeviceLogs(){
        const select=document.getElementById('device-log-select'),wanted=select.value||deviceReporter?.device_id||'';
        try{
          const response=await panelFetch(panelPath(`api/client-diagnostics?${new URLSearchParams({device:wanted})}`),{cache:'no-store'});
          if(!response.ok)throw Error('Nie można odczytać logów urządzenia.');deviceReport=await response.json();
          select.innerHTML=(deviceReport.devices||[]).map(d=>`<option value="${escapeHtml(d.device_id)}">${escapeHtml(d.label)} · ${escapeHtml(d.device_id.slice(0,8))}</option>`).join('');
          if(wanted&&(deviceReport.devices||[]).some(d=>d.device_id===wanted))select.value=wanted;
          if(!deviceReport.selected&&select.value&&select.value!==wanted){await loadDeviceLogs();return;}
          renderDeviceLogs();
        }catch(error){document.getElementById('device-log-view').textContent=error.message;}
      }
      document.getElementById('device-label').addEventListener('change',event=>deviceReporter?.setLabel(event.target.value));
      document.getElementById('device-log-select').addEventListener('change',loadDeviceLogs);
      document.getElementById('device-log-download').addEventListener('click',()=>{
        if(!deviceReport)return;const blob=new Blob([JSON.stringify({generated_at:new Date().toISOString(),device:deviceReport,server:panelLogs},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=url;a.download=`edge-device-${document.getElementById('device-log-select').value||'overview'}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      });

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
        const sourcePath = `${encodeURIComponent(liveId)}_${encodeURIComponent(previewStream)}`;
        const plan = liveConnectionPlan(sourcePath);
        const liveUrl = plan.url;
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
            ? `<iframe class="live-frame" src="${escapeHtml(activePage==='home'&&!document.hidden?liveUrl:'about:blank')}" ${EDGE_NATIVE_RTC&&activePage==='home'&&!document.hidden?`srcdoc="${escapeHtml(inlineLiveDocument(liveUrl))}"`:''} ${activePage==='home'&&!document.hidden?'':'data-live-suspended="true"'} data-live-key="${escapeHtml(liveKey)}" data-live-url="${escapeHtml(liveUrl)}" data-live-path="${escapeHtml(sourcePath)}" title="${escapeHtml(text(camera.name, camera.id))} EdgeRTC live" loading="eager" allow="autoplay; fullscreen; encrypted-media" allowfullscreen onload="window.edgeFrameLoaded && window.edgeFrameLoaded(this)" onerror="window.edgeFrameError && window.edgeFrameError(this)"></iframe>`
            : liveBlockedPreview(plan))
          : `<span>${online ? 'Click to start live' : escapeHtml(text(camera.detail, 'Waiting for camera'))}</span>`;
        const fullscreenButton = live[liveKey] && plan.canEmbed
          ? `<button class="fullscreen-button" type="button" data-fullscreen-live aria-label="Fullscreen" title="Fullscreen">${fullscreenIcon()}</button>`
          : '';
        return `
          <article class="camera video-tile">
            <div class="preview" data-live-key="${escapeHtml(liveKey)}" data-live-index="${escapeHtml(camera.index ?? 0)}" data-live-path="${escapeHtml(sourcePath)}" title="Click to toggle live preview">${preview}${statusBadge}${fullscreenButton}</div>
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
          if(frame.dataset.liveSuspended==='true')return;
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
              hint: EDGE_NATIVE_RTC ? 'Check the EdgeRTC core/source status, H264/H265 profile, browser WebCodecs and the Home Assistant HTTPS/WebSocket connection.' : 'If this happens only on LTE or another Wi-Fi, check WebRTC public URL, ICE candidates, TCP 8889, and ICE TCP/UDP 8189.',
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

      function updateLivePlaybackVisibility() {
        const visible=activePage==='home'&&!document.hidden&&liveSessionReady&&!ingressSessionExpired;
        grid.querySelectorAll('iframe.live-frame').forEach(frame=>{
          if(!visible&&frame.dataset.liveSuspended!=='true'){
            // Release the phone's Live decoder synchronously before NVR can
            // acquire one; iframe navigation/unload alone is asynchronous.
            if(EDGE_NATIVE_RTC)try{frame.contentWindow.edgeStopLive?.();}catch(_){}
            frame.removeAttribute('srcdoc');
            frame.dataset.liveSuspended='true';delete frame.dataset.liveReady;frame.src='about:blank';
            clearTimeout(liveFrameTimers.get(frame.dataset.liveKey));liveFrameTimers.delete(frame.dataset.liveKey);
          }else if(visible&&frame.dataset.liveSuspended==='true'){
            if(EDGE_NATIVE_RTC)frame.srcdoc=inlineLiveDocument(frame.dataset.liveUrl);
            delete frame.dataset.liveSuspended;frame.src=frame.dataset.liveUrl;
          }
        });
      }
      window.addEventListener('message',event=>{
        if(event.origin!==location.origin||!['edge_live_ready','edge_ingress_expired'].includes(event.data?.type))return;
        const frame=[...grid.querySelectorAll('iframe.live-frame')].find(f=>f.contentWindow===event.source&&f.dataset.livePath===event.data.path);
        if(!frame||frame.dataset.liveSuspended==='true')return;
        if(event.data.type==='edge_ingress_expired'){if(usesIngress)expireIngressSession();return;}
        frame.dataset.liveReady='true';debugEvent('ui_live_picture_ready',{liveKey:frame.dataset.liveKey,path:event.data.path});scheduleGalleryPrefetch();
      });
      let sessionResume=null;
      async function resumeVisiblePanel(){
        if(ingressSessionExpired||document.hidden||sessionResume)return;
        sessionResume=checkPanelVersion();try{await sessionResume;}finally{sessionResume=null;}
        if(ingressSessionExpired||document.hidden)return;
        liveSessionReady=true;updateLivePlaybackVisibility();deviceReporter?.setPaused?.(false);
      }
      document.addEventListener('visibilitychange',()=>{
        if(document.hidden){suspendArchivePlayers();liveSessionReady=false;cancelGalleryPrefetch();updateLivePlaybackVisibility();nvrGrid.querySelectorAll('video[data-recording-player]').forEach(pauseRecording);}
        else void resumeVisiblePanel();
      });
      window.addEventListener('pagehide',()=>{suspendArchivePlayers();liveSessionReady=false;updateLivePlaybackVisibility();});
      window.addEventListener('pageshow',event=>{if(event.persisted)void resumeVisiblePanel();});

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
        const desiredRecording = typeof status.desired_recording==='boolean'?status.desired_recording:Boolean(camera.enabled&&camera.record);
        const recordingState = status.recording_status || (isRecording ? 'recording' : desiredRecording ? 'scheduled_stopped' : 'disabled');
        const badgeText = typeof status.recording!=='boolean'?'CHECKING':isRecording
          ? 'RECORDING'
          : recordingState === 'blocked'
            ? 'BLOCKED'
          : recordingState === 'waiting_source'
            ? 'WAITING'
          : desiredRecording
              ? 'SCHEDULED'
              : 'OFF';
        const canRecord = status.can_record !== false;
        const recordError = recordingState === 'waiting_source' ? `Oczekiwanie na źródło EdgeRTC (${status.source_wait?.phase||'metadata'}). Ponowna próba automatycznie; ONVIF nie jest wymagany.` : status.record_error || status.last_error || '';
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
        const preloadMode = Number(index)===0?'auto':'metadata';
        const deferredPlayer=activePage!=='nvr'||phonePlayback()&&Number(index)!==0;
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
          ? archiveAvailable()?archivePlayerMarkup(index,timeline.playbackTotal,targetForPoster?.playbackSeek||0,posterUrl):`<div class="recording-empty">EdgeRTC DVR wymaga HTTPS i przeglądarki z WebCodecs. <a href="${escapeHtml(panelPath(selectedFile?.url||'')+'?download=1')}" download>Pobierz nagranie MP4</a></div>`
          : `<div class="recording-empty">${Array.isArray(status.files)?'Brak zamkniętych nagrań dla tego dnia':'Odczyt listy nagrań…'}</div>`;
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
          : `<p class="notice">${!Array.isArray(status.files)?'Stan rejestratora i lista zapisanych filmów są odczytywane niezależnie.':desiredRecording?'Nagrywanie jest zaplanowane. Pierwszy film pojawi się po zamknięciu fragmentu, na klatce kluczowej kamery.':'Nagrywanie jest wyłączone. Dostępne zapisane dni można odtwarzać niezależnie.'}</p>`;
        const diagnostics = recordError
          ? `<p class="section-status ${recordingState==='waiting_source'?'':'state-offline'}">${escapeHtml(recordErrorText(recordError))}</p>`
          : `<p class="section-status">${escapeHtml(text(status.record_rtsp, 'Record RTSP not ready'))}</p>`;
        return `
          <article class="camera nvr-card" data-nvr-index="${index}" data-nvr-key="${escapeHtml(status.key || '')}" data-nvr-day="${escapeHtml(currentDay)}">
            <div class="body">
              <div class="nvr-head">
                <div class="name">${escapeHtml(text(camera.name, `Camera ${index + 1}`))}</div>
                <div class="rec-badge ${isRecording ? '' : 'off'}">${badgeText}</div>
              </div>
              ${daySelector}
              <p class="field-help">EdgeRTC DVR · <a data-recording-download="${index}" href="${escapeHtml(panelPath(selectedFile?.url||'')+'?download=1')}" download>Pobierz nagranie MP4</a></p>
              ${player}<section class="nvr-alert-detail" data-alert-detail="${index}" hidden></section>
              ${currentDay ? `<section class="recording-events" data-recording-events="${index}"><div class="event-history-row"><b>Zdarzenia dnia / Day events</b><button type="button" data-recording-events-refresh="${index}">Odśwież</button></div><div data-recording-event-content><p class="field-help">Ładowanie zdarzeń…</p></div><p class="nvr-playback-message" data-recording-event-message role="status"></p></section>` : ''}
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
        document.getElementById('ha-card-resource-status').textContent = `Edge Live ${haDashboard.card_version||EDGE_PANEL_VERSION} · EdgeRTC Live / DVR · HTTPS and WebCodecs; shared camera source and G711 microphone.`;
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
          const response = await panelFetch(panelPath('api/home-assistant/dashboard'), { cache: 'no-store' });
          if (!response.ok) throw new Error('Could not read Home Assistant camera entities.');
          haDashboard = await response.json();
          const select = document.getElementById('ha-camera-select');
          const selected = haRequestedCamera || select.value;
          select.innerHTML = haDashboard.cameras.map((camera) => `<option translate="no" value="${escapeHtml(camera.id)}">${escapeHtml(camera.name)}${camera.enabled ? '' : ' (disabled)'}</option>`).join('');
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
                <p>${device.model ? `${escapeHtml(device.model)} ${escapeHtml(device.firmware || '')}` : 'ONVIF / ISAPI zgodnie z wybraną metodą; RTSP używa odczytu producenta. Odczyt nie zmienia jakości ani FPS kamery.'}</p>
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
            <p class="field-help">GOP nagrywanego profilu: 1–2 s przyspiesza przewijanie. Kamera ustala klatki kluczowe; istniejące nagrania zachowują swój GOP. Krótszy GOP może zwiększyć bitrate lub zużyć więcej dostępnego bitrate na klatki kluczowe.</p><div class="actions"><button type="button" data-camera-gop="1" data-camera-index="${index}">Ustaw GOP 1 s</button><button type="button" data-camera-gop="2" data-camera-index="${index}">Ustaw GOP 2 s</button></div><p data-camera-gop-status role="status"></p>
            ${Object.keys(streams).length ? `<div class="stream-grid">${Object.entries(streams).map(([name, stream]) => streamEditor(index, name, stream)).join('')}</div>` : ''}
            ${sectionSummary ? `<p class="section-status">${escapeHtml(sectionSummary)}</p>` : ''}
          </div>
        `;
      }

      let expandedCameraIds = new Set();
      form.addEventListener('click',async event=>{const button=event.target.closest('[data-camera-gop]');if(!button)return;const section=button.closest('[data-camera-form]'),message=section.querySelector('[data-camera-gop-status]');button.disabled=true;message.textContent='Zapisywanie wyłącznie GOP w zapisanym profilu kamery…';try{const response=await panelFetch(panelPath('api/camera-gop'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:Number(button.dataset.cameraIndex),seconds:Number(button.dataset.cameraGop)}),signal:AbortSignal.timeout(25000)});const data=await response.json();if(!response.ok)throw Error(data.error);message.textContent=`Potwierdzono GOP ${data.seconds} s · ${data.frames} klatek przy ${data.fps} FPS. Zmiana dotyczy nowych klatek.`;debugEvent('ui_camera_gop_saved',{index:button.dataset.cameraIndex,...data});}catch(error){message.textContent=error.message;debugEvent('ui_camera_gop_error',{index:button.dataset.cameraIndex,message:error.message});}finally{button.disabled=false;}});
      let cameraExpansionInitialized = false;
      let cameraToExpand = '';
      const zonePreviewUrls = new Map(), zonePreviewRequests = new Map();
      const zoneNames = ['left','top','right','bottom'];
      const zoneDefaults = [0,0,1,1];
      function zoneEditor(camera,index) {
        const zone = zoneNames.map((name,i) => Number(camera[`detection_zone_${name}`] ?? zoneDefaults[i]));
        return `<label class="check-row"><input type="checkbox" name="camera-${index}-zone-enabled" ${camera.detection_zone_enabled ? 'checked' : ''}> Ogranicz lokalne AI do strefy</label><details class="zone-editor" data-zone-editor="${index}"><summary>Ustaw strefę na obrazie / Local AI zone</summary><p class="field-help">Zaznacz jeden prostokąt. Obiekt należy do strefy, gdy dolny środek jego ramki znajduje się w obszarze. Strefa dotyczy lokalnego AI; reguły ONVIF ustawiasz w kamerze. Nagrania obejmują cały kadr.</p><div class="event-history-row"><button type="button" data-zone-action="preview">Odśwież klatkę</button><button type="button" data-zone-action="draw" disabled aria-pressed="false">Rysuj strefę</button><button type="button" data-zone-action="full">Cały kadr</button></div><div class="zone-stage" data-zone-stage><img data-zone-image hidden draggable="false" alt="Klatka ze strumienia nagrywania do ustawienia strefy"><svg viewBox="0 0 1000 1000" preserveAspectRatio="none" role="img" aria-label="Obszar lokalnego AI"><rect data-zone-rect x="${zone[0]*1000}" y="${zone[1]*1000}" width="${Math.max(0,zone[2]-zone[0])*1000}" height="${Math.max(0,zone[3]-zone[1])*1000}"/></svg><span data-zone-placeholder>Odśwież klatkę z zapisanej kamery. Możesz też wpisać granice poniżej.</span></div><div class="form-grid">${zoneNames.map((name,i) => `<label>${['Lewa','Górna','Prawa','Dolna'][i]} granica (%)<input type="number" name="camera-${index}-zone-${name}" min="0" max="100" step="0.1" value="${Math.round(zone[i]*100000)/1000}" data-zone-coordinate></label>`).join('')}</div><p class="field-help" data-zone-message role="status">Domyślnie cały kadr. Po zmianach zapisz kamery.</p></details>`;
      }
      function zoneFromForm(section,index) {
        const enabled = section.querySelector(`[name="camera-${index}-zone-enabled"]`).checked;
        const bounds = zoneNames.map(name => Number(section.querySelector(`[name="camera-${index}-zone-${name}"]`).value) / 100);
        const valid = bounds.every(value => Number.isFinite(value) && value >= 0 && value <= 1) && bounds[2]-bounds[0] >= .005-1e-9 && bounds[3]-bounds[1] >= .005-1e-9;
        return {enabled,bounds,valid};
      }
      function zoneFormValues(section,index,strict=false) {
        const zone = zoneFromForm(section,index);
        if (strict && zone.enabled && !zone.valid) throw new Error('Strefa AI: granice muszą mieścić się w kadrze, a szerokość i wysokość wynosić co najmniej 0,5%.');
        const result = {detection_zone_enabled:zone.enabled};
        zoneNames.forEach((name,i) => result[`detection_zone_${name}`] = Math.round((Number.isFinite(zone.bounds[i]) ? zone.bounds[i] : zoneDefaults[i])*1000000)/1000000);
        return result;
      }
      function zoneShape(editor,bounds) {
        const rect=editor.querySelector('[data-zone-rect]');
        for(const [key,value] of Object.entries({x:bounds[0]*1000,y:bounds[1]*1000,width:Math.max(0,bounds[2]-bounds[0])*1000,height:Math.max(0,bounds[3]-bounds[1])*1000}))rect.setAttribute(key,Number.isFinite(value)?value:0);
      }
      function updateZoneVisual(editor) {
        const index=Number(editor.dataset.zoneEditor), section=editor.closest('[data-camera-form]');
        const zone=zoneFromForm(section,index);zoneShape(editor,zone.bounds);
        editor.querySelector('[data-zone-message]').textContent = zone.enabled && !zone.valid ? 'Nieprawidłowy obszar. Popraw granice przed zapisem.' : 'Zapisz kamery, aby zastosować strefę. Zdarzenia ONVIF mają własne reguły w kamerze.';
      }
      function clearZonePreviews() {
        zoneDrag=null;
        zonePreviewRequests.forEach(controller=>controller.abort());zonePreviewRequests.clear();
        zonePreviewUrls.forEach(url=>URL.revokeObjectURL(url));zonePreviewUrls.clear();
      }
      async function loadZonePreview(editor) {
        const index=Number(editor.dataset.zoneEditor), id=config.cameras[index]?.id;
        const button=editor.querySelector('[data-zone-action="preview"]'), message=editor.querySelector('[data-zone-message]');
        zonePreviewRequests.get(id)?.abort();
        const controller=new AbortController();zonePreviewRequests.set(id,controller);
        const timer=setTimeout(()=>controller.abort(),13000);button.disabled=true;message.textContent='Pobieranie jednej klatki ze wspólnego strumienia…';
        try {
          const response=await panelFetch(panelPath(`api/detection/preview?${new URLSearchParams({camera:id||''})}`),{cache:'no-store',signal:controller.signal});
          if(!response.ok||!response.headers.get('Content-Type')?.startsWith('image/jpeg'))throw new Error(response.status===429?'Analiza jest zajęta. Spróbuj za chwilę.':'Brak podglądu. Najpierw zapisz aktywną kamerę i sprawdź jej strumień nagrywania.');
          const blob=await response.blob();if(!editor.isConnected||controller.signal.aborted)return;
          const image=editor.querySelector('[data-zone-image]'),stage=editor.querySelector('[data-zone-stage]');
          const url=URL.createObjectURL(blob),old=zonePreviewUrls.get(id);zonePreviewUrls.set(id,url);
          image.onload=()=>{
            if(!editor.isConnected)return;
            stage.style.aspectRatio=`${image.naturalWidth} / ${image.naturalHeight}`;
            image.hidden=false;editor.querySelector('[data-zone-placeholder]').hidden=true;
            editor.querySelector('[data-zone-action="draw"]').disabled=false;
            message.textContent='Klatka gotowa. Wybierz Rysuj strefę i przeciągnij po obrazie. Po zmianie zapisz kamery.';
          };
          image.onerror=()=>{message.textContent='Nie udało się wyświetlić klatki. Spróbuj ponownie.';};
          image.src=url;if(old)URL.revokeObjectURL(old);
        }catch(error){if(editor.isConnected)message.textContent=error.name==='AbortError'?'Pobieranie przerwane lub przekroczyło czas. Spróbuj ponownie.':error.message;}
        finally{clearTimeout(timer);if(zonePreviewRequests.get(id)===controller)zonePreviewRequests.delete(id);button.disabled=false;}
      }
      form.addEventListener('click',event=>{
        const button=event.target.closest('[data-zone-action]');if(!button)return;
        event.preventDefault();const editor=button.closest('[data-zone-editor]'),index=Number(editor.dataset.zoneEditor),stage=editor.querySelector('[data-zone-stage]');
        if(button.dataset.zoneAction==='preview'){loadZonePreview(editor);return;}
        if(button.dataset.zoneAction==='draw'){
          const drawing=stage.dataset.drawing!=='true';stage.dataset.drawing=String(drawing);button.setAttribute('aria-pressed',String(drawing));
          editor.querySelector('[data-zone-message]').textContent=drawing?'Przeciągnij prostokąt na obrazie.':'Rysowanie wyłączone.';return;
        }
        zoneNames.forEach((name,i)=>editor.querySelector(`[name="camera-${index}-zone-${name}"]`).value=zoneDefaults[i]*100);
        editor.querySelector('[data-zone-coordinate]').dispatchEvent(new Event('input',{bubbles:true}));
      });
      form.addEventListener('input',event=>{const editor=event.target.closest('[data-zone-editor]');if(editor&&event.target.matches('[data-zone-coordinate]'))updateZoneVisual(editor);});
      let zoneDrag=null;
      const zonePointer=(event,stage)=>{const box=stage.querySelector('svg').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-box.left)/box.width)),Math.max(0,Math.min(1,(event.clientY-box.top)/box.height))];};
      form.addEventListener('pointerdown',event=>{
        const stage=event.target.closest('[data-zone-stage]');
        if(!stage||stage.dataset.drawing!=='true'||!event.isPrimary||event.button!==0)return;
        event.preventDefault();stage.setPointerCapture(event.pointerId);zoneDrag={stage,pointer:event.pointerId,start:zonePointer(event,stage)};
      });
      form.addEventListener('pointermove',event=>{
        if(!zoneDrag||zoneDrag.pointer!==event.pointerId)return;
        const end=zonePointer(event,zoneDrag.stage),start=zoneDrag.start;
        zoneShape(zoneDrag.stage.closest('[data-zone-editor]'),[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])]);
      });
      function finishZoneDrag(event,cancelled=false) {
        if(!zoneDrag||zoneDrag.pointer!==event.pointerId)return;
        const {stage,start}=zoneDrag;zoneDrag=null;
        const editor=stage.closest('[data-zone-editor]'),index=Number(editor.dataset.zoneEditor),end=zonePointer(event,stage);
        const bounds=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])];
        stage.dataset.drawing='false';editor.querySelector('[data-zone-action="draw"]').setAttribute('aria-pressed','false');
        if(!cancelled&&bounds[2]-bounds[0]>=.005&&bounds[3]-bounds[1]>=.005){
          zoneNames.forEach((name,i)=>editor.querySelector(`[name="camera-${index}-zone-${name}"]`).value=Math.round(bounds[i]*1000)/10);
          editor.closest('[data-camera-form]').querySelector(`[name="camera-${index}-zone-enabled"]`).checked=true;
          editor.querySelector('[data-zone-coordinate]').dispatchEvent(new Event('input',{bubbles:true}));
        }else{updateZoneVisual(editor);editor.querySelector('[data-zone-message]').textContent='Rysowanie anulowane lub obszar zbyt mały. Poprzednia strefa została zachowana.';}
      }
      form.addEventListener('pointerup',event=>finishZoneDrag(event));
      form.addEventListener('pointercancel',event=>finishZoneDrag(event,true));

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
              </select></label><label>Host/IP<input name="${prefix}-host" value="${escapeHtml(camera.host || '')}"></label><label>Username<input name="${prefix}-username" value="${escapeHtml(camera.username || 'admin')}"></label><label>Password<input name="${prefix}-password" type="password" value="${escapeHtml(camera.password || '')}"></label><label>Metoda konfiguracji<select name="${prefix}-access-protocol">
                ${option('rtsp', accessProtocol, 'RTSP · profile producenta')}
                ${option('isapi', accessProtocol, 'ISAPI')}
                ${option('onvif', accessProtocol, 'ONVIF')}
                ${option('unicast', accessProtocol, 'Unicast')}
                ${option('multicast', accessProtocol, 'Multicast')}
              </select></label></div><p class="field-help">IP, login i hasło automatycznie budują adresy. ONVIF / ISAPI służą do odczytu profili i ustawień; obraz korzysta ze wspólnego źródła RTSP w Edge. RTSP wybiera automatyczny odczyt producenta. Połączenie z kamerą następuje po kliknięciu odczytu lub zapisaniu ustawień.</p><output data-camera-address-preview="${index}" style="display:block;overflow-wrap:anywhere;font-size:12px"></output></fieldset>
              ${cameraAutoconfig(index)}
              <fieldset class="field-section"><legend>Detekcja / Detection</legend><div class="form-grid"><label class="check-row"><input name="${prefix}-events-enabled" type="checkbox" ${camera.events_enabled ? 'checked' : ''}> Zdarzenia ONVIF: ruch, osoba, pojazd</label><label class="check-row"><input name="${prefix}-local-detection-enabled" type="checkbox" ${camera.local_detection_enabled ? 'checked' : ''}> Lokalne AI na tej kamerze</label></div><p class="field-help">Włącz także odpowiedni moduł w Settings → Detection. Typy zdarzeń i mikrofon zależą od modelu kamery.</p>${zoneEditor(camera,index)}</fieldset><fieldset class="field-section"><legend>Live & recording</legend><p class="field-help">Sub stream is lighter for live tiles. Main stream gives the best recording detail.</p><div class="form-grid"><label class="check-row"><input name="${prefix}-enabled" type="checkbox" ${camera.enabled ? 'checked' : ''}> Camera enabled</label><label class="check-row"><input name="${prefix}-record" type="checkbox" ${camera.record !== false ? 'checked' : ''}> Record continuously</label><label>Live stream<select name="${prefix}-live-stream">
                <option value="sub" ${camera.live_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.live_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label><label>Tile stream<select name="${prefix}-tile-stream">
                <option value="sub" ${camera.tile_stream !== 'main' ? 'selected' : ''}>sub</option>
                <option value="main" ${camera.tile_stream === 'main' ? 'selected' : ''}>main</option>
              </select></label><label>Recording stream<select name="${prefix}-record-stream">
                <option value="main" ${camera.record_stream !== 'sub' ? 'selected' : ''}>main</option>
                <option value="sub" ${camera.record_stream === 'sub' ? 'selected' : ''}>sub</option>
              </select></label></div></fieldset>
              <details class="settings-group camera-advanced"><summary>Advanced connection <small>Custom RTSP, ONVIF, channels and transport</small></summary><div class="form-grid"><label>Camera / NVR channel number<input name="${prefix}-camera-number" value="${escapeHtml(cameraNumber)}"></label><label>RTSP transport<select name="${prefix}-rtsp-transport">
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
        clearZonePreviews();
        form.innerHTML = cameras.map(cameraForm).join('');
        form.querySelectorAll('[data-camera-form]').forEach((section,index)=>updateCameraAddressPreview(section,index));
        if (cameraToExpand) {
          const card = Array.from(form.querySelectorAll('[data-camera-form]')).find((node) => node.dataset.cameraId === cameraToExpand);
          card?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          cameraToExpand = '';
        }
        renderNvrGrid({ reason: 'render_config' });
        renderPresetSlots(cameras);
        renderEdgeSettings();
        renderRecordingStorage();
        updateUnsavedState();
        updateCameraOverview();
      }

      function patchNvrCard(card, camera, index, options = {}) {
        const status = recordingStatus[index] || {};
        const video = card.querySelector('video[data-recording-player]');
        const badge = card.querySelector('.rec-badge');
        if (badge) {
          badge.textContent = typeof status.recording!=='boolean'?'CHECKING':status.recording ? 'RECORDING' : status.recording_status === 'blocked' ? 'BLOCKED' : status.recording_status === 'waiting_source' ? 'WAITING' : status.desired_recording ? 'SCHEDULED' : 'OFF';
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
        archivePlayers.get(String(index))?.refresh();
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
          if (same && Boolean(existing.querySelector('video,[data-dvr-host]')) === hasFiles && Boolean(existing.querySelector('[data-dvr-host]'))===(hasFiles&&archiveAvailable()) && options.reason !== 'render_config') {
            patchNvrCard(existing, camera, index, options);
            return;
          }
          clearRecordingContinuation(index);
          discardRecordingScrub(index);
          const template = document.createElement('template');
          template.innerHTML = nvrCard(camera, index);
          disposeArchivePlayer(index);
          if (existing) { disposeRecordingPlayer(existing.querySelector('video')); existing.replaceWith(template.content); }
          else nvrGrid.append(template.content);
        });
        nvrGrid.querySelectorAll('[data-nvr-index]').forEach((card) => {
          if (!keep.has(card.dataset.nvrIndex)) { disposeArchivePlayer(card.dataset.nvrIndex);disposeRecordingPlayer(card.querySelector('video')); card.remove(); }
        });
        lastNvrRenderSignature = recordingStatusSignature();
        // Initialize only new players; existing nodes keep their source, position and pause state.
        applyRecordingSeek();
        mountArchivePlayers();
        scheduleGalleryPrefetch();
        scheduleRecordingThumbnailHydration();
        refreshRecordingEvents();
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
        const detection = config.detection || {};
        edgeForm.innerHTML = `
          <section class="field-section" id="detection-settings"><h2>Wykrywanie i alerty</h2><p class="field-help">Włącz wykrywanie, wybierz kamery i zapisz ustawienia. Analiza czyta wyłącznie wspólny strumień Edge; klipy powstają z zapisanych plików. Jakość i FPS nagrań pozostają bez zmian.</p>
            <div class="form-grid"><label class="check-row"><input type="checkbox" name="detection-local" ${detection.local_enabled?'checked':''}> Wykrywanie lokalne w Edge</label>
              <label class="check-row"><input type="checkbox" name="detection-person" ${detection.person_enabled!==false?'checked':''}> Osoba</label><label class="check-row"><input type="checkbox" name="detection-animal" ${detection.animal_enabled!==false?'checked':''}> Zwierzę (m.in. kot, pies, ptak)</label><label class="check-row"><input type="checkbox" name="detection-vehicle" ${detection.vehicle_enabled!==false?'checked':''}> Pojazd</label>
              ${(config.cameras||[]).map((camera,index)=>`<label class="check-row"><input type="checkbox" data-easy-detection-camera="${index}" ${camera.local_detection_enabled?'checked':''} ${camera.enabled?'':'disabled'}> <span translate="no">${escapeHtml(camera.name||camera.id)}</span>${camera.enabled?'':' · kamera wyłączona'}</label>`).join('')}
            </div><p class="field-help">Punkt na pasku filmu → zdjęcie osoby lub zwierzęcia → właściwy moment nagrania. Fragment alertu obejmuje najbliższe zamknięte części nagrania przed i po wykryciu. Nagrywanie wybranej kamery musi być włączone.</p>
            <div class="form-grid"><label>Limit klipów alertów (MB)<input type="number" min="32" max="4096" name="detection-alert-mb" value="${detection.alert_media_mb||256}"></label><label>Sekundy przed wykryciem<input type="number" min="0" max="10" name="detection-pre" value="${detection.pre_seconds??5}"></label><label>Sekundy po wykryciu<input type="number" min="5" max="60" name="detection-post" value="${detection.post_seconds||15}"></label></div><p class="field-help">Zdjęcia alertów mają limit 32 MB. Klipy mają osobny limit powyżej i retencję taką jak nagrania; najstarsze alerty wygasają po zapełnieniu limitu. Oryginalny zapis pozostaje niezależny. Klip pojawi się po zamknięciu części nagrania, zdjęcie lokalnego wykrycia od razu.</p>
            <details class="settings-group"><summary>Powiadomienia na telefon</summary><div class="form-grid"><label class="check-row"><input type="checkbox" name="detection-notifications" ${detection.notifications_enabled?'checked':''}> Powiadamiaj o osobie lub zwierzęciu</label><label>Telefon w Home Assistant<input name="detection-notify-service" list="edge-notify-services" placeholder="notify.mobile_app_twoj_telefon" value="${escapeHtml(detection.notify_service||'')}"><datalist id="edge-notify-services"></datalist></label><label>Przerwa między alertami (s)<input type="number" name="detection-notify-cooldown" min="5" max="3600" value="${detection.notify_cooldown||30}"></label></div><p class="field-help" data-phone-services>Wybierz usługę telefonu z aplikacją Home Assistant Companion. Zapisz ustawienia; zaktualizowana integracja Edge wysyła nowe alerty ze zdjęciem, bez dodatkowej automatyzacji YAML.</p></details>
            <details class="settings-group"><summary>Zaawansowana detekcja</summary><div class="form-grid"><label class="check-row"><input type="checkbox" name="detection-camera-events" ${detection.camera_events_enabled?'checked':''}> Zdarzenia ONVIF z kamery</label><label>Dekodowanie AI<select name="detection-decoder"><option value="auto" ${detection.decoder==='auto'||!detection.decoder?'selected':''}>Auto · GPU / ochrona CPU</option><option value="cpu" ${detection.decoder==='cpu'?'selected':''}>CPU · wszystkie klatki</option><option value="keyframes" ${detection.decoder==='keyframes'?'selected':''}>CPU · klatki kluczowe</option></select></label><label>Łączny limit analizy klatek/s<input type="number" name="detection-fps" min="0.2" max="3" step="0.2" value="${detection.max_fps||1}"></label><label>Pewność<input type="number" name="detection-confidence" min="0.1" max="0.95" step="0.05" value="${detection.confidence||.45}"></label><label>Pewność zwierzęcia<input type="number" name="detection-animal-confidence" min="0.1" max="0.95" step="0.05" value="${detection.animal_confidence||.35}"></label><label>Podtrzymanie wykrycia (s)<input type="number" name="detection-hold" min="1" max="120" value="${detection.hold_seconds||10}"></label></div></details><p class="field-help">HP t530: jedna kamera i 1 analizowana klatka/s łącznie. Rozpoznawanie nie identyfikuje tożsamości osoby; możliwości zależą od wielkości obiektu i oświetlenia.</p></section>
          <section class="field-section settings-main" id="recording-settings"><div class="section-heading"><span class="step-number">01</span><div><h2>Recording</h2><p>How long to keep footage and how it plays in the browser.</p></div></div><div class="form-grid"><label>Keep recordings (days)<input name="nvr-retention-days" type="number" min="1" max="365" value="${escapeHtml(text(nvrConfig.retention_days, storage.retention_days || 14))}"></label><label>Zapis obrazu<select name="nvr-browser-playback">

                <option value="copy" ${nvrConfig.browser_playback === 'copy' ? 'selected' : ''}>Oryginalna jakość i kodek (zalecane)</option>

              </select></label></div><p class="field-help">Testy H.264/H.265 i dźwięku są w menu Kodeki odtwarzacza. Zapis zachowuje kodek, rozdzielczość i FPS kamery. Nagranie jest już skompresowane przez kamerę. Zmniejszanie bitrate lub ponowne kodowanie może pogorszyć szczegóły. Obsługa odtwarzania H.265 zależy od przeglądarki.</p><div class="notice" data-recording-disk-usage role="status">Statystyki dysku są odczytywane osobno od nagrań.</div><button type="button" data-cleanup-recording-cache>Posprzątaj cache i miniatury</button><output data-storage-cleanup-result role="status"></output><input type="hidden" name="storage-retention-days" value="${escapeHtml(text(nvrConfig.retention_days, storage.retention_days || 14))}"><details class="settings-group"><summary>Advanced recording <small>Limity miejsca i długość fragmentów</small></summary><div class="form-grid"><label>Segment seconds<input name="nvr-segment-seconds" type="number" min="2" max="300" value="${escapeHtml(text(nvrConfig.segment_seconds, 10))}"></label><label>Limit miniatur (MB)<input name="nvr-thumbnail-cache-mb" type="number" min="1" max="256" value="${nvrConfig.thumbnail_cache_mb ?? 32}"></label><label>Limit kopii do odtwarzania (MB, 0 = wyłączone)<input name="nvr-playback-cache-mb" type="number" min="0" max="2048" value="${nvrConfig.playback_cache_mb ?? 0}"></label><label>Playback cache segments<input name="nvr-playback-cache-segments" type="number" min="12" max="50000" value="${escapeHtml(text(nvrConfig.playback_cache_segments, 10000))}"></label></div><p class="field-help">Domyślnie cały dzień odtwarza się z oryginalnych fragmentów, bez pełnej kopii filmu. Miniatury mają wspólny limit miejsca, maksymalnie 2048 obrazów i wygasają po 48 godzinach bez użycia. Usunięte miniatury można wygenerować ponownie z nagrania.</p></details></section>
          <details class="settings-group" id="network-settings"><summary>Network & remote access <small>Home Assistant HTTPS / WSS</small></summary><p class="notice" data-native-network-info>EdgeRTC Live i DVR korzystają z uwierzytelnionego połączenia Home Assistanta, także przez Nabu Casa. Otwórz panel przez HTTPS. / Live and DVR share the authenticated Home Assistant connection.</p></details>
          <details class="settings-group" id="storage-settings"><summary>Storage locations <small>Keep the defaults unless you use a custom disk layout</small></summary><div class="form-grid"><label>Recordings directory<input name="storage-recordings-dir" value="${escapeHtml(text(storage.recordings_dir, '/media/edge-of-infinity/recordings'))}"></label><label>Database path<input name="storage-database-path" value="${escapeHtml(text(storage.database_path, '/homeassistant/edge/edge.db'))}"></label></div></details>
          <details class="settings-group" id="advanced-settings"><summary>Advanced system settings <small>Server</small></summary><p class="field-help">Źródła kamer działają stale na serwerze. Podgląd zachowuje oryginalną jakość i FPS. / Camera sources stay connected; original video quality and FPS.</p><div class="form-grid"><label>API listen<input name="server-listen" value="${escapeHtml(text(server.listen, '0.0.0.0:8088'))}" readonly></label><label>Public panel URL<input name="server-public-url" value="${escapeHtml(server.public_url || '')}" placeholder="Optional external URL"></label></div><p class="field-help">The API port is configured in Home Assistant.</p></details>
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
        const response = await panelFetch(panelPath('api/presets'), { cache: 'no-store' });
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
          const endpoint = cameraField(section,index,'onvif');
          endpoint.value = refreshCameraHttpEndpoint(endpoint.value,cameraField(section,index,'host').value.trim(),vendor === 'reolink' ? 8000 : 80);
          updateCameraAddressPreview(section,index);
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
        updateCameraAddressPreview(section,index);
      }
      function refreshCameraHttpEndpoint(value,host,port) {
        if (!host) return value || '';
        try {
          const target = new URL(`http://${host}`); if (!target.port) target.port=String(port); const url = new URL(value || `${target.origin}/onvif/device_service`);
          if (!['http:','https:'].includes(url.protocol)) return `http://${host}:${port}/onvif/device_service`;
          url.hostname = target.hostname; const explicit = new URL(`http://${host}`).port; if (explicit) url.port = explicit;
          url.username = ''; url.password = ''; url.hash = ''; return url.href;
        } catch (_) { return value || ''; }
      }
      function updateCameraAddressPreview(section,index) {
        const output = section.querySelector('[data-camera-address-preview]');
        if (!output) return;
        output.textContent = ['main','sub'].map(stream => {
          try { const url = new URL(cameraField(section,index,`rtsp-${stream}`).value); url.username='';url.password='';return `${stream}: ${url.href}`; }
          catch (_) { return `${stream}: wybierz ONVIF i odczytaj profile lub podaj własny adres w Advanced`; }
        }).join(' · ');
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
            ...zoneFormValues(section,index),
            events_enabled: value('events-enabled'),
            local_detection_enabled: value('local-detection-enabled'),
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
            events_enabled: get('events-enabled').checked,
            local_detection_enabled: get('local-detection-enabled').checked,
            ...zoneFormValues(section,index,true),
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
          live: {engine: 'edge_rtc'},
          detection: {
            camera_events_enabled: get('detection-camera-events').checked,
            local_enabled: get('detection-local').checked,
            person_enabled:get('detection-person').checked,animal_enabled:get('detection-animal').checked,vehicle_enabled:get('detection-vehicle').checked,
            alert_media_mb:Number(get('detection-alert-mb').value||256),pre_seconds:Number(get('detection-pre').value||0),post_seconds:Number(get('detection-post').value||15),
            notifications_enabled:get('detection-notifications').checked,notify_service:get('detection-notify-service').value.trim(),notify_cooldown:Number(get('detection-notify-cooldown').value||30),notify_path:notificationPanelPath(),
            max_fps: Number(get('detection-fps').value || 1), decoder:get('detection-decoder').value,
            confidence: Number(get('detection-confidence').value || 0.45),
            animal_confidence:Number(get('detection-animal-confidence').value||.35),
            hold_seconds: Number(get('detection-hold').value || 10)
          },
          nvr: {
            segment_seconds: Number(get('nvr-segment-seconds').value || 10),
            retention_days: Number(get('nvr-retention-days').value || get('storage-retention-days').value || 14),
            playback_cache_segments: Number(get('nvr-playback-cache-segments').value || 10000),
            playback_cache_mb: Number(get('nvr-playback-cache-mb').value || 0),
            thumbnail_cache_mb: Number(get('nvr-thumbnail-cache-mb').value || 32),
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
        const response = await panelFetch(panelPath('api/config'), { cache: 'no-store' });
        if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Could not load configuration.'); }
        config = await response.json();
        debugEvent('ui_config_loaded', { summary: saveSummary(config) });
        configDirty = false;
        renderConfig();
      }

      async function fetchSavedConfig() {
        const { response, data } = await settingsRequest('api/config', { cache: 'no-store' }, 15000);
        if (!response.ok) {
          throw new Error(`Could not verify saved config: ${response.status}`);
        }
        return data;
      }

      async function settingsRequest(path, options = {}, timeoutMs = 45000) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), timeoutMs);
        try {
          const response = await panelFetch(panelPath(path), { ...options, signal: controller.signal });
          const data = await response.json();
          return { response, data };
        } catch (error) {
          if (error.name === 'AbortError') throw new Error('Save or verification timed out. Your form was kept. Refresh saved settings before retrying.');
          throw error;
        } finally {
          window.clearTimeout(timer);
        }
      }

      async function loadCameras() {
        const response = await panelFetch(panelPath('cameras.json'), { cache: 'no-store' });
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
          if ((camera.status === 'online'||EDGE_NATIVE_RTC&&camera.status==='checking') && live[liveKey] === undefined) {
            live[liveKey] = true;
          }
        });
        grid.innerHTML = cameras.length
          ? `${cameras.map(cameraCard).join('')}${addCameraTile()}`
          : `${addCameraTile()}`;
        monitorLiveFrames();
      }

      edgeForm.addEventListener('click',async event=>{
        const button=event.target.closest('[data-cleanup-recording-cache]');if(!button)return;
        const output=edgeForm.querySelector('[data-storage-cleanup-result]');button.disabled=true;output.textContent='Sprzątanie plików pomocniczych…';
        try{
          const response=await panelFetch(panelPath('api/storage/cleanup'),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
          const data=await response.json();if(!response.ok)throw new Error(data.error||'Błąd sprzątania');
          recordingStorage=data.storage;renderRecordingStorage();
          const cleanup=data.cleanup||{},freed=Number(cleanup.playback?.freed_bytes||0)+Number(cleanup.thumbnails?.freed_bytes||0)+Number(cleanup.log_freed_bytes||0);
          output.textContent=cleanup.skipped?'Sprzątanie już trwa. Odśwież panel za chwilę.':`Zwolniono ${formatBytes(freed)}. Oryginalne nagrania pozostały bez zmian.${cleanup.playback?.skipped||cleanup.thumbnails?.skipped?' Część cache jest używana; ponów sprzątanie za chwilę.':''}`;
        }catch(error){output.textContent=error.message;}finally{button.disabled=false;}
      });
      let nvrRequest = null;
      let nvrRequestID = 0;
      let storageUsageTimer=null;
      async function loadStorageUsage(){
        clearTimeout(storageUsageTimer);if(activePage!=='edge-settings')return;
        try{
          const response=await panelFetch(panelPath('api/storage/status'),{cache:'no-store'});
          if(!response.ok)throw Error(`storage_status_${response.status}`);
          const data=await response.json();recordingStorage=data;renderRecordingStorage();
          if(data.pending&&activePage==='edge-settings')storageUsageTimer=setTimeout(loadStorageUsage,2000);
        }catch(error){debugEvent('ui_storage_status_error',{message:error.message});}
      }
      function renderRecordingStorage() {
        document.querySelectorAll('[data-recording-disk-usage]').forEach(output => {
          if (!recordingStorage) return;
          const usage=recordingStorage;
          const write=value=>{for(const [source,target] of [["Wolne miejsce na nagrania", "Free space for recordings"], ["Wolne miejsce", "Free space"], ["Szacunkowy zapis", "Estimated recording"], ["Szczegóły zajętości są odświeżane w tle.", "Usage details are refreshed in the background."], ["Nagrania", "Recordings"], ["Kopie odtwarzania", "Playback copies"], ["Kopie zgodności", "Compatibility copies"], ["Miniatury", "Thumbnails"], ["Klipy alertów", "Alert clips"], ["Zdjęcia alertów", "Alert pictures"], ["Wolne na dysku nagrań", "Free on recordings disk"], ["wolne w konfiguracji HA", "Free in HA configuration"], ["Szacunek ciągłego nagrywania", "Continuous recording estimate"], ["/dobę", "/day"], [" na ", " for "], [" dni.", " days."], ["Zależy od bitrate i sceny.", "Depends on bitrate and scene."]])value=value.replaceAll(source,EdgeI18n.text(target));output.textContent=value;};
          const daily=Object.values(recordingStatus).reduce((sum,item)=>sum+Number(item.bytes_per_day_estimate||0),0);
          const retention=Number(config.nvr?.retention_days||14);
          if(output.hasAttribute('data-storage-compact')){
            output.hidden=usage.recordings_volume?.free_bytes==null;
            write(`Wolne miejsce: ${formatBytes(usage.recordings_volume?.free_bytes)}${daily?' · Szacunkowy zapis: '+formatBytes(daily)+'/dobę':''}`);return;
          }
          if(usage.pending){write(`Wolne miejsce na nagrania: ${usage.recordings_volume?.free_bytes==null?'—':formatBytes(usage.recordings_volume.free_bytes)}. Szczegóły zajętości są odświeżane w tle.`);return;}
          write(`Nagrania: ${formatBytes(usage.recordings_bytes)} · Kopie odtwarzania: ${formatBytes(usage.playback_cache_bytes)} / ${formatBytes(usage.playback_cache_limit_bytes)} · Kopie zgodności: ${formatBytes(usage.phone_cache_bytes)} / ${formatBytes(usage.phone_cache_limit_bytes)} · Miniatury: ${formatBytes(usage.thumbnail_cache_bytes)} / ${formatBytes(usage.thumbnail_cache_limit_bytes)} · Klipy alertów: ${formatBytes(usage.alert_clip_bytes)} / ${formatBytes(usage.alert_clip_limit_bytes)} · Zdjęcia alertów: ${formatBytes(usage.alert_image_bytes)} / ${formatBytes(usage.alert_image_limit_bytes)}. Wolne na dysku nagrań: ${usage.recordings_volume?.free_bytes==null?'—':formatBytes(usage.recordings_volume.free_bytes)}; wolne w konfiguracji HA: ${usage.cache_volume?.free_bytes==null?'—':formatBytes(usage.cache_volume.free_bytes)}.${daily?' Szacunek ciągłego nagrywania: '+formatBytes(daily)+'/dobę, '+formatBytes(daily*retention)+' na '+retention+' dni. Zależy od bitrate i sceny.':''}`);
        });
      }
      function recordingDayLoadMessage(message) {
        const global=document.getElementById('nvr-load-state');global.textContent=message;global.hidden=!message||nvrGrid.querySelector('[data-nvr-index]')!==null;
        nvrGrid.querySelectorAll('[data-nvr-index]').forEach(card => {
          let output = card.querySelector('[data-recording-day-load]');
          if (!output) { output = document.createElement('p'); output.dataset.recordingDayLoad = ''; output.setAttribute('role','status'); card.prepend(output); }
          output.textContent = message; output.hidden = !message;
        });
      }
      async function loadRecordingStatus(options = {}) {
        if (nvrLoading && !options.forceRender&&!options.restartRequest) return;
        nvrRequest?.abort('superseded');
        const controller = new AbortController(), requestID = ++nvrRequestID;
        nvrRequest = controller;
        const timeout = setTimeout(() => controller.abort('timeout'), 20000);
        nvrLoading = true;
        if (options.forceRender&&activePage==='nvr') recordingDayLoadMessage('Ładowanie wybranego dnia…');
        try {
          const requestedDays = JSON.stringify(selectedRecordingDay);
          const dayQuery = Object.entries(selectedRecordingDay)
            .filter(([, day]) => day)
            .map(([index, day]) => `${encodeURIComponent(index)}:${encodeURIComponent(day)}`)
            .join(',');
          const summary=activePage!=='nvr'&&!options.history;
          const fresh=options.reason==='manual_refresh'||options.reason==='recording_continuation_refresh'||options.reason==='detection_event'||String(options.reason||'').startsWith('alert_recording');
          const statusPath = summary?'api/recording/status?summary=1':`api/recording/status?catalog=1${fresh?'&fresh=1':''}${dayQuery?'&days='+dayQuery:''}`;
          const known=options.forceRender?'':Object.values(recordingStatus).filter(item=>item.files_revision).map(item=>`${item.index}:${item.files_revision}`).join(',');
          const response = await panelFetch(panelPath(statusPath+(!summary&&known?`&known=${encodeURIComponent(known)}`:'')), { cache: 'no-store', signal: controller.signal });
          if (!response.ok) throw new Error(`Recording status failed: ${response.status}`);
          const data = await response.json();
          if (requestID !== nvrRequestID || requestedDays !== JSON.stringify(selectedRecordingDay)) return;
          if(data.summary){
            for(const item of data.cameras||[]){const old=recordingStatus[item.index];recordingStatus[item.index]={...(old?.id===item.id?old:{}),...item};}
            updateCameraOverview();
            if(activePage==='nvr')queueMicrotask(()=>{if(activePage==='nvr')void loadRecordingStatus({reason:'after_summary'});});
            return;
          }
          for(const item of data.cameras||[]){
            if(!item.files_unchanged)continue;const previous=recordingStatus[item.index];
            if(!previous||previous.files_revision!==item.files_revision||previous.key!==item.key||previous.selected_day!==item.selected_day)throw new Error('Lista nagrań zmieniła się. Odśwież wybrany dzień.');
            item.files=previous.files;
          }
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
          recordingStorage = data.storage || null;
          recordingStatus = {};
          (Array.isArray(data.cameras) ? data.cameras : []).forEach((item) => {
            recordingStatus[item.index] = item;
            rememberRecordingDay(item);
            const pinnedDay = selectedRecordingDay[item.index];
            if (item.selected_day && !pinnedDay) {
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
          renderRecordingStorage();
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
          recordingDayLoadMessage('');
          persistRecordingGallery();
          if(activePage!=='nvr')void restoreGalleryThumbnails();
          if(data.index_stale&&data.index_refreshing&&activePage==='nvr')setTimeout(()=>{if(activePage==='nvr')void loadRecordingStatus({reason:'timer'});},1500);
        } catch (error) {
          if (requestID !== nvrRequestID) return;
          if(controller.signal.aborted&&controller.signal.reason!=='timeout'){debugEvent('ui_recording_status_cancelled',{reason:options.reason});return;}
          if(activePage==='nvr'&&(options.forceRender||!(config.cameras||[]).every((_,i)=>Array.isArray(recordingStatus[i]?.files))))recordingDayLoadMessage(controller.signal.aborted?'Nie udało się odczytać listy nagrań. Naciśnij Odśwież.':`Błąd listy nagrań: ${error.message}`);
          debugEvent('ui_recording_status_error', {message: String(error), reason: options.reason});
        } finally {
          clearTimeout(timeout);
          if (requestID === nvrRequestID) { nvrLoading = false; nvrRequest = null; }
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
          const response = await panelFetch(panelPath('api/camera-autoconfig'), {
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
          const response = await panelFetch(panelPath('api/camera-stream-config'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ index, camera, stream: streamName, settings: streamSettingsFromEditor(editor) })
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Could not save stream configuration.');
          const next = previous || { streams: {}, device: {}, sections: {} };
          next.streams = { ...(next.streams || {}), [streamName]: data.stream };
          cameraAuto[index] = { data: next, message: `${streamName} stream saved.` };
          await panelFetch(panelPath('api/refresh'), { method: 'POST' });
          await loadCameras();
        } catch (error) {
          cameraAuto[index] = { data: previous, error: error.message };
        }
        renderConfig();
      }

      const eventKindNames = {motion: 'Ruch', person: 'Osoba', vehicle: 'Pojazd', animal:'Zwierzę'};
      let detectionData = {cameras: []}, detectionTimer = null, detectionLoading = false;
      const eventHistory = {events: [], before: null, stack: [], next: null, serial: 0, controller: null, key: ''};
      const recordingEventCache = new Map();
      const recordingEventQueue = [];
      let recordingEventRequests = 0;

      async function eventJson(path, signal) {
        const controller = new AbortController();
        const abort = () => controller.abort();
        if (signal?.aborted) abort();
        else signal?.addEventListener('abort', abort, {once:true});
        const timer = setTimeout(abort, 10000);
        try {
          const response = await panelFetch(panelPath(path), {cache:'no-store', signal:controller.signal});
          if (!response.ok) throw new Error('Nie udało się odczytać zdarzeń. Spróbuj ponownie.');
          return await response.json();
        } finally {
          clearTimeout(timer);signal?.removeEventListener('abort', abort);
        }
      }
      function populateEventCameras() {
        for (const id of ['event-camera-filter', 'event-notify-camera']) {
          const select = document.getElementById(id), selected = select.value;
          const options = (id === 'event-camera-filter' ? '<option value="">Wszystkie / All</option>' : '') + (config.cameras || []).map(camera => `<option translate="no" value="${escapeHtml(camera.id)}">${escapeHtml(camera.name || camera.id)}</option>`).join('');
          if (select.innerHTML !== options) {
            select.innerHTML = options;
            if (Array.from(select.options).some(option => option.value === selected)) select.value = selected;
          }
        }
        updateNotificationYaml();
      }
      async function loadDetection() {
        if (detectionLoading) return;
        detectionLoading = true;
        try {
          detectionData = await eventJson('api/detection');
          document.getElementById('detection-runtime').textContent = `${detectionData.model || 'Detection'} · ${detectionData.backend || 'CPU'} · limit ${detectionData.max_fps || 1} FPS total · analysis ${detectionData.analysis_ms || 0} ms. Model: ${detectionData.model_installed ? 'ready' : 'not installed / starting'}`;
          document.getElementById('detection-cameras').innerHTML = (detectionData.cameras || []).map(camera => `<article class="camera"><div class="body"><b translate="no">${escapeHtml(camera.name)}</b><p>ONVIF: ${escapeHtml(camera.camera_status)} · AI: ${escapeHtml(camera.local_status)} · Dekoder: ${escapeHtml(camera.decoder?.backend||'—')}</p>${camera.decoder?.backend==='cpu_keyframes'?'<p class="notice warning">AI analizuje klatki kluczowe. Krótkie zdarzenia pomiędzy nimi mogą zostać pominięte. Pełna jakość i FPS nagrań pozostają zachowane.</p>':''}${camera.local_error?`<p role="status">AI: ${escapeHtml(camera.local_error.code)} (${escapeHtml(camera.local_error.phase)}). Szczegóły znajdziesz w logach Local AI.</p>`:''}${camera.last_analysis?`<p>Ostatnia analiza: ${escapeHtml(new Date(camera.last_analysis.timestamp*1000).toLocaleTimeString(EdgeI18n.resolve()))} · ${Number(camera.last_analysis.objects)} obiektów · ${Number(camera.last_analysis.analysis_ms)} ms.</p>`:''}<p>Strefa Edge (tylko lokalne AI): ${camera.local_zone?.enabled ? (camera.local_zone.valid ? 'włączona' : 'nieprawidłowa — popraw ustawienia') : 'cały kadr'}</p><p>${Object.entries(camera.states || {}).map(([kind, active]) => `${escapeHtml(eventKindNames[kind] || kind)}: ${(camera.supported_kinds || []).includes(kind) ? (active ? '●' : '○') : '—'}`).join(' · ')}</p></div></article>`).join('');
        } finally {detectionLoading = false;}
      }
      function eventFilters() {
        const query = new URLSearchParams({limit:'100'});
        for (const [field, id] of [['camera','event-camera-filter'], ['kind','event-kind-filter'], ['day','event-day-filter']]) {
          const value = document.getElementById(id).value;
          if (value) query.set(field, value);
        }
        return query;
      }
      function updateEventPagination(loading = false) {
        document.getElementById('events-older').disabled = loading || !eventHistory.next;
        document.getElementById('events-newer').disabled = loading || !eventHistory.stack.length;
        document.getElementById('event-page-label').textContent = `Strona ${eventHistory.stack.length + 1} · ${eventHistory.events.length} zdarzeń`;
      }
      async function loadEventHistory({before=null, stack=[]} = {}) {
        eventHistory.controller?.abort();
        const controller = new AbortController(), serial = ++eventHistory.serial;
        eventHistory.controller = controller;
        const query = eventFilters(), key = query.toString();
        if (key !== eventHistory.key) {
          eventHistory.events = [];eventHistory.stack = [];eventHistory.next = null;
          document.getElementById('event-list').replaceChildren();
        }
        if (before) query.set('before', String(before));
        updateEventPagination(true);
        document.getElementById('event-list').setAttribute('aria-busy', 'true');
        document.getElementById('event-history-status').textContent = 'Ładowanie historii…';
        try {
          const data = await eventJson(`api/events?${query}`, controller.signal);
          if (serial !== eventHistory.serial) return;
          Object.assign(eventHistory, {events:data.events || [], before, stack, next:data.next_before || null, key});
          renderDetectionEvents();
          document.getElementById('event-history-status').textContent = 'Historia gotowa. Daty i godziny według strefy rejestratora.';
        } catch (error) {
          if (serial === eventHistory.serial) document.getElementById('event-history-status').textContent = controller.signal.aborted ? 'Odczyt przerwany.' : error.name === 'AbortError' ? 'Odczyt trwał zbyt długo. Użyj Odśwież.' : error.message;
        } finally {
          if (serial === eventHistory.serial) {updateEventPagination();document.getElementById('event-list').setAttribute('aria-busy', 'false');}
        }
      }
      function renderDetectionEvents() {
        const cameras = config.cameras || [];
        document.getElementById('event-list').innerHTML = eventHistory.events.length ? eventHistory.events.map(item => {
          const camera = cameras.find(camera => camera.id === item.camera_id);
          alertItems.set(String(item.id),item);
          return `<article class="field-section"><div class="event-history-row"><b><span translate="no">${escapeHtml(camera?.name || item.camera_id)}</span> · <span>${escapeHtml(eventKindNames[item.kind] || item.kind)}</span> <small>(${escapeHtml(item.kind)})</small></b><span>${escapeHtml(item.day)} ${escapeHtml(item.time_label || new Date(item.timestamp * 1000).toLocaleTimeString(EdgeI18n.resolve()))}</span></div><p>${item.source === 'local' ? (item.zone_id === 'local_zone' ? 'Local AI · Strefa Edge' : 'Local AI') : 'Camera / ONVIF'}${item.score ? ` · ${Math.round(item.score * 100)}%` : ''}</p>${['person','animal'].includes(item.kind)?intrusionCard(item):''}<button type="button" data-event-recording="${Number(item.id)}" ${camera ? '' : 'disabled'}>Otwórz nagranie / Open recording</button></article>`;
        }).join('') : '<p class="notice">Brak zdarzeń dla wybranych filtrów. Sprawdź dzień, kamerę oraz ustawienia detekcji.</p>';
      }
      function enterEventsPage() {
        populateEventCameras();
        const refresh = () => loadDetection().catch(error => {document.getElementById('detection-runtime').textContent = error.message;});
        refresh();
        if (!eventHistory.key || eventHistory.key !== eventFilters().toString()) loadEventHistory();
        detectionTimer = setInterval(refresh, 5000);
      }
      document.getElementById('refresh-events').addEventListener('click', () => {
        loadDetection().catch(error => {document.getElementById('detection-runtime').textContent = error.message;});
        loadEventHistory();
      });
      for (const id of ['event-camera-filter', 'event-kind-filter', 'event-day-filter']) document.getElementById(id).addEventListener('change', () => loadEventHistory());
      document.getElementById('event-clear-day').addEventListener('click', () => {document.getElementById('event-day-filter').value = '';loadEventHistory();});
      document.getElementById('events-older').addEventListener('click', () => {
        if (eventHistory.next) loadEventHistory({before:eventHistory.next, stack:[...eventHistory.stack,eventHistory.before]});
      });
      document.getElementById('events-newer').addEventListener('click', () => {
        if (eventHistory.stack.length) loadEventHistory({before:eventHistory.stack.at(-1), stack:eventHistory.stack.slice(0,-1)});
      });
      let openingEventRecording = false;
      document.getElementById('event-list').addEventListener('click', async event => {
        const alert=event.target.closest('[data-alert-watch]');if(alert){try{await openAlertById(alert.dataset.alertWatch);}catch(error){document.getElementById('event-history-status').textContent=error.message;}return;}
        const button = event.target.closest('[data-event-recording]');
        if (!button || openingEventRecording) return;
        const item = eventHistory.events.find(row => String(row.id) === button.dataset.eventRecording);
        const index = (config.cameras || []).findIndex(camera => camera.id === item?.camera_id);
        if (!item || index < 0) return;
        openingEventRecording = true;button.disabled = true;
        selectedRecordingDay[index] = item.day;
        delete selectedRecording[index];delete selectedRecordingSeek[index];delete selectedRecordingTimeline[index];delete recordingStreamStartOffset[index];
        showPage('nvr');
        try {
          const waitDeadline = Date.now() + 12000;
          while (nvrLoading && Date.now() < waitDeadline) await new Promise(resolve => setTimeout(resolve, 50));
          if (nvrLoading) throw new Error('Odczyt nagrań trwa. Spróbuj ponownie.');
          await loadRecordingStatus({forceRender:true,reason:'detection_event'});
          if (!seekRecordingEvent(index, item)) throw new Error('Brak nagrania w chwili zdarzenia. Sprawdź, czy nagrywanie było włączone.');
        } catch (error) {
          showPage('events');document.getElementById('event-history-status').textContent = error.message;
        } finally {openingEventRecording = false;button.disabled = false;}
      });

      function seekRecordingEvent(index, item) {
        const target = recordingTargetForOffset(index, item.day_seconds);
        if (!target || recordingStatus[index]?.selected_day !== item.day || Math.abs(target.requested - item.day_seconds) > 2) return false;
        selectRecordingAtOffset(index, item.day_seconds);
        return true;
      }
      const alertItems=new Map();let alertDetailSerial=0;
      function renderAlertAxis(index,data) {
        const axis=nvrGrid.querySelector(`[data-alert-axis="${index}"]`);if(!axis)return;
        const total=Math.max(.1,Number(recordingStatus[index]?.timeline?.playback_total_seconds||0));
        const width=Math.max(1,axis.getBoundingClientRect().width),bins=new Map(),timeline=recordingTimeline(recordingStatus[index]?.files||[],recordingStatus[index]||{}),entries=timeline.entries;
        for(const event of data.alerts||[]){
          if(!entries.length)continue;let low=0,high=entries.length;
          while(low<high){const middle=(low+high)>>1;if(entries[middle].offset<=event.day_seconds)low=middle+1;else high=middle;}
          let entry=entries[Math.max(0,low-1)];if(event.day_seconds>=entry.offset+entry.duration&&low<entries.length)entry=entries[low];
          const seek=Math.max(0,Math.min(event.day_seconds-entry.offset,entry.duration-.1));
          const position=Math.max(0,Math.min(1,(entry.playbackOffset+seek)/Math.max(.1,total-.1))),bin=Math.round(position*width/12);
          const group=bins.get(bin)||{position,events:[]};group.events.push(event);bins.set(bin,group);
        }
        const html=[...bins.values()].map(group=>{
          const label=group.events.map(event=>eventKindNames[event.kind]||event.kind).filter((value,index,values)=>values.indexOf(value)===index).join(', ');
          return `<button type="button" data-alert-point="${index}" data-alert-position="${group.position*Math.max(.1,total-.1)}" data-alert-ids="${[...group.events].reverse().map(event=>Number(event.id)).join(',')}" style="left:${group.position*100}%" aria-label="${escapeHtml(label)} · ${group.events.length} alertów" title="${escapeHtml(label)} · ${new Date(group.events[0].timestamp*1000).toLocaleTimeString(EdgeI18n.resolve())}"></button>`;
        }).join('');
        if(axis.dataset.signature!==html){axis.innerHTML=html;axis.dataset.signature=html;}
        const video=nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
        updateAlertProgress(index,archivePlayers.get(String(index))?.position??recordingScrubs.get(String(index))?.desired??recordingPlaybackPosition(video));
      }
      function updateAlertProgress(index,position) {
        nvrGrid.querySelectorAll(`[data-alert-point="${index}"]`).forEach(point=>point.dataset.passed=String(Number(point.dataset.alertPosition)<=position+.05));
      }
      function intrusionCard(event, index=null) {
        const kind=eventKindNames[event.kind]||event.kind,state={pending:'Zapisywanie fragmentu…',ready:'Fragment zapisany',expired:'Fragment wygasł',unavailable:'Brak klipu · dostępne zdjęcie',error:'Nie udało się zapisać klipu'}[event.clip_state]||'';
        return `<div class="nvr-alert-card"><button type="button" data-alert-watch="${Number(event.id)}" ${index===null?'':`data-alert-index="${index}"`}><b>${escapeHtml(kind)} · ${escapeHtml(event.time_label||new Date(event.timestamp*1000).toLocaleTimeString(EdgeI18n.resolve()))}</b>${event.image_url?`<img src="${escapeHtml(panelPath(event.image_url))}" alt="${escapeHtml(kind)} w chwili wykrycia" loading="lazy">`:'<span class="field-help">Miniatura oczekuje lub wygasła</span>'}<small>${escapeHtml(state)}</small><span>Obejrzyj od tego momentu</span></button>${event.clip_url?`<button type="button" data-alert-clip="${Number(event.id)}">Zapisany fragment</button>`:''}</div>`;
      }
      async function showAlertDetails(index,ids) {
        const wrap=nvrGrid.querySelector(`[data-recording-wrap="${index}"]`);
        const fullscreen=document.fullscreenElement===wrap||wrap?.classList.contains('edge-soft-fullscreen');
        const holder=nvrGrid.querySelector(`[data-alert-${fullscreen?'popup':'detail'}="${index}"]`);if(!holder)return;
        const serial=String(++alertDetailSerial);holder.dataset.request=serial;holder.hidden=false;holder.innerHTML='<p role="status">Odczyt alertów…</p>';
        const events=[];
        for(const id of ids.slice(0,12)){
          try{const event=await eventJson(`api/alerts/${id}`);events.push(event);alertItems.set(String(event.id),event);}catch(_){}
          if(holder.dataset.request!==serial||!holder.isConnected)return;
        }
        while(alertItems.size>100)alertItems.delete(alertItems.keys().next().value);
        holder.innerHTML=events.map(event=>intrusionCard(event,index)).join('')||'<p>Alert wygasł.</p>';
        if(ids.length>12)holder.insertAdjacentHTML('beforeend','<p>Więcej alertów znajdziesz w pełnej historii dnia.</p>');
        if(!fullscreen)holder.scrollIntoView({block:'nearest',behavior:'smooth'});
      }
      async function openAlertRecording(event) {
        const index=(config.cameras||[]).findIndex(camera=>camera.id===event.camera_id);
        if(index>=0){
          const loaded=recordingStatus[index]?.selected_day===event.day&&nvrGrid.querySelector(`video[data-recording-player="${index}"],[data-dvr-host="${index}"]`);
          if(!loaded){selectedRecordingDay[index]=event.day;delete selectedRecording[index];delete recordingStreamNonce[index];}
          if(activePage!=='nvr')showPage('nvr');if(!loaded)await loadRecordingStatus({forceRender:true,reason:'alert_recording'});
          let found=seekRecordingEvent(index,event);
          if(!found&&loaded){await loadRecordingStatus({reason:'alert_recording_refresh'});found=seekRecordingEvent(index,event);}
          if(found){
            const archive=archivePlayers.get(String(index));if(archive){archive.host.scrollIntoView({block:'center'});archive.seekPlayback(archive.position,{play:true});return;}
            nvrGrid.querySelectorAll('[data-alert-detail],[data-alert-popup]').forEach(node=>node.hidden=true);
            const video=nvrGrid.querySelector(`video[data-recording-player="${index}"]`);
            video?.closest('[data-recording-wrap]')?.scrollIntoView({block:'center',behavior:'auto'});
            if(video){if(video.readyState<2&&event.image_url)video.poster=panelPath(event.image_url);playRecording(video);}return;
          }
        }
        if(event.clip_url){showSavedAlertClip(event);return;}
        throw new Error('W tym momencie brak zapisanego wideo. Zdjęcie alertu pozostaje w historii.');
      }
      async function showSavedAlertClip(event) {
          suspendArchivePlayers();
          const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Zapisany fragment alertu');dialog.style.cssText='max-width:95vw;width:850px;background:#101c29;color:white;border:0;border-radius:8px';
          dialog.innerHTML=`<p>${escapeHtml(eventKindNames[event.kind]||event.kind)} · zapisany fragment alertu</p>${archivePlayerMarkup('clip',1,0,event.image_url?panelPath(event.image_url):'')}<p><a href="${escapeHtml(panelPath(event.clip_url)+'?download=1')}" download>Pobierz fragment MP4</a></p><button data-close-clip type="button">Zamknij</button>`;
          let player;const close=()=>{player?.destroy();dialog.remove();};dialog.querySelector('[data-close-clip]').onclick=close;dialog.addEventListener('cancel',close);document.body.append(dialog);dialog.showModal();
          try{
            const {EdgeDVRPanel}=await (archiveModule??=import(panelPath('api/edge-rtc/edge-dvr-panel.js?v='+EDGE_PANEL_VERSION)));if(!dialog.isConnected)return;
            let duration=1;const entry={file:{name:'event-'+event.id+'.mp4'},playbackOffset:0,offset:0,duration};
            player=new EdgeDVRPanel(dialog.querySelector('[data-dvr-host]'),{url:panelPath('api/edge-rtc/clip/'+event.id),diagnostics:deviceReporter,total:()=>duration,format:recordingPlaybackClock,targetAt:position=>({target:entry,seek:Math.max(0,Math.min(duration-.01,position))}),activate:()=>{},fullscreen:wrap=>requestEdgeFullscreen(wrap,'alert_dvr'),onPosition:()=>{},onMetadata:metadata=>{duration=entry.duration=metadata.duration_us/1e6;player.refresh();}});
            await player.seekTarget({target:entry,seek:Math.max(0,Number(event.clip_offset_seconds)||0)},{play:true});
          }catch(error){dialog.querySelector('[data-dvr-message]').textContent=error.message;}
      }
      document.addEventListener('click',async event=>{const button=event.target.closest('[data-alert-clip]');if(!button)return;try{const item=await eventJson(`api/alerts/${button.dataset.alertClip}`);if(item.clip_url)showSavedAlertClip(item);}catch(_){button.textContent='Fragment chwilowo niedostępny';}});
      async function openAlertById(id){const event=alertItems.get(String(id))||await eventJson(`api/alerts/${id}`);return openAlertRecording(event);}
      nvrGrid.addEventListener('click',async event=>{
        const point=event.target.closest('[data-alert-point]');if(point){event.preventDefault();void showAlertDetails(point.dataset.alertPoint,point.dataset.alertIds.split(',').map(Number));return;}
        const watch=event.target.closest('[data-alert-watch]');if(watch){event.preventDefault();try{await openAlertById(watch.dataset.alertWatch);}catch(error){watch.closest('[data-alert-detail],[data-alert-popup]')?.insertAdjacentHTML('beforeend',`<p role="status">${escapeHtml(error.message)}</p>`);}}
      });
      document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement)nvrGrid.querySelectorAll('[data-alert-popup]').forEach(node=>node.hidden=true);});
      const repaintAlertAxes=()=>{for(const section of nvrGrid.querySelectorAll('[data-recording-events]')){const data=recordingEventCache.get(section.dataset.eventKey)?.data;if(data)renderAlertAxis(section.dataset.recordingEvents,data);}};window.addEventListener('resize',repaintAlertAxes);document.addEventListener('fullscreenchange',repaintAlertAxes);

      function renderRecordingEvents(section, data) {
        renderAlertAxis(section.dataset.recordingEvents,data);
        const content = section.querySelector('[data-recording-event-content]');
        const signature = JSON.stringify(data);
        if (content.dataset.signature === signature || (section.contains(document.activeElement) && document.activeElement.matches('select'))) return;
        const index = section.dataset.recordingEvents;
        const total = Math.max(1, Number(data.day_total_seconds || 86400));
        content.innerHTML=`<p>${Number(data.count||0)} zdarzeń · ${(data.alerts||[]).length} alertów osoby / zwierzęcia zaznaczonych kropkami na pasku filmu.</p><label>Wszystkie zdarzenia dnia<select data-recording-event-select="${index}"><option value="">Wybierz godzinę…</option>${(data.markers||[]).map(marker=>`<option value="${Number(marker.slot)}">${escapeHtml(marker.time_label)} · ${escapeHtml(marker.kinds.map(kind=>eventKindNames[kind]||kind).join(', '))} · ${Number(marker.count)}</option>`).join('')}</select></label><div class="event-history-row"><button type="button" data-recording-event-step="previous" data-record-index="${index}" ${data.markers?.length?'':'disabled'}>Poprzednie zdarzenia</button><button type="button" data-recording-event-step="next" data-record-index="${index}" ${data.markers?.length?'':'disabled'}>Następne zdarzenia</button><button type="button" data-recording-event-history="${index}">Pełna historia i alerty</button></div>`;
        content.dataset.signature = signature;
      }
      function pumpRecordingEvents() {
        while (recordingEventRequests < 2 && recordingEventQueue.length) {
          const job = recordingEventQueue.shift();
          if (activePage !== 'nvr') {job.entry.pending = false;continue;}
          recordingEventRequests++;
          eventJson(`api/events/timeline?${new URLSearchParams({camera:job.camera,day:job.day})}`).then(data => {
            job.entry.data = data;job.entry.at = Date.now();
            nvrGrid.querySelectorAll('[data-recording-events]').forEach(section => {if (section.dataset.eventKey === job.key) renderRecordingEvents(section,data);});
          }).catch(error => {
            job.entry.at = Date.now();
            nvrGrid.querySelectorAll('[data-recording-events]').forEach(section => {if (section.dataset.eventKey === job.key) section.querySelector('[data-recording-event-message]').textContent = error.name === 'AbortError' ? 'Odczyt zdarzeń trwał zbyt długo. Użyj Odśwież.' : error.message;});
          }).finally(() => {job.entry.pending = false;recordingEventRequests--;pumpRecordingEvents();});
        }
      }
      function refreshRecordingEvents(forceIndex = null) {
        if (activePage !== 'nvr') return;
        nvrGrid.querySelectorAll('[data-recording-events]').forEach(section => {
          const index = section.dataset.recordingEvents, card = section.closest('[data-nvr-day]');
          const camera = config.cameras?.[Number(index)]?.id, day = card?.dataset.nvrDay;
          if (!camera || !day) return;
          const key = JSON.stringify([camera,day]);section.dataset.eventKey = key;
          let entry = recordingEventCache.get(key);
          if (!entry) {entry = {at:0,pending:false};recordingEventCache.set(key,entry);}
          if (entry.data) renderRecordingEvents(section,entry.data);
          if (entry.pending || (String(forceIndex) !== index && Date.now() - entry.at < 15000)) return;
          entry.pending = true;section.querySelector('[data-recording-event-message]').textContent = '';
          recordingEventQueue.push({key,camera,day,entry});
        });
        if (recordingEventCache.size > 30) for (const [key,entry] of recordingEventCache) {
          if (!entry.pending && !Array.from(nvrGrid.querySelectorAll('[data-recording-events]')).some(section => section.dataset.eventKey === key)) recordingEventCache.delete(key);
          if (recordingEventCache.size <= 30) break;
        }
        pumpRecordingEvents();
      }
      function handleRecordingEventClick(event) {
        const section = event.target.closest('[data-recording-events]');
        if (!section) return false;
        const index = section.dataset.recordingEvents, data = recordingEventCache.get(section.dataset.eventKey)?.data;
        if (event.target.closest('[data-recording-events-refresh]')) {refreshRecordingEvents(index);return true;}
        if (event.target.closest('[data-recording-event-history]')) {
          populateEventCameras();document.getElementById('event-camera-filter').value = config.cameras[index].id;
          document.getElementById('event-day-filter').value = section.closest('[data-nvr-day]').dataset.nvrDay;
          document.getElementById('event-kind-filter').value = '';showPage('events');return true;
        }
        const target = event.target.closest('[data-recording-event-step]');
        if (!target || !data) return false;
        const current = Number(selectedRecordingTimeline[index] || 0);
        const marker = target.dataset.recordingEventStep === 'next' ? data.markers.find(item => item.day_seconds > current + .5) : [...data.markers].reverse().find(item => item.day_seconds < current - .5);
        showRecordingMarker(section,marker);
        return true;
      }

      function showRecordingMarker(section, marker) {
        const index = section.dataset.recordingEvents, data = recordingEventCache.get(section.dataset.eventKey)?.data;
        if (!data) return;
        const message = section.querySelector('[data-recording-event-message]');
        if (!marker) message.textContent = 'Brak kolejnych zdarzeń w tym kierunku.';
        else if (!seekRecordingEvent(index,{...marker,day:data.day})) message.textContent = `Brak nagrania o ${marker.time_label}. Historia zdarzeń jest niezależna od zapisu wideo.`;
        else message.textContent = `${marker.time_label} · ${marker.count} zdarzeń w tym przedziale.`;
      }

      function updateNotificationYaml() {
        const camera = (config.cameras || []).find(camera => camera.id === document.getElementById('event-notify-camera').value);
        const service = document.getElementById('event-notify-service').value.trim();
        const cooldown = Number(document.getElementById('event-notify-cooldown').value);
        const source = document.getElementById('event-notify-source').value;
        const valid = camera && /^notify\.mobile_app_[a-z0-9_]+$/.test(service) && Number.isInteger(cooldown) && cooldown >= 0 && cooldown <= 3600 && ['', 'camera', 'local', 'zone'].includes(source) && (source !== 'zone' || camera.detection_zone_enabled);
        document.getElementById('event-copy-notification').disabled = !valid;
        document.getElementById('event-notification-status').textContent = valid ? 'Gotowe do wklejenia w HA → Automatyzacje → Edytuj w YAML.' : source === 'zone' && !camera?.detection_zone_enabled ? 'Najpierw włącz i zapisz strefę Edge dla tej kamery.' : 'Wybierz kamerę i wpisz usługę notify.mobile_app_… z Home Assistanta.';
        const lines = valid ? [
          `alias: ${JSON.stringify('Edge: osoba, zwierzę lub pojazd — ' + (camera.name || camera.id))}`,
          'description: "Powiadomienia ze zdarzeń Edge of Infinity"',
          'triggers:', '  - trigger: event', '    event_type: edge_event', '    event_data:', `      camera_id: ${JSON.stringify(camera.id)}`,
          ...(source ? [`      source: ${source === 'camera' ? 'camera' : 'local'}`] : []),
          ...(source === 'zone' ? ['      zone_id: local_zone'] : []),
          'conditions:', '  - condition: template', `    value_template: "{{ trigger.event.data.kind in ['person', 'animal', 'vehicle'] }}"`,
          'actions:', `  - action: ${service}`, '    data:', '      title: "Edge of Infinity"', '      message: >-',
          "        {{ 'Osoba' if trigger.event.data.kind == 'person' else ('Zwierzę' if trigger.event.data.kind == 'animal' else 'Pojazd') }}",
          '        · {{ trigger.event.data.camera_id }}',
          "        · {{ as_local(as_datetime(trigger.event.data.timestamp)).strftime('%d.%m.%Y %H:%M:%S') }}",
          '  - delay:', `      seconds: ${cooldown}`, 'mode: single', 'max_exceeded: silent', ''
        ] : [];
        document.getElementById('event-notification-yaml').value = lines.join('\n');
      }
      for (const id of ['event-notify-camera','event-notify-service','event-notify-cooldown','event-notify-source']) document.getElementById(id).addEventListener('input', updateNotificationYaml);
      document.getElementById('event-copy-notification').addEventListener('click', async () => {
        const area = document.getElementById('event-notification-yaml');area.focus();area.select();
        let copied = false;
        try {
          if (navigator.clipboard?.writeText) {await navigator.clipboard.writeText(area.value);copied = true;}
          else copied = document.execCommand('copy');
        } catch (_) {}
        document.getElementById('event-notification-status').textContent = copied ? 'YAML skopiowany. Wklej i zapisz automatyzację w HA.' : 'Kod zaznaczony. Skopiuj go ręcznie i wklej w automatyzacji HA.';
      });

      function showPage(pageName) {
        cancelGalleryPrefetch();
        if(pageName!=='nvr')suspendArchivePlayers();
        if(pageName!=='nvr'){for(const state of recordingScrubs.values())discardRecordingScrub(state.index);nvrGrid.querySelectorAll('video[data-recording-player]').forEach(pauseRecording);}
        if(pageName!=='nvr')releaseInactiveRecordingPlayers(null,true);
        if(pageName!=='nvr')releaseRecordedPreviewDecoder();
        activePage = pageName;
        updateLivePlaybackVisibility();
        if(pageName==='edge-settings'){void loadPhoneServices();void loadStorageUsage();}
        else {clearTimeout(storageUsageTimer);storageUsageTimer=null;}
        document.querySelectorAll('[data-page]').forEach((page) => {
          page.hidden = page.dataset.page !== pageName;
        });
        document.querySelectorAll('[data-page-target]').forEach((button) => {
          button.classList.toggle('active', button.dataset.pageTarget === pageName);
        });
        debugEvent('ui_page_change', { page: pageName });
        if (detectionTimer) {clearInterval(detectionTimer);detectionTimer=null;}
        if (pageName === 'events') enterEventsPage();
        if (pageName === 'nvr'){refreshRecordingEvents();void loadRecordingStatus({forceRender:!(config.cameras||[]).every((_,i)=>Array.isArray(recordingStatus[i]?.files)),reason:'enter_nvr'});}
        if (pageName === 'nvr'){scheduleRecordingThumbnailHydration();pumpRecordingThumbnails();}
        if (pageName === 'nvr') {
          const videos=nvrGrid.querySelectorAll('video[data-recording-player]');
          (phonePlayback()?[...videos].slice(0,1):videos).forEach(restoreRecordingPlayer);
        }
        if (pageName === 'ha-dashboard') loadDashboardEntities();
        if(pageName==='nvr')mountArchivePlayers();
        if (pageName === 'home') loadCameras().catch((error) => debugEvent('ui_cameras_refresh_error', { message: error.message }));
        if (pageName === 'home') scheduleGalleryPrefetch();
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
          loadRecordingStatus({ reason: 'timer' }).then(() => refreshRecordingEvents()).catch((error) => {
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
        await panelFetch(panelPath('api/refresh'), { method: 'POST' });
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
        let saved = false;
        try {
          const payload = collectEdgeSettings({ refreshGenerated: true });
          if (!hasMeaningfulCameras(payload)) throw new Error('At least one camera needs a host/IP or RTSP URL.');
          state.textContent = 'Saving all current camera and Edge settings...';
          const { response, data } = await settingsRequest('api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });
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
          saved = true;
          if (liveTimer) { window.clearInterval(liveTimer); liveTimer = null; updateLiveTimer(); }
        } catch (error) {
          state.textContent = error.message || 'Could not save configuration. Your form has been kept.';
          debugEvent(`ui_${reason}_error`, { message: error.message });
        } finally {
          configSaveInFlight = false;
          [form, edgeForm].forEach((element) => { element.inert = false; });
          buttons.forEach((button) => { button.disabled = false; });
        }
        // Status refreshes must not keep the settings form locked after a verified save.
        if (saved) void Promise.allSettled([loadPresets(), loadCameras(), ...(panelLogs ? [loadLogs()] : [])]);
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

      function notificationPanelPath(){try{return config.detection?.notify_path||window.top.location.pathname;}catch(_){return config.detection?.notify_path||'';}}
      async function loadPhoneServices(){
        try{const hass=window.top.document.querySelector('home-assistant')?.hass;if(!hass)return;const services=await hass.callWS({type:'get_services'});const options=Object.keys(services.notify||{}).filter(name=>name.startsWith('mobile_app_'));document.getElementById('edge-notify-services').innerHTML=options.map(name=>`<option value="notify.${escapeHtml(name)}"></option>`).join('');}catch(_){}
      }
      edgeForm.addEventListener('change',event=>{
        const input=event.target;
        if(input.hasAttribute('data-easy-detection-camera')){
          form.elements[`camera-${input.dataset.easyDetectionCamera}-local-detection-enabled`].checked=input.checked;
          if(input.checked)edgeForm.elements['detection-local'].checked=true;
        }
        if(input.name==='detection-local'&&input.checked&&!edgeForm.querySelector('[data-easy-detection-camera]:checked')){
          const first=edgeForm.querySelector('[data-easy-detection-camera]:not(:disabled)');if(first){first.checked=true;form.elements[`camera-${first.dataset.easyDetectionCamera}-local-detection-enabled`].checked=true;}
        }
        syncConfigDraftFromForm();
      });
      edgeForm.addEventListener('input', syncConfigDraftFromForm);
      edgeForm.addEventListener('change', syncConfigDraftFromForm);

      form.addEventListener('input', (event) => {
        const connection = event.target.name?.match(/^camera-(\d+)-(host|username|password|camera-number|rtsp-main-channel|rtsp-sub-channel)$/);
        if (connection) refreshGeneratedCameraFields(event.target.closest('[data-camera-form]'),Number(connection[1]));
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

      nvrGrid.addEventListener('pointermove',event=>showRecordingControls(event.target.closest('[data-recording-wrap]')));
      nvrGrid.addEventListener('pointerdown',event=>{
        const slider=event.target.closest('[data-recording-playback-seek]');
        if(!slider||event.button!==0)return;
        beginRecordingScrub(slider);
        try{slider.setPointerCapture(event.pointerId);}catch(_){}
      });
      for(const name of ['pointerup','lostpointercapture'])nvrGrid.addEventListener(name,event=>{
        const slider=event.target.closest('[data-recording-playback-seek]');if(!slider)return;
        const state=recordingScrubs.get(slider.dataset.recordingPlaybackSeek);
        setTimeout(()=>{if(state&&recordingScrubs.get(state.index)===state&&state.active&&!state.keyboard)finishRecordingScrub(slider);},0);
      });
      nvrGrid.addEventListener('pointercancel',event=>{
        const slider=event.target.closest('[data-recording-playback-seek]');
        if(slider&&recordingScrubs.get(slider.dataset.recordingPlaybackSeek)?.active)finishRecordingScrub(slider,true);
      });
      nvrGrid.addEventListener('focusin',event=>showRecordingControls(event.target.closest('[data-recording-wrap]')));
      nvrGrid.addEventListener('focusout',event=>{
        const slider=event.target.closest('[data-recording-playback-seek]');if(!slider)return;
        if(recordingScrubs.get(slider.dataset.recordingPlaybackSeek)?.active)finishRecordingScrub(slider);

      });
      nvrGrid.addEventListener('input',event=>{
        const slider=event.target.closest('[data-recording-playback-seek]');if(slider)queueRecordingScrub(slider);
      });
      nvrGrid.addEventListener('change', (event) => {
        const eventSelect = event.target.closest('[data-recording-event-select]');
        if (eventSelect) {
          const section = eventSelect.closest('[data-recording-events]');
          const data = recordingEventCache.get(section.dataset.eventKey)?.data;
          if (eventSelect.value) showRecordingMarker(section,data?.markers.find(marker => String(marker.slot) === eventSelect.value));
          return;
        }
        const playback=event.target.closest('[data-recording-playback-seek]');
        if(playback){if(!recordingScrubs.get(playback.dataset.recordingPlaybackSeek)?.keyboard)finishRecordingScrub(playback);return;}
        const speed = event.target.closest('[data-recording-speed]');
        if (speed) {
          const video = nvrGrid.querySelector(`video[data-recording-player="${speed.dataset.recordingSpeed}"]`);
          if (video) { video.playbackRate = Number(speed.value); video.defaultPlaybackRate = video.playbackRate; }
        }
      });

      nvrGrid.addEventListener('click', async (event) => {
        if (handleRecordingEventClick(event)) return;
        const clickedVideo=event.target.closest('video[data-recording-player]');
        if(clickedVideo){
          showRecordingControls(clickedVideo.closest('[data-recording-wrap]'));
          if(event.pointerType!=='touch')nvrGrid.querySelector(`[data-recording-toggle="${clickedVideo.dataset.recordingPlayer}"]`)?.click();
          return;
        }
        const frameStep=event.target.closest('[data-recording-frame]');
        if(frameStep){const index=frameStep.dataset.recordIndex,video=nvrGrid.querySelector(`video[data-recording-player="${index}"]`),fps=Number(video?.dataset.recordingFps||0);if(video&&fps>0){pauseRecording(video);seekRecordingPlayback(index,recordingPlaybackPosition(video)+Number(frameStep.dataset.recordingFrame)/fps);}return;}
        const more=event.target.closest('[data-recording-more]');
        if(more){const wrap=more.closest('[data-recording-wrap]');const open=wrap.dataset.extraOpen!=='true';wrap.dataset.extraOpen=String(open);more.setAttribute('aria-expanded',String(open));showRecordingControls(wrap);return;}
        const toggle = event.target.closest('[data-recording-toggle]');
        if (toggle) {
          const video = nvrGrid.querySelector(`video[data-recording-player="${toggle.dataset.recordingToggle}"]`);
          if (video) {
            clearRecordingContinuation(toggle.dataset.recordingToggle);
            const scrub=recordingScrubs.get(video.dataset.recordingPlayer);
            if(scrub){scrub.resume=!scrub.resume;video.dataset.recordingWantsPlay=String(scrub.resume);if(scrub.resume&&scrub.finishing)playRecording(video);else if(!scrub.resume)pauseRecording(video);if(scrub.finishing)flushRecordingScrub(scrub);updateRecordingPlayerControls(video,'');return;}
            if (recordingPlaybackRequested(video)) pauseRecording(video);
            else {
              const total = Number(recordingStatus[toggle.dataset.recordingToggle]?.timeline?.playback_total_seconds || 0);
              if (video.ended && recordingPlaybackPosition(video) >= total - 0.5) seekRecordingPlayback(toggle.dataset.recordingToggle, 0);
              playRecording(video);
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
        const recordingUrl = playTarget?.dataset?.playRecording;
        let index = playTarget?.dataset?.recordIndex;
        if (index !== undefined && recordingUrl) {
          selectRecordingAtOffset(index, playTarget.dataset.recordingOffset || offsetForRecordingUrl(index, recordingUrl));
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
        const response = await panelFetch(panelPath(`api/recording/${action}`), {
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
        const slider=event.target.closest('[data-recording-playback-seek]');
        if(slider&&event.key==='Escape'&&recordingScrubs.has(slider.dataset.recordingPlaybackSeek)){event.preventDefault();finishRecordingScrub(slider,true);return;}
        if(slider&&['ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(event.key)){const state=beginRecordingScrub(slider);if(state)state.keyboard=event.key;}

        const video = event.target.closest('video[data-recording-player]');
        if (!video || ![' ', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        const index = video.dataset.recordingPlayer;
        if (event.key === ' ') nvrGrid.querySelector(`[data-recording-toggle="${index}"]`)?.click();
        else seekRecordingPlayback(index, recordingPlaybackPosition(video) + (event.key === 'ArrowLeft' ? -1 : 1)*(event.shiftKey?10:1));
      });

      nvrGrid.addEventListener('keyup',event=>{
        const slider=event.target.closest('[data-recording-playback-seek]');
        if(slider&&['ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(event.key)&&recordingScrubs.get(slider.dataset.recordingPlaybackSeek)?.active)finishRecordingScrub(slider);
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
          markNvrInteraction(recordingScrubs.has(video.dataset.recordingPlayer)?'timeupdate':'recording_video_seeking', recordingVideoDiagnostics(video));
          syncRecordingVideoProgress(video);
        }
      }, true);

      nvrGrid.addEventListener('seeked', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) {
          if(video.dataset.recordingWantsPlay!=='true')video.dataset.recordingLoading='false';
          if(video.dataset.recordingSeekStarted)debugEvent('ui_recording_seek_ready',recordingVideoDiagnostics(video));
          if(video.dataset.recordingWantsPlay==='true'&&!recordingScrubs.has(video.dataset.recordingPlayer)&&video.paused)playRecording(video);
          markNvrInteraction(recordingScrubs.has(video.dataset.recordingPlayer)?'timeupdate':'recording_video_seeked', recordingVideoDiagnostics(video));
          syncRecordingVideoProgress(video);
        }
      }, true);

      for (const name of ['loadeddata', 'playing', 'seeked']) nvrGrid.addEventListener(name, (event) => {
        if (event.target.matches?.('video[data-recording-player]')) {if(name!=='playing')recordingSurface.settled(event.target,name);releaseRecordingFrame(event.target);}
      }, true);

      nvrGrid.addEventListener('play', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if(video)recordingSurface.resume(video);
        if(video&&recordingScrubs.has(video.dataset.recordingPlayer)){const scrub=recordingScrubs.get(video.dataset.recordingPlayer);if(scrub.active){video.pause();return;}scrub.resume=true;scrub.playStarted=performance.now();}
        if(video&&video.paused)return;
        if(video){video.dataset.recordingWantsPlay='true';video.dataset.recordingPlayPending='false';video.dataset.recordingStableAt=String(performance.now());video.dataset.recordingStableClock=String(video.currentTime);}
        if(video)showRecordingControls(video.closest('[data-recording-wrap]'));
        if (video) { markNvrInteraction('recording_video_play', recordingVideoDiagnostics(video)); updateRecordingPlayerControls(video, 'Playing recordings for selected day'); }
      }, true);

      nvrGrid.addEventListener('pause', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if(video)recoverUnexpectedRecordingPause(video);
        if(video)showRecordingControls(video.closest('[data-recording-wrap]'));
        if (video) { markNvrInteraction('recording_video_pause', recordingVideoDiagnostics(video)); updateRecordingPlayerControls(video); }
      }, true);

      nvrGrid.addEventListener('playing',event=>{
        const video=event.target?.matches?.('video[data-recording-player]')?event.target:null;
        if(!video)return;
        if(!video.dataset.recordingStableAt){video.dataset.recordingStableAt=String(performance.now());video.dataset.recordingStableClock=String(video.currentTime);}
        video.dataset.recordingLoading='false';video.dataset.recordingBuffering='false';
        if(video.dataset.recordingSeekStarted){debugEvent('ui_recording_seek_playing',recordingVideoDiagnostics(video));delete video.dataset.recordingSeekStarted;}
        updateRecordingPlayerControls(video,'Playing recordings for selected day');
      },true);

      nvrGrid.addEventListener('ratechange', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) markNvrInteraction('recording_video_ratechange', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('ended', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video&&!recordingScrubs.has(video.dataset.recordingPlayer)) playNextRecordingSegment(video.dataset.recordingPlayer, video);
      }, true);

      nvrGrid.addEventListener('loadedmetadata', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if(video){
          const requested=Number(video.dataset.recordingSeek||0),seek=Number.isFinite(video.duration)?Math.min(requested,Math.max(0,video.duration-.05)):requested;
          video.dataset.recordingSeek=String(seek);if(Math.abs(video.currentTime-seek)>.01)video.currentTime=seek;
        }
        if(video)loadRecordingTiming(video);
        if (video) debugEvent('ui_recording_video_loadedmetadata', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('canplay', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if(video&&recordingPictureUnavailable(video))return;
        if(video&&video.dataset.recordingWantsPlay==='true'&&!recordingScrubs.has(video.dataset.recordingPlayer)&&video.paused)playRecording(video);
        if (video) debugEvent('ui_recording_video_canplay', recordingVideoDiagnostics(video));
      }, true);

      nvrGrid.addEventListener('waiting', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_waiting', recordingVideoDiagnostics(video));
        if(video&&video.dataset.recordingWantsPlay==='true'){video.dataset.recordingBuffering='true';holdRecordingFrame(video);updateRecordingPlayerControls(video,'Ładowanie fragmentu nagrania…');}
      }, true);

      nvrGrid.addEventListener('stalled', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_stalled', recordingVideoDiagnostics(video));
        if(video&&video.readyState<3&&video.dataset.recordingWantsPlay==='true')video.dataset.recordingBuffering='true';
      }, true);

      nvrGrid.addEventListener('error', (event) => {
        const video = event.target?.matches?.('video[data-recording-player]') ? event.target : null;
        if (video) debugEvent('ui_recording_video_error', recordingVideoDiagnostics(video));
        if(video?.error?.code===2&&video.dataset.recordingWantsPlay==='true'&&Number(video.dataset.recordingNetworkRetries||0)<1&&activePage==='nvr'&&!document.hidden){
          video.dataset.recordingNetworkRetries=String(Number(video.dataset.recordingNetworkRetries||0)+1);
          video.dataset.recordingLoading='true';holdRecordingFrame(video);
          playRecording(video);updateRecordingPlayerControls(video,'Ponowne łączenie z nagraniem…');return;
        }
        if(video&&video.error){
          discardRecordingScrub(video.dataset.recordingPlayer);pauseRecording(video);
          updateRecordingPlayerControls(video,'Nie można odtworzyć tego fragmentu. Naciśnij Play, aby ponowić, lub wybierz inny moment.');
        }
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

      EdgeI18n.subscribe(renderRecordingStorage);
      restoreNavState();

      async function checkPanelVersion() {
        if(document.hidden)return;
        try{
          const response=await panelFetch(panelPath('api/version'),{cache:'no-store',signal:AbortSignal.timeout(4000)});
          if(!response.ok)return;const current=await response.json();
          if(current.version===EDGE_PANEL_VERSION&&current.ui_build===EDGE_UI_BUILD)return;
          let notice=document.querySelector('[data-panel-update]');
          if(!notice){notice=document.createElement('aside');notice.dataset.panelUpdate='';notice.setAttribute('role','status');notice.style.cssText='position:fixed;bottom:12px;left:12px;right:12px;z-index:2147483646;background:#142536;color:white;padding:12px;border-radius:10px;display:flex;gap:12px;align-items:center;flex-wrap:wrap';document.body.append(notice);}
          notice.replaceChildren(document.createTextNode(`Dostępny panel ${current.version}. Odśwież kartę, aby wczytać poprawki.`));const button=document.createElement('button');button.textContent='Odśwież panel';button.onclick=()=>location.reload();notice.append(button);
        }catch(_){}
      }
      setInterval(checkPanelVersion,60000);

      async function boot() {
        void checkPanelVersion();
        debugEvent('ui_boot', {
          panel_version: EDGE_PANEL_VERSION,
          ui_build: EDGE_UI_BUILD,
          path: window.location.pathname,
        });
        await loadConfig();
        void restoreRecordingGallery();
        await Promise.all([loadPresets(), loadCameras(), loadRecordingStatus({ forceRender: true, reason: 'boot' })]);
        setTimeout(()=>{if(activePage==='home'&&!document.hidden)scheduleGalleryPrefetch();},3000);
        let eventId=new URLSearchParams(location.search).get('edge_event');try{eventId=eventId||new URLSearchParams(window.top.location.search).get('edge_event');}catch(_){}if(/^[1-9][0-9]{0,18}$/.test(eventId||''))await openAlertById(eventId);
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
    .replace("__EDGE_INTERFACE_LANGUAGE__", (Path(__file__).parent/'custom_components'/'edge'/'www'/'edge-i18n.js').read_text().replace('</script', '<\\/script'))
    .replace("__EDGE_LIVE_PRELOAD__", '<script>{for(const name of ["edge-rtc-client.js","device-diagnostics.js","edge-dvr-client.js","edge-dvr-panel.js"]){const link=document.createElement("link");link.rel="modulepreload";link.href=new URL("api/edge-rtc/"+name+"?v=__EDGE_PANEL_VERSION__",document.baseURI).href;document.head.append(link);}}</script>')
    .replace("__EDGE_NATIVE_PLAYER_DOCUMENT__", json.dumps(EDGE_RTC_PLAYER_HTML.replace('__EDGE_PANEL_VERSION__',APP_VERSION)).replace('<', '\\u003c'))
    .replace("__EDGE_PANEL_VERSION__", APP_VERSION)
    .replace("__EDGE_GALLERY_CACHE__", (Path(__file__).parent/'custom_components/edge/www/edge-gallery-cache.js').read_text())
    .replace("__EDGE_RECORDING_SURFACE__", (Path(__file__).parent/'edge-recording-surface.js').read_text())
    .replace("__EDGE_LOG_VIEW__", (Path(__file__).parent/'edge-log-view.js').read_text())
    .replace("__EDGE_UI_BUILD__", UI_BUILD)
    .replace("__EDGE_NATIVE_RTC__", "true")
    .replace("__EDGE_HA_LIVE_HELP__", "EdgeRTC sends original H264/H265 frames through authenticated Home Assistant WebSocket connections. Native live requires HTTPS and browser WebCodecs. A compatible RTSP G711 microphone uses the existing camera session. HA streaming needs no extra public media ports.")
    .replace("__EDGE_CAMERA_VENDORS__", json.dumps(CAMERA_VENDORS))
    .replace("__EDGE_CAMERA_TEMPLATES__", json.dumps({vendor: {stream: camera_stream_template(vendor, "1", stream) for stream in ("main", "sub")} for vendor in CAMERA_VENDORS}))
)


class EdgeHandler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser or ingress can close a response during navigation/cancellation.
            self.close_connection = True

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[edge-panel] {self.address_string()} {format % args}")

    def send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Edge-Version", APP_VERSION)
        self.send_header("X-Edge-UI-Build", UI_BUILD)
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"}
        headers.update(extra_headers or {})
        for key, value in headers.items():
            self.send_header(key, str(value))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def send_live_script(self, filename: str) -> None:
        versioned=parse_qs(urlparse(self.path).query).get('v')==[APP_VERSION]
        headers={"Cache-Control":"private, max-age=86400, immutable","Pragma":"","Expires":self.date_time_string(time.time()+86400)} if versioned else {}
        self.send_bytes((Path(__file__).parent/'custom_components/edge/www'/filename).read_bytes(),'text/javascript',extra_headers=headers)

    def send_json(self, payload, status: HTTPStatus = HTTPStatus.OK, extra_headers: dict | None = None, compress=False) -> None:
        started=time.monotonic()
        body=json.dumps(payload).encode('utf-8');headers=dict(extra_headers or {})
        raw_size=len(body)
        encodings=str(getattr(self,'headers',{}).get('Accept-Encoding',''))
        match=re.search(r'(?:^|,)\s*gzip\s*(?:;\s*q=([0-9.]+))?\s*(?:,|$)',encodings)
        try:accepts_gzip=bool(match and float(match[1] or 1)>0)
        except ValueError:accepts_gzip=False
        if compress:headers['Vary']='Accept-Encoding'
        if compress and len(body)>16384 and accepts_gzip:
            body=gzip.compress(body,compresslevel=1,mtime=0);headers.update({'Content-Encoding':'gzip','Vary':'Accept-Encoding'})
        if compress:append_jsonl_log(HOME_DIR/'playback.jsonl','recording_index_encoded',{'json_bytes':raw_size,'wire_bytes':len(body),'encoding':headers.get('Content-Encoding','identity'),'encode_ms':round((time.monotonic()-started)*1000)})
        self.send_bytes(body,'application/json',status,headers)

    def serve_edge_rtc_stream(self, stream_path: str, archive=False, clip=False) -> None:
        """Read-only media/timing tunnel; microphone requires the HA ACL bridge."""
        import socket
        import select
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", stream_path) or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_json({"error": "invalid_stream"}, HTTPStatus.BAD_REQUEST)
            return
        backend = None
        upgraded = False
        try:
            token = (HOME_DIR / "edge-rtc-token").read_text().strip()
            camera=next((c for c in load_config().get('cameras',[]) if c.get('enabled') and stream_path in (safe_id(c.get('id','camera'))+'_main',safe_id(c.get('id','camera'))+'_sub')),None)
            if camera and not archive and not clip:CAMERA_KEYFRAMES.request(camera['id'])
            backend = socket.create_connection(("127.0.0.1", EDGE_RTC_API_PORT), timeout=5)
            key = self.headers.get("Sec-WebSocket-Key", "")
            if not re.fullmatch(r"[A-Za-z0-9+/=]{20,32}", key):
                raise ValueError("invalid_websocket_key")
            endpoint='clip' if clip else 'archive' if archive else 'stream'
            feedback='' if archive or clip else '?network_feedback=1'
            header = f"GET /api/{endpoint}/{stream_path}{feedback} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {token}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
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
        if path in ('/api/brand/icon.png','/api/brand/logo.png','/api/brand/logo-light.png'):
            asset=Path(__file__).resolve().parent/'brand'/path.rsplit('/',1)[1]
            try:
                stat=asset.stat();etag=f'"edge-brand-{APP_VERSION}-{stat.st_size}-{stat.st_mtime_ns}"'
                headers={'Cache-Control':'public, max-age=31536000, immutable','ETag':etag,'Pragma':'','Expires':self.date_time_string(time.time()+31536000)}
                if self.headers.get('If-None-Match')==etag:
                    self.send_bytes(b'', 'image/png', HTTPStatus.NOT_MODIFIED,headers)
                else:self.send_bytes(asset.read_bytes(),'image/png',extra_headers=headers)
            except OSError:self.send_json({'error':'brand_asset_unavailable'},HTTPStatus.NOT_FOUND)
            return
        if path == "/health":
            payload = health_payload()
            self.send_json(payload, HTTPStatus.OK if payload["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/api/home-assistant/dashboard":
            self.send_json(home_assistant_dashboard_payload())
            return
        if path in ("/api/edge-rtc/client.js", "/api/edge-rtc/edge-rtc-client.js"):
            self.send_live_script("edge-rtc-client.js")
            return
        if path in ('/api/edge-rtc/edge-dvr-client.js','/api/edge-rtc/edge-dvr-panel.js'):
            self.send_live_script(path.rsplit('/',1)[1])
            return
        if path == "/api/edge-rtc/edge-pcm-worklet.js":
            self.send_live_script("edge-pcm-worklet.js")
            return
        if path.startswith('/api/edge-rtc/stream/'):
            self.serve_edge_rtc_stream(path.removeprefix("/api/edge-rtc/stream/"))
            return
        if path.startswith('/api/edge-rtc/archive/'):
            self.serve_edge_rtc_stream(path.removeprefix('/api/edge-rtc/archive/'),archive=True)
            return
        if path.startswith('/api/edge-rtc/clip/'):
            ident=path.removeprefix('/api/edge-rtc/clip/')
            if not re.fullmatch(r'[1-9][0-9]{0,18}',ident):
                self.send_json({'error':'invalid_clip'},HTTPStatus.BAD_REQUEST)
                return
            self.serve_edge_rtc_stream(ident,clip=True)
            return
        if path.startswith('/api/edge-rtc/player/'):
            self.send_bytes(EDGE_RTC_PLAYER_HTML.replace('__EDGE_PANEL_VERSION__',APP_VERSION).encode(), "text/html; charset=utf-8")
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
        if path == "/api/detection":
            self.send_json(DETECTION_SERVICE.snapshot() if DETECTION_SERVICE else {"cameras": [], "recent_events": [], "status": "starting"})
            return
        if path == "/api/detection/preview":
            try:
                if not DETECTION_SERVICE:
                    raise RuntimeError('detection_starting')
                camera_id = (parse_qs(parsed.query).get('camera') or [''])[0]
                payload, width, height, stamp = DETECTION_SERVICE.preview(camera_id)
                self.send_bytes(payload, 'image/jpeg', extra_headers={'Cache-Control': 'no-store', 'X-Edge-Frame-Width': str(width), 'X-Edge-Frame-Height': str(height), 'X-Edge-Captured-At': str(stamp)})
            except KeyError:
                self.send_json({'error': 'camera_unavailable'}, HTTPStatus.NOT_FOUND)
            except (RuntimeError, OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
                busy = isinstance(error, RuntimeError) and str(error) == 'preview_busy'
                self.send_json({'error': 'preview_busy' if busy else 'detection_preview_unavailable'}, HTTPStatus.TOO_MANY_REQUESTS if busy else HTTPStatus.SERVICE_UNAVAILABLE)
            return
        if path == "/api/events":
            try:
                query = parse_qs(parsed.query)
                result = DETECTION_SERVICE.store.page(camera_id=(query.get("camera") or [None])[0], limit=int((query.get("limit") or [200])[0]),
                    day=(query.get("day") or [None])[0], kind=(query.get("kind") or [None])[0], before=(query.get("before") or [None])[0]) if DETECTION_SERVICE else {"events": [], "has_more": False, "next_before": None}
                result['events']=[alert_payload(event) for event in result['events']]
                self.send_json(result)
            except ValueError:
                self.send_json({"error": "invalid_event_query"}, HTTPStatus.BAD_REQUEST)
            return
        if path == '/api/recording/timing':
            query=parse_qs(parsed.query)
            source=recording_file_for_key((query.get('key') or [''])[0],(query.get('name') or [''])[0])
            try:
                if source is None:raise FileNotFoundError()
                self.send_json(video_timing(source))
            except (OSError,ValueError):self.send_json({'error':'recording_timing_unavailable'},HTTPStatus.NOT_FOUND)
            return
        test_match=re.fullmatch(r'/api/recording/test/([0-9a-f]{64}[.](?:mp4|mov))',path)
        if test_match:
            target=RECORDING_COMPAT.directory/test_match[1]
            if target.is_symlink() or not target.is_file():self.send_json({'error':'recording_test_expired'},HTTPStatus.NOT_FOUND);return
            self.serve_mp4_file(target,path,target.name,'recording_codec_test_request',{},content_type='video/quicktime' if target.suffix=='.mov' else 'video/mp4')
            return
        alert_match=re.fullmatch(r'/api/alerts/([1-9][0-9]{0,18})(?:/(image|clip[.]mp4))?',path)
        if alert_match:
            self.serve_alert(int(alert_match[1]),alert_match[2]);return
        if path == "/api/events/timeline":
            try:
                query = parse_qs(parsed.query)
                camera, day = (query.get("camera") or [""])[0], (query.get("day") or [""])[0]
                if not DETECTION_SERVICE:
                    self.send_json({"error": "detection_starting"}, HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self.send_json(DETECTION_SERVICE.store.timeline(camera, day))
            except ValueError:
                self.send_json({"error": "invalid_event_query"}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/presets":
            self.send_json({"presets": load_presets()})
            return
        if path == "/api/recording/status":
            config = load_config()
            schedule_recording_ensure(config, "recording_status")
            started=time.monotonic();payload=recording_status_response(config,parse_qs(parsed.query))
            append_jsonl_log(HOME_DIR/'playback.jsonl','recording_index_ready',{'elapsed_ms':round((time.monotonic()-started)*1000),'summary':bool(payload.get('summary')),
                'files':sum(len(c.get('files',[])) for c in payload['cameras'])})
            self.send_json(payload,compress=True)
            if payload.get('summary'):RECORDING_INDEX.warm_once(config)
            return
        if path=='/api/storage/status':
            self.send_json(storage_usage_snapshot(load_config(),refresh=True));return
        if path == "/api/logs":
            self.send_json(redact_for_log(collect_panel_logs()))
            return
        if path=='/api/client-diagnostics':
            try:
                device=(parse_qs(parsed.query).get('device') or [''])[0]
                report=DEVICE_LOGS.snapshot(device)
                if parse_qs(parsed.query).get('map')==['0']:
                    self.send_json(redact_for_log(report));return
                config=load_config()
                report['network_map']={'browser_to_ha':'See selected device browser_endpoint and resource timings; proxy peer addresses are observations.',
                    'ha_to_edge':'Ingress HTTP / WebSocket or authenticated HA card bridge; configured routing, not traceroute.',
                    'edge_core':engine_runtime_status(),'hardware':hardware_inventory(),
                    'cameras':[{'id':c.get('id'),'host':c.get('host'),'manufacturer':c.get('manufacturer'),'model':c.get('model'),
                                'control_protocol':c.get('access_protocol'),'video_transport':'RTSP through shared Edge source','record_stream':c.get('record_stream'),
                                'camera_endpoint':safe_url(camera_stream(c,c.get('record_stream') or 'main'))} for c in config.get('cameras',[])]}
                self.send_json(redact_for_log(report))
            except ValueError:self.send_json({'error':'invalid_device'},HTTPStatus.BAD_REQUEST)
            return
        if path=='/api/client-diagnostics.js' or path=='/api/edge-rtc/device-diagnostics.js':
            self.send_live_script('edge-device-diagnostics.js');return
        if path == "/api/stream/capabilities":
            self.send_json(stream_capabilities())
            return
        if path == "/api/core/status":
            self.send_json(engine_runtime_status())
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
            self.serve_recording_thumbnail(path, parsed.query)
            return
        if path.startswith("/recordings/") and path.endswith(".mp4"):
            self.serve_recording(path)
            return

        self.send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = route_path(parsed.path)
        test_match=re.fullmatch(r'/api/recording/test/([0-9a-f]{64}[.](?:mp4|mov))',path)
        if test_match:
            target=RECORDING_COMPAT.directory/test_match[1]
            if not target.is_symlink() and target.is_file():
                self.serve_mp4_file(target,path,target.name,'recording_codec_test_request',{},send_body=False,content_type='video/quicktime' if target.suffix=='.mov' else 'video/mp4');return
        alert_match=re.fullmatch(r'/api/alerts/([1-9][0-9]{0,18})/clip[.]mp4',path)
        if alert_match:
            self.serve_alert(int(alert_match[1]),'clip.mp4',False);return
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
        if parsed.path.startswith('/api/edge-rtc/keyframe/'):
            try:token=(HOME_DIR/'edge-rtc-token').read_text().strip()
            except OSError:token=''
            if not token or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                self.send_json({'error':'unauthorized'},HTTPStatus.FORBIDDEN);return
            camera_id=parsed.path.removeprefix('/api/edge-rtc/keyframe/')
            accepted=bool(re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',camera_id)) and CAMERA_KEYFRAMES.request(camera_id)
            self.send_json({'accepted':accepted},HTTPStatus.ACCEPTED);return
        if parsed.path == "/api/config":
            self.save_config()
            return
        if parsed.path=='/api/client-diagnostics':
            try:
                if safe_int(self.headers.get('Content-Length'),0)>32768:self.send_json({'error':'device_report_too_large'},HTTPStatus.REQUEST_ENTITY_TOO_LARGE);return
                accepted=DEVICE_LOGS.append(self.read_body_json(),{'panel_observed_peer':self.client_address[0],
                    'reported_forwarded_for':self.headers.get('X-Forwarded-For','')[:250],
                    'reported_forwarded_proto':self.headers.get('X-Forwarded-Proto','')[:20]})
                self.send_json({'ok':accepted},HTTPStatus.OK if accepted else HTTPStatus.TOO_MANY_REQUESTS)
            except (ValueError,TypeError):self.send_json({'error':'invalid_device_report'},HTTPStatus.BAD_REQUEST)
            return
        if parsed.path=='/api/recording/prepare':
            try:
                payload=self.read_body_json()
                if not isinstance(payload,dict):raise ValueError('invalid_recording')
                source=recording_file_for_key(str(payload.get('key') or ''),str(payload.get('name') or ''))
                if source is None:self.send_json({'error':'recording_not_found'},HTTPStatus.NOT_FOUND);return
                target=RECORDING_COMPAT.prepare(source,allow_passthrough=True,allow_small_offset=True)
                self.send_json({'ready':True,'size_bytes':target.stat().st_size})
            except (ValueError,json.JSONDecodeError):self.send_json({'error':'invalid_recording'},HTTPStatus.BAD_REQUEST)
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
                append_jsonl_log(HOME_DIR/'playback.jsonl','recording_prepare_error',{'error':type(error).__name__+': '+str(error)})
                self.send_json({'error':'recording_prepare_busy'},HTTPStatus.SERVICE_UNAVAILABLE,{'Retry-After':'1'})
            return
        if parsed.path=='/api/recording/test':
            try:
                payload=self.read_body_json()
                if not isinstance(payload,dict):raise ValueError('invalid_recording_test')
                source=recording_file_for_key(str(payload.get('key') or ''),str(payload.get('name') or ''))
                if source is None:self.send_json({'error':'recording_not_found'},HTTPStatus.NOT_FOUND);return
                target,report=RECORDING_COMPAT.test_clip(source,str(payload.get('video') or 'source'),str(payload.get('audio') or 'aac'),payload.get('start',0))
                self.send_json({'url':'api/recording/test/'+target.name,'report':report})
            except (ValueError,TypeError,OverflowError,json.JSONDecodeError) as error:
                write_debug_event('recording_codec_test_invalid',{'error':str(error)})
                self.send_json({'error':str(error) or 'invalid_recording_test'},HTTPStatus.BAD_REQUEST)
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
                write_debug_event('recording_codec_test_error',{'error':str(error)})
                self.send_json({'error':str(error),'hint':'One short test at a time. Normal recordings are unchanged.'},HTTPStatus.SERVICE_UNAVAILABLE,{'Retry-After':'2'})
            return
        if parsed.path == '/api/storage/cleanup':
            try:
                self.read_body_json()
                config = load_config()
                report = cleanup_disposable_storage(config, wait_for_thumbnail=True)
                write_debug_event('recording_storage_maintenance',report)
                self.send_json({'cleanup':report,'storage':storage_usage_payload(config)})
            except (OSError,ValueError) as error:
                self.send_json({'error':str(error)},HTTPStatus.BAD_REQUEST)
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
        if parsed.path == '/api/camera-gop':
            try:
                payload=self.read_body_json();index=int(payload['index']);seconds=int(payload['seconds']);config=load_config()
                if index<0 or index>=len(config['cameras']) or seconds not in (1,2):raise ValueError('invalid_camera_or_gop')
                camera=config['cameras'][index]
                if camera.get('vendor')=='hikvision':
                    profile=camera.get('record_stream') or 'main';current=parse_stream_config(isapi_request(camera,'GET',f'/ISAPI/Streaming/channels/{stream_channel(camera,profile)}'))
                    fps=current.get('video',{}).get('fps');frames=int(keyframe_interval_from_fps(str(fps),seconds));verified=update_stream_config(camera,profile,{'video':{'keyframe_interval':frames}})
                    if int(verified.get('video',{}).get('keyframe_interval',0))!=frames:raise ValueError('gop_camera_readback_mismatch')
                    result={'status':'saved','seconds':seconds,'frames':frames,'fps':frames/seconds,'profile':profile}
                else:
                    result=set_onvif_gop(camera,seconds,CAMERA_KEYFRAMES.resolve,lambda *args:onvif_request(*args,timeout=3))
                result.pop('encoder_token',None);write_debug_event('camera_gop_saved',{'camera_id':camera.get('id'),**result,'video_transcoded':False});self.send_json(result)
            except (ValueError,KeyError,TypeError,OSError,RuntimeError,ET.ParseError):
                write_debug_event('camera_gop_unavailable',{'action':'explicit_gop_write','readback_confirmed':False})
                self.send_json({'error':'Kamera nie udostępniła lub nie potwierdziła GOP przez to API. Sprawdź GOP w panelu kamery; pozostałe ustawienia profilu są zachowywane.'},HTTPStatus.BAD_REQUEST)
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
            if str(event).startswith(('ui_recording_','ui_codec_')):
                append_jsonl_log(HOME_DIR/'playback.jsonl',str(event),{'ui':payload})
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
            if DETECTION_SERVICE:
                DETECTION_SERVICE.apply(saved_payload)
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
            append_jsonl_log(HOME_DIR/'camera-discovery.jsonl','camera_capabilities_detected',{
                'camera_id':camera.get('id'),'device':result.get('device'),'streams':result.get('streams'),
                'profiles':result.get('profiles'),'channels':result.get('channels'),
                'scope':'Profiles reported by the camera; absence does not prove unsupported features.'})
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

    def serve_alert(self, ident, kind=None, send_body=True):
        if not 0<ident<=9223372036854775807:self.send_json({'error':'invalid_alert_id'},HTTPStatus.BAD_REQUEST);return
        event=DETECTION_SERVICE.store.get(ident) if DETECTION_SERVICE else None
        if not event:self.send_json({'error':'alert_not_found'},HTTPStatus.NOT_FOUND);return
        if kind is None:self.send_json(alert_payload(event));return
        if kind=='image':
            target=alert_image_path(ident)
            if not target.is_file() or target.is_symlink():self.send_json({'error':'alert_image_expired'},HTTPStatus.NOT_FOUND);return
            self.send_bytes(target.read_bytes(),'image/jpeg');return
        target=ALERT_SERVICE.path(ident) if ALERT_SERVICE else AlertMediaService.directory(load_config())/f'event-{ident}.mp4'
        if not target.is_file() or target.is_symlink():self.send_json({'error':'alert_clip_pending_or_expired'},HTTPStatus.NOT_FOUND);return
        target=self.compatible_recording(target)
        if target is None:return
        self.serve_mp4_file(target,self.path,f'alert-{ident}.mp4','alert_clip_request',{'id':ident},send_body=send_body,cache_control='private, max-age=60')

    def compatible_recording(self, target):
        query=parse_qs(urlparse(self.path).query)
        if query.get('download')==['1'] or query.get('compat')!=['1']:return target
        # Older open tabs keep their previous byte representation until reload.
        modern=query.get('container')==['aac-clock-v6']
        try:return RECORDING_COMPAT.prepare(target,allow_passthrough=modern or query.get('container')==['direct-clock-v5'],allow_small_offset=modern,legacy=not modern)
        except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
            write_debug_event('recording_compat_error',{'filename':target.name,'error':str(error)})
            append_jsonl_log(HOME_DIR/'playback.jsonl','recording_compat_error',{'filename':target.name,'error':type(error).__name__+': '+str(error)})
            self.send_json({'error':'recording_compat_unavailable'},HTTPStatus.SERVICE_UNAVAILABLE,{'Retry-After':'1'})
            return None

    def serve_recording_thumbnail(self, request_path: str, query: str = '') -> None:
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
            requested = parse_qs(query).get('at', [])
            seek = min(86400, max(0, safe_int(requested[0], 0))) if requested else None
            thumb = ensure_recording_thumbnail(key, source, filename, seek_seconds=seek)
            data = thumb.read_bytes()
            os.utime(thumb, None)  # Access time for bounded LRU; no change to image data.
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            payload = {
                "key": key,
                "filename": Path(filename).name,
                "error": str(error),
                "type": type(error).__name__,
            }
            if str(error)=='thumbnail_generation_busy':
                self.send_json({'error':'thumbnail_generation_busy'},HTTPStatus.TOO_MANY_REQUESTS,{'Retry-After':'1'});return
            append_jsonl_log(RECORDING_THUMB_LOG_PATH, "thumbnail_request_error", payload)
            write_debug_event("recording_thumbnail_error", payload)
            self.send_json({"error": str(error)}, HTTPStatus.TOO_MANY_REQUESTS if str(error) == 'thumbnail_generation_busy' else HTTPStatus.BAD_GATEWAY)
            return
        self.send_bytes(data, "image/jpeg")

    def serve_mp4_file(
        self,
        target: Path,
        request_path: str,
        disposition_name: str,
        debug_event_name: str,
        debug_payload: dict,
        send_body: bool = True,
        cache_control: str = "private, max-age=86400, immutable",
        content_type: str = 'video/mp4',
    ) -> None:
        with target.open("rb") as handle:
            stat=os.fstat(handle.fileno());file_size=stat.st_size
            if file_size <= 0:
                self.send_json({"error": "recording_empty"}, HTTPStatus.NOT_FOUND)
                return

            immutable_copy=target.parent==RECORDING_COMPAT.directory and re.fullmatch(r'[0-9a-f]{64}\.(?:mp4|mov)',target.name)
            identity=f'{stat.st_dev}:{stat.st_ino}:{stat.st_size}'
            if not immutable_copy:identity+=f':{stat.st_mtime_ns}:{stat.st_ctime_ns}'
            etag='"'+hashlib.sha256(identity.encode()).hexdigest()[:32]+'"'
            modified=formatdate(stat.st_mtime,usegmt=True)
            matches=self.headers.get('If-None-Match','').split(',')
            if any(value.strip()=='*' or value.strip().removeprefix('W/')==etag for value in matches):
                self.send_response(HTTPStatus.NOT_MODIFIED);self.send_header('ETag',etag);self.send_header('Last-Modified',modified);self.send_header('Cache-Control',cache_control);self.end_headers()
                append_jsonl_log(HOME_DIR/'playback.jsonl','recording_http_cache_hit',{'filename':target.name,'method':self.command,**debug_payload});return
            range_header = self.headers.get("Range", "")
            if_range=self.headers.get('If-Range')
            if range_header and if_range:
                valid=if_range==etag
                if not valid:
                    try:valid=int(stat.st_mtime)<=int(parsedate_to_datetime(if_range).timestamp())
                    except (ValueError,TypeError,OverflowError):valid=False
                if not valid:range_header=''
            start = 0
            end = file_size - 1
            status = HTTPStatus.OK

            if range_header.startswith("bytes="):
                requested = range_header.removeprefix("bytes=").split("-", 1)
                try:
                    if not requested[0]:
                        suffix=int(requested[1])
                        if suffix<=0:raise ValueError('empty_suffix')
                        start=max(0,file_size-suffix);end=file_size-1
                    else:
                        start=int(requested[0]);end=int(requested[1]) if len(requested)>1 and requested[1] else file_size-1
                        if start<0 or start>=file_size or end<start:raise ValueError('unsatisfied_range')
                        end=min(end,file_size-1)
                    status = HTTPStatus.PARTIAL_CONTENT
                except (ValueError,IndexError):
                    self.send_json({"error": "invalid_range"}, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,{'Content-Range':f'bytes */{file_size}'})
                    return

            length = end - start + 1
            safe_disposition = disposition_name.replace('"', "_")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header('ETag',etag)
            self.send_header('Last-Modified',modified)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            disposition="attachment" if parse_qs(urlparse(getattr(self,"path",request_path)).query).get("download")==["1"] else "inline"
            self.send_header("Content-Disposition", f'{disposition}; filename="{safe_disposition}"')
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

            handle.seek(start)
            remaining = length;transfer_started=time.monotonic();disconnected=False
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    disconnected=True
                    break
                remaining -= len(chunk)
            append_jsonl_log(HOME_DIR/'playback.jsonl','recording_transfer',{'path':request_path,'range':range_header,
                'elapsed_ms':round((time.monotonic()-transfer_started)*1000),'bytes_sent':length-remaining,'requested_bytes':length,
                'client_closed':disconnected})

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
        if recording_cache_budget(config) <= 0:
            self.send_json({'error':'recording_cache_disabled'}, HTTPStatus.NOT_FOUND)
            return
        with RECORDING_CACHE_LOCK:
            RECORDING_CACHE_ACCESS[target] = time.monotonic()
        os.utime(target, None)
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
        target=self.compatible_recording(target)
        if target is None:return
        self.serve_mp4_file(
            target,
            request_path,
            safe_name,
            "recording_file_request",
            {"key": key, "filename": safe_name},
            send_body=send_body,
        )

def migrate_recording_policy():
    with CONFIG_LOCK:
        raw=read_json(CONFIG_PATH,None)
        if not isinstance(raw,dict) or not isinstance(raw.get('cameras'),list) or not isinstance(raw.get('nvr'),dict) or raw['nvr'].get('browser_playback')!='h264':return False
        backup_config();raw['nvr']['browser_playback']='copy';write_json(CONFIG_PATH,raw)
    write_debug_event('recording_policy_migrated',{'previous':'h264','current':'copy','reason':'Codec tests must not transcode the continuous recorder','camera_settings_changed':False})
    return True


def main() -> None:
    global DETECTION_SERVICE, ALERT_SERVICE
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    STREAM_LIST_DIR.mkdir(parents=True, exist_ok=True)
    RECORDING_THUMB_DIR.mkdir(parents=True, exist_ok=True)
    RECORDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    clear_legacy_override_files()
    migrate_recording_policy()
    write_debug_event("boot_start", {
        "server_version": EdgeHandler.server_version,
        "home_dir": str(HOME_DIR),
        "data_dir": str(DATA_DIR),
        "config_path": str(CONFIG_PATH),
        "port": PORT,
    })
    write_debug_event('release_features',{'version':APP_VERSION,'features':APP_RELEASE_FEATURES,
                      'hardware':hardware_inventory(),'recordings_changed':False,'camera_codec_settings_changed':False,
                      'edge_rtc':'Sources stay connected on the server; viewers share its bounded keyframe buffer.'})
    print(f'[edge-panel] {APP_VERSION}: EdgeRTC-only Live / DVR, bounded AAC playback, native configuration migration and shared recording source. Original video/FPS unchanged.')
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
        # Previews decode in the browser; avoid competing background FFmpeg jobs.
        DETECTION_SERVICE = DetectionService(HOME_DIR, onvif_request,
            lambda camera, index: edge_rtsp_url(camera, index, camera.get("record_stream") or "main"),
            write_json, on_event=enqueue_alert)
        ALERT_SERVICE=AlertMediaService(DETECTION_SERVICE.store,load_config,alert_source_entries,save_alert_image,save_alert_fallback_image,write_debug_event)
        DETECTION_SERVICE.apply(config)
        CAMERA_KEYFRAMES.warm()
    except Exception as error:
        write_debug_event("boot_refresh_status_error", {"error": str(error), "type": type(error).__name__})
        raise
    server = ThreadingHTTPServer(("0.0.0.0", PORT), EdgeHandler)
    print(f"[edge-panel] listening on 0.0.0.0:{PORT}")
    write_debug_event("boot_server_listening", {"listen": f"0.0.0.0:{PORT}"})
    try:
        server.serve_forever()
    finally:
        RECORDING_SOURCE_RETRY_STOP.set()
        if ALERT_SERVICE:
            ALERT_SERVICE.close()
        if DETECTION_SERVICE:
            DETECTION_SERVICE.close()


if __name__ == "__main__":
    main()

