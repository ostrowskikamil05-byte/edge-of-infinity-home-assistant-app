"""Register the bundled Edge Live card with Home Assistant's frontend."""
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import CARD_VERSION, DOMAIN

CARD_PATH = "/edge/edge-live-card.js"
CARD_URL = f"{CARD_PATH}?v={CARD_VERSION}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve a credential-free module and load it once for HA dashboards."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("_card_registered"):
        return
    path = str(Path(__file__).parent / "www" / "edge-live-card.js")
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_PATH, path, False), *[StaticPathConfig("/edge/" + name, str(Path(__file__).parent / "www" / name), False) for name in ("edge-rtc-client.js", "edge-pcm-worklet.js")]])
    frontend.add_extra_js_url(hass, CARD_URL)
    data["_card_registered"] = True
