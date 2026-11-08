import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from test_edge_panel_config import load_panel_module


class DeviceDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.panel=load_panel_module()
        from _device_logs import DeviceLogs
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DeviceLogs(Path(self.temp.name)/'devices')

    def payload(self,ident='a'):
        return {'device_id':ident*32,'session_id':'b'*32,'device':{'label':'iPhone Kamil','user_agent':'test'},
                'browser_endpoint':'https://ha.example/api/hassio_ingress/private-id/api/live?authSig=private',
                'events':[{'event':'error','password':'secret','url':'rtsp://admin:secret@192.0.2.5:554/main?token=private'}]}

    def test_per_device_sessions_proxy_provenance_and_secret_redaction(self):
        self.assertTrue(self.store.append(self.payload(),{'panel_observed_peer':'172.30.32.2'}))
        result=self.store.snapshot('a'*32);encoded=json.dumps(result)
        self.assertNotIn('secret',encoded);self.assertNotIn('private',encoded);self.assertNotIn('admin',encoded)
        self.assertIn('192.0.2.5:554/main',encoded)
        self.assertEqual(result['selected']['latest']['transport_observation']['panel_observed_peer'],'172.30.32.2')
        self.assertEqual(result['devices'][0]['session_id'],'b'*32)
        self.assertTrue(self.store.append(self.payload('c')))
        self.assertEqual(len(self.store.snapshot()['devices']),2)

    def test_identity_size_symlink_and_rotation_limits(self):
        for bad in ('../escape','x'*32,'a'*33):
            with self.assertRaises(ValueError):self.store.append({**self.payload(),'device_id':bad})
        with self.assertRaises(ValueError):self.store.append({**self.payload(),'huge':'x'*40000})
        with self.assertRaises(ValueError):self.store.append({**self.payload(),'bad':float('nan')})
        with self.assertRaises(ValueError):self.store.append({**self.payload(),'device':'invalid'})
        self.store.max_bytes=4096;self.store.max_devices=2
        for i in range(20):
            with patch('_device_logs.time.time',return_value=time.time()+i*10):self.store.append({**self.payload(),'event':'x'*900})
        path=self.store.directory/('a'*32+'.jsonl')
        self.assertLessEqual(path.stat().st_size,4096)
        for line in path.read_text().splitlines():json.loads(line)
        self.store.append(self.payload('c'));self.store.append(self.payload('d'))
        self.assertLessEqual(len(self.store.files()),2)
        link=self.store.directory/('e'*32+'.jsonl');foreign=Path(self.temp.name)/'foreign';foreign.write_text('keep');link.symlink_to(foreign)
        with self.assertRaises(ValueError):self.store.append(self.payload('e'))
        self.assertEqual(foreign.read_text(),'keep')

    def test_short_report_bursts_are_limited_and_old_devices_expire(self):
        self.store.append(self.payload());self.assertFalse(self.store.append(self.payload()))
        import os
        path=self.store.files()[0];os.utime(path,(1,1))
        self.assertEqual(self.store.snapshot()['devices'],[])
