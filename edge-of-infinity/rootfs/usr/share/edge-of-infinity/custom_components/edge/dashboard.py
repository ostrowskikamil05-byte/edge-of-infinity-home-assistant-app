"""Authenticated read-only bridge for the Edge recordings and events dashboard cards.

The cards never talk to the add-on directly. Every request names a Home Assistant
camera entity, is checked against that user's read permission and is mapped to a
fixed add-on route; file names and event IDs are validated before forwarding.
"""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

ENTITY = re.compile(r"camera\.[a-z0-9_]+")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
SEGMENT = re.compile(r"\d{8}-\d{6}\.mp4")
EVENT_ID = re.compile(r"[1-9][0-9]{0,18}")
KINDS = ("person", "vehicle", "animal", "motion")
COMPAT = "?compat=1&container=aac-clock-v6"
MAX_IMAGE_BYTES = 4 * 1024 * 1024
KEY_TTL_SECONDS = 30
JSON_TIMEOUT = ClientTimeout(total=20)
MEDIA_TIMEOUT = ClientTimeout(total=None, sock_connect=5, sock_read=30)
FORWARDED_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Retry-After")


class BridgeError(Exception):
    """A request that cannot be forwarded; carries the HTTP status for the card."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


def resolve_camera(hass, user, entity_id: str) -> dict:
    """Map a readable camera entity to its config entry and Edge camera ID."""
    if not ENTITY.fullmatch(entity_id or ""):
        raise BridgeError(400, "invalid_entity")
    if not user.permissions.check_entity(entity_id, POLICY_READ):
        raise BridgeError(403, "forbidden")
    for entry in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry, dict) or not entry.get("client"):
            continue
        for row in entry.get("entities", {}).values():
            if row.get("entity_id") == entity_id:
                return {"entry": entry, "camera_id": row.get("camera_id"), "entity_id": entity_id}
    raise BridgeError(404, "camera_not_found")


def readable_cameras(hass, user) -> list[dict]:
    """Every recording camera entity (not the Main/Sub/Talk helpers) this user may read."""
    found = []
    for entry in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry, dict) or not entry.get("client"):
            continue
        for row in entry.get("entities", {}).values():
            entity_id = row.get("entity_id") or ""
            if row.get("role", "nvr") == "nvr" and user.permissions.check_entity(entity_id, POLICY_READ):
                found.append({"entry": entry, "camera_id": row.get("camera_id"), "entity_id": entity_id})
    return found


def upstream(entry: dict) -> tuple[str, dict]:
    """Return the add-on base URL reachable from Home Assistant, plus its headers."""
    client = entry["client"]
    if not client.is_local:
        return client.base_url, ({"Authorization": "Bearer " + client.api_key} if client.api_key else {})
    coordinator = entry.get("coordinator")
    for camera in ((coordinator.data if coordinator else None) or {}).get("cameras", []):
        parts = urlsplit(str((camera.get("edge_rtc") or {}).get("keyframe_api") or ""))
        if parts.scheme in ("http", "https") and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}", {}
    raise BridgeError(503, "edge_addon_unreachable")


async def fetch_json(hass, entry: dict, path: str):
    base, headers = upstream(entry)
    try:
        async with async_get_clientsession(hass).get(base + path, headers=headers, timeout=JSON_TIMEOUT) as response:
            if response.status == 400:
                raise BridgeError(400, "invalid_query")
            if response.status == 404:
                raise BridgeError(404, "not_found")
            if response.status != 200:
                raise BridgeError(502, "edge_addon_error")
            return await response.json(content_type=None)
    except (ClientError, asyncio.TimeoutError, ValueError) as error:
        raise BridgeError(502, "edge_addon_unreachable") from error


async def recording_slot(hass, target: dict) -> dict:
    """Find the add-on's recording key/index for a camera; cached briefly per entry."""
    entry = target["entry"]
    cache = entry.setdefault("_dashboard_slots", {"at": 0.0, "rows": {}})
    if time.monotonic() - cache["at"] > KEY_TTL_SECONDS or target["camera_id"] not in cache["rows"]:
        payload = await fetch_json(hass, entry, "/api/recording/status?summary=1")
        cache["rows"] = {row.get("id"): row for row in payload.get("cameras", []) if re.fullmatch(r"[A-Za-z0-9_-]{1,96}", str(row.get("key") or ""))}
        cache["at"] = time.monotonic()
    row = cache["rows"].get(target["camera_id"])
    if not row:
        raise BridgeError(404, "camera_not_recording")
    return row


def public_event(event: dict, entity_id: str) -> dict:
    """Keep only what the card shows; add-on paths and media errors stay private."""
    return {
        "id": event.get("id"), "entity_id": entity_id, "kind": event.get("kind"), "source": event.get("source"),
        "timestamp": event.get("timestamp"), "day": event.get("day"), "time": event.get("time_label"),
        "score": event.get("score"), "image": bool(event.get("has_image")),
        "clip": event.get("clip_state") == "ready", "clip_offset": event.get("clip_offset_seconds") or 0,
    }


def fail(error: BridgeError) -> web.Response:
    return web.json_response({"error": error.code}, status=error.status)


class EdgeRecordingsView(HomeAssistantView):
    """Days and a compact segment list for one camera."""

    url = "/api/edge/dashboard/recordings/{entity_id}"
    name = "api:edge:dashboard:recordings"
    requires_auth = True

    def __init__(self, hass) -> None:
        self.hass = hass

    async def get(self, request, entity_id):
        try:
            target = resolve_camera(self.hass, request[KEY_HASS_USER], entity_id)
            day = request.query.get("day", "")
            if day and not DAY.fullmatch(day):
                raise BridgeError(400, "invalid_day")
            slot = await recording_slot(self.hass, target)
            query = "/api/recording/status?catalog=1" + (f"&days={int(slot['index'])}:{day}" if day else "")
            payload = await fetch_json(self.hass, target["entry"], query)
            camera = next((row for row in payload.get("cameras", []) if row.get("id") == target["camera_id"]), None)
            if camera is None:
                raise BridgeError(404, "camera_not_recording")
        except BridgeError as error:
            return fail(error)
        files = sorted(
            ([row["name"], row.get("start_ts") or 0, row.get("duration_seconds") or camera.get("segment_seconds") or 10]
             for row in camera.get("files", []) if SEGMENT.fullmatch(str(row.get("name") or ""))),
            key=lambda row: row[1])
        days = []
        for row in camera.get("days", []):
            if not DAY.fullmatch(str(row.get("day") or "")):
                continue
            thumb = re.search(r"/(\d{8}-\d{6}\.mp4)\.jpg", str(row.get("thumbnail_url") or ""))
            days.append({"day": row["day"], "count": row.get("file_count") or 0, "seconds": row.get("recorded_seconds") or 0,
                         "start": row.get("day_start_ts") or 0, "end": row.get("day_end_ts") or 0, "thumb": thumb[1] if thumb else ""})
        return web.json_response({
            "entity_id": entity_id, "recording": bool(camera.get("recording")), "status": camera.get("recording_status"),
            "day": camera.get("selected_day") or "", "days": days, "files": files, "revision": camera.get("files_revision") or "",
        }, headers={"Cache-Control": "no-store"})


class EdgeEventsView(HomeAssistantView):
    """Paged detection history for one camera, or for every camera the user may read."""

    url = "/api/edge/dashboard/events"
    name = "api:edge:dashboard:events"
    requires_auth = True

    def __init__(self, hass) -> None:
        self.hass = hass

    async def get(self, request):
        user = request[KEY_HASS_USER]
        query = request.query
        try:
            entity_id = query.get("entity", "")
            targets = [resolve_camera(self.hass, user, entity_id)] if entity_id else readable_cameras(self.hass, user)
            kinds = [kind for kind in query.get("kinds", "").split(",") if kind]
            if any(kind not in KINDS for kind in kinds) or len(kinds) != len(set(kinds)):
                raise BridgeError(400, "invalid_kind")
            day, before = query.get("day", ""), query.get("before", "")
            if (day and not DAY.fullmatch(day)) or (before and not EVENT_ID.fullmatch(before)):
                raise BridgeError(400, "invalid_query")
            try:
                limit = max(1, min(200, int(query.get("limit", "24"))))
            except ValueError as error:
                raise BridgeError(400, "invalid_query") from error
            if not targets:
                return web.json_response({"events": [], "has_more": False, "next_before": None})
            events, has_more = await self._collect(targets, bool(entity_id), kinds, day, before, limit)
        except BridgeError as error:
            return fail(error)
        return web.json_response({"events": events, "has_more": has_more, "next_before": events[-1]["id"] if has_more and events else None},
                                 headers={"Cache-Control": "no-store"})

    async def _collect(self, targets, single, kinds, day, before, limit):
        """Merge keyset pages across kinds and entries; IDs keep the order stable."""
        by_entry = {}
        for target in targets:
            by_entry.setdefault(id(target["entry"]), (target["entry"], {}))[1][target["camera_id"]] = target["entity_id"]
        rows, more = [], False
        for entry, cameras in by_entry.values():
            cursor, taken, page_more = before, 0, False
            # Without a camera filter, skip cameras this user cannot read and keep paging.
            for _ in range(5):
                page, page_more, cursor = await self._page(entry, next(iter(cameras)) if single else "", kinds, day, cursor, limit)
                for event in page:
                    if event.get("camera_id") in cameras:
                        rows.append(public_event(event, cameras[event["camera_id"]]))
                        taken += 1
                page_more = bool(page_more and cursor)
                if not page_more or taken >= limit:
                    break
            more = more or page_more
        rows.sort(key=lambda event: event["id"] or 0, reverse=True)
        return rows[:limit], more or len(rows) > limit

    async def _page(self, entry, camera_id, kinds, day, before, limit):
        base = "/api/events?limit=" + str(limit) + (f"&camera={camera_id}" if camera_id else "") + (f"&day={day}" if day else "") + (f"&before={before}" if before else "")
        if len(kinds) <= 1:
            payload = await fetch_json(self.hass, entry, base + (f"&kind={kinds[0]}" if kinds else ""))
            return payload.get("events", []), bool(payload.get("has_more")), str(payload.get("next_before") or "")
        pages = await asyncio.gather(*(fetch_json(self.hass, entry, base + "&kind=" + kind) for kind in kinds))
        merged = sorted((event for page in pages for event in page.get("events", [])), key=lambda event: event.get("id") or 0, reverse=True)
        more = len(merged) > limit or any(page.get("has_more") for page in pages)
        merged = merged[:limit]
        return merged, more, str(merged[-1]["id"]) if more and merged else ""


class EdgeMediaView(HomeAssistantView):
    """Thumbnails, recording segments, alert pictures and alert clips."""

    url = "/api/edge/dashboard/media/{entity_id}/{kind}/{name}"
    name = "api:edge:dashboard:media"
    requires_auth = True

    def __init__(self, hass) -> None:
        self.hass = hass

    async def get(self, request, entity_id, kind, name):
        try:
            target = resolve_camera(self.hass, request[KEY_HASS_USER], entity_id)
            path, image = await self._route(target, kind, name)
            base, headers = upstream(target["entry"])
        except BridgeError as error:
            return fail(error)
        if "Range" in request.headers and not image:
            headers = {**headers, "Range": request.headers["Range"]}
        response = None
        try:
            async with async_get_clientsession(self.hass).get(base + path, headers=headers, timeout=MEDIA_TIMEOUT) as source:
                if source.status not in (200, 206):
                    passed = {"Retry-After": source.headers["Retry-After"]} if "Retry-After" in source.headers else {}
                    return web.json_response({"error": "media_unavailable"}, status=source.status if source.status in (404, 416, 425, 429, 503) else 502, headers=passed)
                if image:
                    data = await source.read()
                    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\xff\xd8\xff"):
                        return web.json_response({"error": "invalid_image"}, status=502)
                    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
                response = web.StreamResponse(status=source.status, headers={
                    **{key: source.headers[key] for key in FORWARDED_HEADERS if key in source.headers}, "Cache-Control": "private, max-age=300"})
                await response.prepare(request)
                async for chunk in source.content.iter_chunked(65536):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (ClientError, asyncio.TimeoutError):
            if response is not None and response.prepared:
                return response
            return web.json_response({"error": "edge_addon_unreachable"}, status=502)
        except ConnectionError:
            # The viewer closed the player or sought elsewhere; nothing to report.
            return response

    async def _route(self, target, kind, name):
        """Translate a validated card path into (add-on path, is_image)."""
        if kind in ("thumb", "video"):
            wanted = name[:-4] if kind == "thumb" and name.endswith(".jpg") else name
            if not SEGMENT.fullmatch(wanted):
                raise BridgeError(400, "invalid_name")
            key = (await recording_slot(self.hass, target))["key"]
            return (f"/recording-thumbs/{key}/{wanted}.jpg", True) if kind == "thumb" else (f"/recordings/{key}/{wanted}{COMPAT}", False)
        if kind in ("event-image", "event-clip"):
            ident = name.rsplit(".", 1)[0]
            if not EVENT_ID.fullmatch(ident) or name != ident + (".jpg" if kind == "event-image" else ".mp4"):
                raise BridgeError(400, "invalid_name")
            owners = target["entry"].setdefault("_dashboard_events", {})
            if ident not in owners:
                meta = await fetch_json(self.hass, target["entry"], f"/api/alerts/{ident}")
                if len(owners) > 2000:
                    owners.clear()
                owners[ident] = meta.get("camera_id")
            if owners[ident] != target["camera_id"]:
                raise BridgeError(404, "event_not_found")
            return (f"/api/alerts/{ident}/image", True) if kind == "event-image" else (f"/api/alerts/{ident}/clip.mp4{COMPAT}", False)
        raise BridgeError(404, "not_found")


def async_register_dashboard(hass) -> None:
    for view in (EdgeRecordingsView, EdgeEventsView, EdgeMediaView):
        hass.http.register_view(view(hass))
