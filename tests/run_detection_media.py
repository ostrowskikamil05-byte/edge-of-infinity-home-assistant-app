"""Launch the disposable HEVC camera/core and run the actual media/AI test."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    log_path = Path(directory) / 'fixture.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen([sys.executable, str(root/'tests/browser/edge_rtc_fixture.py')],
            cwd=root, env={**os.environ, 'EDGE_RTC_TEST_CODEC':'h265', 'EDGE_RTC_TEST_DETECTION':'1'}, stdout=log, stderr=subprocess.STDOUT)
        try:
            subprocess.run([sys.executable, str(root/'tests/edge_rtc_hevc_wire.py')], cwd=root, check=True, timeout=120)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
            # Also on failure: the fixture log is where the cause is.
            log.flush()
            print(log_path.read_text(errors='replace')[-4000:])
