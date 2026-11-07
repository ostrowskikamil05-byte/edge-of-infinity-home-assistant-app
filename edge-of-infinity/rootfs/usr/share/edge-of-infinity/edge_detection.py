"""Camera events and bounded local detection, independent of the recorder.

NanoDet postprocessing follows OpenCV Zoo (Apache-2.0); see models/LICENSE.
Only the shared Edge RTSP relay is read for analysis. No cloud inference.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

KINDS = ('motion', 'person', 'vehicle')
MODEL_PATH = Path(os.environ.get('EDGE_DETECTION_MODEL', str(Path(__file__).parent / 'models/nanodet.onnx')))
MODEL_SHA256 = '4b82da9944b88577175ee23a459dce2e26e6e4be573def65b1055dc2d9720186'
EVENT_ACTION = 'http://www.onvif.org/ver10/events/wsdl/'
WSN_ACTION = 'http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/'
ZONE_FIELDS = ('detection_zone_enabled', 'detection_zone_left', 'detection_zone_top', 'detection_zone_right', 'detection_zone_bottom')


def zone_settings(camera):
    bounds, valid = [], True
    for field, default in zip(ZONE_FIELDS[1:], (0, 0, 1, 1)):
        try:
            value = float(camera.get(field, default))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('invalid_zone')
            bounds.append(round(value, 6))
        except (TypeError, ValueError):
            bounds.append(0)
            valid = False
    valid = valid and bounds[2] - bounds[0] >= .005 - 1e-9 and bounds[3] - bounds[1] >= .005 - 1e-9
    return {'enabled': camera.get(ZONE_FIELDS[0]) is True, 'valid': bool(valid), 'bounds': bounds if valid else [0, 0, 0, 0]}


def zone_objects(objects, camera):
    """Use the object's bottom-center point in original-image coordinates."""
    zone = zone_settings(camera)
    if not zone['enabled']:
        return objects
    if not zone['valid']:
        return []
    left, top, right, bottom = zone['bounds']
    return [obj for obj in objects if left - 1e-6 <= (obj['box'][0] + obj['box'][2]) / 2 <= right + 1e-6
            and top - 1e-6 <= obj['box'][3] <= bottom + 1e-6]


def camera_event_signature(camera):
    return tuple(camera.get(key) for key in ('id', 'host', 'username', 'password', 'onvif_url', 'enabled', 'events_enabled'))


class AnalysisFrame:
    """416-pixel model input with an exact rectangle for the unpadded scene."""
    def __init__(self, pixels, width=416, height=416):
        if len(pixels) != 416 * 416 * 3 or not 1 <= width <= 416 or not 1 <= height <= 416:
            raise RuntimeError('invalid_analysis_frame')
        self.pixels, self.width, self.height = pixels, width, height
        self.left, self.top = (416 - width) // 2, (416 - height) // 2

    def scene_objects(self, objects):
        result = []
        for obj in objects:
            x1, y1, x2, y2 = obj['box']
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                continue
            box = [max(0, min(1, (x1 * 416 - self.left) / self.width)),
                   max(0, min(1, (y1 * 416 - self.top) / self.height)),
                   max(0, min(1, (x2 * 416 - self.left) / self.width)),
                   max(0, min(1, (y2 * 416 - self.top) / self.height))]
            if box[2] > box[0] and box[3] > box[1]:
                result.append({**obj, 'box': [round(value, 6) for value in box]})
        return result

    def jpeg(self):
        import cv2
        import numpy as np
        image = np.frombuffer(self.pixels, np.uint8).reshape(416, 416, 3)
        scene = image[self.top:self.top + self.height, self.left:self.left + self.width]
        ok, encoded = cv2.imencode('.jpg', scene, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError('preview_encoding_failed')
        return encoded.tobytes()


def settings(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    def number(name, default, lower, upper):
        try:
            value = float(raw.get(name, default))
            return max(lower, min(upper, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default
    return {'camera_events_enabled': raw.get('camera_events_enabled') is True,
            'local_enabled': raw.get('local_enabled') is True,
            'max_fps': number('max_fps', 1, .2, 3),
            'confidence': number('confidence', .45, .1, .95),
            'hold_seconds': number('hold_seconds', 10, 1, 120)}


def local_name(node):
    return node.tag.rsplit('}', 1)[-1]


def first(root, name):
    return next((n for n in root.iter() if local_name(n) == name), None)


def node_text(root, name):
    node = first(root, name)
    return (node.text or '').strip() if node is not None else ''


def notifications(root):
    """Normalize common ONVIF topics without claiming unavailable smart events."""
    result = []
    for notice in root.iter():
        if local_name(notice) != 'NotificationMessage':
            continue
        topic = node_text(notice, 'Topic')[:512]
        data = first(notice, 'Data')
        if data is None:
            continue
        items = {n.get('Name', ''): n.get('Value', '') for n in data.iter() if local_name(n) == 'SimpleItem'}
        explicit = ' '.join(value for key, value in items.items() if 'type' in key.lower() or 'class' in key.lower())
        kind = classify(explicit) or classify(topic + ' ' + ' '.join(items))
        if not kind:
            continue
        values = [v.strip().lower() for v in items.values() if v.strip().lower() in ('true', 'false', '1', '0', 'active', 'inactive')]
        if not values:
            continue
        active = any(v in ('true', '1', 'active') for v in values)
        message = next((n for n in notice.iter() if n.get('UtcTime')), None)
        timestamp = time.time()
        if message is not None:
            try:
                camera_time = datetime.fromisoformat(message.get('UtcTime').replace('Z', '+00:00')).timestamp()
                # Bad camera clocks must not place events outside the current recording day.
                if abs(camera_time - timestamp) < 300:
                    timestamp = camera_time
            except (ValueError, OverflowError):
                pass
        result.append({'kind': kind, 'active': active, 'timestamp': timestamp, 'topic': topic})
    return result


def classify(description):
    description = description.lower()
    if any(x in description for x in ('person', 'human', 'people')):
        return 'person'
    if any(x in description for x in ('vehicle', 'automobile', 'caralarm')):
        return 'vehicle'
    if any(x in description for x in ('motion', 'linecross', 'intrusion', 'fielddetect', 'vmd')):
        return 'motion'
    return None


def day_bounds(day):
    """Recorder-local calendar boundaries, including 23/25-hour DST days."""
    if not isinstance(day, str) or len(day) != 10:
        raise ValueError('invalid_event_day')
    start = datetime.strptime(day, '%Y-%m-%d')
    if start.strftime('%Y-%m-%d') != day:
        raise ValueError('invalid_event_day')
    try:
        return start.timestamp(), (start + timedelta(days=1)).timestamp()
    except (OverflowError, OSError) as error:
        raise ValueError('invalid_event_day') from error


class EventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS edge_events (id INTEGER PRIMARY KEY AUTOINCREMENT, camera_id TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, timestamp REAL NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS edge_events_time ON edge_events(timestamp)')
            db.execute('CREATE INDEX IF NOT EXISTS edge_events_camera_time ON edge_events(camera_id,timestamp)')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, camera_id, kind, source, timestamp, data):
        with self.lock, self.connect() as db:
            cursor = db.execute('INSERT INTO edge_events(camera_id,kind,source,timestamp,data) VALUES(?,?,?,?,?)',
                                (camera_id, kind, source, timestamp, json.dumps(data, allow_nan=False)))
            return cursor.lastrowid

    def recent(self, camera_id=None, limit=200):
        return self.page(camera_id=camera_id, limit=limit)['events']

    def page(self, camera_id=None, limit=200, day=None, kind=None, before=None):
        """Bounded keyset pagination stays stable when new events arrive."""
        limit = max(1, min(1000, int(limit)))
        clauses, parameters = [], []
        if camera_id is not None:
            clauses.append('camera_id=?')
            parameters.append(str(camera_id))
        if day:
            start, end = day_bounds(day)
            clauses.append('timestamp>=? AND timestamp<?')
            parameters.extend((start, end))
        if kind:
            if kind not in KINDS:
                raise ValueError('invalid_event_kind')
            clauses.append('kind=?')
            parameters.append(kind)
        if before is not None:
            before = int(before)
            if not 0 < before <= 9223372036854775807:
                raise ValueError('invalid_event_cursor')
            clauses.append('id<?')
            parameters.append(before)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT id,camera_id,kind,source,timestamp,data FROM edge_events' + where + ' ORDER BY id DESC LIMIT ?', (*parameters, limit + 1)).fetchall()
        has_more = len(rows) > limit
        result = []
        for ident, camera, kind, source, stamp, data in rows[:limit]:
            dt = datetime.fromtimestamp(stamp)
            midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            result.append({**json.loads(data), 'id': ident, 'camera_id': camera, 'kind': kind, 'source': source, 'timestamp': stamp,
                           'day': dt.strftime('%Y-%m-%d'), 'day_seconds': stamp - midnight, 'time_label': dt.strftime('%H:%M:%S')})
        return {'events': result, 'has_more': has_more, 'next_before': result[-1]['id'] if has_more else None}

    def timeline(self, camera_id, day):
        """Summarize every retained event in five-minute bins, without media I/O."""
        if not camera_id or not day:
            raise ValueError('camera_and_day_required')
        start, end = day_bounds(day)
        with self.lock, self.connect() as db:
            rows = db.execute('SELECT CAST((timestamp-?)/300 AS INTEGER),kind,COUNT(*),MIN(timestamp) FROM edge_events WHERE camera_id=? AND timestamp>=? AND timestamp<? GROUP BY 1,2 ORDER BY 1,2',
                              (start, str(camera_id), start, end)).fetchall()
        markers = {}
        for slot, kind, count, stamp in rows:
            if kind not in KINDS:
                continue
            marker = markers.setdefault(slot, {'slot': slot, 'count': 0, 'kinds': [], 'timestamp': stamp})
            marker['count'] += count
            marker['kinds'].append(kind)
            marker['timestamp'] = min(marker['timestamp'], stamp)
        for marker in markers.values():
            marker['day_seconds'] = marker['timestamp'] - start
            marker['time_label'] = datetime.fromtimestamp(marker['timestamp']).strftime('%H:%M:%S')
        return {'camera_id': str(camera_id), 'day': day, 'day_start_ts': start, 'day_total_seconds': end - start,
                'midpoint_label': datetime.fromtimestamp((start + end) / 2).strftime('%H:%M'),
                'count': sum(marker['count'] for marker in markers.values()), 'markers': list(markers.values())}

    def prune(self, days):
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM edge_events WHERE timestamp < ?', (time.time() - max(1, int(days)) * 86400,))
            db.execute('DELETE FROM edge_events WHERE id IN (SELECT id FROM edge_events ORDER BY id DESC LIMIT -1 OFFSET 20000)')


class NanoDet:
    """Small packaged COCO model; one CPU thread, class-aware NMS."""
    def __init__(self, model_path=MODEL_PATH):
        import cv2
        import numpy as np
        model_path = Path(model_path)
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise RuntimeError('model_checksum_failed')
        self.cv, self.np = cv2, np
        cv2.setNumThreads(1)
        self.net = cv2.dnn.readNet(str(model_path))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    def infer(self, pixels, confidence=.45):
        cv, np = self.cv, self.np
        image = np.frombuffer(pixels, np.uint8).reshape(416, 416, 3)
        image = (image.astype(np.float32) - np.array([103.53, 116.28, 123.675], np.float32)) / np.array([57.375, 57.12, 58.395], np.float32)
        self.net.setInput(cv.dnn.blobFromImage(image))
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        detections = []
        classes = {0: 'person', 1: 'vehicle', 2: 'vehicle', 3: 'vehicle', 5: 'vehicle', 7: 'vehicle'}
        candidates = {index: [] for index in classes}
        for stride, scores, regressions in zip((8, 16, 32, 64), outputs[::2], outputs[1::2]):
            scores, regressions = scores[0], regressions[0]
            side = int(round(math.sqrt(len(scores))))
            x, y = np.meshgrid(np.arange(side), np.arange(side))
            anchors = np.column_stack((x.reshape(-1), y.reshape(-1))) * stride + .5 * (stride - 1)
            logits = regressions.reshape(-1, 8)
            exp = np.exp(logits - logits.max(axis=1, keepdims=True))
            distances = ((exp / exp.sum(axis=1, keepdims=True)) @ np.arange(8)).reshape(-1, 4) * stride
            boxes = np.column_stack((anchors[:, 0] - distances[:, 0], anchors[:, 1] - distances[:, 1], anchors[:, 0] + distances[:, 2], anchors[:, 1] + distances[:, 3])).clip(0, 416)
            for index in classes:
                for position in np.flatnonzero(scores[:, index] >= confidence):
                    candidates[index].append((boxes[position], float(scores[position, index])))
        for index, rows in candidates.items():
            if not rows:
                continue
            boxes = [list(map(float, (b[0], b[1], b[2] - b[0], b[3] - b[1]))) for b, _ in rows]
            scores = [score for _, score in rows]
            for selected in np.asarray(cv.dnn.NMSBoxes(boxes, scores, confidence, .6)).reshape(-1):
                box, score = rows[int(selected)]
                detections.append({'kind': classes[index], 'class_id': index, 'score': round(score, 4), 'box': [round(float(v) / 416, 4) for v in box]})
        return sorted(detections, key=lambda item: item['score'], reverse=True)[:32]


def capture_frame(proxy_url):
    # One short-lived local reader at a time. Decode keyframes only on thin clients.
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'info', '-threads', '1', '-filter_threads', '1', '-skip_frame', 'nokey', '-rtsp_transport', 'tcp',
               '-i', proxy_url, '-an', '-threads', '1', '-vf', 'scale=416:416:force_original_aspect_ratio=decrease,format=bgr24,showinfo,pad=416:416:(ow-iw)/2:(oh-ih)/2:color=black',
               '-frames:v', '1', '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
    geometry = re.search(rb'\[Parsed_showinfo_\d+[^\]]*\].*\bs:(\d+)x(\d+)\b', result.stderr)
    if result.returncode or len(result.stdout) != 416 * 416 * 3 or not geometry:
        raise RuntimeError('shared_source_frame_unavailable')
    return AnalysisFrame(result.stdout, int(geometry[1]), int(geometry[2]))


class DetectionService:
    def __init__(self, home, soap, proxy_url, publish):
        self.home, self.soap, self.proxy_url, self.publish = Path(home), soap, proxy_url, publish
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.config = {'cameras': [], 'detection': settings()}
        self.store = EventStore(self.home / 'events.sqlite')
        self.workers, self.states, self.status, self.reported = {}, {}, {}, {}
        self.capture_lock, self.preview_lock = threading.Lock(), threading.Lock()
        self.frames = {}
        self.model, self.model_error = None, ''
        self.analysis_ms = 0
        self.thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self.thread.start()
        self.publisher = threading.Thread(target=self._publish_loop, daemon=True)
        self.publisher.start()

    def apply(self, config):
        current = copy.deepcopy(config)
        current['detection'] = settings(config.get('detection'))
        desired = {str(c['id']): c for c in current.get('cameras', []) if c.get('enabled') and c.get('events_enabled') and current['detection']['camera_events_enabled']}
        with self.lock:
            previous = {str(c['id']): c for c in self.config.get('cameras', [])}
            for camera in current.get('cameras', []):
                ident = str(camera['id'])
                if previous.get(ident) != camera:
                    self._clear(ident, 'local')
                    self.frames.pop(ident, None)
                if camera_event_signature(previous.get(ident) or {}) != camera_event_signature(camera):
                    self.reported.pop((ident, 'camera'), None)
            for ident in list(self.frames):
                if ident not in {str(camera['id']) for camera in current.get('cameras', [])}:
                    self.frames.pop(ident, None)
            self.config = current
            for ident, (camera, stop) in list(self.workers.items()):
                if ident not in desired or camera_event_signature(desired[ident]) != camera_event_signature(camera):
                    stop.set()
                    del self.workers[ident]
                    self._clear(ident, 'camera')
            for ident, camera in desired.items():
                if ident not in self.workers:
                    stop = threading.Event()
                    self.workers[ident] = (camera, stop)
                    threading.Thread(target=self._camera_loop, args=(camera, stop), daemon=True).start()
            allowed = {str(c['id']) for c in current.get('cameras', []) if c.get('enabled') and c.get('local_detection_enabled') and current['detection']['local_enabled']}
            for ident, source, kind in list(self.states):
                if source == 'local' and ident not in allowed:
                    self.states.pop((ident, source, kind), None)

    def _clear(self, ident, source):
        for key in list(self.states):
            if key[:2] == (str(ident), source):
                self.states.pop(key, None)

    def record(self, camera_id, kind, source, active, timestamp=None, **data):
        if kind not in KINDS or source not in ('camera', 'local'):
            return
        stamp = timestamp if timestamp is not None else time.time()
        key = (str(camera_id), source, kind)
        with self.lock:
            self.reported.setdefault((str(camera_id), source), set()).add(kind)
            previous = self.states.get(key) or {}
            if active and not previous.get('active'):
                self.store.add(str(camera_id), kind, source, stamp, data)
            self.states[key] = {'active': bool(active), 'last_seen': stamp, **data}

    def _set_status(self, ident, source, value):
        with self.lock:
            self.status[(str(ident), source)] = value
            if value not in ('connected', 'running'):
                self._clear(str(ident), source)

    def _proxy(self, camera, index):
        proxy = self.proxy_url(camera, index)
        parsed = urlparse(proxy or '')
        if parsed.scheme != 'rtsp' or parsed.hostname != '127.0.0.1' or parsed.username is not None or parsed.password is not None or not parsed.port:
            raise RuntimeError('shared_source_required')
        return proxy

    def _remember_frame(self, camera, frame, captured_at):
        ident = str(camera['id'])
        self.frames.pop(ident, None)
        self.frames[ident] = (copy.deepcopy(camera), frame, captured_at)
        while len(self.frames) > 4:
            self.frames.pop(next(iter(self.frames)))

    def preview(self, camera_id):
        """One explicit, bounded preview request; reuse recent analysis frames."""
        if not self.preview_lock.acquire(blocking=False):
            raise RuntimeError('preview_busy')
        try:
            with self.lock:
                match = next(((i, copy.deepcopy(c)) for i, c in enumerate(self.config.get('cameras', [])) if str(c['id']) == camera_id and c.get('enabled')), None)
                cached = self.frames.get(camera_id)
            if not match:
                raise KeyError('camera_unavailable')
            index, camera = match
            if cached and cached[0] == camera and time.time() - cached[2] < 5:
                frame, stamp = cached[1:]
            else:
                if not self.capture_lock.acquire(timeout=1):
                    raise RuntimeError('preview_busy')
                try:
                    frame, stamp = capture_frame(self._proxy(camera, index)), time.time()
                finally:
                    self.capture_lock.release()
                with self.lock:
                    if camera not in self.config.get('cameras', []):
                        raise RuntimeError('configuration_changed')
                    self._remember_frame(camera, frame, stamp)
            jpeg = frame.jpeg()
            with self.lock:
                if camera not in self.config.get('cameras', []):
                    raise RuntimeError('configuration_changed')
            return jpeg, frame.width, frame.height, stamp
        finally:
            self.preview_lock.release()

    def _camera_loop(self, camera, stopped):
        ident, delay = str(camera['id']), 5
        def status(value):
            with self.lock:
                if not stopped.is_set() and self.workers.get(ident, (None, None))[1] is stopped:
                    self._set_status(ident, 'camera', value)
        while not self.stop.is_set() and not stopped.is_set():
            subscription, refs = '', ''
            try:
                status('connecting')
                caps = self.soap(camera, camera.get('onvif_url') or '', 'http://www.onvif.org/ver10/device/wsdl/GetCapabilities', '<tds:GetCapabilities><tds:Category>Events</tds:Category></tds:GetCapabilities>')
                events = first(caps, 'Events')
                endpoint = (events.get('XAddr') or node_text(events, 'XAddr')) if events is not None else ''
                if not endpoint:
                    status('unsupported')
                    stopped.wait(60)
                    continue
                try:
                    properties = self.soap(camera, endpoint, EVENT_ACTION + 'EventPortType/GetEventPropertiesRequest', '<tev:GetEventProperties/>')
                    topics = first(properties, 'TopicSet')
                    if topics is not None:
                        with self.lock:
                            if not stopped.is_set():
                                self.reported[(ident, 'camera')] = {kind for node in topics.iter() if (kind := classify(local_name(node))) is not None}
                except (OSError, ValueError, RuntimeError, ET.ParseError):
                    pass
                response = self.soap(camera, endpoint, EVENT_ACTION + 'EventPortType/CreatePullPointSubscriptionRequest', '<tev:CreatePullPointSubscription><tev:InitialTerminationTime>PT60S</tev:InitialTerminationTime></tev:CreatePullPointSubscription>')
                reference = first(response, 'SubscriptionReference')
                subscription = urljoin(endpoint, node_text(reference, 'Address')) if reference is not None else ''
                if not subscription:
                    raise RuntimeError('subscription_missing')
                parameters = first(reference, 'ReferenceParameters')
                if parameters is not None:
                    for child in parameters:
                        child.set('{http://www.w3.org/2005/08/addressing}IsReferenceParameter', 'true')
                refs = ''.join(ET.tostring(child, encoding='unicode') for child in parameters) if parameters is not None else ''
                renewed = time.monotonic()
                while not self.stop.is_set() and not stopped.is_set():
                    if time.monotonic() - renewed > 35:
                        self.soap(camera, subscription, WSN_ACTION + 'RenewRequest', '<wsnt:Renew><wsnt:TerminationTime>PT60S</wsnt:TerminationTime></wsnt:Renew>', refs)
                        renewed = time.monotonic()
                    response = self.soap(camera, subscription, EVENT_ACTION + 'PullPointSubscription/PullMessagesRequest', '<tev:PullMessages><tev:Timeout>PT5S</tev:Timeout><tev:MessageLimit>32</tev:MessageLimit></tev:PullMessages>', refs)
                    if stopped.is_set() or self.stop.is_set():
                        break
                    with self.lock:
                        if stopped.is_set() or self.stop.is_set():
                            break
                        self._set_status(ident, 'camera', 'connected')
                        for notice in notifications(response):
                            self.record(ident, notice.pop('kind'), 'camera', notice.pop('active'), **notice)
                    delay = 5
                    stopped.wait(.1)
            except (OSError, ValueError, RuntimeError, ET.ParseError):
                if not stopped.is_set():
                    status('error')
                    stopped.wait(delay)
                    delay = min(60, delay * 2)
            finally:
                if subscription:
                    try:
                        self.soap(camera, subscription, WSN_ACTION + 'UnsubscribeRequest', '<wsnt:Unsubscribe/>', refs)
                    except (OSError, ValueError, RuntimeError, ET.ParseError):
                        pass

    def _analysis_loop(self):
        try:
            # Linux applies this to the analysis thread and its FFmpeg children.
            os.nice(10)
        except (AttributeError, OSError):
            pass
        cursor = 0
        while not self.stop.is_set():
            with self.lock:
                config = copy.deepcopy(self.config)
            options = config['detection']
            cameras = [(i, c) for i, c in enumerate(config.get('cameras', [])) if c.get('enabled') and c.get('local_detection_enabled') and options['local_enabled']]
            if not cameras:
                self.stop.wait(.5)
                continue
            index, camera = cameras[cursor % len(cameras)]
            cursor += 1
            ident, started = str(camera['id']), time.monotonic()
            try:
                self._set_status(ident, 'local', 'loading' if self.model is None else 'running')
                if self.model is None:
                    self.model = NanoDet()
                with self.capture_lock:
                    frame = capture_frame(self._proxy(camera, index))
                    captured_at = time.time()
                with self.lock:
                    if self.config != config:
                        continue
                    self._remember_frame(camera, frame, captured_at)
                objects = zone_objects(frame.scene_objects(self.model.infer(frame.pixels, options['confidence'])), camera)
                with self.lock:
                    # Discard results from settings that have been superseded during analysis.
                    if self.config != config:
                        continue
                    self._set_status(ident, 'local', 'running')
                    for kind in ('person', 'vehicle'):
                        matched = [obj for obj in objects if obj['kind'] == kind]
                        old = self.states.get((ident, 'local', kind)) or {}
                        if matched:
                            self.record(ident, kind, 'local', True, timestamp=captured_at, score=max(obj['score'] for obj in matched), objects=matched,
                                        **({'zone_id': 'local_zone'} if zone_settings(camera)['enabled'] else {}))
                        elif not old.get('active') or time.time() - old.get('last_seen', 0) > options['hold_seconds']:
                            self.record(ident, kind, 'local', False)
                    self.analysis_ms = round((time.monotonic() - started) * 1000)
            except Exception:
                self._set_status(ident, 'local', 'error')
                self.stop.wait(5)
            self.stop.wait(max(0, 1 / options['max_fps'] - (time.monotonic() - started)))

    def snapshot(self):
        with self.lock:
            rows, now = [], time.time()
            for index, camera in enumerate(self.config.get('cameras', [])):
                ident = str(camera['id'])
                camera_on = bool(camera.get('enabled') and camera.get('events_enabled') and self.config['detection']['camera_events_enabled'])
                local_on = bool(camera.get('enabled') and camera.get('local_detection_enabled') and self.config['detection']['local_enabled'])
                states = {}
                for kind in KINDS:
                    values = [self.states.get((ident, source, kind), {}) for source in ('camera', 'local')]
                    states[kind] = any(value.get('active') and (source != 'local' or now - value.get('last_seen', 0) < max(30, self.config['detection']['hold_seconds'] * 3)) for source, value in zip(('camera', 'local'), values))
                supported = set(self.reported.get((ident, 'camera'), set())) if camera_on else set()
                if local_on:
                    supported.update(('person', 'vehicle'))
                rows.append({'id': ident, 'index': index, 'name': camera.get('name', ident), 'states': states, 'supported_kinds': sorted(supported),
                             'camera_status': self.status.get((ident, 'camera'), 'connecting') if camera_on else 'disabled',
                             'local_status': self.status.get((ident, 'local'), 'loading') if local_on else 'disabled',
                             'local_zone': zone_settings(camera)})
            return {'updated_at': now, 'cameras': rows, 'recent_events': self.store.recent(limit=200),
                    'model': 'NanoDet-m-plus-1.5x_416', 'model_installed': MODEL_PATH.is_file(), 'backend': 'CPU (1 thread)',
                    'max_fps': self.config['detection']['max_fps'], 'analysis_ms': self.analysis_ms}

    def _publish_loop(self):
        last_prune = 0
        while not self.stop.wait(1):
            try:
                self.publish(self.home / 'detection.json', self.snapshot())
                if time.monotonic() - last_prune > 60:
                    with self.lock:
                        days = (self.config.get('storage') or {}).get('retention_days', 14)
                    self.store.prune(days)
                    last_prune = time.monotonic()
            except (OSError, ValueError, sqlite3.Error):
                pass

    def close(self):
        self.stop.set()
        with self.lock:
            for _, event in self.workers.values():
                event.set()
        self.publisher.join(timeout=2)
        self.thread.join(timeout=2)
