"""Actual RTSP/HA-proxy/WebCodecs startup with and without camera PLI support."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

root=Path(__file__).resolve().parents[1]
for responds in ('0','1'):
    with tempfile.TemporaryDirectory() as directory:
        log_path=Path(directory)/'fixture.log'
        env={**os.environ,'EDGE_RTC_TEST_FPS':'20','EDGE_RTC_TEST_GOP_SECONDS':'24','EDGE_RTC_TEST_RESPOND_PLI':responds}
        with log_path.open('wb') as log:
            process=subprocess.Popen([sys.executable,str(root/'tests/browser/edge_rtc_fixture.py')],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+45
                while True:
                    if process.poll() is not None:raise RuntimeError(log_path.read_text()[-4000:])
                    try:
                        urllib.request.urlopen('http://127.0.0.1:18089/test/status',timeout=1).close();break
                    except OSError:
                        if time.monotonic()>deadline:raise
                        time.sleep(.1)
                subprocess.run(['node','tests/browser/edge_rtc_long_gop.cjs'],cwd=root,env=env,check=True,timeout=75)
            except BaseException:
                print(log_path.read_text()[-4000:]);raise
            finally:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.wait()
