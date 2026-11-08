import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module


class RecordingCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module()
        from edge_recording_compat import CompatibleRecordings
        from edge_recording_media import video_timing
        self.Service,self.timing=CompatibleRecordings,video_timing
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.cache=self.Service(self.root/'cache')

    def sample(self, codec='libx264'):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
        path=self.root/f'{codec}.mp4'
        command=['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=20',
                 '-t','1.5','-c:v',codec,'-threads','1']
        if codec=='libx265':command+=['-x265-params','pools=none:frame-threads=1:log-level=error','-tag:v','hev1']
        subprocess.run([*command,'-movflags','+faststart',str(path)],check=True,capture_output=True)
        return path

    def test_mobile_h264_and_legacy_hevc_preserve_all_pixels_fps_and_originals(self):
        for codec,entry in [('libx264','avc1'),('libx265','hvc1')]:
            source=self.sample(codec);before=hashlib.sha256(source.read_bytes()).digest()
            copy=self.cache.prepare(source)
            self.assertEqual(self.timing(copy)['sample_entry'],entry)
            self.assertEqual(self.timing(copy)['fps'],20)
            self.assertEqual(self.timing(copy)['duration'],self.timing(source)['duration'])
            def frames(path):
                result=subprocess.run(['ffmpeg','-v','error','-threads','1','-i',str(path),'-threads','1','-f','framemd5','-'],capture_output=True,text=True,check=True)
                return [line.rsplit(',',1)[-1] for line in result.stdout.splitlines() if line and not line.startswith('#')]
            self.assertEqual(frames(source),frames(copy))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
            with patch('edge_recording_compat.subprocess.run',side_effect=AssertionError('Range requests must reuse the cached remux')):
                self.assertEqual(self.cache.prepare(source),copy)

    def test_cache_budget_and_retention_preserve_originals_foreign_files_and_links(self):
        source=self.sample();cache=self.Service(self.root/'cache',budget=3*1024*1024,max_files=1)
        first=cache.prepare(source)
        foreign=first.parent/'family.mp4';foreign.write_bytes(b'keep')
        linked=first.parent/('a'*64+'.mp4');linked.symlink_to(source)
        other=self.root/'other.mp4';shutil.copyfile(source,other)
        abandoned=first.parent/'remux.tmp';abandoned.write_bytes(b'interrupted remux')
        second=cache.prepare(other)
        self.assertFalse(abandoned.exists())
        self.assertFalse(first.exists());self.assertTrue(second.exists())
        self.assertTrue(foreign.exists());self.assertTrue(linked.is_symlink());self.assertTrue(source.exists())
        self.assertLessEqual(sum(p.stat().st_size for p in second.parent.glob('*.mp4') if not p.is_symlink() and len(p.stem)==64),cache.budget)
        with patch('edge_recording_compat.subprocess.run',side_effect=AssertionError('Over budget must never transcode')):
            with self.assertRaisesRegex(RuntimeError,'budget'):self.Service(self.root/'small',budget=1).prepare(source)

    def test_recording_uses_source_timestamps_and_hvc1_without_transcoding(self):
        command=self.p.build_recording_command('rtsp://127.0.0.1:8554/front','test-%Y.mp4',10,'copy','hevc')
        self.assertNotIn('-use_wallclock_as_timestamps',command)
        self.assertEqual(command[command.index('-tag:v')+1],'hvc1')
        self.assertEqual(command[command.index('-c:v')+1],'copy')
        self.assertEqual(command[command.index('-af')+1],'aresample=async=1:first_pts=0,asetpts=N/SR/TB')

    def test_backward_audio_clock_is_repaired_without_nonmonotonic_dts(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg required')
        audio='sine=sample_rate=48000:duration=3,asetpts=PTS-if(gte(T\\,1)\\,0.03/TB\\,0)'
        common=['ffmpeg','-hide_banner','-loglevel','warning','-f','lavfi','-i',audio]
        broken=subprocess.run([*common,'-c:a','aac',str(self.root/'broken.mp4')],capture_output=True,text=True,check=True)
        self.assertIn('backward in time',broken.stderr)
        command=self.p.build_recording_command('rtsp://127.0.0.1:8554/front','unused',10,'copy','hevc')
        fixed=subprocess.run([*common,'-af',command[command.index('-af')+1],'-c:a','aac',str(self.root/'fixed.mp4')],capture_output=True,text=True,check=True)
        self.assertNotIn('Non-monotonic',fixed.stderr);self.assertNotIn('backward in time',fixed.stderr)

    def test_cache_rejects_symlink_and_removes_failed_temporary(self):
        source=self.sample();link=self.root/'link.mp4';link.symlink_to(source)
        with self.assertRaisesRegex(RuntimeError,'invalid_recording_source'):self.cache.prepare(link)
        with patch('edge_recording_compat.subprocess.run',side_effect=subprocess.TimeoutExpired('ffmpeg',20)):
            with self.assertRaises(subprocess.TimeoutExpired):self.cache.prepare(source)
        self.assertFalse((self.cache.directory/'remux.tmp').exists());self.assertTrue(source.exists())

    def test_old_video_track_with_large_start_offset_has_no_black_lead_in(self):
        source=self.sample();delayed=self.root/'delayed.mp4'
        subprocess.run(['ffmpeg','-v','error','-itsoffset','9','-i',str(source),'-c','copy','-avoid_negative_ts','disabled',str(delayed)],check=True)
        def start(path):
            result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=start_time','-of','json',str(path)],capture_output=True,text=True,check=True)
            return float(json.loads(result.stdout)['streams'][0]['start_time'])
        self.assertGreater(start(delayed),8)
        prepared=self.cache.prepare(delayed)
        self.assertLess(abs(start(prepared)),.03)
        self.assertEqual(self.timing(prepared)['fps'],self.timing(delayed)['fps'])

    def test_previous_recorder_warnings_do_not_masquerade_as_current_failures(self):
        log=self.root/'ffmpeg.log'
        content='old run: Non-monotonic DTS; error\n=== Edge recording start ===\n{"event":"recording_start_command"}\n'
        log.write_text(content)
        current=self.p.current_recording_log_tail(log)
        self.assertNotIn('Non-monotonic',current);self.assertIn('recording_start_command',current)
        self.assertEqual(log.read_text(),content,'Keep full historical logs on disk')

    def test_regular_aac_is_copied_and_cached_reads_do_not_wait_for_another_worker(self):
        source=self.sample();audio=self.root/'audio.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-f','lavfi','-i','sine=sample_rate=48000',
                        '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-t','1.5',str(audio)],check=True)
        self.assertTrue(self.timing(audio)['audio_clock_regular'])
        original_run=subprocess.run
        with patch('edge_recording_compat.subprocess.run',wraps=original_run) as run:
            target=self.cache.prepare(audio)
        command=run.call_args.args[0]
        self.assertEqual(command[command.index('-c:a')+1],'copy')
        def aac_packets(path):
            result=original_run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','adts','pipe:1'],capture_output=True,check=True)
            return result.stdout
        self.assertEqual(aac_packets(audio),aac_packets(target))
        with self.cache.lock:
            self.assertEqual(self.cache.prepare(audio),target,'An unrelated warming job must not block a cached Range request')

    def test_irregular_aac_is_detected_and_repaired_instead_of_copied(self):
        source=self.sample();audio=self.root/'backward-audio.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-f','lavfi','-i','sine=sample_rate=48000,asetpts=PTS-if(gte(T\\,0.5)\\,0.03/TB\\,0)',
                        '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-t','1.5',str(audio)],check=True,capture_output=True)
        self.assertFalse(self.timing(audio)['audio_clock_regular'])
        original_run=subprocess.run
        with patch('edge_recording_compat.subprocess.run',wraps=original_run) as run:target=self.cache.prepare(audio)
        command=run.call_args.args[0]
        self.assertEqual(command[command.index('-c:a')+1],'aac')
        self.assertTrue(self.timing(target)['audio_clock_regular'])

    def test_valid_original_bypasses_remux_and_budget_even_when_worker_is_busy(self):
        source=self.sample();cache=self.Service(self.root/'no-copy',budget=1)
        before=hashlib.sha256(source.read_bytes()).digest()
        self.assertTrue(self.timing(source)['faststart'])
        self.assertTrue(self.timing(source)['video_zero_start'])
        with cache.lock,patch('edge_recording_compat.subprocess.run',side_effect=AssertionError('No subprocess for valid source')):
            self.assertEqual(cache.prepare(source,allow_passthrough=True),source)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
        self.assertFalse(cache.directory.exists())

    def test_old_hev1_and_delayed_originals_still_require_normalization(self):
        source=self.sample('libx265')
        self.assertNotEqual(self.cache.prepare(source,allow_passthrough=True),source)
        delayed=self.root/'later.mp4'
        subprocess.run(['ffmpeg','-v','error','-itsoffset','9','-i',str(source),'-c','copy','-movflags','+faststart',str(delayed)],check=True)
        self.assertFalse(self.timing(delayed)['video_zero_start'])
        self.assertNotEqual(self.cache.prepare(delayed,allow_passthrough=True),delayed)

    def test_regular_aac_with_a_large_audio_lead_in_is_not_mistaken_for_a_direct_file(self):
        source=self.sample();audio=self.root/'late-audio.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-itsoffset','0.8','-f','lavfi','-i','sine=sample_rate=48000',
                        '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-t','1.5','-movflags','+faststart',str(audio)],check=True)
        self.assertTrue(self.timing(audio)['video_zero_start']);self.assertFalse(self.timing(audio)['audio_zero_start'])
        target=self.cache.prepare(audio,allow_passthrough=True)
        self.assertNotEqual(target,audio);self.assertTrue(self.timing(target)['audio_zero_start'])

    def test_explicit_codec_tests_cover_both_video_codecs_and_g711_audio_without_touching_original(self):
        source=self.sample();sound=self.root/'sound.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-f','lavfi','-i','sine=sample_rate=48000',
                        '-c:v','copy','-c:a','aac','-t','1.5',str(sound)],check=True)
        original=hashlib.sha256(sound.read_bytes()).digest()
        for video,audio,want_video,want_audio in [('source','none','h264',None),('h264','aac','h264','aac'),('hevc','aac','hevc','aac'),('source','alaw','h264','pcm_alaw'),('source','ulaw','h264','pcm_mulaw')]:
            target,report=self.cache.test_clip(sound,video,audio,.2)
            data=json.loads(subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(target)],capture_output=True,text=True,check=True).stdout)
            self.assertEqual(next(s['codec_name'] for s in data['streams'] if s['codec_type']=='video'),want_video)
            self.assertEqual(next((s['codec_name'] for s in data['streams'] if s['codec_type']=='audio'),None),want_audio)
            info=self.timing(target);self.assertEqual((info['width'],info['height'],info['fps']),(160,90,20))
            self.assertLess(info['duration'],6.5);self.assertTrue(report['original_unchanged'])
            self.assertEqual(report['video_transcoded'],video=='hevc')
            self.assertEqual(hashlib.sha256(sound.read_bytes()).digest(),original)
        with self.cache.lock:
            with self.assertRaisesRegex(RuntimeError,'busy'):self.cache.test_clip(sound)
        for kwargs in ({'video':'invalid'},{'audio':'opus'},{'start':float('nan')},{'start':-1}):
            with self.assertRaises(ValueError):self.cache.test_clip(sound,**kwargs)
        reused,details=self.cache.test_clip(sound,'source','ulaw',.2)
        self.assertTrue(details['cached']);self.assertEqual(details['measured_streams'][1]['codec_name'],'pcm_mulaw')

    def test_test_worker_timeout_cleans_temporary_file_and_releases_lock(self):
        source=self.sample()
        with patch('edge_recording_compat.subprocess.run',side_effect=subprocess.TimeoutExpired('ffmpeg',90)):
            with self.assertRaises(subprocess.TimeoutExpired):self.cache.test_clip(source,audio='none')
        self.assertFalse(self.cache.lock.locked());self.assertFalse((self.cache.directory/'remux.tmp').exists())
