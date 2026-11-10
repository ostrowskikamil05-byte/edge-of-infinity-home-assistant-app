"""Native-only migration preserves cameras, profiles and recorded media."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from test_edge_panel_config import ROOT, camera


def migration():
    path = ROOT / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity/edge_native_migration.py'
    spec = importlib.util.spec_from_file_location('native_migration_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.m = migration()

    def test_options_keep_camera_secrets_recording_settings_and_custom_ports(self):
        old = {'core_engine': 'mediamtx_janus', 'mediamtx_rtsp_port': 9556,
               'mediamtx_api_port': 10997, 'edge_rtc_rtsp_port': 8556,
               'mediamtx_webrtc_turn_password': 'retired-secret', 'janus_enabled': True,
               'cameras': [camera('unv', '192.0.2.10', 'sub')], 'retention_days': 14,
               'recordings_dir': '/media/user-recordings', 'local_detection_enabled': False}
        before = json.dumps(old, sort_keys=True)
        clean = self.m.native_options(old)
        self.assertEqual(clean['cameras'], old['cameras'])
        self.assertEqual(clean['recordings_dir'], old['recordings_dir'])
        self.assertEqual(clean['retention_days'], 14)
        self.assertFalse(clean['local_detection_enabled'])
        self.assertEqual(clean['edge_rtc_rtsp_port'], 9556)
        self.assertEqual(clean['edge_rtc_api_port'], 10997)
        self.assertFalse(any(key.startswith(('mediamtx_', 'janus_')) or key == 'core_engine' for key in clean))
        self.assertEqual(json.dumps(old, sort_keys=True), before)
        self.assertEqual(self.m.native_options(clean), clean)

    def test_new_listener_choice_has_precedence_and_invalid_old_port_is_bounded(self):
        clean = self.m.native_options({'edge_rtc_rtsp_port': 9557, 'mediamtx_rtsp_port': 9556, 'mediamtx_api_port': 'bad'})
        self.assertEqual(clean['edge_rtc_rtsp_port'], 9557)
        self.assertEqual(clean['edge_rtc_api_port'], 9997)

    def test_cleanup_preserves_each_camera_file_timestamps_and_all_recordings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home, addon, runtime = [root / part for part in ('home', 'addon', 'runtime')]
            for path in (home, addon, runtime):
                path.mkdir()
            original = {'cameras': [camera('unv', '192.0.2.10', 'sub')],
                        'live': {'engine': 'janus_webrtc', 'mobile_webrtc_turn_password': 'old'},
                        'storage': {'retention_days': 14, 'recordings_dir': str(root / 'media')},
                        'nvr': {'segment_seconds': 10, 'browser_playback': 'copy'}}
            stale = {**original, 'cameras': [camera('old-camera', '192.0.2.11', 'main')]}
            self.m.atomic_json(home / 'edge.json', original)
            self.m.atomic_json(home / 'edge.backup.json', stale)
            self.m.atomic_json(addon / 'edge.json', original)
            os.utime(home / 'edge.backup.json', ns=(1000000000, 1000000000))
            before_time = (home / 'edge.backup.json').stat().st_mtime_ns
            (runtime / 'janus').mkdir()
            (runtime / 'janus/janus.plugin.streaming.jcfg').write_text('generated')
            (runtime / 'mediamtx.yml').write_text('generated')
            media = root / 'media'; media.mkdir()
            video = media / '20261110-000000.mp4'; video.write_bytes(b'original recording')
            token = home / 'edge-rtc-token'; token.write_text('native-private-token')
            report = self.m.migrate_saved_files(home, addon, runtime)
            active = json.loads((home / 'edge.json').read_text())
            self.assertEqual(active['cameras'], original['cameras'])
            self.assertEqual(active['storage'], original['storage'])
            self.assertEqual(active['nvr'], original['nvr'])
            self.assertEqual(active['live'], {'engine': 'edge_rtc'})
            self.assertEqual(json.loads((home / 'edge.backup.json').read_text())['cameras'], stale['cameras'])
            self.assertEqual((home / 'edge.backup.json').stat().st_mtime_ns, before_time)
            self.assertEqual((home / 'edge.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(video.read_bytes(), b'original recording')
            self.assertEqual(token.read_text(), 'native-private-token')
            self.assertEqual(report['retired_files_removed'], 2)
            self.assertEqual(report['configs_updated'], 3)
            self.assertNotIn('secret', json.dumps(report))
            again = self.m.migrate_saved_files(home, addon, runtime)
            self.assertEqual(again['configs_updated'], 0)
            self.assertEqual(again['retired_files_removed'], 0)

    def test_cleanup_does_not_follow_old_engine_directory_or_config_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); home=root/'home'; addon=root/'addon'; runtime=root/'runtime'; media=root/'media'
            for path in (home, addon, runtime, media):
                path.mkdir()
            source=media/'camera.json'; source.write_text('{"private":"unchanged"}')
            (home/'edge.json').symlink_to(source)
            (runtime/'janus').symlink_to(media, target_is_directory=True)
            report=self.m.migrate_saved_files(home, addon, runtime)
            self.assertEqual(source.read_text(), '{"private":"unchanged"}')
            self.assertTrue(media.is_dir())
            self.assertFalse((runtime/'janus').exists())
            self.assertIn('edge.json', report['skipped'])

    def test_supervisor_replaces_only_retired_fields_and_network_bindings(self):
        current={'core_engine':'mediamtx', 'janus_enabled':True, 'mediamtx_rtsp_port':8556,
                 'cameras':[camera('current-camera','192.0.2.10','sub')], 'retention_days':21}
        clean=self.m.native_options(current)
        replies=[{'options':current,'network':{'8088/tcp':8088,'8556/tcp':None,'8889/tcp':8889}},
                 {}, {'options':clean,'network':{'8088/tcp':8088,'8556/tcp':None}}]
        calls=[]
        class Response:
            def __init__(self, data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'result':'ok','data':self.data}).encode()
        def request(req, **kwargs):
            self.assertTrue(req.full_url.startswith('http://supervisor/addons/self/'))
            calls.append(req)
            return Response(replies.pop(0))
        result=self.m.sync_supervisor_options({'cameras':[]},'private-token',request)
        self.assertEqual(result,{'status':'synced'})
        payload=json.loads(calls[1].data)
        self.assertEqual(payload['options']['cameras'],current['cameras'])
        self.assertEqual(payload['options']['retention_days'],21)
        self.assertEqual(payload['network'],{'8088/tcp':8088,'8556/tcp':None})
        self.assertNotIn('private-token',json.dumps(result))

    def test_unavailable_supervisor_does_not_leak_credentials_or_claim_success(self):
        request=Mock(side_effect=OSError('private-password'))
        self.assertEqual(self.m.sync_supervisor_options({},'token',request),{'status':'retry_required'})
        self.assertEqual(self.m.sync_supervisor_options({},None,request),{'status':'unavailable'})

    def test_recover_filtered_legacy_ports_without_replacing_resolved_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'native-options.json'
            mounted=self.m.native_options({'cameras':[{'password':'resolved-camera-password'}]})
            persisted={'mediamtx_rtsp_port':9556,'mediamtx_api_port':10997,
                       'cameras':[{'password':'!secret camera_password'}]}
            clean=self.m.native_options(persisted)
            replies=[{'options':persisted},{},{'options':clean}]
            class Response:
                def __init__(self,data):self.data=data
                def __enter__(self):return self
                def __exit__(self,*args):pass
                def read(self):return json.dumps({'result':'ok','data':self.data}).encode()
            calls=[]
            def request(req,**kwargs):
                calls.append(req)
                return Response(replies.pop(0))
            result=self.m.sync_supervisor_options(mounted,'token',request,output_path=output)
            self.assertEqual(result['status'],'synced')
            runtime=json.loads(output.read_text())
            self.assertEqual(runtime['edge_rtc_rtsp_port'],9556)
            self.assertEqual(runtime['edge_rtc_api_port'],10997)
            self.assertEqual(runtime['cameras'][0]['password'],'resolved-camera-password')
            self.assertEqual(json.loads(calls[1].data)['options']['cameras'][0]['password'],'!secret camera_password')
