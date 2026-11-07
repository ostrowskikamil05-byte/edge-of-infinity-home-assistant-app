"""Phone delivery and private saved-image access without a running HA server."""
from contextlib import contextmanager
import importlib.util
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_detection_integration import COMPONENT
from test_edge_integration_client import load_client_module


@contextmanager
def integration(name):
    names=['homeassistant','homeassistant.config_entries','homeassistant.core','homeassistant.helpers',
           'homeassistant.helpers.update_coordinator','homeassistant.auth','homeassistant.auth.permissions',
           'homeassistant.auth.permissions.const','homeassistant.components','homeassistant.components.http',
           'homeassistant.components.http.const','alert_test','alert_test.client','alert_test.const','aiohttp']
    modules={key:types.ModuleType(key) for key in names}
    class Coordinator:
        def __class_getitem__(cls,item):return cls
        def __init__(self,hass,**kwargs):self.hass=hass
    modules['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator=Coordinator
    modules['homeassistant.helpers.update_coordinator'].UpdateFailed=RuntimeError
    modules['homeassistant.config_entries'].ConfigEntry=object
    modules['homeassistant.core'].HomeAssistant=object
    modules['homeassistant.auth.permissions.const'].POLICY_READ='read'
    modules['homeassistant.components.http'].HomeAssistantView=object
    modules['homeassistant.components.http.const'].KEY_HASS_USER='hass_user'
    modules['alert_test.const'].DOMAIN='edge'
    modules['alert_test.const'].DEFAULT_SCAN_INTERVAL_SECONDS=10
    modules['alert_test.client'].EdgeClient=object
    modules['alert_test.client'].EdgeClientError=type('EdgeClientError',(Exception,),{})
    modules['aiohttp'].web=types.SimpleNamespace(Response=lambda **kw:types.SimpleNamespace(status=kw.pop('status',200),**kw))
    with patch.dict(sys.modules,modules):
        spec=importlib.util.spec_from_file_location('alert_test.'+name,COMPONENT/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        yield module


class AlertNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_fresh_new_intrusions_notify_selected_phone_once_with_image_and_seek_link(self):
        with integration('coordinator') as module:
            events=[{'id':1,'camera_id':'front','kind':'person','source':'local','timestamp':time.time()}]
            options={'notifications_enabled':True,'notify_service':'notify.mobile_app_phone','notify_cooldown':30,'notify_path':'/abc/ingress/edge?tab=nvr'}
            client=types.SimpleNamespace(detection=AsyncMock(side_effect=lambda:{'updated_at':time.time(),'recent_events':events[:],'notifications':options}))
            services=types.SimpleNamespace(has_service=Mock(return_value=True),async_call=AsyncMock())
            hass=types.SimpleNamespace(bus=types.SimpleNamespace(async_fire=Mock()),services=services)
            coordinator=module.EdgeDetectionCoordinator(hass,client,types.SimpleNamespace(entry_id='entry'))
            await coordinator._async_update_data();services.async_call.assert_not_awaited()
            events.append({**events[0],'id':2,'kind':'animal'})
            await coordinator._async_update_data();await coordinator._async_update_data()
            services.async_call.assert_awaited_once()
            args=services.async_call.call_args.args
            self.assertEqual(args[:2],('notify','mobile_app_phone'))
            self.assertEqual(args[2]['data']['image'],'/api/edge/events/entry/front/2/image')
            self.assertEqual(args[2]['data']['url'],'/abc/ingress/edge?tab=nvr&edge_event=2')
            self.assertIn('Zwierzę',args[2]['message'])
            events.append({**events[-1],'id':3})
            await coordinator._async_update_data();self.assertEqual(services.async_call.await_count,1)
            for stamp,service in [(time.time()-120,'notify.mobile_app_phone'),(float('nan'),'notify.mobile_app_phone'),('bad','notify.mobile_app_phone'),(time.time(),'notify.mobile_app_phone;bad')]:
                coordinator._notify_last.clear();options['notify_service']=service
                await coordinator._notify({**events[-1],'timestamp':stamp},options)
            self.assertEqual(services.async_call.await_count,1)
            options.update(notify_service='notify.mobile_app_phone',notifications_enabled=False)
            await coordinator._notify({**events[-1],'timestamp':time.time()},options)
            self.assertEqual(services.async_call.await_count,1)

    async def test_notification_picture_requires_camera_read_permission_and_valid_id(self):
        with integration('events') as module:
            client=types.SimpleNamespace(event_image=AsyncMock(return_value=b'jpeg'))
            permissions=types.SimpleNamespace(check_entity=Mock(return_value=True))
            request={'hass_user':types.SimpleNamespace(permissions=permissions)}
            hass=types.SimpleNamespace(data={'edge':{'entry':{'client':client,'entities':{'front':{'camera_id':'front','entity_id':'camera.renamed'}}}}})
            view=module.EdgeEventImageView(hass)
            self.assertTrue(view.requires_auth)
            response=await view.get(request,'entry','front','7')
            self.assertEqual(response.status,200);self.assertEqual(response.body,b'jpeg')
            permissions.check_entity.assert_called_once_with('camera.renamed','read')
            client.event_image.reset_mock();permissions.check_entity.return_value=False
            self.assertEqual((await view.get(request,'entry','front','7')).status,403)
            self.assertEqual((await view.get(request,'entry','other','7')).status,403)
            for camera,ident in [('front','0'),('front','9223372036854775808'),('../front','7')]:
                self.assertEqual((await view.get(request,'entry',camera,ident)).status,400)
            self.assertEqual((await view.get(request,'missing','front','7')).status,404)
            client.event_image.assert_not_awaited()

    async def test_remote_picture_cannot_bypass_camera_ownership_or_accept_invalid_bytes(self):
        module=load_client_module();client=module.EdgeClient('https://example.invalid',object())
        with patch.object(module.EdgeClient,'_request',new=AsyncMock(return_value={'camera_id':'other'})) as request:
            with self.assertRaisesRegex(module.EdgeClientError,'mismatch'):await client.event_image('front',3)
            self.assertEqual(request.await_count,1)
        for image in (b'not-jpeg',b'\xff\xd8\xff'+bytes(2*1024*1024)):
            with patch.object(module.EdgeClient,'_request',new=AsyncMock(side_effect=[{'camera_id':'front'},image])):
                with self.assertRaisesRegex(module.EdgeClientError,'Invalid alert image'):await client.event_image('front',3)
