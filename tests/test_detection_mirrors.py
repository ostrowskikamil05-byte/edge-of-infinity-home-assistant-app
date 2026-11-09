import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from test_edge_integration_client import load_client_module


class DetectionMirrorTests(unittest.TestCase):
    def test_stale_first_mirror_does_not_hide_fresh_publisher(self):
        module=load_client_module()
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/'preferred.json',Path(directory)/'alternate.json']
            paths[0].write_text(json.dumps({'updated_at':time.time()-600,'cameras':[]}))
            paths[1].write_text(json.dumps({'updated_at':time.time(),'cameras':[{'id':'current'}]}))
            with patch.object(module,'Path',side_effect=paths):data=module.EdgeClient._read_detection_sync()
            self.assertEqual(data['cameras'],[{'id':'current'}]);self.assertEqual(data['status_file'],str(paths[1]))
            paths[0].write_text('not JSON')
            with patch.object(module,'Path',side_effect=paths):self.assertEqual(module.EdgeClient._read_detection_sync()['cameras'],[{'id':'current'}])
            paths[1].write_text('also not JSON')
            with patch.object(module,'Path',side_effect=paths):data=module.EdgeClient._read_detection_sync()
            self.assertEqual(data['status_error'],'invalid_json');self.assertIsNone(data['updated_at'])

    def test_all_stale_files_remain_stale(self):
        module=load_client_module()
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/'preferred.json',Path(directory)/'alternate.json']
            for i,path in enumerate(paths):path.write_text(json.dumps({'updated_at':time.time()-600+i*10,'cameras':[{'id':str(i)}]}))
            with patch.object(module,'Path',side_effect=paths):data=module.EdgeClient._read_detection_sync()
            self.assertEqual(data['cameras'],[{'id':'1'}]);self.assertGreater(time.time()-data['updated_at'],500)


if __name__=='__main__':unittest.main()
