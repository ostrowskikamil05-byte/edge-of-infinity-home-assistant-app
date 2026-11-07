"""Export disposable panel pages; no HA, camera or streaming core is contacted."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_edge_panel_config import camera, load_panel_module

fixtures = {}
for mode, engine in [('native', 'edge_rtc'), ('legacy', 'mediamtx_janus')]:
    os.environ['EDGE_CORE_ENGINE'] = engine
    panel = load_panel_module()
    fixtures[mode] = {
        'html': panel.INDEX_HTML,
        'config': panel.normalize_config({'cameras': [camera('front', '192.0.2.10', 'sub')]}),
        'engine': engine,
        'version': panel.APP_VERSION,
        'ui_build': panel.UI_BUILD,
    }
print(json.dumps(fixtures))
