"""Register the bundled Edge Live, recordings and events cards with Home Assistant's frontend."""
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import CARD_VERSION, DOMAIN

CARD_PATH = "/edge/edge-live-card.js"
CARD_URL = f"{CARD_PATH}?v={CARD_VERSION}"
DASHBOARD_CARDS_PATH = "/edge/edge-dashboard-cards.js"
DASHBOARD_CARDS_URL = f"{DASHBOARD_CARDS_PATH}?v={CARD_VERSION}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve a credential-free module and load it once for HA dashboards."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("_card_registered"):
        return
    # Serve the folder, not a list of files: the add-on replaces these files while Home
    # Assistant is running, and a card may then import a module this (older) code never
    # listed. Listing files here made such an update fail until Core restarted.
    await hass.http.async_register_static_paths([StaticPathConfig("/edge", str(Path(__file__).parent / "www"), False)])
    frontend.add_extra_js_url(hass, CARD_URL)
    frontend.add_extra_js_url(hass, DASHBOARD_CARDS_URL)
    data["_card_registered"] = True
