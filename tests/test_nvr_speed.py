import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_edge_panel_config import load_panel_module


class NvrSpeedTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module();self.p.MIN_RECORDING_FILE_READY_SECONDS=0

    def test_unchanged_recording_index_is_not_retransmitted_and_changes_invalidate_revision(self):
        files=[{'name':str(i),'size_bytes':10000,'duration_seconds':10,'timeline_offset':i*10,'playback_offset':i*10} for i in range(12000)]
        revision=self.p.recording_files_revision('front','2026-11-07',files)
        data={'cameras':[{'index':0,'key':'front','selected_day':'2026-11-07','files':files,'files_revision':revision}]}
        with patch.object(self.p,'recording_status_payload',return_value=data):
            response=self.p.recording_status_response({}, {'known':['0:'+revision]})
        self.assertNotIn('files',response['cameras'][0]);self.assertTrue(response['cameras'][0]['files_unchanged'])
        files[0]['duration_seconds']=12
        self.assertNotEqual(self.p.recording_files_revision('front','2026-11-07',files),revision)
        self.assertNotEqual(self.p.recording_files_revision('front','2026-11-06',files),revision)

    def test_open_recording_is_pending_even_when_its_buffer_has_not_flushed_recently(self):
        folder=self.p.HOME_DIR/'recordings';folder.mkdir(parents=True)
        path=folder/'20261107-080000.mp4'
        with path.open('wb') as writer:
            writer.write(b'x'*4096);writer.flush();old=time.time()-40;os.utime(path,(old,old))
            self.p.RECORDING_PROCESSES['front']=SimpleNamespace(pid=os.getpid())
            rows,total=self.p.recording_file_candidates(folder)
            self.assertEqual((rows,total),([],1))
            with self.assertRaisesRegex(RuntimeError,'source_not_ready'):
                self.p.ensure_recording_thumbnail('front',path,path.name)
        rows,total=self.p.recording_file_candidates(folder)
        self.assertEqual(len(rows),1);self.assertEqual(total,1)
        self.assertEqual(path.stat().st_size,4096)

    def test_zero_second_preview_and_poster_reuse_same_jpeg_without_decoder(self):
        source=self.p.HOME_DIR/'20261107-080000.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'x'*4096)
        old=time.time()-10;os.utime(source,(old,old))
        jpeg=self.p.recording_thumbnail_path('front',source.name);jpeg.parent.mkdir(parents=True);jpeg.write_bytes(b'\xff\xd8\xff'+b'x'*900)
        with patch.object(self.p,'subprocess_run_low_priority',side_effect=AssertionError('Cached preview must not decode again')):
            self.assertEqual(self.p.ensure_recording_thumbnail('front',source,source.name,seek_seconds=0),jpeg)
            self.assertEqual(self.p.ensure_recording_thumbnail('front',source,source.name),jpeg)

    def test_simultaneous_thumbnail_requests_decode_only_once(self):
        source=self.p.HOME_DIR/'20261107-080000.mp4';source.parent.mkdir(parents=True);source.write_bytes(b'x'*4096)
        old=time.time()-10;os.utime(source,(old,old))
        entered,release=threading.Event(),threading.Event();result=[];errors=[]
        def decode(command,**kwargs):
            entered.set();release.wait(2);Path(command[-1]).write_bytes(b'\xff\xd8\xff'+b'x'*900)
            return SimpleNamespace(returncode=0,stderr=b'')
        def request():
            try:result.append(self.p.ensure_recording_thumbnail('front',source,source.name))
            except Exception as error:errors.append(error)
        with patch.object(self.p.shutil,'which',return_value='/usr/bin/ffmpeg'),patch.object(self.p,'subprocess_run_low_priority',side_effect=decode) as process:
            first=threading.Thread(target=request);first.start();self.assertTrue(entered.wait(1))
            second=threading.Thread(target=request);second.start();time.sleep(.02);release.set();first.join(3);second.join(3)
        self.assertEqual(errors,[]);self.assertEqual(len(result),2);self.assertEqual(process.call_count,1)
