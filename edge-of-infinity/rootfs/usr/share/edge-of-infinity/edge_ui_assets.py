"""Bounded, bundled UI assets; no runtime dependency on a font CDN."""
import base64
from functools import lru_cache
import hashlib
import json
from pathlib import Path


@lru_cache(maxsize=3)
def ui_font(name):
    if name not in ('inter-latin.woff2', 'inter-latin-ext.woff2', 'inter-cyrillic.woff2'):
        raise ValueError('ui_font_not_found')
    source = Path(__file__).with_name('edge-ui-fonts.json')
    if source.stat().st_size > 512_000:
        raise ValueError('ui_font_bundle_invalid')
    encoded = json.loads(source.read_text())[name]
    data = base64.b64decode(encoded, validate=True)
    if not data.startswith(b'wOF2') or len(data) > 150_000:
        raise ValueError('ui_font_invalid')
    return data, '"' + hashlib.sha256(data).hexdigest() + '"'
