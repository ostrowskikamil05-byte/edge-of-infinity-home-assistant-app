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

    def test_recording_keys_match_the_add_on_and_cannot_leave_their_folder(self):
        root=Path(__file__).parents[1]/'edge-of-infinity/rootfs/usr/share/edge-of-infinity'
        source=(root/'custom_components/edge/dashboard.py').read_text()
        pattern=re.compile(re.search(r'^RECORDING_KEY = re\.compile\(r"(.+)"\)$',source,re.M)[1])
        # One definition: both lookups (coordinator data and the summary fallback) use it.
        self.assertEqual(source.count('RECORDING_KEY.fullmatch('),2)
        safe_id=re.search(r'def safe_id\(value: str\) -> str:\n    return re\.sub\(r"\[\^(.+)\]", "_", value or "camera"\)',(root/'edge-panel.py').read_text())[1]
        add_on_key=lambda ident,index:re.sub('[^'+safe_id+']','_',ident or 'camera')+'_'+str(index)
        for ident in ('front','front.door','hikvision_1','Garaż 2','a/b','..','.'):
            self.assertTrue(pattern.fullmatch(add_on_key(ident,0)),ident)
        for key in ('','.','..','...','a/b','a b','x'*97,'../edge'):
            self.assertFalse(pattern.fullmatch(key),key)
        self.assertIn("quote(str(camera_id), safe='')",source)
