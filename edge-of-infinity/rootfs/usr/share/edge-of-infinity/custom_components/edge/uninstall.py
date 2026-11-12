"""Admin-only removal of this installation's local HA entries through Core APIs."""
import json
import os
from pathlib import Path
import re
import tempfile
import time


def read_job(home, ident):
    home=Path(home);path=home/'uninstall.json'
    if home.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size>131072:
        raise ValueError('invalid_uninstall_job')
    job=json.loads(path.read_text())
    owner_path=home/'installation.json'
    if owner_path.is_symlink(): raise ValueError('invalid_uninstall_job')
    owner=json.loads(owner_path.read_text())
    if (not re.fullmatch(r'[0-9a-f]{32}',str(ident)) or job.get('id')!=ident
            or job.get('phase')!='awaiting_integration' or job.get('installation_id')!=owner.get('id')
            or not job.get('scopes',{}).get('integration') or time.time()-job.get('created',0)>900):
        raise ValueError('invalid_uninstall_job')
    return job


def save_ack(home, job, remaining):
    home=Path(home);path=home/'uninstall-integration.json'
    if path.is_symlink(): raise ValueError('invalid_uninstall_job')
    data={'id':job['id'],'nonce':job['nonce'],'ok':True,'remaining':remaining}
    with tempfile.NamedTemporaryFile('w',dir=home,delete=False) as f:
        json.dump(data,f);temp=f.name
    os.chmod(temp,0o600);os.replace(temp,path)


async def remove_local(hass, home, ident):
    job=await hass.async_add_executor_job(read_job,home,ident)
    entries=list(hass.config_entries.async_entries('edge'))
    local={e.entry_id for e in entries if str(e.data.get('url','')).lower() in ('','local','file','filesystem')}
    expected=set(job.get('integration',{}).get('entries',[]))
    if not job.get('integration',{}).get('known') or not local.issubset(expected):
        # Unknown registry can be resolved safely from Core's authenticated view,
        # but known plans must not silently broaden to newly added entries.
        if job.get('integration',{}).get('known'):
            raise ValueError('integration_entries_changed')
        expected=local
    # A preceding attempt may have removed some entries before a Core error or
    # HTTP disconnect. Retry the remaining subset without accepting new entries.
    pending=local & expected
    for ident in sorted(pending):
        entry=hass.config_entries.async_get_entry(ident)
        if not entry or entry.domain!='edge' or str(entry.data.get('url','')).lower() not in ('','local','file','filesystem'):
            raise ValueError('invalid_integration_entry')
        result=await hass.config_entries.async_remove(ident)
        if result is False: raise ValueError('integration_unload_failed')
    remaining=len(hass.config_entries.async_entries('edge'))
    await hass.async_add_executor_job(save_ack,home,job,remaining)
    return {'removed':len(pending),'remaining':remaining}


def async_register_uninstall(hass):
    import voluptuous as vol
    from homeassistant.components import websocket_api

    @websocket_api.websocket_command({vol.Required('type'):'edge/uninstall_capabilities'})
    @websocket_api.async_response
    async def capabilities(hass, connection, msg):
        if not connection.user.is_admin:
            connection.send_error(msg['id'],'unauthorized','Administrator required');return
        connection.send_result(msg['id'],{'available':True})

    @websocket_api.websocket_command({vol.Required('type'):'edge/uninstall_integration',vol.Required('job_id'):str})
    @websocket_api.async_response
    async def remove(hass, connection, msg):
        if not connection.user.is_admin:
            connection.send_error(msg['id'],'unauthorized','Administrator required');return
        try:
            result=await remove_local(hass,Path(hass.config.path('edge')),msg['job_id'])
            connection.send_result(msg['id'],result)
        except (OSError,ValueError,KeyError,TypeError):
            connection.send_error(msg['id'],'edge_uninstall_failed','Edge integration removal was not confirmed')
    websocket_api.async_register_command(hass,capabilities)
    websocket_api.async_register_command(hass,remove)
