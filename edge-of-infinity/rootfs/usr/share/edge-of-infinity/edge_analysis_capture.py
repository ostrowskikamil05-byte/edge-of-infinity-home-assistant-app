"""Persistent, bounded sampling of the existing local Edge RTSP relay."""
import os
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse


def analysis_command(url, fps):
    parsed=urlparse(url)
    if parsed.scheme!='rtsp' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or not parsed.port:
        raise RuntimeError('shared_source_required')
    command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','1','-filter_threads','1',
             '-rtsp_transport','tcp','-probesize','32768','-analyzeduration','0','-i',url,'-an',
             '-threads','1','-vf',f'fps={max(.01,min(3,float(fps))):.4f},scale=416:416:force_original_aspect_ratio=decrease',
             '-c:v','ppm','-pix_fmt','rgb24','-f','image2pipe','pipe:1']
    return ['nice','-n','10',*command] if shutil.which('nice') else command


def read_ppm(handle):
    """Read a self-described frame; avoid parsing FFmpeg log formatting."""
    if handle.readline(64).strip()!=b'P6':raise RuntimeError('analysis_frame_header')
    dimensions=handle.readline(64).split()
    if len(dimensions)!=2 or handle.readline(64).strip()!=b'255':raise RuntimeError('analysis_frame_header')
    width,height=map(int,dimensions)
    if not 1<=width<=416 or not 1<=height<=416:raise RuntimeError('analysis_frame_size')
    remaining=width*height*3;chunks=[]
    while remaining:
        chunk=handle.read(remaining)
        if not chunk:raise RuntimeError('analysis_stream_closed')
        chunks.append(chunk);remaining-=len(chunk)
    rgb=b''.join(chunks);bgr=bytearray(len(rgb))
    bgr[0::3],bgr[1::3],bgr[2::3]=rgb[2::3],rgb[1::3],rgb[0::3]
    padded=bytearray(416*416*3);left,top=(416-width)//2,(416-height)//2
    for row in range(height):
        offset=((row+top)*416+left)*3
        padded[offset:offset+width*3]=bgr[row*width*3:(row+1)*width*3]
    return bytes(padded),width,height


class SharedFrameReader:
    """One low-priority decoder and one latest frame, with no camera URL."""
    def __init__(self,url,fps):
        self.command=analysis_command(url,fps)
        self.condition=threading.Condition();self.stopped=threading.Event()
        self.process=None;self.latest=None;self.sequence=0;self.consumed=0;self.error=''
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def _run(self):
        try:
            process=subprocess.Popen(self.command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            with self.condition:self.process=process
            if self.stopped.is_set():process.terminate();return
            while not self.stopped.is_set():
                frame=read_ppm(process.stdout)
                with self.condition:
                    self.sequence+=1;self.latest=(frame,time.time());self.condition.notify_all()
        except Exception as error:
            # Publish fixed error codes only; never relay addresses/credentials.
            self.error='analysis_stream_closed' if isinstance(error,RuntimeError) else 'analysis_decoder_start_failed'
        finally:
            self.stopped.set()
            with self.condition:self.condition.notify_all()
            self._stop_process()

    def read(self,timeout=45):
        with self.condition:
            ready=self.condition.wait_for(lambda:self.sequence>self.consumed or self.stopped.is_set(),timeout)
            if not ready:raise RuntimeError('analysis_frame_timeout')
            if self.sequence<=self.consumed:raise RuntimeError(self.error or 'analysis_stream_closed')
            self.consumed=self.sequence
            return self.latest

    def _stop_process(self):
        process=self.process
        if process:
            try:
                if process.poll() is None:process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=2)
            except OSError:pass
            if process.stdout:process.stdout.close()

    def close(self):
        self.stopped.set()
        with self.condition:self.condition.notify_all()
        self._stop_process()
        if threading.current_thread()!=self.thread:self.thread.join(timeout=3)
