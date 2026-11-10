"""Opt-in local networking never rewrites cameras, recordings or HA settings."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE=Path(__file__).parents[1]/'edge-of-infinity/rootfs/usr/share/edge-of-infinity'
sys.path.insert(0,str(CORE))
from edge_local_network import local_settings,save_local_settings,normalize_hosts,network_status,proxy_configuration
from edge_rtc_config import build_config

class LocalNetworkTests(unittest.TestCase):
    def test_disabled_by_default_and_separate_persistent_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);camera={'id':'front','enabled':True,'rtsp_main':'rtsp://disposable@192.0.2.1/main','record_stream':'main'}
            original={'cameras':[camera],'storage':{'recordings_dir':'/media/recordings'}}
            saved=json.dumps(original);(home/'edge.json').write_text(saved);(home/'configuration.yaml').write_text('http:\n  ip_ban_enabled: true\n')
            before=build_config(original,home)
            self.assertFalse(before['local_https']['enabled'])
            self.assertFalse((home/'local-network.json').exists())
            save_local_settings(home,{'enabled':True,'hosts':['192.168.1.10','homeassistant.local']})
            after=build_config(original,home)
            self.assertTrue(after['local_https']['enabled'])
            self.assertEqual({k:v for k,v in before.items() if k!='local_https'},{k:v for k,v in after.items() if k!='local_https'})
            self.assertEqual((home/'edge.json').read_text(),saved)
            self.assertEqual((home/'configuration.yaml').read_text(),'http:\n  ip_ban_enabled: true\n')
            self.assertEqual((home/'local-network.json').stat().st_mode&0o777,0o600)
            with patch('edge_local_network.socket.gethostbyname',return_value='172.30.33.3'):
                status=network_status(home,{'active':True})
            self.assertEqual(status['urls'][0],'https://192.168.1.10:8443')
            self.assertIn('    - 172.30.33.3\n',status['home_assistant_yaml'])
            self.assertNotIn('/16',status['home_assistant_yaml'])
            self.assertTrue(status['trust_required']);self.assertFalse(status['cloud_required']);self.assertFalse(status['configuration_changed'])
    def test_host_validation_and_untrusted_config_fail_closed(self):
        self.assertEqual(normalize_hosts([' HOMEASSISTANT.local ','192.168.1.10','homeassistant.local']),['homeassistant.local','192.168.1.10'])
        for hosts in ([],['0.0.0.0'],['::'],['224.0.0.1'],['https://ha.local'],['*.local'],['a/b'],['user@host'],['foo.-bar.local'],['256.1.2.3'],['foo?token=x'],[None]):
            with self.assertRaises(ValueError,msg=str(hosts)):normalize_hosts(hosts)
        with tempfile.TemporaryDirectory() as folder:
            for payload in ({'enabled':'true','hosts':['ha.local']},{'enabled':True,'hosts':[]},{'enabled':True,'hosts':['ha.local'],'upstream':'http://attacker'}):
                with self.assertRaises(ValueError):save_local_settings(folder,payload)
            (Path(folder)/'local-network.json').write_text('not json')
            self.assertFalse(local_settings(folder)['enabled'])
            target=Path(folder)/'other.json';target.write_text('{}')
            (Path(folder)/'local-network.json').unlink();(Path(folder)/'local-network.json').symlink_to(target)
            with self.assertRaises(ValueError):save_local_settings(folder,{'enabled':False,'hosts':[]})
            self.assertEqual(target.read_text(),'{}')
        self.assertEqual(proxy_configuration('invalid'),('',''))
