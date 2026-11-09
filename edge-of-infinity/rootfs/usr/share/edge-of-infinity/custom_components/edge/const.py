"""Constants for the Edge of Infinity integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "edge"
NAME = "Edge of Infinity"
CARD_VERSION = "0.12.3"

CONF_API_KEY = "api_key"

PLATFORMS: list[Platform] = [Platform.CAMERA, Platform.BINARY_SENSOR]

DEFAULT_SCAN_INTERVAL_SECONDS = 10

