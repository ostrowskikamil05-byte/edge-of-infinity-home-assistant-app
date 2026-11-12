"""Validate deployment with Compose without starting public services or mail."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
deployment = root / 'deploy/accounts'
spec = importlib.util.spec_from_file_location('edge_account_prepare', deployment / 'prepare.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='edge-account-compose-') as folder:
    envfile = module.prepare(folder, 'accounts.example.org', 'example.org', 'operator@example.org')
    original = envfile.read_bytes()
    assert envfile.stat().st_mode & 0o777 == 0o600
    try:
        module.prepare(folder, 'accounts.example.org', 'example.org', 'operator@example.org')
    except FileExistsError:
        pass
    else:
        raise AssertionError('Existing database encryption credentials were overwritten')
    assert envfile.read_bytes() == original
    command = ['docker', 'compose', '--project-directory', str(deployment), '--env-file', str(envfile), 'config']
    result = subprocess.run(command + ['--quiet'], capture_output=True)
    assert result.returncode != 0, 'An empty SMTP credential must block startup'
    environment = {**os.environ, 'EDGE_SMTP_PASSWORD': 'fixture-only-smtp-password'}
    result = subprocess.run(command + ['--format', 'json'], env=environment, capture_output=True)
    assert result.returncode == 0, 'Compose rejected account deployment configuration'
    model = json.loads(result.stdout)
    services = model['services']
    assert set(services) == {'postgres', 'identity', 'gateway'}
    for name in ('postgres', 'identity'):
        assert not services[name].get('ports'), 'Private identity/database port exposed'
    assert {int(item['published']) for item in services['gateway']['ports']} == {80, 443}
    assert services['identity']['environment']['ZITADEL_DEFAULTINSTANCE_SMTPCONFIGURATION_TLS'] == 'true'
    assert len(services['identity']['environment']['ZITADEL_MASTERKEY']) == 32
    assert services['identity']['command'] == ['start-from-init', '--masterkeyFromEnv', '--tlsMode', 'external']
    for service in services.values():
        assert not service['image'].endswith(':latest')
        assert not service.get('privileged') and service.get('network_mode') != 'host'
        assert not any('docker.sock' in str(volume) for volume in service.get('volumes', []))
print('PASS account deployment: Compose schema, private listeners, TLS mail, generated secrets and non-overwriting preparation; no services started')
