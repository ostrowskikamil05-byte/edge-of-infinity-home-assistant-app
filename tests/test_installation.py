import importlib.util
import io
import ast
import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity'
sys.path.insert(0, str(APP))
import edge_installation as setup


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home, self.addon, self.private, self.media = [self.base / s for s in ('home', 'addon', 'private', 'media/recordings')]
        for p in (self.home, self.addon, self.private, self.media): p.mkdir(parents=True)
        self.options = self.private / 'options.json'
        self.output = self.private / 'bootstrap.json'
        setup.write(self.options, {'cameras': [], 'api_port': 8088})

    def boot(self):
        return setup.bootstrap(self.home, self.private, self.addon, self.options, self.output, self.media)

    def config(self): return setup.read(self.home / 'edge.json')

    def legacy(self):
        config = setup.blank_config(self.media)
        config['cameras'] = [{'id': 'front', 'host': '192.0.2.10', 'password': 'fixture-only', 'record': True}]
        config['nvr'] = {'segment_seconds': 10, 'browser_playback': 'copy'}
        setup.write(self.home / 'edge.json', config)
        return config

    def pending(self, media):
        state = setup.read(self.home / setup.OWNER)
        state['pending_reset'] = {'include_media': media}
        setup.write(self.home / setup.OWNER, state)

    def test_new_install_is_blank_with_own_recording_namespace(self):
        self.assertEqual(self.boot()['mode'], 'new')
        self.assertEqual(self.config()['cameras'], [])
        self.assertNotEqual(self.config()['storage']['recordings_dir'], str(self.media))
        self.assertTrue(setup.status(self.home)['needs_setup'])

    def test_interrupted_initial_adoption_preserves_legacy_configuration(self):
        old = self.legacy()
        write = setup.write
        def interrupted(path, data):
            if Path(path).name == setup.OWNER:
                raise OSError('fixture interrupted write')
            return write(path, data)
        with patch.object(setup, 'write', side_effect=interrupted):
            with self.assertRaises(OSError): self.boot()
        self.boot()
        self.assertEqual(self.config(), old)

    def test_old_tab_cannot_restore_cameras_after_reset(self):
        self.legacy(); self.boot()
        old = setup.status(self.home)['id']
        self.assertTrue(setup.generation_matches(self.home, None))
        self.pending(False)
        self.assertFalse(setup.generation_matches(self.home, old))
        self.boot()
        self.assertFalse(setup.generation_matches(self.home, old))
        self.assertFalse(setup.generation_matches(self.home, None))
        self.assertTrue(setup.generation_matches(self.home, setup.status(self.home)['id']))

    def test_full_removal_includes_retained_default_edge_media(self):
        self.boot()
        (self.media / 'old-installation').mkdir()
        (self.media / 'old-installation/clip.mp4').write_bytes(b'retained')
        self.pending(True); self.boot()
        self.assertFalse((self.media / 'old-installation/clip.mp4').exists())

    def test_update_and_restart_preserve_exact_camera_and_recording_settings(self):
        old = self.legacy()
        self.assertEqual(self.boot()['mode'], 'upgrade')
        ident = setup.status(self.home)['id']
        self.boot()
        self.assertEqual(self.config(), old)
        self.assertEqual(setup.status(self.home)['id'], ident)

    def test_legacy_panel_only_upgrade_is_imported_once(self):
        old = self.legacy()
        (self.home / 'edge.json').rename(self.home / 'panel-config.json')
        self.boot()
        self.assertEqual(self.config(), old)

    def test_legacy_supervisor_options_are_left_for_the_existing_importer(self):
        setup.write(self.options, {'cameras': [{'id': 'front', 'password': 'fixture-only'}]})
        self.boot()
        self.assertFalse((self.home / 'edge.json').exists())
        self.assertTrue(setup.status(self.home)['legacy_upgrade'])
        self.assertTrue(setup.read(self.output)['cameras'])

    def test_reinstall_does_not_restore_camera_or_preset_and_keeps_media(self):
        self.legacy(); self.boot()
        old_id = setup.status(self.home)['id']
        setup.write(self.home / 'camera-presets.json', [{'password': 'fixture-only'}])
        (self.home / 'snapshots').mkdir(); (self.home / 'snapshots/old.jpg').write_bytes(b'image')
        (self.media / 'front').mkdir(); (self.media / 'front/old.mp4').write_bytes(b'video')
        (self.private / setup.MARKER).unlink()
        report = self.boot()
        self.assertEqual(report['mode'], 'reinstalled')
        self.assertEqual(self.config()['cameras'], [])
        self.assertNotEqual(old_id, setup.status(self.home)['id'])
        self.assertFalse((self.home / 'camera-presets.json').exists())
        self.assertFalse((self.home / 'snapshots/old.jpg').exists())
        self.assertTrue((self.media / 'front/old.mp4').exists())
        self.assertNotEqual(self.config()['storage']['recordings_dir'], str(self.media))

    def test_reset_removes_mirrors_backups_and_supervisor_camera_import(self):
        self.legacy(); self.boot()
        setup.write(self.options, {'cameras': [{'password': 'fixture-only'}], 'hikvision_1_host': '192.0.2.10'})
        setup.write(self.home / 'edge.backup.json', {'old': True})
        setup.write(self.addon / 'panel-config.json', {'old': True})
        self.pending(False); self.boot()
        self.assertEqual(setup.read(self.output)['cameras'], [])
        self.assertNotIn('hikvision_1_host', setup.read(self.output))
        self.assertEqual(setup.read(self.addon / 'edge.json')['cameras'], [])
        self.assertFalse((self.home / 'edge.backup.json').exists())
        self.assertFalse((self.addon / 'panel-config.json').exists())

    def test_full_removal_also_survives_uninstall_before_restart(self):
        self.legacy(); self.boot()
        (self.media / 'front').mkdir(); (self.media / 'front/clip.mp4').write_bytes(b'video')
        (self.media / '.alerts').mkdir(); (self.media / '.alerts/event-1.mp4').write_bytes(b'alert')
        (self.home / 'recording-cache').mkdir(); (self.home / 'recording-cache/old.mp4').write_bytes(b'cache')
        self.pending(True)
        (self.private / setup.MARKER).unlink()
        report = self.boot()
        self.assertTrue(report['media_deleted'])
        self.assertFalse((self.media / 'front/clip.mp4').exists())
        self.assertFalse((self.media / '.alerts/event-1.mp4').exists())
        self.assertFalse((self.home / 'recording-cache/old.mp4').exists())
        self.assertTrue(setup.status(self.home)['needs_setup'])

    def test_custom_shared_recording_root_only_removes_registered_camera_folders(self):
        shared = self.base / 'shared'
        (shared / 'front').mkdir(parents=True); (shared / 'front/clip.mp4').write_bytes(b'video')
        (shared / 'other-project').mkdir(); (shared / 'other-project/keep.mp4').write_bytes(b'other')
        (shared / 'keep.txt').write_text('other')
        config = self.legacy(); config['storage']['recordings_dir'] = str(shared)
        setup.write(self.home / 'edge.json', config)
        self.boot(); self.pending(True); self.boot()
        self.assertFalse((shared / 'front').exists())
        self.assertTrue((shared / 'other-project/keep.mp4').exists())
        self.assertTrue((shared / 'keep.txt').exists())

    def test_symlink_inside_edge_directory_never_erases_target(self):
        self.legacy(); self.boot()
        other = self.base / 'unrelated'; other.mkdir(); (other / 'keep.txt').write_text('keep')
        (self.home / 'snapshot-link').symlink_to(other, target_is_directory=True)
        self.pending(False); self.boot()
        self.assertTrue((other / 'keep.txt').exists())

    def test_symlink_recording_root_aborts_before_configuration_is_erased(self):
        other = self.base / 'unrelated'; other.mkdir(); (other / 'keep.txt').write_text('keep')
        linked = self.base / 'linked'; linked.symlink_to(other, target_is_directory=True)
        config = self.legacy(); config['storage']['recordings_dir'] = str(linked)
        setup.write(self.home / 'edge.json', config)
        self.boot(); self.pending(True)
        with self.assertRaises(ValueError): self.boot()
        self.assertTrue(self.config()['cameras'])
        self.assertTrue((other / 'keep.txt').exists())

    def test_invalid_authoritative_json_does_not_restore_a_mirror_or_delete_data(self):
        self.legacy(); self.boot()
        (self.home / 'edge.json').write_text('{')
        with self.assertRaises(ValueError): self.boot()
        self.assertEqual((self.home / 'edge.json').read_text(), '{')

    def test_broad_and_system_roots_are_rejected(self):
        for path in ('/', '/media', '/homeassistant', '/tmp', '/tmp/../etc'):
            with self.assertRaises(ValueError): setup.checked(path)
        with self.assertRaises(ValueError): setup.remove_media([{'path': '/etc/ssl', 'camera_dirs': ['front']}])

    def test_nested_mount_is_rejected_before_deleting_anything(self):
        (self.media / 'camera').mkdir(); (self.media / 'camera/mount').mkdir()
        (self.media / 'keep.mp4').write_bytes(b'video')
        with patch.object(setup.os.path, 'ismount', side_effect=lambda p: Path(p).name == 'mount'):
            with self.assertRaises(ValueError): setup.clear_directory(self.media)
        self.assertTrue((self.media / 'keep.mp4').exists())

    def handler(self, path='/api/setup', payload=None, peer='127.0.0.1', origin=None):
        class Handler:
            command = 'POST' if payload is not None else 'GET'
            client_address = (peer, 80)
            headers = {'Host': 'edge.example.test', 'Content-Type': 'application/json',
                       'Content-Length': str(len(json.dumps(payload or {}).encode()))}
            def read_body_json(inner): return payload
            def send_json(inner, data, status=200): inner.result = (status, data)
        h = Handler()
        if origin: h.headers['Origin'] = origin
        setup.serve(h, path, self.home, lambda *args: None)
        return h.result

    def test_setup_persists_mode_and_never_rewrites_camera_config(self):
        old = self.legacy(); self.boot()
        result = self.handler('/api/setup/access', {'csrf': setup.CSRF, 'mode': 'local'})
        self.assertEqual(result[0], 200)
        self.assertFalse(result[1]['needs_setup'])
        self.assertEqual(self.config(), old)
        self.boot(); self.assertEqual(setup.status(self.home)['mode'], 'local')

    def test_reset_requires_csrf_phrase_and_explicit_media_choice(self):
        self.legacy(); self.boot()
        for payload in ({'csrf': 'wrong'}, {'csrf': setup.CSRF}, {'csrf': setup.CSRF, 'confirm': 'DELETE EDGE', 'include_media': 'true'}):
            status, _ = self.handler('/api/setup/reset', payload)
            self.assertIn(status, (400, 403))
        self.assertFalse(setup.status(self.home)['pending_reset'])

    def test_cross_origin_and_direct_untrusted_requests_cannot_read_csrf_or_reset(self):
        self.boot()
        for kwargs in ({'origin': 'https://evil.example'}, {'peer': '192.0.2.10'}):
            status, result = self.handler(**kwargs)
            self.assertEqual(status, 403)
            self.assertNotIn('csrf', result)

    def test_trusted_ingress_is_allowed_but_cross_site_is_not(self):
        class H:
            client_address = ('172.30.32.2', 80)
            headers = {'X-Ingress-Path': '/api/hassio_ingress/example/', 'Host': 'supervisor',
                       'X-Forwarded-Host': 'edge.example.test', 'Origin': 'https://edge.example.test'}
        self.assertTrue(setup.trusted(H()))
        h = H(); h.headers = {**h.headers, 'Sec-Fetch-Site': 'cross-site'}
        self.assertFalse(setup.trusted(h))

    def test_setup_body_handles_ingress_chunking_with_a_small_read_limit(self):
        from test_edge_panel_config import load_panel_module
        panel = load_panel_module()
        handler = panel.EdgeHandler.__new__(panel.EdgeHandler)
        handler.headers = {'Transfer-Encoding': 'chunked'}
        body = json.dumps({'csrf': setup.CSRF, 'mode': 'local'}).encode()
        handler.rfile = io.BytesIO(f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n')
        self.assertEqual(handler.read_setup_json()['mode'], 'local')
        handler.rfile = io.BytesIO(b'1001\r\n' + b'x' * 4097 + b'\r\n0\r\n\r\n')
        with self.assertRaises(ValueError): handler.read_setup_json()


class CandidateDiscoveryTests(unittest.TestCase):
    def test_default_candidates_never_probe_a_personal_ip(self):
        source = APP / 'custom_components/edge/config_flow.py'
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node, ast.FunctionDef) and node.name == '_candidate_urls')
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(source), 'exec'), namespace)
        candidates = namespace['_candidate_urls']('')
        for candidate in candidates:
            from urllib.parse import urlsplit
            host = urlsplit(candidate).hostname
            if host:
                with self.assertRaises(ValueError): ipaddress.ip_address(host)
        chosen = 'https://192.0.2.70:8088'
        self.assertEqual(namespace['_candidate_urls'](chosen)[0], chosen)


if __name__ == '__main__': unittest.main()
