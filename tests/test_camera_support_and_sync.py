import base64
import hashlib
import io
import json
import os
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch
from urllib.parse import urlparse, unquote

from test_edge_panel_config import load_panel_module, camera


def soap(body):
    return ET.fromstring('<Envelope>' + body + '</Envelope>')


class CameraSupportTests(unittest.TestCase):
    def setUp(self):
        self.p = load_panel_module()

    def test_vendor_templates_and_reserved_credentials(self):
        paths = {'uniview': '/media/video1', 'reolink': '/h264Preview_01_main', 'axis': '/axis-media/media.amp', 'hanwha': '/profile1/media.smp', 'amcrest': '/cam/realmonitor', 'foscam': '/videoMain', 'tapo': '/stream1', 'vigi': '/stream1'}
        for vendor, path in paths.items():
            with self.subTest(vendor=vendor):
                result = self.p.normalize_camera({'vendor': vendor, 'host': '192.0.2.8', 'username': 'user@local', 'password': 'p@ss:#/?', 'camera_number': '1'}, 1)
                parsed = urlparse(result['rtsp_main'])
                self.assertEqual(parsed.path, path)
                self.assertEqual(unquote(parsed.username), 'user@local')
                self.assertEqual(unquote(parsed.password), 'p@ss:#/?')
                self.assertEqual(parsed.hostname, '192.0.2.8')

    def test_explicit_custom_path_and_port_survive_ip_and_password_change(self):
        result = self.p.normalize_camera({'vendor': 'reolink', 'host': '192.0.2.9', 'username': 'new', 'password': 'changed', 'rtsp_main': 'rtsp://old:old@192.0.2.8:8554/custom/stream?x=1'}, 1)
        self.assertEqual(result['rtsp_main'], 'rtsp://new:changed@192.0.2.9:8554/custom/stream?x=1')

    def test_generic_onvif_does_not_invent_vendor_stream_paths(self):
        for vendor in ('bosch', 'vivotek', 'sony', 'onvif', 'rtsp', 'unknown_brand'):
            result = self.p.normalize_camera({'vendor': vendor, 'host': '192.0.2.8'}, 1)
            self.assertEqual(result['rtsp_main'], '')
            self.assertIn('/onvif/device_service', result['onvif_url'])
        self.assertIn(':8000/', self.p.normalize_camera({'vendor': 'reolink', 'host': '192.0.2.8'}, 1)['onvif_url'])

    def test_saved_non_hikvision_endpoint_updates_to_new_host(self):
        p = self.p
        old = p.normalize_config({'cameras': [{'vendor': 'uniview', 'id': 'front', 'host': '192.0.2.8', 'username': 'old', 'password': 'old'}]})
        p.commit_panel_config(old)
        changed = {**old, 'cameras': [{**old['cameras'][0], 'host': '192.0.2.9', 'password': 'new'}]}
        saved = p.persist_config_payload(changed)[-1]['cameras'][0]
        self.assertEqual(urlparse(saved['onvif_url']).hostname, '192.0.2.9')
        self.assertEqual(unquote(urlparse(saved['rtsp_main']).password), 'new')

    def test_only_changed_recorder_is_restarted(self):
        p = self.p
        old = p.normalize_config({'cameras': [camera('first', '192.0.2.1', 'sub'), camera('second', '192.0.2.2', 'sub')]})
        new = json.loads(json.dumps(old))
        new['cameras'][0]['password'] = 'new-password'
        process = Mock()
        process.poll.return_value = None
        p.RECORDING_PROCESSES = {p.recording_key(c, i): Mock() for i, c in enumerate(old['cameras'])}
        with patch.object(p, 'stop_recording') as stop:
            stopped = p.restart_changed_recordings(old, new)
        stop.assert_called_once_with(old['cameras'][0], 0)
        self.assertEqual(stopped, [p.recording_key(old['cameras'][0], 0)])

    def test_janus_reload_is_only_requested_on_config_change(self):
        p = self.p
        with patch.object(p, 'write_mediamtx_runtime_config', return_value={}), patch.object(p, 'write_janus_streaming_runtime_config', side_effect=[{'written': True}, {'written': False}]), patch.object(p, 'reload_managed_janus', return_value={'applied': True}) as reload:
            p.sync_runtime_engine_config({}, 'save')
            p.sync_runtime_engine_config({}, 'save')
        reload.assert_called_once()

    def test_pending_status_uses_new_host_without_plaintext_password(self):
        p = self.p
        config = p.normalize_config({'cameras': [{'vendor': 'reolink', 'host': '192.0.2.99', 'username': 'user', 'password': 'new-secret', 'enabled': True}]})
        p.publish_pending_camera_status(config)
        payload = p.read_json(p.DATA_DIR / 'cameras.json', {})
        self.assertEqual(payload['cameras'][0]['host'], '192.0.2.99')
        self.assertEqual(payload['cameras'][0]['status'], 'checking')
        self.assertNotIn('new-secret', json.dumps(payload))

    def test_stale_camera_probe_cannot_publish_old_settings(self):
        p = self.p
        old = p.normalize_config({'cameras': [{'vendor': 'uniview', 'host': '192.0.2.1'}]})
        new = p.normalize_config({'cameras': [{'vendor': 'uniview', 'host': '192.0.2.99'}]})
        calls = []
        def active_config():
            calls.append(None)
            return old if len(calls) == 1 else new
        fresh = {'cameras': [{'host': '192.0.2.99'}]}
        with patch.object(p, 'load_config', side_effect=active_config), patch.object(p, 'read_json', return_value=fresh), patch.object(p, 'write_json') as write:
            result = p.refresh_status()
        self.assertEqual(result, fresh)
        write.assert_not_called()


class OnvifTests(unittest.TestCase):
    def setUp(self):
        self.p = load_panel_module()
        self.camera = self.p.normalize_camera({'vendor': 'uniview', 'host': '192.0.2.8', 'username': 'camera-user', 'password': 'p@ss-secret'}, 1)

    def test_profile_resolution_order_selects_main_and_sub_stream(self):
        caps = soap('<Media><XAddr>http://192.0.2.8/onvif/media</XAddr></Media>')
        profiles = soap('<Profiles token="small"><Resolution><Width>640</Width><Height>360</Height></Resolution></Profiles><Profiles token="large"><Resolution><Width>1920</Width><Height>1080</Height></Resolution></Profiles>')
        with patch.object(self.p, 'onvif_request', side_effect=[caps, profiles, soap('<Uri>rtsp://192.0.2.8/customMain</Uri>'), soap('<Uri>rtsp://192.0.2.8/customSub</Uri>')]) as request:
            result = self.p.fetch_onvif_camera_config(self.camera)
        self.assertTrue(result['ok'])
        self.assertEqual(urlparse(result['camera']['rtsp_main']).path, '/customMain')
        self.assertEqual(unquote(urlparse(result['camera']['rtsp_main']).password), 'p@ss-secret')
        self.assertIn('large', request.call_args_list[2].args[3])
        self.assertIn('small', request.call_args_list[3].args[3])

    def test_soap_uses_password_digest_not_plaintext(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'<Envelope><Profiles/></Envelope>'
        opener = Mock()
        opener.open.return_value = response
        with patch('urllib.request.build_opener', return_value=opener):
            self.p.onvif_request(self.camera, self.camera['onvif_url'], 'test-action', '<GetProfiles/>')
        request = opener.open.call_args.args[0]
        self.assertNotIn(b'p@ss-secret', request.data)
        self.assertIn(b'PasswordDigest', request.data)
        self.assertIn(b'UsernameToken', request.data)

    def test_discovered_service_cannot_forward_credentials_to_another_host(self):
        with self.assertRaises(ValueError):
            self.p.onvif_request(self.camera, 'http://other.example.com/onvif', 'action', '<GetProfiles/>')

    def test_missing_media_service_has_actionable_error(self):
        with patch.object(self.p, 'onvif_request', return_value=soap('<Capabilities/>')):
            with self.assertRaisesRegex(RuntimeError, 'manual RTSP'):
                self.p.fetch_onvif_camera_config(self.camera)


class SupervisorOptionsTests(unittest.TestCase):
    def setUp(self):
        self.p = load_panel_module()
        self.config = self.p.normalize_config({'cameras': [{'vendor': 'reolink', 'host': '192.0.2.8', 'username': 'user', 'password': 'secret'}]})

    def test_projection_keeps_other_options_and_updates_both_views(self):
        options = self.p.addon_options_from_config(self.config, {'janus_enabled': True, 'log_level': 'info'})
        self.assertTrue(options['janus_enabled'])
        self.assertEqual(options['cameras'][0]['vendor'], 'reolink')
        self.assertEqual(options['hikvision_1_host'], '192.0.2.8')
        self.assertEqual(options['hikvision_1_password'], 'secret')
        self.assertFalse(options['hikvision_2_enabled'])

    def test_sync_posts_only_own_options_and_verifies_result(self):
        p = self.p
        desired = p.addon_options_from_config(self.config, {'log_level': 'info'})
        with patch.dict(os.environ, {'SUPERVISOR_TOKEN': 'never-log-this'}), patch.object(p, 'supervisor_self_request', side_effect=[{'options': {'log_level': 'info'}}, {}, {'options': desired}]) as request:
            result = p.sync_supervisor_options(self.config)
        self.assertEqual(result, {'synced': True, 'status': 'synced'})
        self.assertEqual([call.args[0] for call in request.call_args_list], ['/addons/self/info', '/addons/self/options', '/addons/self/info'])
        self.assertEqual(request.call_args_list[1].args[1]['options'], desired)
        diagnostics = (p.HOME_DIR / 'addon-options-sync.json').read_text()
        self.assertNotIn('secret', diagnostics)
        self.assertNotIn('never-log-this', diagnostics)

    def test_sync_failure_does_not_report_success_or_expose_secret(self):
        with patch.dict(os.environ, {'SUPERVISOR_TOKEN': 'never-log-this'}), patch.object(self.p, 'supervisor_self_request', side_effect=OSError('secret response')):
            result = self.p.sync_supervisor_options(self.config)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('secret', json.dumps(result))
