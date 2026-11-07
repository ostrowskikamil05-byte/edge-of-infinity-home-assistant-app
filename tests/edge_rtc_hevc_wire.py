"""Actual HEVC RTP -> EdgeRTC frames -> FFmpeg decode and RTSP microphone.

Run only against tests/browser/edge_rtc_fixture.py in H265/detection mode.
"""
import asyncio
import struct
import subprocess
import time

from aiohttp import ClientSession, WSMsgType


async def main():
    base = 'http://127.0.0.1:18089'
    async with ClientSession() as session:
        deadline = time.monotonic() + 20
        while True:
            try:
                async with session.get(base + '/test/status') as response:
                    status = await response.json()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(.1)
        async with session.ws_connect(base + '/api/edge/rtc/camera.front?microphone=1') as ws:
            hello = await ws.receive_json(timeout=10)
            assert hello['video']['codec_name'] == 'h265', hello
            assert hello['microphone']['active']
            data, count, timestamps = bytearray(), 0, []
            while count < 8:
                message = await ws.receive(timeout=10)
                assert message.type == WSMsgType.BINARY, message
                packet = message.data
                if packet[0] == 1 and (count or packet[1] & 1):
                    data.extend(packet[10:])
                    timestamps.append(struct.unpack('>q', packet[2:10])[0])
                    count += 1
            assert len(set(timestamps)) == len(timestamps) and timestamps == sorted(timestamps), timestamps
            decoded = subprocess.run(['ffmpeg','-v','error','-threads','1','-filter_threads','1','-f','hevc','-i','pipe:0','-frames:v','1','-threads','1','-f','image2pipe','-vcodec','png','pipe:1'], input=bytes(data), capture_output=True, timeout=15)
            assert decoded.returncode == 0, decoded.stderr.decode()
            assert decoded.stdout.startswith(b'\x89PNG\r\n\x1a\n')
            assert struct.unpack('>II', decoded.stdout[16:24]) == (640, 360)
            for _ in range(8):
                await ws.send_bytes(bytes([3]) + bytes(320))
                await asyncio.sleep(.02)
            async with session.get(base + '/test/record') as response:
                recording = await response.json()
            assert recording['copy_video'] and any(row['codec_name'] == 'hevc' and row['width'] == 640 and row['height'] == 360 for row in recording['files']), recording
            assert recording['pixel_identical'], 'recording changed decoded source pixels'
            assert all(row['codec_tag_string']=='hvc1' for row in recording['files']),recording
            async with session.get(base + '/test/alert') as response:
                alert = await response.json()
            assert alert['state']=='ready' and alert['pixel_identical'] and abs(alert['fps']-alert['recorded_fps'])<.05, alert
            deadline = time.monotonic() + 15
            while True:
                async with session.get(base + '/test/detection') as response:
                    detection = await response.json()
                if detection.get('analysis_ms', 0) > 0 and detection['cameras'][0]['local_status'] == 'running':
                    break
                assert time.monotonic() < deadline, detection
                await asyncio.sleep(.1)
            async with session.get('http://127.0.0.1:18090/api/detection/preview?camera=front') as response:
                assert response.status == 200, await response.text()
                assert response.headers['Content-Type'].startswith('image/jpeg')
                assert response.headers['X-Edge-Frame-Width'] == '416'
                assert response.headers['X-Edge-Frame-Height'] == '234'
                jpeg = await response.read()
                import cv2, numpy as np
                assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (234, 416, 3)
            async with session.get(base + '/test/status') as response:
                source = (await response.json())['sources'][0]
            assert source['upstream_connections'] == 1 and source['connection_attempts'] == 1, source
            assert source['microphone_packets'] >= 8, source
            print('PASS: H265 EdgeRTC, original HEVC recording and intrusion clip, local inference, preview and G711 microphone share exactly one camera connection; alert pixels and FPS unchanged')


asyncio.run(main())
