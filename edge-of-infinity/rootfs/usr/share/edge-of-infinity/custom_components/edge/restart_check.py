"""Tell the user when Home Assistant must restart to finish an Edge add-on update.

The add-on copies a new integration into custom_components as soon as it updates,
but Home Assistant keeps running the code it loaded at start. Until Core restarts
the dashboard cards (new files) and this integration (old code) can disagree.
"""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from .const import CARD_VERSION, DOMAIN

ISSUE_ID = "restart_required"
MANIFEST = Path(__file__).parent / "manifest.json"
CHECK_INTERVAL = timedelta(minutes=1)


def installed_version(manifest: Path = MANIFEST) -> str | None:
    """Version of the integration files on disk; None while they are being replaced."""
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def async_watch_installed_version(hass):
    """Raise a Repairs issue while the files on disk are newer or older than this code."""
    from homeassistant.helpers import issue_registry
    from homeassistant.helpers.event import async_track_time_interval

    async def check(_now=None) -> None:
        version = await hass.async_add_executor_job(installed_version)
        if version is None:
            return
        if version == CARD_VERSION:
            issue_registry.async_delete_issue(hass, DOMAIN, ISSUE_ID)
            return
        issue_registry.async_create_issue(
            hass, DOMAIN, ISSUE_ID, is_fixable=False, is_persistent=False,
            severity=issue_registry.IssueSeverity.WARNING, translation_key=ISSUE_ID,
            translation_placeholders={"installed": version, "running": CARD_VERSION})

    return async_track_time_interval(hass, check, CHECK_INTERVAL), check
