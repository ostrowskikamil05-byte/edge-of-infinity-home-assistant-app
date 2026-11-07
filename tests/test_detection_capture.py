import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_detection import module


class DetectionCaptureTests(unittest.TestCase):
    def setUp(self):
        self.d=module()
        import edge_analysis_capture
        self.m=edge_analysis_capture

    def test_ppm_geometry_channels_and_padding_need_no_log_regex(self):
        pixels,width,height=self.m.read_ppm(io.BytesIO(b'P6\n2 1\n255\n'+bytes([255,0,0,0,0,255])))
        frame=self.d.AnalysisFrame(pixels,width,height)
        offset=(frame.top*416+frame.left)*3
        self.assertEqual(pixels[offset:offset+6],bytes([0,0,255,255,0,0]))
        self.assertEqual(len(pixels),416*416*3)
        with self.assertRaises(RuntimeError):self.m.read_ppm(io.BytesIO(b'P6\n9000 9000\n255\n'))

    def test_sampler_accepts_only_shared_relay_and_decodes_between_keyframes(self):
        for address in ('rtsp://user:secret@127.0.0.1:8556/main','rtsp://192.0.2.5/live','https://127.0.0.1:8556/main'):
            with self.assertRaisesRegex(RuntimeError,'shared_source_required'):self.m.analysis_command(address,1)
        command=self.m.analysis_command('rtsp://127.0.0.1:8556/main',1)
        self.assertNotIn('-skip_frame',command)
        self.assertIn('fps=1.0000,scale=416:416:force_original_aspect_ratio=decrease',command)
        self.assertEqual(command[command.index('-threads')+1],'1')

    def test_superseded_camera_cannot_start_a_late_decoder_after_disabling_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            service=self.d.DetectionService(directory,lambda *a:None,lambda *a:'rtsp://127.0.0.1:8556/main',lambda *a:None)
            try:
                with patch.object(self.d,'SharedFrameReader',side_effect=AssertionError('Disabled AI must not start a decoder')):
                    with self.assertRaisesRegex(RuntimeError,'configuration_changed'):
                        service._analysis_frame({'id':'front','enabled':True,'local_detection_enabled':True},0,1)
            finally:service.close()

    def test_persistent_reader_keeps_only_latest_frame_and_closes_decoder(self):
        program="import sys,time\nfor i in range(80):\n sys.stdout.buffer.write(b'P6\\n1 1\\n255\\n'+bytes([i,0,0]));sys.stdout.buffer.flush();time.sleep(.025)\n"
        with patch.object(self.m,'analysis_command',return_value=[sys.executable,'-c',program]):
            reader=self.m.SharedFrameReader('rtsp://127.0.0.1:8556/main',1)
        try:
            first,stamp=reader.read(timeout=3)
            process=reader.process
            time.sleep(.25)
            second,stamp2=reader.read(timeout=3)
            self.assertIs(reader.process,process)
            self.assertGreater(reader.sequence,5)
            self.assertNotEqual(first[0],second[0]);self.assertGreater(stamp2,stamp)
            self.assertLess(time.time()-stamp2,.2)
        finally:reader.close()
        self.assertIsNotNone(process.poll())

    def test_capture_error_is_reported_without_credentials_and_logs_include_state(self):
        def failed(url):raise RuntimeError('analysis_frame_timeout')
        with tempfile.TemporaryDirectory() as directory,patch.object(self.d,'NanoDet',lambda:object()):
            service=self.d.DetectionService(directory,lambda *a:None,lambda *a:'rtsp://127.0.0.1:8556/main',lambda *a:None,frame_source=failed)
            try:
                service.apply({'detection':{'local_enabled':True},'cameras':[{'id':'front','enabled':True,'local_detection_enabled':True}]})
                deadline=time.monotonic()+3
                while not service.snapshot()['cameras'][0]['local_error']:
                    self.assertLess(time.monotonic(),deadline);time.sleep(.01)
                camera=service.snapshot()['cameras'][0]
                self.assertEqual(camera['local_status'],'error')
                self.assertEqual(camera['local_error']['code'],'analysis_frame_timeout')
                self.assertEqual(camera['local_error']['phase'],'capture')
                from test_edge_panel_config import load_panel_module
                p=load_panel_module();p.DETECTION_SERVICE=service
                with patch.object(p,'engine_runtime_status',return_value={}),patch.object(p,'system_diagnostics_payload',return_value={}):
                    log=p.collect_panel_logs()
                self.assertEqual(log['detection']['cameras'][0]['local_error']['code'],'analysis_frame_timeout')
                self.assertTrue(any(row['title']=='Local AI' and row['severity']=='error' for row in log['diagnostics']))
            finally:service.close()

    def test_actual_model_detects_animal_between_keyframes_from_one_decoder(self):
        model,image=os.environ.get('EDGE_TEST_MODEL'),os.environ.get('EDGE_TEST_IMAGE')
        if not model or not image or not shutil.which('ffmpeg'):self.skipTest('Actual model, image and FFmpeg are required')
        import cv2
        scene=cv2.resize(cv2.imread(image),(416,416)).tobytes()
        # One I-frame, which is black. The animal enters only in P-frames.
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'long-gop.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','rawvideo','-pix_fmt','bgr24','-s','416x416','-r','10','-i','pipe:0','-c:v','libx264','-threads','1','-g','100','-keyint_min','100','-sc_threshold','0','-bf','0','-movflags','+faststart',str(path)],input=bytes(len(scene))*15+scene*30,capture_output=True,check=True)
            command=['ffmpeg','-v','error','-re','-stream_loop','-1','-threads','1','-filter_threads','1','-i',str(path),'-an','-vf','fps=1,scale=416:416','-c:v','ppm','-pix_fmt','rgb24','-f','image2pipe','pipe:1']
            with patch.object(self.m,'analysis_command',return_value=command):reader=self.m.SharedFrameReader('rtsp://127.0.0.1:8556/main',1)
            try:
                detector=self.d.NanoDet(model);found=False;process=None
                for _ in range(4):
                    (pixels,width,height),stamp=reader.read(timeout=10)
                    process=process or reader.process;self.assertIs(reader.process,process)
                    if any(obj['kind']=='animal' for obj in detector.infer(pixels,.35)):found=True;break
                self.assertTrue(found,'Sampling only I-frames would never see this animal')
            finally:reader.close()
