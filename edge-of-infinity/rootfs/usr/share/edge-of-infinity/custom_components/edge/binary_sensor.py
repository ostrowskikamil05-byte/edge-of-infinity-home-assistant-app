"""Motion, person and vehicle state from camera events or local Edge inference."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]['detection_coordinator']
    known = set()
    def add_new():
        entities = []
        for camera in (coordinator.data or {}).get('detection', {}).get('cameras', []):
            ident = camera.get('id')
            if not ident or ident in known:
                continue
            known.add(ident)
            entities.extend(EdgeDetectionSensor(coordinator, entry.entry_id, camera, kind) for kind in ('motion', 'person', 'vehicle'))
        if entities:
            async_add_entities(entities)
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class EdgeDetectionSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, entry_id, camera, kind):
        super().__init__(coordinator)
        self.camera_id, self.kind = camera['id'], kind
        self._attr_unique_id = f'{entry_id}_{self.camera_id}_detection_{kind}'
        self._attr_name = {'motion': 'Motion', 'person': 'Person', 'vehicle': 'Vehicle'}[kind]
        self._attr_device_class = BinarySensorDeviceClass.MOTION if kind == 'motion' else BinarySensorDeviceClass.OCCUPANCY
        self._attr_icon = {'motion': 'mdi:motion-sensor', 'person': 'mdi:account', 'vehicle': 'mdi:car'}[kind]
        self._attr_device_info = {'identifiers': {(DOMAIN, f'{entry_id}_{self.camera_id}')}, 'name': camera.get('name', self.camera_id)}

    def _camera(self):
        return next((camera for camera in (self.coordinator.data or {}).get('detection', {}).get('cameras', []) if camera.get('id') == self.camera_id), {})

    @property
    def available(self):
        camera = self._camera()
        return bool(self.coordinator.last_update_success and self.kind in camera.get('supported_kinds', []) and (camera.get('camera_status') == 'connected' or self.kind != 'motion' and camera.get('local_status') == 'running'))

    @property
    def is_on(self):
        return bool(self._camera().get('states', {}).get(self.kind))

    @property
    def extra_state_attributes(self):
        camera = self._camera()
        return {'edge_camera_id': self.camera_id, 'camera_events': camera.get('camera_status'), 'local_detection': camera.get('local_status'), 'local_zone_enabled': (camera.get('local_zone') or {}).get('enabled', False)}
