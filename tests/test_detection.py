import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from test_edge_panel_config import ROOT, camera, load_panel_module

PATH = ROOT / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity/edge_detection.py'


def module():
    spec = importlib.util.spec_from_file_location('detection_test', PATH)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def xml(text):
    return ET.fromstring(text)


class DetectionTests(unittest.TestCase):
    def test_notification_topics_classification_and_camera_clock(self):
        m = module()
        payload = xml('<Envelope><NotificationMessage><Topic>RuleEngine/CellMotionDetector/Motion</Topic><Message UtcTime="2000-01-01T00:00:00Z"><Data><SimpleItem Name="IsMotion" Value="true"/></Data></Message></NotificationMessage><NotificationMessage><Topic>HumanVehicleDetector/Alarm</Topic><Message><Data><SimpleItem Name="ObjectType" Value="Vehicle"/><SimpleItem Name="State" Value="true"/></Data></Message></NotificationMessage><NotificationMessage><Topic>PersonDetector/Alarm</Topic><Message><Data><SimpleItem Name="State" Value="false"/></Data></Message></NotificationMessage></Envelope>')
        result = m.notifications(payload)
        self.assertEqual([(n['kind'], n['active']) for n in result], [('motion', True), ('vehicle', True), ('person', False)])
        self.assertLess(abs(result[0]['timestamp'] - time.time()), 2)
        self.assertEqual(m.notifications(xml('<Envelope><NotificationMessage><Topic>Unknown</Topic><Data><SimpleItem Name="State" Value="true"/></Data></NotificationMessage></Envelope>')), [])

    def test_history_persists_prunes_filters_and_has_private_permissions(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.sqlite'
            store = m.EventStore(path)
            store.add('front', 'person', 'local', time.time(), {'score': .8})
            store.add('back', 'motion', 'camera', time.time() - 16 * 86400, {})
            store.prune(14)
            rows = m.EventStore(path).recent()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['camera_id'], 'front')
            self.assertEqual(len(rows[0]['day']), 10)
            self.assertGreaterEqual(rows[0]['day_seconds'], 0)
            self.assertEqual(store.recent('back'), [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_disabled_detection_never_contacts_camera_or_starts_capture(self):
        m = module()
        with tempfile.TemporaryDirectory() as directory:
            service = m.DetectionService(directory, lambda *a: self.fail('Unexpected camera HTTP request'), lambda *a: self.fail('Unexpected RTSP reader'), lambda *a: None)
            try:
                service.apply({'cameras': [camera('front', '192.0.2.10', 'sub')]})
                time.sleep(.05)
                service.record('front', 'person', 'local', True, score=.9)
                service.record('front', 'person', 'local', True, score=.95)
                self.assertEqual(len(service.store.recent()), 1, 'repeated active state must not flood history')
                service.record('front', 'person', 'local', False)
                service.record('front', 'person', 'local', True, score=.9)
                self.assertEqual(len(service.store.recent()), 2)
                self.assertNotIn('secret', json.dumps(service.snapshot()))
            finally:
                service.close()

    def test_onvif_subscription_pull_stop_and_unsubscribe(self):
        m = module()
        received, unsubscribed = threading.Event(), threading.Event()
        def soap(camera, endpoint, action, body, refs=''):
            if action.endswith('GetCapabilities'):
                return xml('<Envelope><Events XAddr="http://192.0.2.10/events"/></Envelope>')
            if action.endswith('GetEventPropertiesRequest'):
                return xml('<Envelope><TopicSet><MotionAlarm/></TopicSet></Envelope>')
            if action.endswith('CreatePullPointSubscriptionRequest'):
                return xml('<Envelope><SubscriptionReference><Address>http://192.0.2.10/pull</Address><ReferenceParameters><Resource>test-ref</Resource></ReferenceParameters></SubscriptionReference></Envelope>')
            if action.endswith('PullMessagesRequest'):
                self.assertIn('test-ref', refs)
                received.set()
                return xml('<Envelope><NotificationMessage><Topic>MotionAlarm</Topic><Message><Data><SimpleItem Name="State" Value="true"/></Data></Message></NotificationMessage></Envelope>')
            if action.endswith('UnsubscribeRequest'):
                unsubscribed.set()
            return xml('<Envelope/>')
        with tempfile.TemporaryDirectory() as directory:
            service = m.DetectionService(directory, soap, lambda *a: self.fail('Unexpected video reader'), lambda *a: None)
            try:
                config = {'detection': {'camera_events_enabled': True}, 'cameras': [{**camera('front', '192.0.2.10', 'sub'), 'events_enabled': True}]}
                service.apply(config)
                self.assertTrue(received.wait(2))
                deadline = time.monotonic() + 2
                while not service.store.recent() and time.monotonic() < deadline:
                    time.sleep(.01)
                snapshot = service.snapshot()
                self.assertTrue(snapshot['cameras'][0]['states']['motion'])
                self.assertEqual(snapshot['cameras'][0]['supported_kinds'], ['motion'])
                service.apply({'cameras': [], 'detection': {}})
                self.assertTrue(unsubscribed.wait(2))
            finally:
                service.close()

    def test_local_worker_only_uses_shared_proxy_and_persists_detection(self):
        m = module()
        class Detector:
            def infer(self, pixels, confidence):
                return [{'kind': 'person', 'score': .9, 'box': [.1, .2, .3, .4]}]
        captured = threading.Event()
        def frame(url):
            self.assertEqual(url, 'rtsp://127.0.0.1:8556/front_main')
            captured.set()
            return m.AnalysisFrame(bytes(416 * 416 * 3))
        with tempfile.TemporaryDirectory() as directory, patch.object(m, 'NanoDet', Detector), patch.object(m, 'capture_frame', frame):
            service = m.DetectionService(directory, lambda *a: self.fail('Unexpected ONVIF request'), lambda *a: 'rtsp://127.0.0.1:8556/front_main', lambda *a: None)
            try:
                service.apply({'detection': {'local_enabled': True}, 'cameras': [{**camera('front', '192.0.2.10', 'sub'), 'local_detection_enabled': True}]})
                self.assertTrue(captured.wait(2))
                deadline = time.monotonic() + 2
                while not service.store.recent() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(service.store.recent()[0]['kind'], 'person')
                self.assertTrue(service.snapshot()['cameras'][0]['states']['person'])
            finally:
                service.close()

    def test_model_detects_vehicle_on_real_image(self):
        model, image = os.environ.get('EDGE_TEST_MODEL'), os.environ.get('EDGE_TEST_IMAGE')
        if not model or not image:
            self.skipTest('Set EDGE_TEST_MODEL and EDGE_TEST_IMAGE for real inference')
        import cv2
        detector = module().NanoDet(model)
        pixels = cv2.resize(cv2.imread(image), (416, 416)).tobytes()
        result = detector.infer(pixels, .4)
        self.assertTrue(any(row['kind'] == 'vehicle' for row in result))

    def test_detection_settings_and_camera_flags_survive_roundtrip(self):
        panel = load_panel_module()
        config = panel.normalize_config({'detection': {'local_enabled': True, 'max_fps': 1}, 'cameras': [{**camera('front', '192.0.2.10', 'sub'), 'events_enabled': True, 'local_detection_enabled': True}]})
        saved = panel.persist_config_payload(config)[-1]
        self.assertTrue(saved['detection']['local_enabled'])
        self.assertTrue(saved['cameras'][0]['events_enabled'])
        options = panel.addon_options_from_config(saved, {})
        self.assertTrue(options['local_detection_enabled'])
        self.assertTrue(options['cameras'][0]['local_detection_enabled'])
