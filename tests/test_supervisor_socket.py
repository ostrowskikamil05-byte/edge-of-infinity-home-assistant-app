"""Exercise native-only cleanup against a real tokenless Supervisor Unix socket."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from test_core_engine_settings import migration


@contextmanager
def supervisor_socket(options, *, first_write_fails=False, deny_write=False):
    state = {'options': options, 'network': {'8088/tcp': 8088, '8556/tcp': None,
                                           '8889/tcp': 8889}, 'calls': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def answer(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state['calls'].append(('GET', self.path, self.headers.get('Authorization')))
            self.answer(200, {'result': 'ok', 'data': {
                'options': state['options'], 'network': state['network']}})

        def do_POST(self):
            state['calls'].append(('POST', self.path, self.headers.get('Authorization')))
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if deny_write:
                self.answer(403, {'message': 'private-camera-password'})
                return
            if first_write_fails and 'first_failure' not in state:
                state['first_failure'] = True
                # Simulate the user changing valid settings before the next try.
                state['options'] = {**state['options'], 'retention_days': 21,
                                    'cameras': [{'id': 'latest', 'password': '!secret new_camera'}]}
                self.answer(503, {'message': 'private-camera-password'})
                return
            state['options'] = payload['options']
            state['network'] = payload.get('network', state['network'])
            self.answer(200, {'result': 'ok'})

    with tempfile.TemporaryDirectory(prefix='edge-supervisor-') as folder:
        path = str(Path(folder) / 'api.sock')
        with socketserver.UnixStreamServer(path, Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            with patch.dict(os.environ, {'SUPERVISOR_SOCKET': path,
                                        'SUPERVISOR_TOKEN': '', 'HASSIO_TOKEN': ''}):
                try:
                    yield state
                finally:
                    server.shutdown()
                    worker.join(timeout=2)


class SupervisorSocketTests(unittest.TestCase):
    def setUp(self):
        self.m = migration()

    def test_compaction_runs_after_authoritative_import_even_if_engine_cleanup_was_synced(self):
        initial = {'janus_enabled': True, 'mediamtx_enabled': True,
                   'hikvision_1_password': 'legacy-camera-secret',
                   'detection_fps': 2, 'cameras': [{'id': 'old'}],
                   'retention_days': 21, 'log_level': 'debug'}
        authoritative = {'cameras': [{'id': 'unv', 'password': 'current-camera-secret',
                                     'record_stream': 'main'}],
                         'detection': {'max_fps': .5}}
        with supervisor_socket(initial) as state, tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.m.atomic_json(root / 'edge.json', authoritative)
            original = (root / 'edge.json').read_bytes()
            self.m.atomic_json(root / 'native-migration.json', {'supervisor': {'status': 'synced'}})
            stop = Mock(spec=threading.Event)
            stop.wait.return_value = False
            stop.is_set.return_value = False
            worker = self.m.start_supervisor_cleanup(folder, stop, threading.RLock(), Mock())
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(state['options']['cameras'], [])
            self.assertEqual(state['options']['retention_days'], 21)
            self.assertEqual(state['options']['log_level'], 'debug')
            self.assertFalse(any(key.startswith(('mediamtx_', 'janus_', 'hikvision_', 'detection_'))
                                 for key in state['options']))
            self.assertEqual((root / 'edge.json').read_bytes(), original)
            report = json.loads((root / 'native-migration.json').read_text())
            self.assertEqual(report['supervisor'], {'status': 'synced', 'scope': 'panel_owned_settings'})
            self.assertNotIn('secret', json.dumps(report))

    def test_failed_authoritative_import_preserves_legacy_camera_options(self):
        initial = {'janus_enabled': True, 'cameras': [{'id': 'last-camera', 'password': 'keep'}]}
        with supervisor_socket(initial) as state, tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'edge.json').write_text('{broken')
            stop = Mock(spec=threading.Event)
            stop.wait.return_value = False
            stop.is_set.return_value = False
            worker = self.m.start_supervisor_cleanup(folder, stop, threading.RLock(), Mock())
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(state['options']['cameras'], initial['cameras'])
            self.assertNotIn('janus_enabled', state['options'])

    def test_socket_without_token_replaces_old_persisted_options_and_keeps_camera(self):
        original = {'core_engine': 'mediamtx_janus', 'mediamtx_enabled': True,
                    'mediamtx_rtsp_port': 9556, 'mediamtx_api_port': 10997,
                    'janus_enabled': True, 'janus_http_port': 8192,
                    'cameras': [{'id': 'unv', 'password': '!secret camera'}],
                    'retention_days': 14, 'recordings_dir': '/media/user-recordings'}
        with supervisor_socket(original) as state, tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / 'native-options.json'
            mounted = self.m.native_options({**original, 'cameras': [{'password': 'resolved-secret'}]})
            result = self.m.sync_supervisor_options(mounted, output_path=runtime)
            self.assertEqual(result['status'], 'synced')
            self.assertEqual(state['options'], self.m.native_options(original))
            self.assertEqual(state['network'], {'8088/tcp': 8088, '8556/tcp': None})
            self.assertEqual(json.loads(runtime.read_text())['cameras'][0]['password'], 'resolved-secret')
            self.assertEqual([call[:2] for call in state['calls']], [
                ('GET', '/addons/self/info'), ('POST', '/addons/self/options'),
                ('GET', '/addons/self/info')])
            self.assertTrue(all(auth is None for _, _, auth in state['calls']))
            self.assertNotIn('secret', json.dumps(result))

    def test_retry_rereads_current_options_and_does_not_restore_old_camera_settings(self):
        initial = {'janus_enabled': True, 'cameras': [{'id': 'old'}], 'retention_days': 14}
        with supervisor_socket(initial, first_write_fails=True) as state, tempfile.TemporaryDirectory() as folder:
            stop = threading.Event()
            log = Mock()
            self.m.retry_supervisor_cleanup(folder, stop, threading.RLock(), log, delays=(0, 0, 0))
            report = json.loads((Path(folder) / 'native-migration.json').read_text())
            self.assertEqual(report['supervisor']['status'], 'synced')
            self.assertEqual(report['supervisor_attempts'], 2)
            self.assertEqual(state['options']['retention_days'], 21)
            self.assertEqual(state['options']['cameras'][0]['id'], 'latest')
            self.assertEqual(state['options']['cameras'][0]['password'], '!secret new_camera')
            self.assertNotIn('janus_enabled', state['options'])
            self.assertEqual(len([c for c in state['calls'] if c[0] == 'POST']), 2)
            self.assertNotIn('private-camera-password', json.dumps(report))
            self.assertEqual(log.call_args_list[0].args[1]['http_status'], 503)

    def test_permission_failure_is_safe_and_names_the_failed_phase(self):
        with supervisor_socket({'janus_enabled': True}, deny_write=True):
            result = self.m.sync_supervisor_options({})
        self.assertEqual(result, {'status': 'retry_required', 'phase': 'write_options',
                                  'code': 'supervisor_http_error', 'http_status': 403})
        self.assertNotIn('private-camera-password', json.dumps(result))

    def test_startup_cli_records_a_verified_socket_migration(self):
        with supervisor_socket({'janus_enabled': True, 'cameras': []}), tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            options = root / 'options.json'
            options.write_text('{"cameras":[]}')  # Supervisor already filtered this mounted file.
            result = subprocess.run([sys.executable, self.m.__file__, '--options', str(options),
                                     '--output', str(root / 'runtime/native-options.json'),
                                     '--home', str(root / 'home'), '--addon', str(root / 'addon')],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((root / 'home/native-migration.json').read_text())
            self.assertEqual(report['supervisor']['status'], 'synced')
            self.assertEqual(report['supervisor_transport'], 'unix')
            self.assertIn('[edge-migration]', result.stdout)

    def test_retry_cancels_on_shutdown_and_skips_already_completed_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            sync = Mock()
            stop = threading.Event()
            stop.set()
            self.m.retry_supervisor_cleanup(folder, stop, threading.RLock(), Mock(), sync=sync, delays=(0,))
            sync.assert_not_called()
            stop.clear()
            self.m.atomic_json(Path(folder) / 'native-migration.json', {'supervisor': {'status': 'synced'}})
            self.m.retry_supervisor_cleanup(folder, stop, threading.RLock(), Mock(), sync=sync, delays=(0,))
            sync.assert_not_called()

    def test_cleanup_only_removes_named_artifacts_and_does_not_follow_directory_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home, addon, runtime, media = [root / p for p in ('home', 'addon', 'runtime', 'media')]
            for p in (home, addon, runtime, media):
                p.mkdir()
            (media / 'keep.mp4').write_bytes(b'original video')
            (home / 'janus').symlink_to(media, target_is_directory=True)
            (addon / 'janus').mkdir()
            (addon / 'janus/keep.mp4').write_bytes(b'user recording')
            (addon / 'janus/janus.transport.http.jcfg').write_text('obsolete settings')
            (addon / 'mediamtx.yml').write_text('obsolete settings')
            (home / 'janus.log').write_text('obsolete log')
            self.m.migrate_saved_files(home, addon, runtime)
            self.assertFalse((home / 'janus').is_symlink())
            self.assertEqual((media / 'keep.mp4').read_bytes(), b'original video')
            self.assertEqual((addon / 'janus/keep.mp4').read_bytes(), b'user recording')
            self.assertFalse((addon / 'janus/janus.transport.http.jcfg').exists())
            self.assertFalse((addon / 'mediamtx.yml').exists())
            self.assertFalse((home / 'janus.log').exists())

    def test_missing_current_options_never_posts_a_stale_mounted_fallback(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock()
        response.read.return_value = b'{"result":"ok","data":{}}'
        request = Mock(return_value=response)
        result = self.m.sync_supervisor_options({'cameras': [{'id': 'stale'}]}, 'token', request)
        self.assertEqual(result['status'], 'retry_required')
        self.assertEqual(request.call_count, 1)

    def test_helper_keeps_legacy_token_support_and_rejects_other_app_paths(self):
        import edge_supervisor as api
        with patch.dict(os.environ, {'SUPERVISOR_SOCKET': '', 'SUPERVISOR_TOKEN': '',
                                   'HASSIO_TOKEN': 'legacy-private-token'}), \
                patch.object(api.http.client, 'HTTPConnection') as connection:
            instance = connection.return_value
            instance.getresponse.return_value.status = 200
            instance.getresponse.return_value.read.return_value = b'{"result":"ok","data":{"hostname":"edge"}}'
            self.assertTrue(api.supervisor_available())
            self.assertEqual(api.request_self('info'), {'hostname': 'edge'})
            self.assertEqual(instance.request.call_args.args, ('GET', '/addons/self/info'))
            self.assertEqual(instance.request.call_args.kwargs['headers']['Authorization'], 'Bearer legacy-private-token')
            connection.assert_called_once_with('supervisor', timeout=5)
            instance.close.assert_called_once()
            for path in ('/addons/other/options', 'http://example.test', '../info'):
                with self.assertRaises(ValueError):
                    api.request_self(path)
