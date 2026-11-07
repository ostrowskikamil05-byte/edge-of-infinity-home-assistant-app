"""Persistent, bounded sampling of the existing local Edge RTSP relay."""
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse


_GPU_LOCK=threading.Lock()
_GPU_CACHE=(-1e9,'')


def vaapi_device():
    """Probe only a local render device; never probe a camera or open a feed."""
    global _GPU_CACHE
    with _GPU_LOCK:
        if time.monotonic()-_GPU_CACHE[0]<300:return _GPU_CACHE[1]
        device=''
        for node in sorted(Path('/dev/dri').glob('renderD*')):
            if not os.access(node,os.R_OK|os.W_OK):continue
            command=['ffmpeg','-v','error','-nostdin','-init_hw_device',f'vaapi=edge:{node}','-filter_hw_device','edge','-f','lavfi','-i','color=s=64x64','-vf','format=nv12,hwupload,hwdownload,format=nv12','-frames:v','1','-f','null','-']
            try:
                if subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5).returncode==0:device=str(node);break
            except (OSError,subprocess.TimeoutExpired):pass
        _GPU_CACHE=(time.monotonic(),device)
        return device


def cpu_overloaded():
    try:return os.getloadavg()[0]/max(1,os.cpu_count() or 1)>=1.25
    except (OSError,AttributeError):return False


def analysis_command(url, fps, backend='cpu', device=''):
    parsed=urlparse(url)
    if parsed.scheme!='rtsp' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or not parsed.port:
        raise RuntimeError('shared_source_required')
    command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','1','-filter_threads','1',
             '-rtsp_transport','tcp','-user_agent',f'EdgeAI/{backend}','-probesize','32768','-analyzeduration','0']
    sampling=f'fps={max(.01,min(3,float(fps))):.4f},'
    scaling='scale=416:416:force_original_aspect_ratio=decrease'
    if backend=='vaapi':
        command+=['-hwaccel','vaapi','-hwaccel_device',device,'-hwaccel_output_format','vaapi']
        scaling='scale_vaapi=w=416:h=416:force_original_aspect_ratio=decrease:format=nv12,hwdownload,format=nv12'
    elif backend=='cpu_keyframes':
        command+=['-skip_frame','nokey'];sampling=''
    command+=['-i',url,'-an','-threads','1','-vf',sampling+scaling,
              '-fps_mode','passthrough','-c:v','ppm','-pix_fmt','rgb24','-f','image2pipe','pipe:1']
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
    def __init__(self,url,fps,mode='auto'):
        self.url,self.fps,self.mode=url,fps,mode
        self.device=vaapi_device() if mode=='auto' else ''
        self.backend='vaapi' if self.device else ('cpu_keyframes' if mode=='keyframes' else 'cpu')
        self.fallback_reason='';self.last_delivery=0.;self.frame_interval=0.
        self.command=analysis_command(url,fps,self.backend,self.device)
        self.condition=threading.Condition();self.stopped=threading.Event()
        self.process=None;self.latest=None;self.sequence=0;self.consumed=0;self.error=''
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()

    def _run(self):
        try:
            while not self.stopped.is_set():
                process=subprocess.Popen(self.command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                with self.condition:self.process=process
                if self.stopped.is_set():process.terminate();return
                previous=0.;restart=False
                try:
                    while not self.stopped.is_set():
                        frame=read_ppm(process.stdout);now=time.monotonic()
                        gap=now-previous if previous else 0.;previous=now
                        if self.should_reduce_decode(gap):
                            with self.condition:
                                self.backend='cpu_keyframes';self.fallback_reason='cpu_overload';self.latest=None;self.consumed=self.sequence
                            restart=True;break
                        with self.condition:
                            self.frame_interval=gap;self.last_delivery=time.time()
                            self.sequence+=1;self.latest=(frame,self.last_delivery);self.condition.notify_all()
                except Exception:
                    if self.backend=='vaapi' and not self.stopped.is_set():
                        self.backend='cpu';self.fallback_reason='vaapi_unavailable_for_stream';restart=True
                    else:raise
                finally:self._stop_process()
                if not restart:break
                self.command=analysis_command(self.url,self.fps,self.backend,self.device)
        except Exception as error:
            # Publish fixed error codes only; never relay addresses/credentials.
            self.error='analysis_stream_closed' if isinstance(error,RuntimeError) else 'analysis_decoder_start_failed'
        finally:
            self.stopped.set()
            with self.condition:self.condition.notify_all()
            self._stop_process()

    def should_reduce_decode(self,gap):
        return self.mode=='auto' and self.backend=='cpu' and gap>max(3,3/max(.01,self.fps)) and cpu_overloaded()

    def status(self):
        return {'backend':self.backend,'fallback_reason':self.fallback_reason,'frame_interval_seconds':round(self.frame_interval,2),'last_frame_at':self.last_delivery}

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
