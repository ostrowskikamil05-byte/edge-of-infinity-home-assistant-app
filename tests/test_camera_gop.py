import copy
import importlib.util
import unittest
import xml.etree.ElementTree as ET

from test_edge_panel_config import ROOT

spec=importlib.util.spec_from_file_location('camera_gop_test',ROOT/'edge-of-infinity/rootfs/usr/share/edge-of-infinity/edge_camera_gop.py')
gop=importlib.util.module_from_spec(spec);spec.loader.exec_module(gop)


class CameraGOPTests(unittest.TestCase):
    def test_only_govlength_changes_and_readback_is_required_for_h264_and_h265(self):
        for codec in ('H264','H265'):
            with self.subTest(codec=codec):
                config=ET.fromstring(f'<Configuration token="encoder"><Encoding>{codec}</Encoding><Resolution><Width>3840</Width><Height>2160</Height></Resolution><Quality>5</Quality><RateControl><FrameRateLimit>20</FrameRateLimit><EncodingInterval>1</EncodingInterval><BitrateLimit>8000</BitrateLimit></RateControl><{codec}><GovLength>200</GovLength></{codec}></Configuration>')
                original=copy.deepcopy(config);calls=[]
                def soap(camera,endpoint,action,body):
                    calls.append(action)
                    if action.endswith('GetProfiles'):return ET.fromstring('<Response><Profiles token="main"><VideoEncoderConfiguration token="encoder"/></Profiles></Response>')
                    if action.endswith('GetVideoEncoderConfiguration'):return copy.deepcopy(config)
                    if action.endswith('SetVideoEncoderConfiguration'):
                        self.assertIn('ForcePersistence',body);changed=ET.fromstring('<Envelope xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'+body+'</Envelope>');value=gop.find(changed,'GovLength').text;gop.find(config,'GovLength').text=value;return ET.Element('Response')
                    self.fail(action)
                result=gop.set_onvif_gop({'record_stream':'main'},2,lambda c:('http://camera/media','main'),soap)
                self.assertEqual(result['frames'],40);self.assertEqual(gop.stable_fields(config),gop.stable_fields(original));self.assertEqual(sum(action.endswith('GetVideoEncoderConfiguration') for action in calls),2)
                self.assertEqual(sum(action.endswith('SetVideoEncoderConfiguration') for action in calls),1)

    def test_unsupported_interval_is_rejected_before_camera_access(self):
        with self.assertRaises(ValueError):gop.set_onvif_gop({},3,lambda c:self.fail('camera access'),lambda *a:self.fail('write'))


if __name__=='__main__':unittest.main()
