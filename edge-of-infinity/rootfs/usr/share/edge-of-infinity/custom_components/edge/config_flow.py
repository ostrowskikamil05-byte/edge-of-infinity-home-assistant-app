"""Config flow for Edge of Infinity."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import EdgeAuthError, EdgeClient, EdgeClientError
from .const import CONF_API_KEY, DOMAIN


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_URL,
            default="http://4e5f32ea-edge-of-infinity:8088",
        ): str,
        vol.Optional(CONF_API_KEY): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate user input."""
    session = async_get_clientsession(hass)
    last_error: EdgeClientError | None = None

    for url in _candidate_urls(data.get(CONF_URL, "")):
        client = EdgeClient(
            base_url=url,
            api_key=data.get(CONF_API_KEY),
            session=session,
        )
        try:
            health = await client.health()
        except EdgeClientError as err:
            last_error = err
            continue

        data[CONF_URL] = url
        return {"title": health.get("product", "Edge of Infinity")}

    if last_error is not None:
        raise last_error
    raise EdgeClientError("Unable to connect to Edge of Infinity")


def _candidate_urls(url: str) -> list[str]:
    """Return likely Edge app URLs."""
    cleaned = url.rstrip("/")
    candidates = []
    if cleaned:
        candidates.append(cleaned)

    candidates.extend(
        [
            "http://4e5f32ea-edge-of-infinity:8088",
            "http://4e5f32ea_edge_of_infinity:8088",
            "http://addon_4e5f32ea_edge_of_infinity:8088",
            "http://192.168.33.17:8088",
        ]
    )

    out = []
    for candidate in candidates:
        if candidate and candidate not in out:
            out.append(candidate)
    return out


class EdgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Edge of Infinity config flow."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input.get(CONF_URL, "").rstrip("/")
            user_input[CONF_URL] = url

            parsed = urlparse(url)
            if url and (parsed.scheme not in ("http", "https") or not parsed.netloc):
                errors["base"] = "invalid_url"
            else:
                try:
                    info = await validate_input(self.hass, user_input)
                except EdgeAuthError:
                    errors["base"] = "invalid_auth"
                except EdgeClientError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "unknown"
                else:
                    parsed = urlparse(user_input[CONF_URL])
                    await self.async_set_unique_id(parsed.netloc)
                    self._abort_if_unique_id_configured(updates={CONF_URL: url})
                    return self.async_create_entry(
                        title=info["title"],
                        data=user_input,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
