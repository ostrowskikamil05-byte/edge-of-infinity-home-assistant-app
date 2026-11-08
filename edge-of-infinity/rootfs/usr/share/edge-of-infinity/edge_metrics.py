"""Nonblocking host CPU sampling; load average is not CPU utilization."""
from pathlib import Path
import threading
import time
import functools


@functools.lru_cache(maxsize=1)
def hardware_inventory():
    """Read-only inventory, once per boot. No serial numbers or subprocesses."""
    def read(path):
        try:return Path(path).read_text().strip()[:160]
        except OSError:return ''
    cpu=read('/proc/cpuinfo')
    try:
        cpu=next(line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith(('model name','Hardware')))
    except (OSError,StopIteration):cpu='unknown'
    graphics=[]
    for card in Path('/sys/class/drm').glob('card[0-9]*'):
        if '-' in card.name:continue
        device=card/'device'
        try:driver=(device/'driver').resolve().name if (device/'driver').exists() else 'unavailable'
        except OSError:driver='unavailable'
        graphics.append({'card':card.name,'vendor_id':read(device/'vendor'),'device_id':read(device/'device'),'driver':driver})
    return {'cpu_model':cpu,'manufacturer':read('/sys/class/dmi/id/sys_vendor'),
            'product':read('/sys/class/dmi/id/product_name'),'graphics':graphics,
            'render_nodes':[p.name for p in Path('/dev/dri').glob('renderD*')],
            'note':'Detected devices do not establish working hardware decoding/encoding.'}


class CpuSampler:
    def __init__(self):
        self.lock=threading.Lock();self.previous=None;self.latest={}

    def sample(self):
        try:
            fields=Path('/proc/stat').read_text().splitlines()[0].split()
            if fields[0]!='cpu':return {}
            values=[int(value) for value in fields[1:9]]
            if len(values)<5:return {}
            now=time.monotonic();total=sum(values);idle=values[3];waiting=values[4]
            with self.lock:
                previous=self.previous
                if previous and now-previous[0]<.5:return dict(self.latest)
                self.previous=(now,total,idle,waiting)
                if not previous:return {}
                elapsed=now-previous[0];ticks=total-previous[1]
                if ticks<=0:return {}
                wait=max(0,waiting-previous[3]);free=max(0,idle-previous[2])
                self.latest={'busy_percent':round(max(0,min(100,100*(ticks-free-wait)/ticks)),1),
                             'io_wait_percent':round(max(0,min(100,100*wait/ticks)),1),'sample_seconds':round(elapsed,2)}
                return dict(self.latest)
        except (OSError,ValueError,IndexError):return {}


def cpu_load_severity(hardware):
    load=hardware.get('load_per_core_1m')
    if not isinstance(load,(int,float)):return 'warning'
    cores=max(1,hardware.get('cpu_count') or 1);averages=hardware.get('loadavg') or []
    five=averages[1]/cores if len(averages)>1 else 0
    busy=(hardware.get('cpu_usage') or {}).get('busy_percent',0)
    # Confirm sustained queue pressure AND measured busy CPU, after boot.
    if load>=1.25 and five>=1.25 and busy>=90 and (hardware.get('system_uptime_seconds') or 0)>=600:return 'error'
    return 'warning' if load>=.75 or busy>=90 else 'ok'
