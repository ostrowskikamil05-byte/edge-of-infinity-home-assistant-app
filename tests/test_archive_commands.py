"""Validate the read-only HA archive boundary independently of HA installation."""
import ast
import json
import math
from pathlib import Path
import re
import unittest

class ArchiveCommandTests(unittest.TestCase):
    def test_bounded_commands_and_alert_ownership(self):
        path=Path(__file__).parents[1]/'edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/dashboard.py'
        function=next(row for row in ast.parse(path.read_text()).body if isinstance(row,ast.FunctionDef) and row.name=='archive_command')
        scope={'json':json,'math':math,'SEGMENT':re.compile(r'\d{8}-\d{6}\.mp4')}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),scope)
        valid=scope['archive_command']
        seek={'type':'seek','epoch':1,'file':'20261110-000000.mp4','at':2.1,'count':32,'bytes':524288}
        self.assertTrue(valid(json.dumps(seek)))
        for change in ({'epoch':True},{'epoch':0},{'at':float('nan')},{'at':-1},{'at':172801},{'file':'../edge.json'},{'bytes':1},{'bytes':2097153},{'count':65},{'type':'microphone'},{'source':'another_camera'}):
            self.assertFalse(valid(json.dumps({**seek,**change})),change)
        for value in ([],None,True,'invalid'):
            self.assertFalse(valid(json.dumps(value)))
        self.assertTrue(valid(json.dumps({**seek,'file':'event-12.mp4'}),'12'))
        self.assertFalse(valid(json.dumps({**seek,'file':'event-13.mp4'}),'12'))
        self.assertFalse(valid(json.dumps(seek),'12'))
        self.assertTrue(valid(json.dumps({'type':'pull','epoch':1,'count':16,'bytes':524288})))
        self.assertFalse(valid(json.dumps({'type':'pull','epoch':1,'count':0})))
        self.assertFalse(valid(' '*4097))
