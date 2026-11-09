import importlib.util
from pathlib import Path
import sys
import types
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_edge_panel_config import ROOT

COMPONENT = ROOT / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge'


class DetectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_events_fire_once_and_initial_history_is_suppressed(self):
        names = ['homeassistant', 'homeassistant.config_entries', 'homeassistant.core', 'homeassistant.helpers', 'homeassistant.helpers.update_coordinator', 'edge_events_test', 'edge_events_test.client', 'edge_events_test.const']
        modules = {name: types.ModuleType(name) for name in names}
        class Coordinator:
            def __class_getitem__(cls, item): return cls
            def __init__(self, hass, **kwargs): self.hass = hass
        modules['homeassistant.config_entries'].ConfigEntry = object
        modules['homeassistant.core'].HomeAssistant = object
        modules['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator = Coordinator
        modules['homeassistant.helpers.update_coordinator'].UpdateFailed = RuntimeError
        modules['edge_events_test.client'].EdgeClient = object
        modules['edge_events_test.client'].EdgeClientError = type('ClientError', (Exception,), {})
        modules['edge_events_test.const'].DOMAIN = 'edge'
        modules['edge_events_test.const'].DEFAULT_SCAN_INTERVAL_SECONDS = 10
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('edge_events_test.coordinator', COMPONENT/'coordinator.py')
            module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            hass = types.SimpleNamespace(bus=types.SimpleNamespace(async_fire=Mock()))
            events = [{'id':1, 'camera_id':'front', 'kind':'motion', 'source':'camera', 'timestamp':1}]
            client = types.SimpleNamespace(health=AsyncMock(return_value={}),cameras=AsyncMock(return_value=[]),detection=AsyncMock(side_effect=lambda:{'updated_at':time.time(),'recent_events':list(events)}))
            coordinator = module.EdgeDetectionCoordinator(hass, client, object())
            client.detection = AsyncMock(return_value={'updated_at': time.time() - 60, 'recent_events': events})
            self.assertEqual(await coordinator._async_update_data(),{'detection':{},'status':'starting'})
            hass.bus.async_fire.assert_not_called()
            coordinator._started-=46
            with self.assertRaisesRegex(RuntimeError,'stale'):await coordinator._async_update_data()
            client.detection = AsyncMock(side_effect=lambda:{'updated_at':time.time(),'recent_events':list(events)})
            await coordinator._async_update_data()
            hass.bus.async_fire.assert_not_called()
            events.append({'id':2, 'camera_id':'front', 'kind':'person', 'source':'local', 'timestamp':2, 'private_extra':'not forwarded'})
            await coordinator._async_update_data()
            await coordinator._async_update_data()
            hass.bus.async_fire.assert_called_once_with('edge_event', {'id':2,'camera_id':'front','kind':'person','source':'local','timestamp':2})
            events.append({'id':3,'camera_id':'front','kind':'person','source':'local','timestamp':3,'zone_id':'local_zone','private_extra':'not forwarded'})
            await coordinator._async_update_data()
            hass.bus.async_fire.assert_called_with('edge_event',{'id':3,'camera_id':'front','kind':'person','source':'local','timestamp':3,'zone_id':'local_zone'})
            client.detection = AsyncMock(return_value={'updated_at': time.time() - 60, 'recent_events': events})
            with self.assertRaisesRegex(RuntimeError, 'stale'):
                await coordinator._async_update_data()
            self.assertEqual(hass.bus.async_fire.call_count, 2)

    async def test_binary_sensor_availability_tracks_reported_capabilities(self):
        names = ['homeassistant','homeassistant.components','homeassistant.components.binary_sensor','homeassistant.helpers','homeassistant.helpers.update_coordinator','edge_sensor_test','edge_sensor_test.const']
        modules={name:types.ModuleType(name) for name in names}
        class Entity: pass
        class CoordinatorEntity:
            def __init__(self, coordinator):self.coordinator=coordinator
        modules['homeassistant.components.binary_sensor'].BinarySensorEntity=Entity
        modules['homeassistant.components.binary_sensor'].BinarySensorDeviceClass=types.SimpleNamespace(MOTION='motion',OCCUPANCY='occupancy')
        modules['homeassistant.helpers.update_coordinator'].CoordinatorEntity=CoordinatorEntity
        modules['edge_sensor_test.const'].DOMAIN='edge'
        with patch.dict(sys.modules,modules):
            spec=importlib.util.spec_from_file_location('edge_sensor_test.binary_sensor',COMPONENT/'binary_sensor.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            camera={'id':'front','name':'Front','states':{'motion':True},'camera_status':'connected','local_status':'disabled','supported_kinds':['motion']}
            coordinator=types.SimpleNamespace(data={'detection':{'cameras':[camera]}},last_update_success=True)
            motion=module.EdgeDetectionSensor(coordinator,'entry',camera,'motion')
            person=module.EdgeDetectionSensor(coordinator,'entry',camera,'person')
            self.assertTrue(motion.available);self.assertTrue(motion.is_on)
            self.assertFalse(person.available)
            camera.update(local_status='running',supported_kinds=['motion','person','vehicle'])
            self.assertTrue(person.available);self.assertFalse(person.is_on)
            camera['states']['person']=True
            self.assertTrue(person.is_on)
            coordinator.last_update_success=False
            self.assertFalse(person.available)
