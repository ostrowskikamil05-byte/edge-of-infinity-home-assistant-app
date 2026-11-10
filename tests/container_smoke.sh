#!/usr/bin/env bash
set -euo pipefail

edge_smoke_image="${1:-edge-native-smoke}"
edge_smoke_dir="$(mktemp -d "${RUNNER_TEMP:-/tmp}/edge-native-smoke.XXXXXX")"
edge_smoke_container="edge-native-smoke-${RANDOM}"
mkdir -p "${edge_smoke_dir}/home" "${edge_smoke_dir}/addon" "${edge_smoke_dir}/runtime" "${edge_smoke_dir}/media"
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
}
trap cleanup EXIT
docker run --detach --name "${edge_smoke_container}" \
  --volume "${edge_smoke_dir}/options.json:/data/options.json:ro" \
  --volume "${edge_smoke_dir}/home:/homeassistant/edge" \
  --volume "${edge_smoke_dir}/addon:/config" \
  --volume "${edge_smoke_dir}/runtime:/tmp/edge-runtime" \
  --volume "${edge_smoke_dir}/media:/media/edge-of-infinity/recordings" \
  "${edge_smoke_image}" >/dev/null
edge_smoke_ready=false
for _ in $(seq 1 45); do
  if docker exec "${edge_smoke_container}" curl --silent --fail http://127.0.0.1:8088/health >/dev/null 2>&1; then
    edge_smoke_ready=true
    break
  fi
  sleep 1
done
test "${edge_smoke_ready}" = true
docker exec -i "${edge_smoke_container}" python3 - <<'PY'
import json
from pathlib import Path
import shutil
assert shutil.which('edge-rtc') and shutil.which('ffmpeg')
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
print('PASS container: native-only boot, healthy API, custom-port migration, camera settings and recording preservation')
PY
