"""Native H264/Android and H265/iPhone delivery bursts on disposable sources."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

root = Path(__file__).resolve().parents[1]
for codec, browser in (('h264', 'chromium'), ('h265', 'webkit')):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'fixture.log'
        env = {**os.environ, 'EDGE_RTC_TEST_CODEC': codec, 'EDGE_TEST_BROWSER': browser,
               'EDGE_RTC_TEST_FPS': '20', 'EDGE_RTC_TEST_GOP_SECONDS': '1',
               'EDGE_RTC_TEST_RESPOND_PLI': '0'}
        with path.open('wb') as log:
            process = subprocess.Popen([sys.executable, str(root / 'tests/browser/edge_rtc_fixture.py')],
                                       cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(path.read_text()[-4000:])
                    try:
                        urllib.request.urlopen('http://127.0.0.1:18089/test/status', timeout=1).close()
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(.1)
                subprocess.run(['node', 'tests/browser/edge_rtc_cellular.cjs'], cwd=root, env=env,
                               check=True, timeout=90)
                if browser == 'chromium':
                    subprocess.run(['node', 'tests/browser/edge_rtc_native.cjs'], cwd=root, env=env,
                                   check=True, timeout=120)
            except BaseException:
                print(path.read_text()[-4000:])
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
