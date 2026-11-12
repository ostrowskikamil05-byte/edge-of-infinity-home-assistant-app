import copy
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET

from test_detection import module
from test_edge_panel_config import camera, load_panel_module


def configured_zone(**kwargs):
    return {'detection_zone_enabled':True,'detection_zone_left':.5,'detection_zone_top':.2,'detection_zone_right':.95,'detection_zone_bottom':.9,**kwargs}


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Worker did not produce expected state')
        time.sleep(.01)


class DetectionZoneTests(unittest.TestCase):
    def test_bottom_center_membership_boundaries_and_invalid_zone_fail_closed(self):
        m = module()
        objects = [{'kind':'person','box':[.1,.1,.7,.6]}, {'kind':'vehicle','box':[.5,.1,.8,.8]}, {'kind':'person','box':[.5,.1,.8,.95]}, {'kind':'person','box':[.4,.1,.6,.2]}]
        self.assertEqual(m.zone_objects(objects,configured_zone()),[objects[1],objects[3]])
        self.assertEqual(m.zone_objects(objects,{}),objects)
        for value in (float('nan'),float('inf'),-1,2,'bad'):
            self.assertFalse(m.zone_settings(configured_zone(detection_zone_left=value))['valid'])
            self.assertEqual(m.zone_objects(objects,configured_zone(detection_zone_left=value)),[])
        self.assertEqual(m.zone_objects(objects,configured_zone(detection_zone_right=.5)),[])
        self.assertTrue(m.zone_settings(configured_zone(detection_zone_left=.1,detection_zone_right=.105,detection_zone_top=0,detection_zone_bottom=.005))['valid'])

    def test_model_boxes_are_unpadded_before_zone_selection_for_landscape_and_portrait(self):
        m = module()
        for width,height in [(416,234),(234,416),(416,416)]:
            frame = m.AnalysisFrame(bytes(416*416*3),width,height)
            box = [(frame.left+.5*width)/416,(frame.top+.1*height)/416,(frame.left+.8*width)/416,(frame.top+.8*height)/416]
            result = frame.scene_objects([{'kind':'person','score':.91,'box':box}])
            self.assertEqual(result[0]['box'],[.5,.1,.8,.8])
            self.assertEqual(m.zone_objects(result,configured_zone()),result)
            self.assertEqual(m.zone_objects(result,configured_zone(detection_zone_top=.81)),[])
        frame = m.AnalysisFrame(bytes(416*416*3),416,234)
        self.assertEqual(frame.scene_objects([{'kind':'person','box':[0,0,.5,.1]}]),[])
        with self.assertRaises(RuntimeError):
            m.AnalysisFrame(b'incomplete',416,234)

    def test_zone_settings_survive_authoritative_restart_and_invalid_save_preserves_file(self):
        p = load_panel_module()
        config = p.normalize_config({'cameras':[{**camera('front','192.0.2.10','sub'),**configured_zone()}]})
        saved = p.persist_config_payload(config)[-1]
        self.assertTrue(saved['cameras'][0]['detection_zone_enabled'])
        self.assertEqual(saved['cameras'][0]['detection_zone_left'],.5)
        options = p.addon_options_from_config(saved,{})
        self.assertEqual(options['cameras'], [])
        restarted = p.load_config()
        for field in module().ZONE_FIELDS:
            self.assertEqual(restarted['cameras'][0][field],saved['cameras'][0][field])
        before = p.CONFIG_PATH.read_bytes()
        for bad in [.95,float('nan'),2]:
            changed = copy.deepcopy(saved);changed['cameras'][0]['detection_zone_left']=bad
            with self.assertRaises(ValueError):p.persist_config_payload(changed)
            self.assertEqual(p.CONFIG_PATH.read_bytes(),before)
        invalid_startup = p.normalize_config({'cameras':[{**camera('front','192.0.2.10','sub'),**configured_zone(detection_zone_right=.1)}]})
        self.assertTrue(invalid_startup['cameras'][0]['detection_zone_enabled'])
        self.assertFalse(module().zone_settings(invalid_startup['cameras'][0])['valid'])
        with patch.object(p,'stop_recording') as stop:
            p.RECORDING_PROCESSES[p.recording_key(saved['cameras'][0],0)]=object()
            original=copy.deepcopy(saved);original['cameras'][0]['detection_zone_enabled']=False
            self.assertEqual(p.restart_changed_recordings(original,saved),[])
            stop.assert_not_called()

    def test_local_worker_filters_before_history_and_states_and_zone_edits_clear_previous_state(self):
        m = module()
        class Detector:
            def infer(self,pixels,confidence):
                return [{'kind':'person','score':.9,'box':[.1,.1,.3,.7]}, {'kind':'vehicle','score':.8,'box':[.6,.1,.8,.7]}]
        def capture(url):
            self.assertEqual(url,'rtsp://127.0.0.1:8556/front_main')
            return m.AnalysisFrame(bytes(416*416*3))
        with tempfile.TemporaryDirectory() as directory, patch.object(m,'NanoDet',Detector), patch.object(m,'capture_frame',capture):
            service=m.DetectionService(directory,lambda *a:self.fail('Unexpected camera HTTP'),lambda *a:'rtsp://127.0.0.1:8556/front_main',lambda *a:None,frame_source=capture)
            try:
                cam={**camera('front','192.0.2.10','sub'),**configured_zone(),'local_detection_enabled':True}
                config={'detection':{'local_enabled':True,'max_fps':3},'cameras':[cam]}
                service.apply(config)
                wait_until(lambda:service.store.recent())
                self.assertEqual([row['kind'] for row in service.store.recent()],['vehicle'])
                self.assertEqual(service.store.recent()[0]['zone_id'],'local_zone')
                self.assertFalse(service.snapshot()['cameras'][0]['states']['person'])
                self.assertTrue(service.snapshot()['cameras'][0]['states']['vehicle'])
                cam={**cam,'detection_zone_left':0,'detection_zone_right':.4}
                service.apply({**config,'cameras':[cam]})
                wait_until(lambda:any(row['kind']=='person' for row in service.store.recent()))
                self.assertFalse(service.snapshot()['cameras'][0]['states']['vehicle'])
                self.assertTrue(service.snapshot()['cameras'][0]['states']['person'])
                # Camera ONVIF events retain their separate camera-side rules.
                service.record('front','vehicle','camera',True)
                self.assertTrue(service.snapshot()['cameras'][0]['states']['vehicle'])
                self.assertNotIn('zone_id',service.store.recent()[0])
            finally:service.close()

    def test_preview_reuses_recent_frame_rejects_camera_urls_and_drops_changed_config(self):
        m = module()
        frame=m.AnalysisFrame(bytes(416*416*3),416,234)
        with tempfile.TemporaryDirectory() as directory, patch.object(m.AnalysisFrame,'jpeg',return_value=b'jpeg'), patch.object(m,'capture_frame',return_value=frame) as capture:
            service=m.DetectionService(directory,lambda *a:None,lambda *a:'rtsp://127.0.0.1:8556/front_main',lambda *a:None)
            try:
                cam=camera('front','192.0.2.10','sub');service.apply({'cameras':[cam]})
                first=service.preview('front');second=service.preview('front')
                self.assertEqual(first,second);self.assertEqual(first[:3],(b'jpeg',416,234));self.assertEqual(capture.call_count,1)
                service.preview_lock.acquire()
                try:
                    with self.assertRaisesRegex(RuntimeError,'busy'):service.preview('front')
                finally:service.preview_lock.release()
                service.apply({'cameras':[]})
                with self.assertRaises(KeyError):service.preview('front')
                self.assertEqual(service.frames,{})
                service.apply({'cameras':[cam]});service.proxy_url=lambda *a:'rtsp://admin:secret@192.0.2.10/live'
                with self.assertRaisesRegex(RuntimeError,'shared_source'):service.preview('front')
                self.assertEqual(capture.call_count,1)
                service.proxy_url=lambda *a:'rtsp://127.0.0.1:8556/front_main'
                entered,release=threading.Event(),threading.Event()
                def paused(url):entered.set();release.wait(2);return frame
                errors=[]
                def preview():
                    try:service.preview('front')
                    except RuntimeError as error:errors.append(str(error))
                with patch.object(m,'capture_frame',paused):
                    thread=threading.Thread(target=preview);thread.start();self.assertTrue(entered.wait(1))
                    service.apply({'cameras':[{**cam,'host':'192.0.2.20'}]});release.set();thread.join(2)
                self.assertEqual(errors,['configuration_changed']);self.assertEqual(service.frames,{})
            finally:service.close()

    def test_zone_edit_does_not_replace_existing_onvif_subscription(self):
        m=module();created=[];pulled=threading.Event();unsubscribed=threading.Event()
        def soap(camera,endpoint,action,body,refs=''):
            if action.endswith('GetCapabilities'):return ET.fromstring('<E><Events XAddr="http://192.0.2.10/events"/></E>')
            if action.endswith('GetEventPropertiesRequest'):return ET.fromstring('<E><TopicSet><MotionAlarm/></TopicSet></E>')
            if action.endswith('CreatePullPointSubscriptionRequest'):
                created.append(1);return ET.fromstring('<E><SubscriptionReference><Address>http://192.0.2.10/pull</Address></SubscriptionReference></E>')
            if action.endswith('PullMessagesRequest'):pulled.set()
            if action.endswith('UnsubscribeRequest'):unsubscribed.set()
            return ET.fromstring('<E/>')
        with tempfile.TemporaryDirectory() as directory:
            service=m.DetectionService(directory,soap,lambda *a:None,lambda *a:None)
            try:
                cam={**camera('front','192.0.2.10','sub'),'events_enabled':True};config={'cameras':[cam],'detection':{'camera_events_enabled':True}}
                service.apply(config);self.assertTrue(pulled.wait(2))
                service.apply({**config,'cameras':[{**cam,**configured_zone()}]});time.sleep(.15)
                self.assertEqual(len(created),1);self.assertFalse(unsubscribed.is_set())
                self.assertEqual(service.snapshot()['cameras'][0]['supported_kinds'],['motion'])
                service.apply({'cameras':[]});self.assertTrue(unsubscribed.wait(2))
            finally:service.close()

    @unittest.skipUnless(os.environ.get('EDGE_TEST_MODEL'), 'Set EDGE_TEST_MODEL for media runtime checks')
    def test_jpeg_preview_contains_only_the_unpadded_scene(self):
        import cv2
        import numpy as np
        m=module()
        for width,height in [(416,234),(234,416)]:
            frame=m.AnalysisFrame(bytes(416*416*3),width,height)
            image=np.zeros((416,416,3),np.uint8)
            image[frame.top:frame.top+height,frame.left:frame.left+width]=[5,100,240]
            frame=m.AnalysisFrame(image.tobytes(),width,height)
            preview=cv2.imdecode(np.frombuffer(frame.jpeg(),np.uint8),cv2.IMREAD_COLOR)
            self.assertEqual(preview.shape,(height,width,3))
            self.assertTrue(np.abs(preview.astype(int)-np.array([5,100,240])).max()<=3)

    def test_preview_api_is_no_store_and_sanitizes_missing_busy_and_capture_errors(self):
        p=load_panel_module()
        preview=Mock(return_value=(b'jpeg',416,234,123.0))
        p.DETECTION_SERVICE=SimpleNamespace(preview=preview)
        handler=object.__new__(p.EdgeHandler);handler.path='/api/detection/preview?camera=front'
        images=[];responses=[]
        handler.send_bytes=lambda payload,content_type,**kwargs:images.append((payload,content_type,kwargs))
        handler.send_json=lambda payload,status=200:responses.append((payload,int(status)))
        handler.do_GET()
        self.assertEqual(images[0][:2],(b'jpeg','image/jpeg'))
        self.assertEqual(images[0][2]['extra_headers']['Cache-Control'],'no-store')
        self.assertEqual(images[0][2]['extra_headers']['X-Edge-Frame-Height'],'234')
        preview.assert_called_once_with('front')
        for error,status,message in [(KeyError('missing'),404,'camera_unavailable'),(RuntimeError('preview_busy'),429,'preview_busy'),(RuntimeError('rtsp://admin:secret@camera/live'),503,'detection_preview_unavailable')]:
            preview.side_effect=error;handler.do_GET()
            self.assertEqual(responses[-1],({'error':message},status))
            self.assertNotIn('secret',str(responses[-1]))
