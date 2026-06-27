"""Home Assistant integration for Edge of Infinity."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import EdgeClient
from .const import CONF_API_KEY, DOMAIN, PLATFORMS
from .coordinator import EdgeCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Edge of Infinity from a config entry."""
    session = async_get_clientsession(hass)
    client = EdgeClient(
        base_url=entry.data[CONF_URL],
        api_key=entry.data.get(CONF_API_KEY),
        session=session,
        hass=hass,
    )
    coordinator = EdgeCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Edge of Infinity config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
