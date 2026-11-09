import json
import unittest
from unittest.mock import patch

from test_edge_panel_config import camera,load_panel_module


class RecorderMetadataTests(unittest.TestCase):
    def test_live_status_uses_shared_registry_and_not_a_timing_sensitive_rtsp_probe(self):
        p=load_panel_module();p.EDGE_RTC_ENABLED=True
        config=p.normalize_config({'cameras':[camera('unv','192.0.2.15','main')]});p.commit_panel_config(config)
        source={'camera_id':'unv','paths':['unv_main','unv_sub'],'ready':True,'upstream_connections':1,'packets':600,'video':{'codec_name':'h265','parameters_ready':True,'width':3840,'height':2160,'fps':20},'audio':{'codec':'mp4a.40.2','sampleRate':48000,'channels':1}}
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra RTSP probe')):
            row=p.refresh_status()['cameras'][0]
        self.assertEqual(row['status'],'online');self.assertEqual(row['status_source'],'edge_rtc_registry');self.assertEqual(row['live_video_codec'],'hevc');self.assertEqual(row['audio_codec'],'aac');self.assertEqual(row['width'],'3840')
        source['ready']=False
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra RTSP probe')):
            row=p.refresh_status()['cameras'][0]
        self.assertEqual(row['status'],'checking');self.assertNotIn('credentials',row['detail'])

    def test_aac_is_identified_from_existing_registry_without_probe_and_copied(self):
        p=load_panel_module();cam=camera('unv','192.0.2.15','main')
        source={'camera_id':'unv','paths':['unv_main','unv_sub'],'ready':True,'video':{'codec_name':'h265','codec':'hev1.1.6.L120.90'},'audio':{'codec':'mp4a.40.2','sampleRate':48000,'channels':1}}
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[source]}),patch.object(p,'probe_rtsp_stream',side_effect=AssertionError('extra probe')):
            metadata=p.recording_registry_metadata(cam,0,'main',wait_seconds=0)
            mode,video,audio,_=p.recording_codec_mode('rtsp://shared/unv_main',{},metadata)
        command=p.build_recording_command('rtsp://shared/unv_main','record.mp4',10,mode,video,audio,48000,1)
        self.assertEqual(video,'hevc');self.assertEqual(audio,'aac');self.assertEqual(command[command.index('-c:a')+1],'copy');self.assertEqual(command[command.index('-c:v')+1],'copy')
        self.assertNotIn('-af',command);self.assertEqual(command[command.index('-tag:v')+1],'hvc1')
        self.assertGreaterEqual(int(command[command.index('-probesize')+1]),1024*1024)

    def test_aliases_match_and_unready_source_is_not_guessed(self):
        p=load_panel_module();cam=camera('alias','192.0.2.15','main')
        source={'camera_id':'original','paths':['alias_main'],'ready':True,'video':{'codec_name':'h264'},'audio':{'codec':'pcma','sampleRate':8000,'channels':1}}
        with patch.object(p,'edge_rtc_status',return_value={'sources':[source]}):
            self.assertEqual(p.recording_registry_metadata(cam,0,'main',wait_seconds=0)['audio']['codec_name'],'pcm_alaw')
        with patch.object(p,'edge_rtc_status',return_value={'api_reachable':True,'sources':[]}),patch.object(p,'probe_rtsp_stream') as probe:
            with self.assertRaisesRegex(RuntimeError,'metadata_pending'):p.recording_registry_metadata(cam,0,'main',wait_seconds=0)
            probe.assert_not_called()

    def test_ffmpeg_command_is_not_a_runtime_error_but_actual_failure_is(self):
        p=load_panel_module();header='=== Edge recording start ===\n'+json.dumps({'event':'recording_start_command','command':['ffmpeg','-loglevel','error','-i','rtsp://shared/failed-name']},indent=2)+'\n'
        self.assertEqual(p.diagnostic_from_tail('Recording FFmpeg ffmpeg.log',header)['severity'],'ok')
        failure=header+'[rtsp @ 0x123] method DESCRIBE failed: 503 Service Unavailable\n'
        self.assertEqual(p.diagnostic_from_tail('Recording FFmpeg ffmpeg.log',failure)['severity'],'error')
        warning=header+'[aac @ 0x123] Non-monotonic DTS; changing to 42\n'
        self.assertEqual(p.diagnostic_from_tail('Recording FFmpeg ffmpeg.log',warning)['severity'],'warning')
        self.assertEqual(p.ffmpeg_runtime_messages(header[:-12]),'')

    def test_archive_keys_map_to_camera_directories_not_api_keys(self):
        from test_edge_rtc import builder
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            payload=builder().build_config({'cameras':[camera('hikvision_1','192.0.2.15','main')],'storage':{'recordings_dir':root}},root)
        self.assertEqual(payload['archives'],{'hikvision_1_0':'hikvision_1'})


if __name__=='__main__':unittest.main()
