import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_edge_panel_config import ROOT, camera, load_panel_module

CORE = ROOT/'edge-of-infinity/rootfs/usr/share/edge-of-infinity'

def builder():
    spec=importlib.util.spec_from_file_location('rtc_config_test',CORE/'edge_rtc_config.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class NativeRegistryTests(unittest.TestCase):
    def test_one_source_aliases_all_consumers_and_duplicate_camera_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            module=builder();first=camera('front','192.0.2.10','sub');second={**first,'id':'alias'}
            config=module.build_config({'cameras':[first,second]},folder)
            self.assertEqual(len(config['sources']),1)
            self.assertEqual(config['sources'][0]['url'],first['rtsp_main'])
            self.assertEqual(config['sources'][0]['paths'],['front_main','front_sub','alias_main','alias_sub'])
            self.assertEqual((Path(folder)/'edge-rtc-token').stat().st_mode & 0o777,0o600)
            self.assertEqual(config['token'],module.private_token(folder))

    def test_reload_file_is_atomic_private_and_unchanged_config_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            module=builder();path=Path(folder)/'runtime/config.json';config={'cameras':[camera('front','192.0.2.10','sub')]}
            module.write_config(config,folder,path);mtime=path.stat().st_mtime_ns
            module.write_config(config,folder,path)
            self.assertEqual(path.stat().st_mtime_ns,mtime)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            config['cameras'][0]['record_stream']='sub';module.write_config(config,folder,path)
            self.assertEqual(json.loads(path.read_text())['sources'][0]['url'],config['cameras'][0]['rtsp_sub'])

    def test_native_metadata_does_not_advertise_second_profile_or_token(self):
        p=load_panel_module();p.EDGE_RTC_ENABLED=True;p._HA_RESTREAM_HOST='edge-addon'
        config=p.normalize_config({'cameras':[camera('front','192.0.2.10','sub')]})
        payload=p.home_assistant_restream_payload(config)
        self.assertEqual(payload['source'],'edge_rtc')
        self.assertEqual(set(payload['cameras'][0]['streams']),{'nvr','main'})
        self.assertEqual(payload['cameras'][0]['edge_rtc']['path'],'front_main')
        self.assertNotIn('secret',json.dumps(payload));self.assertNotIn('token',json.dumps(payload))
        self.assertEqual(p.recording_source_stream(config['cameras'][0],0,'sub'),('rtsp://127.0.0.1:8556/front_sub','edge_rtc_shared'))

    def test_native_health_reports_core_failure_for_the_watchdog(self):
        p=load_panel_module();p.EDGE_RTC_ENABLED=True
        with patch.object(p, 'engine_runtime_status', return_value={'edge_rtc': {'api_reachable': False}}):
            self.assertEqual(p.health_payload()['status'],'error')
        with patch.object(p, 'engine_runtime_status', return_value={'edge_rtc': {'api_reachable': True}}):
            self.assertEqual(p.health_payload()['status'],'ok')


class ProxyPermissionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_permission_and_microphone_control_are_separate(self):
        modules={}
        for name in ('homeassistant','homeassistant.auth','homeassistant.auth.permissions','homeassistant.auth.permissions.const','homeassistant.components','homeassistant.components.http','homeassistant.components.http.const','homeassistant.helpers','homeassistant.helpers.aiohttp_client','rtc_acl','rtc_acl.const','rtc_acl.client','aiohttp'):
            modules[name]=types.ModuleType(name)
        class Response:
            def __init__(self,status):self.status=status
        modules['aiohttp'].web=types.SimpleNamespace(Response=Response)
        modules['aiohttp'].ClientError=type('ClientError',(Exception,),{})
        modules['aiohttp'].WSMsgType=object
        modules['homeassistant.auth.permissions.const'].POLICY_READ='read'
        modules['homeassistant.auth.permissions.const'].POLICY_CONTROL='control'
        modules['homeassistant.components.http'].HomeAssistantView=object
        modules['homeassistant.components.http.const'].KEY_HASS_USER='user'
        modules['homeassistant.helpers.aiohttp_client'].async_get_clientsession=lambda hass:self.fail('Unauthorized connection attempted')
        modules['rtc_acl.const'].DOMAIN='edge'
        error=modules['rtc_acl.client'].EdgeClientError=type('EdgeClientError',(Exception,),{})
        with patch.dict(sys.modules,modules):
            spec=importlib.util.spec_from_file_location('rtc_acl.rtc',CORE/'custom_components/edge/rtc.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            self.assertTrue(module.network_ping('{"type":"network_ping","id":1}'))
            for payload in ('{}','[]','null','{"type":"network_ping","id":true}','{"type":"network_ping","id":0}','{"type":"network_ping","id":4294967296}','{"type":"network_ping","id":1,"microphone":1}','{"type":"other","id":1}','x'*129):
                self.assertFalse(module.network_ping(payload),payload)
            secret=AsyncMock(side_effect=error('missing private token'))
            data={'client':types.SimpleNamespace(is_local=True,_read_local_bytes=secret),'entities':{'front':{'camera_id':'front','entity_id':'camera.front'}},'coordinator':types.SimpleNamespace(data={'cameras':[{'id':'front','enabled':True,'edge_rtc':{'api':'http://edge-addon:9997','path':'front_main'}}]})}
            view=module.EdgeRTCView(types.SimpleNamespace(data={'edge':{'entry':data}}))
            class Request(dict):query={}
            request=Request(user=types.SimpleNamespace(permissions=types.SimpleNamespace(check_entity=lambda entity,policy:False)))
            self.assertEqual((await view.get(request,'camera.front')).status,403)
            request['user'].permissions.check_entity=lambda entity,policy:policy=='read';request.query={'microphone':'1'}
            self.assertEqual((await view.get(request,'camera.front')).status,403)
            secret.assert_not_awaited()
            request.query={}
            self.assertEqual((await view.get(request,'camera.front')).status,503)
            secret.assert_awaited_once()
            self.assertEqual((await view.get(request,'sensor.invalid')).status,400)
