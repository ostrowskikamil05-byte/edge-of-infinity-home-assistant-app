import json
import os
import unittest
from unittest.mock import Mock, patch

from test_edge_panel_config import load_panel_module


class CoreEngineSettingsTests(unittest.TestCase):
    def setUp(self):
        self.p = load_panel_module()
        self.options = {'core_engine': 'mediamtx_janus', 'cameras': [{'password': 'camera-secret'}], 'log_level': 'debug', 'retention_days': 14}

    def test_saves_only_engine_preserves_options_and_reports_pending_restart(self):
        observed = {**self.options, 'core_engine': 'edge_rtc'}
        with patch.dict(os.environ, {'EDGE_CORE_ENGINE': 'mediamtx_janus'}), patch.object(self.p, 'supervisor_self_request', side_effect=[{'options': self.options}, {}, {'options': observed}]) as request:
            result = self.p.save_core_engine('edge_rtc')
        self.assertEqual(request.call_args_list[1].args, ('/addons/self/options', {'options': observed}))
        self.assertEqual(result, {'available': True, 'running_engine': 'mediamtx_janus', 'core_engine': 'edge_rtc', 'restart_required': True})
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.options['core_engine'], 'mediamtx_janus')

    def test_success_requires_readback(self):
        with patch.object(self.p, 'supervisor_self_request', side_effect=[{'options': self.options}, {}, {'options': self.options}]):
            with self.assertRaisesRegex(RuntimeError, 'verification_failed'):
                self.p.save_core_engine('edge_rtc')

    def test_unchanged_engine_is_verified_without_writing(self):
        with patch.object(self.p, 'supervisor_self_request', return_value={'options': self.options}) as request:
            self.p.save_core_engine('mediamtx_janus')
        self.assertEqual([call.args[0] for call in request.call_args_list], ['/addons/self/info', '/addons/self/info'])

    def test_invalid_choice_never_contacts_supervisor(self):
        with patch.object(self.p, 'supervisor_self_request') as request:
            for value in (None, '', 'unknown', {}, []):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.p.save_core_engine(value)
        request.assert_not_called()

    def test_unavailable_supervisor_has_no_false_success_or_private_error(self):
        with patch.object(self.p, 'supervisor_self_request', side_effect=OSError('private-token')):
            result = self.p.core_engine_settings()
        self.assertFalse(result['available'])
        self.assertNotIn('private-token', json.dumps(result))

    def test_camera_sync_preserves_selected_engine(self):
        config = self.p.normalize_config({'cameras': []})
        self.assertEqual(self.p.addon_options_from_config(config, {**self.options, 'core_engine': 'edge_rtc'})['core_engine'], 'edge_rtc')

    def test_http_save_failure_is_sanitized_and_does_not_claim_success(self):
        handler = self.p.EdgeHandler.__new__(self.p.EdgeHandler)
        handler.path = '/api/core/engine'
        handler.client_address = ('127.0.0.1', 1234)
        handler.read_body_json = Mock(return_value={'core_engine': 'edge_rtc'})
        handler.send_json = Mock()
        with patch.object(self.p, 'save_core_engine', side_effect=OSError('password=private-secret')):
            handler.do_POST()
        payload, status = handler.send_json.call_args.args
        self.assertEqual(status, 502)
        self.assertNotIn('private-secret', json.dumps(payload))

    def test_http_rejects_non_object_payload_without_writing(self):
        handler = self.p.EdgeHandler.__new__(self.p.EdgeHandler)
        handler.path = '/api/core/engine'
        handler.client_address = ('127.0.0.1', 1234)
        handler.read_body_json = Mock(return_value=['edge_rtc'])
        handler.send_json = Mock()
        with patch.object(self.p, 'save_core_engine') as save:
            handler.do_POST()
        save.assert_not_called()
        self.assertEqual(handler.send_json.call_args.args[1], 400)


if __name__ == '__main__':
    unittest.main()
