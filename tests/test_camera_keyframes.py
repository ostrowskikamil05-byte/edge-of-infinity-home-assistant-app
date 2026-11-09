import copy
import importlib.util
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from test_edge_panel_config import ROOT, camera, load_panel_module

spec=importlib.util.spec_from_file_location('camera_keyframes_test',ROOT/'edge-of-infinity/rootfs/usr/share/edge-of-infinity/edge_camera_keyframes.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class KeyframeTests(unittest.TestCase):
    def setUp(self):
        self.camera={**camera('front','192.0.2.10','sub'),'onvif_url':'http://192.0.2.10/onvif/device_service'}
        self.calls=[];self.events=[];self.now=100

    def soap(self,camera,endpoint,action,body):
        self.calls.append((action,body))
        if action.endswith('GetCapabilities'):return ET.fromstring('<Capabilities><Media><XAddr>http://192.0.2.10/media</XAddr></Media></Capabilities>')
        if action.endswith('GetProfiles'):return ET.fromstring('<GetProfilesResponse><Profiles token="wrong"/><Profiles token="main&amp;token"/></GetProfilesResponse>')
        if action.endswith('GetStreamUri'):
            path='102' if '>wrong<' in body else '101'
            return ET.fromstring('<Response><Uri>rtsp://192.0.2.10/Streaming/Channels/'+path+'</Uri></Response>')
        if action.endswith('SetSynchronizationPoint'):return ET.fromstring('<SetSynchronizationPointResponse/>')
        self.fail(action)

    def service(self):
        return module.CameraKeyframes(lambda:{'cameras':[self.camera]},self.soap,lambda event,data:self.events.append((event,data)),clock=lambda:self.now)

    def test_matches_recorded_profile_caches_discovery_and_never_changes_encoder(self):
        service=self.service();original=copy.deepcopy(self.camera)
        service.slots.acquire();service.run(self.camera)
        self.assertEqual(len(self.calls),5);self.assertIn('main&amp;token',self.calls[-1][1])
        self.assertTrue(self.calls[-1][0].endswith('SetSynchronizationPoint'))
        service.slots.acquire();service.run(self.camera)
        self.assertEqual(len(self.calls),6,'cached join repeated discovery')
        self.assertEqual(self.camera,original);self.assertFalse(self.events[-1][1]['encoder_settings_changed'])
        self.assertFalse(any('SetVideoEncoder' in action for action,_ in self.calls))

    def test_unmatched_recording_is_not_signaled_and_failure_backs_off(self):
        self.camera['rtsp_main']='rtsp://192.0.2.10/Streaming/Channels/999'
        service=self.service();service.slots.acquire();service.run(self.camera)
        self.assertFalse(any(action.endswith('SetSynchronizationPoint') for action,_ in self.calls))
        self.assertEqual(self.events[-1][0],'keyframe_onvif_unavailable')
        self.assertFalse(service.request('front'));self.assertEqual(service.next['front'],160)

    def test_cooldown_worker_limit_disabled_and_unknown_cameras(self):
        service=self.service();entered=threading.Event();release=threading.Event()
        original=service.soap
        def blocked(*args):entered.set();release.wait(2);return original(*args)
        service.soap=blocked
        self.assertTrue(service.request('front'));self.assertTrue(entered.wait(1))
        self.assertFalse(service.request('front'));self.assertFalse(service.request('missing'))
        self.camera['enabled']=False;self.assertFalse(service.request('front'));release.set()
        self.camera['enabled']=True
        self.assertFalse(service.request('front'))

    def test_uri_matching_keeps_channel_query_and_ignores_transport(self):
        identity=module.stream_identity
        self.assertEqual(identity('rtsp://user:secret@cam/c1?transportmode=unicast'),identity('rtsp://cam/c1'))
        self.assertNotEqual(identity('rtsp://cam/realmonitor?channel=1&subtype=0'),identity('rtsp://cam/realmonitor?channel=1&subtype=1'))

    def test_uniview_known_aliases_keep_channel_stream_and_explicit_profile(self):
        identity=module.stream_identity
        self.assertEqual(identity('rtsp://cam/media/video1','uniview'),identity('rtsp://cam/unicast/c1/s0/live','uniview'))
        self.assertEqual(identity('rtsp://cam/media/video2','uniview'),identity('rtsp://cam/unicast/c1/s1/live','uniview'))
        for other in ('/unicast/c1/s1/live','/unicast/c2/s0/live','/custom/main','/unicast/c1/s0/live?profile=other'):
            self.assertNotEqual(identity('rtsp://cam/media/video1','uniview'),identity('rtsp://cam'+other,'uniview'))
        self.assertNotEqual(identity('rtsp://cam/media/video1'),identity('rtsp://cam/unicast/c1/s0/live'))

    def test_uniview_alias_resolution_skips_an_unavailable_profile_without_writes(self):
        self.camera.update(vendor='uniview',rtsp_main='rtsp://user:secret@192.0.2.10/media/video1')
        service=self.service();original=service.soap
        def soap(camera,endpoint,action,body):
            if action.endswith('GetStreamUri'):
                self.calls.append((action,body))
                if '>wrong<' in body:raise RuntimeError('profile URI unavailable')
                return ET.fromstring('<Response><Uri>rtsp://192.0.2.10/unicast/c1/s0/live</Uri></Response>')
            return original(camera,endpoint,action,body)
        service.soap=soap;service.slots.acquire();service.run(self.camera,synchronize=False)
        self.assertEqual(self.events[-1][0],'keyframe_onvif_profile_ready')
        self.assertFalse(any('Set' in action.rsplit('/',1)[-1] for action,_ in self.calls))

    def test_unmatched_profile_diagnostics_do_not_claim_recording_dependency(self):
        self.camera['rtsp_main']='rtsp://user:DO_NOT_LOG@192.0.2.10/custom'
        service=self.service();service.slots.acquire();service.run(self.camera)
        event,payload=self.events[-1]
        self.assertEqual(payload['code'],'keyframe_recording_profile_not_found')
        self.assertEqual(payload['profile_count'],2);self.assertFalse(payload['recording_dependency'])
        self.assertNotIn('DO_NOT_LOG',str(payload))

    def test_boot_discovery_does_not_request_keyframes_or_write_encoder(self):
        service=self.service();service.slots.acquire();service.run(self.camera,synchronize=False)
        self.assertEqual(len(self.calls),4)
        self.assertFalse(any('Set' in action.rsplit('/',1)[-1] for action,_ in self.calls))
        self.assertEqual(self.events[-1][0],'keyframe_onvif_profile_ready')
        service.slots.acquire();service.run(self.camera)
        self.assertEqual(len(self.calls),5,'first viewer repeated boot discovery')
        self.assertTrue(self.calls[-1][0].endswith('SetSynchronizationPoint'))

    def test_viewer_join_during_discovery_coalesces_into_one_sync(self):
        service=self.service();entered=threading.Event();release=threading.Event();done=threading.Event()
        original=service.soap
        def blocked(*args):
            if args[2].endswith('GetCapabilities'):entered.set();release.wait(2)
            return original(*args)
        service.soap=blocked
        original_log=service.log
        def log(*args):original_log(*args);done.set()
        service.log=log;service.warm();self.assertTrue(entered.wait(1))
        self.assertTrue(service.request('front'));self.assertTrue(service.request('front'))
        release.set();self.assertTrue(done.wait(2))
        self.assertEqual(sum(a.endswith('SetSynchronizationPoint') for a,_ in self.calls),1)
        self.assertEqual(len(self.calls),5)

    def test_endpoint_requires_private_token(self):
        panel=load_panel_module();handler=panel.EdgeHandler.__new__(panel.EdgeHandler)
        handler.path='/api/edge-rtc/keyframe/front';handler.headers={};handler.client_address=('127.0.0.1',1)
        panel.HOME_DIR.mkdir(parents=True,exist_ok=True);(panel.HOME_DIR/'edge-rtc-token').write_text('private-test-token')
        with patch.object(panel.CAMERA_KEYFRAMES,'request',return_value=True) as request,patch.object(handler,'send_json') as send:
            handler.do_POST();self.assertEqual(send.call_args.args[1],403);request.assert_not_called()
            handler.headers={'Authorization':'Bearer private-test-token'};handler.do_POST();self.assertEqual(send.call_args.args[1],202);request.assert_called_once_with('front')


if __name__=='__main__':unittest.main()
