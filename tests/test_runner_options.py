import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_edge_panel_config import ROOT


class RunnerOptionsTests(unittest.TestCase):
    def test_explicit_false_and_zero_are_not_replaced_with_defaults(self):
        if not shutil.which('jq'):
            self.skipTest('jq is required')
        runner=(ROOT/'edge-of-infinity/rootfs/usr/bin/edge-app-run').read_text()
        start=runner.index('read_option() {');end=runner.index('\n}\n',start)+3
        code='OPTIONS_PATH="$1"\n'+runner[start:end]+'\nread_option "$2" "$3"'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'options.json';path.write_text(json.dumps({'enabled':False,'count':0,'empty':None}))
            for key,fallback,expected in [('enabled','true','false'),('count','14','0'),('missing','default','default'),('empty','default','default')]:
                with self.subTest(key=key):
                    result=subprocess.run(['bash','-c',code,'test',str(path),key,fallback],capture_output=True,text=True,check=True)
                    self.assertEqual(result.stdout.strip(),expected)
