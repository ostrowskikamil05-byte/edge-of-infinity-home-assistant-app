#!/usr/bin/env bash
set -euo pipefail

edge_smoke_image="${1:-edge-native-smoke}"
edge_smoke_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/edge-native-smoke.XXXXXX")"
edge_smoke_container="edge-native-smoke-${RANDOM}"
edge_smoke_supervisor_pid=''
mkdir -p "${edge_smoke_dir}/home" "${edge_smoke_dir}/addon" "${edge_smoke_dir}/runtime" "${edge_smoke_dir}/media"
# A version-label change must reuse the same filesystem/dependency layers.
if ! docker build --build-arg BUILD_VERSION=layer-cache-probe --tag "${edge_smoke_image}-layer-probe" edge-of-infinity > "${edge_smoke_dir}/layer-probe.log" 2>&1; then
  cat "${edge_smoke_dir}/layer-probe.log"
  exit 1
fi
docker image inspect "${edge_smoke_image}" --format '{{json .RootFS.Layers}}' > "${edge_smoke_dir}/base-layers.json"
docker image inspect "${edge_smoke_image}-layer-probe" --format '{{json .RootFS.Layers}}' > "${edge_smoke_dir}/probe-layers.json"
if ! cmp -s "${edge_smoke_dir}/base-layers.json" "${edge_smoke_dir}/probe-layers.json"; then
  echo 'Version-only build unexpectedly changed filesystem/dependency layers'
  cat "${edge_smoke_dir}/layer-probe.log"
  exit 1
fi
printf '%s\n' 'original recording' > "${edge_smoke_dir}/media/keep.mp4"
cat > "${edge_smoke_dir}/options.json" <<'JSON'
{"core_engine":"mediamtx_janus","mediamtx_rtsp_port":9556,"mediamtx_api_port":10997,"janus_enabled":true,"cameras":[],"retention_days":14}
JSON
cat > "${edge_smoke_dir}/home/edge.json" <<'JSON'
{"live":{"engine":"janus_webrtc","mobile_webrtc_turn_password":"retired-test-value"},"storage":{"recordings_dir":"/media/edge-of-infinity/recordings","database_path":"/homeassistant/edge/edge.db","retention_days":14},"nvr":{"segment_seconds":10,"browser_playback":"copy"},"cameras":[{"id":"unv","vendor":"uniview","host":"192.0.2.10","username":"admin","password":"fixture-only-password","enabled":false,"record":true,"record_stream":"main"}]}
JSON
printf '%s\n' 'retired engine log' > "${edge_smoke_dir}/home/janus.log"
cleanup() {
  docker logs "${edge_smoke_container}" 2>&1 || true
  docker rm -f "${edge_smoke_container}" >/dev/null 2>&1 || true
  if [[ -n "${edge_smoke_supervisor_pid}" ]]; then
    kill "${edge_smoke_supervisor_pid}" 2>/dev/null || true
    wait "${edge_smoke_supervisor_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
python3 tests/supervisor_socket_fixture.py "${edge_smoke_dir}/supervisor" > "${edge_smoke_dir}/supervisor.log" 2>&1 &
edge_smoke_supervisor_pid=$!
for _ in $(seq 1 20); do
  if [[ -f "${edge_smoke_dir}/supervisor/ready" ]]; then break; fi
  sleep .1
done
test -f "${edge_smoke_dir}/supervisor/ready"
start_container() {
docker run --detach --name "${edge_smoke_container}" \
  --env SUPERVISOR_SOCKET=/run/edge-fixture-supervisor/api.sock \
  --env SUPERVISOR_TOKEN=fixture-only-supervisor-token \
  --volume "${edge_smoke_dir}/supervisor:/run/edge-fixture-supervisor" \
  --volume "${edge_smoke_dir}/options.json:/data/options.json:ro" \
  --volume "${edge_smoke_dir}/home:/homeassistant/edge" \
  --volume "${edge_smoke_dir}/addon:/config" \
  --volume "${edge_smoke_dir}/runtime:/tmp/edge-runtime" \
  --volume "${edge_smoke_dir}/media:/media/edge-of-infinity/recordings" \
  "${edge_smoke_image}" >/dev/null
}
wait_ready() {
edge_smoke_ready=false
for _ in $(seq 1 45); do
  if docker exec "${edge_smoke_container}" curl --silent --fail http://127.0.0.1:8088/health >/dev/null 2>&1; then
    edge_smoke_ready=true
    break
  fi
  sleep 1
done
test "${edge_smoke_ready}" = true
}
start_container
wait_ready
docker exec -i "${edge_smoke_container}" python3 - <<'PY'
import json
import os
from pathlib import Path
import shutil
import time
assert shutil.which('edge-rtc') and shutil.which('ffmpeg')
assert os.environ.get('SUPERVISOR_SOCKET')=='/run/edge-fixture-supervisor/api.sock', 's6 did not restore container environment'
assert os.environ.get('SUPERVISOR_TOKEN')=='fixture-only-supervisor-token'
assert 'GOMAXPROCS' not in os.environ and 'GOMEMLIMIT' not in os.environ, 'compiler limits leaked into media runtime'
assert not shutil.which('janus') and not shutil.which('mediamtx')
for name in ('icon.png','logo.png','logo-light.png'):
    data=Path('/usr/share/edge-of-infinity/brand',name).read_bytes()
    assert data.startswith(b'\x89PNG\r\n\x1a\n') and data[25]==6, name
options=json.loads(Path('/tmp/edge-runtime/native-options.json').read_text())
assert options['edge_rtc_rtsp_port']==9556 and options['edge_rtc_api_port']==10997
assert not any(k.startswith(('mediamtx_', 'janus_')) or k=='core_engine' for k in options)
config=json.loads(Path('/homeassistant/edge/edge.json').read_text())
assert config['live']=={'engine':'edge_rtc'}
assert config['cameras'][0]['password']=='fixture-only-password'
assert config['nvr']['segment_seconds']==10
assert config['storage']['retention_days']==14
assert Path('/media/edge-of-infinity/recordings/keep.mp4').read_text()=='original recording\n'
assert not Path('/homeassistant/edge/janus.log').exists()
runtime=json.loads(Path('/tmp/edge-runtime/edge-rtc.json').read_text())
assert runtime['sources']==[] and runtime['http']==':10997' and runtime['rtsp']==':9556'
for _ in range(15):
    report=json.loads(Path('/homeassistant/edge/native-migration.json').read_text())
    if report.get('supervisor')=={'status':'synced','scope':'panel_owned_settings'}:break
    time.sleep(1)
assert report['supervisor']=={'status':'synced','scope':'panel_owned_settings'}, report
assert report['supervisor_transport']=='unix'
persisted=json.loads(Path('/run/edge-fixture-supervisor/state.json').read_text())
assert persisted['writes']>=2 and persisted['options']['cameras']==[]
assert not any(k.startswith(('mediamtx_', 'janus_', 'hikvision_', 'detection_')) or k=='core_engine' for k in persisted['options'])
assert '8889/tcp' not in persisted['network']
import urllib.request
import urllib.error
setup=json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/setup'))
assert setup['needs_setup']
for path in ('/api/config','/cameras.json','/api/edge-rtc/stream/unv_main','/api/recording/status'):
    try:
        urllib.request.urlopen('http://127.0.0.1:8088'+path)
        raise AssertionError('operational route bypassed setup: '+path)
    except urllib.error.HTTPError as error:
        assert error.code==423
        error.close()
request=urllib.request.Request('http://127.0.0.1:8088/api/setup/access',data=json.dumps({'csrf':setup['csrf'],'mode':'local','confirmed':True}).encode(),headers={'Content-Type':'application/json'})
assert not json.load(urllib.request.urlopen(request))['needs_setup']
assert json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/config'))['cameras'][0]['password']=='fixture-only-password'
print('PASS container: real s6 restores Supervisor access, verified persisted cleanup, dependency layers reused, compiler limits stay build-only, native boot/API, custom ports and recordings preserved')
PY

# Recreating the app drops private /data but keeps the HA/media mounts.
docker exec "${edge_smoke_container}" sh -c 'cp /homeassistant/edge/edge.json /homeassistant/edge/camera-presets.json; mkdir -p /homeassistant/edge/snapshots; echo fixture > /homeassistant/edge/snapshots/old.jpg'
docker rm -f "${edge_smoke_container}" >/dev/null
start_container
wait_ready
docker exec -i "${edge_smoke_container}" python3 - <<'PY'
import json
from pathlib import Path
import urllib.request
config=json.loads(Path('/homeassistant/edge/edge.json').read_text())
assert config['cameras']==[], 'reinstalled cameras returned from a shared mirror'
assert '/recordings/' in config['storage']['recordings_dir']
assert not Path('/homeassistant/edge/camera-presets.json').exists()
assert not Path('/homeassistant/edge/snapshots/old.jpg').exists()
assert Path('/media/edge-of-infinity/recordings/keep.mp4').exists(), 'reinstall silently deleted recordings'
status=json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/setup'))
assert status['needs_setup'] and not status['legacy_upgrade']
request=urllib.request.Request('http://127.0.0.1:8088/api/setup/reset',data=json.dumps({'csrf':status['csrf'],'confirm':'DELETE EDGE','include_media':True}).encode(),headers={'Content-Type':'application/json'})
assert json.load(urllib.request.urlopen(request))['pending_reset']
PY
docker restart "${edge_smoke_container}" >/dev/null
wait_ready
docker exec -i "${edge_smoke_container}" python3 - <<'PY'
import json
from pathlib import Path
import urllib.request
assert not Path('/media/edge-of-infinity/recordings/keep.mp4').exists()
assert json.loads(Path('/homeassistant/edge/edge.json').read_text())['cameras']==[]
assert json.loads(Path('/config/edge.json').read_text())['cameras']==[]
assert json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/setup'))['needs_setup']
print('PASS real container reinstall: blank configuration, snapshots/presets removed, recordings kept until explicit full removal, clean first-run setup')
PY

# The real runner enters cleanup-only mode before starting the media core.
# The fixture records the Supervisor uninstall dispatch; it does not remove Docker.
docker exec -i "${edge_smoke_container}" python3 - <<'PY'
import json
from pathlib import Path
import urllib.request
Path('/media/edge-of-infinity/recordings/uninstall.mp4').write_bytes(b'fixture video')
def request(path, payload=None):
    req=urllib.request.Request('http://127.0.0.1:8088'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req))
status=request('/api/uninstall')
scopes=dict.fromkeys(('recordings','alerts','thumbnails','snapshots','cache','settings','backups','history'),True)
scopes['integration']=False
plan=request('/api/uninstall/plan',{'csrf':status['csrf'],'scopes':scopes})
result=request('/api/uninstall/commit',{'csrf':status['csrf'],'id':plan['id'],'confirm':'UNINSTALL EDGE','bridge_ready':False})
assert result['phase']=='queued'
PY
docker restart "${edge_smoke_container}" >/dev/null
wait_ready
docker exec -i "${edge_smoke_container}" python3 - <<'PY'
import json,time,urllib.request
from pathlib import Path
for _ in range(30):
    status=json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/uninstall'))
    if status['phase']=='supervisor_pending':break
    time.sleep(.2)
assert status['phase']=='supervisor_pending',status
assert json.load(urllib.request.urlopen('http://127.0.0.1:8088/health'))['mode']=='uninstall'
assert not Path('/homeassistant/edge/edge.json').exists()
assert not Path('/media/edge-of-infinity/recordings/uninstall.mp4').exists()
state=json.loads(Path('/run/edge-fixture-supervisor/state.json').read_text())
assert state['uninstall_requests']==1
assert state['uninstall_request']=={'remove_config':True}
print('PASS real offline cleanup and own-Supervisor uninstall dispatch; no camera core was restarted')
PY
