"""Optional local HTTPS setup, independent of camera/recording configuration."""
import ipaddress
import json
from pathlib import Path
import re
import socket
import tempfile
import os

HOST = re.compile(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?')


def normalize_hosts(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise ValueError('local_https_hosts_required')
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('local_https_invalid_host')
        value = value.strip().lower()
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            ip = None
        if ip is not None:
            if ip.is_unspecified or ip.is_multicast:
                raise ValueError('local_https_invalid_host')
            value = str(ip)
        elif not HOST.fullmatch(value) or re.fullmatch(r'[0-9.]+',value) or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in value.split('.')):
            raise ValueError('local_https_invalid_host')
        if value not in result:
            result.append(value)
    return result


def local_settings(home):
    path = Path(home) / 'local-network.json'
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
        if not isinstance(data, dict):
            raise ValueError('local_https_invalid_config')
        hosts = normalize_hosts(data['hosts']) if data.get('hosts') else []
        return {'enabled': data.get('enabled') is True, 'hosts': hosts}
    except (OSError, ValueError):
        return {'enabled': False, 'hosts': [], 'error': 'local_https_invalid_config'}


def save_local_settings(home, data):
    if not isinstance(data, dict) or set(data) - {'enabled', 'hosts'} or type(data.get('enabled')) is not bool:
        raise ValueError('local_https_invalid_config')
    hosts = normalize_hosts(data['hosts']) if data.get('hosts') else []
    if data['enabled'] and not hosts:
        raise ValueError('local_https_hosts_required')
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    path = home / 'local-network.json'
    if path.is_symlink():
        raise ValueError('local_https_symlink')
    with tempfile.NamedTemporaryFile('w', dir=home, delete=False) as handle:
        handle.write(json.dumps({'enabled': data['enabled'], 'hosts': hosts}, indent=2) + '\n')
        name = handle.name
    os.chmod(name, 0o600)
    os.replace(name, path)
    return local_settings(home)


def proxy_configuration(address=None):
    if address is None:
        try:
            address = socket.gethostbyname(socket.gethostname())
        except OSError:
            address = ''
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        return '', ''
    return address, 'http:\n  use_x_forwarded_for: true\n  trusted_proxies:\n    - ' + address + '\n'


def network_status(home, runtime=None):
    settings = local_settings(home)
    address, yaml = proxy_configuration()
    runtime = runtime or {}
    urls = []
    for name in settings['hosts']:
        authority = '[' + name + ']' if ':' in name else name
        urls.append('https://' + authority + ':8443')
    return {**settings, 'gateway': runtime, 'urls': urls, 'proxy_address': address,
            'home_assistant_yaml': yaml, 'port': 8443,
            'trust_required': True, 'cloud_required': False,
            'configuration_changed': False}


def save_proxy_helper(home, content):
    """Write only our separate helper, never a followed link or HA's main YAML."""
    home=Path(home);path=home/'local-https-home-assistant.yaml'
    if path.is_symlink():raise ValueError('local_https_symlink')
    with tempfile.NamedTemporaryFile('w',dir=home,delete=False) as handle:
        handle.write(content);name=handle.name
    os.replace(name,path)
