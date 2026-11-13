"""Export disposable panel pages; no HA, camera or streaming core is contacted."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_edge_panel_config import camera, load_panel_module

fixtures = {}
for mode, engine in [('native', 'edge_rtc')]:
    panel = load_panel_module({'id': 'f' * 32, 'setup_complete': False}
                              if os.environ.get('EDGE_TEST_LOCKED_SETUP') == '1' else None)
    document=json.dumps(panel.EDGE_RTC_PLAYER_HTML.replace('__EDGE_PANEL_VERSION__',panel.APP_VERSION)).replace('<','\\u003c')
    panel.INDEX_HTML=panel.INDEX_HTML.replace(document,json.dumps('<p>Inline player routing fixture</p>').replace('<','\\u003c'))
    fixtures[mode] = {
        'html': panel.INDEX_HTML,
        'player_html': panel.EDGE_RTC_PLAYER_HTML.replace('__EDGE_PANEL_VERSION__',panel.APP_VERSION),
        'config': panel.normalize_config({'cameras': [camera('front', '192.0.2.10', 'sub')]}),
        'engine': engine,
        'version': panel.APP_VERSION,
        'ui_build': panel.UI_BUILD,
    }
print(json.dumps(fixtures))
