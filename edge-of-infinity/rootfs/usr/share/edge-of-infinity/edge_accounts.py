"""Optional central-account linking; independent from HA/media authorization.

OAuth device authorization keeps registration/passwords/MFA at the identity
service. Neither passwords nor bearer/refresh tokens are returned to the panel.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import CookieError, SimpleCookie
import urllib.error
import urllib.parse
import urllib.request


class AccountError(RuntimeError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class CentralAccounts:
    MAX_SESSIONS = 128

    def __init__(self, home, *, clock=time.time):
        self.home = Path(home) / 'accounts'
        self.clock = clock
        self.lock = threading.RLock()
        self.discovery = None
        self.discovery_until = 0

    @contextmanager
    def _connect(self):
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.home.is_symlink():
            raise AccountError('account_storage_invalid', 503)
        path = self.home / 'sessions.sqlite'
        if path.is_symlink():
            raise AccountError('account_storage_invalid', 503)
        db = sqlite3.connect(path, timeout=3)
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expiry REAL NOT NULL, value TEXT NOT NULL)')
        db.execute('DELETE FROM sessions WHERE expiry < ?', (self.clock(),))
        db.commit()
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _key(token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', token or ''):
            return ''
        return hashlib.sha256(token.encode()).hexdigest()

    def _read(self, token):
        with self.lock, self._connect() as db:
            row = db.execute('SELECT value FROM sessions WHERE id=?', (self._key(token),)).fetchone()
        if not row:
            raise AccountError('account_session_expired', 401)
        return json.loads(row[0])

    def _save(self, token, value):
        # UPDATE cannot resurrect a session removed by a concurrent logout.
        with self.lock, self._connect() as db:
            if not db.execute('UPDATE sessions SET value=? WHERE id=?',
                              (json.dumps(value), self._key(token))).rowcount:
                raise AccountError('account_session_expired', 401)

    def session(self, token, principal=''):
        try:
            if self._read(token).get('principal', '') != principal:
                raise AccountError('account_session_expired', 401)
            return token, False
        except AccountError as error:
            if error.code != 'account_session_expired':
                raise
        token = secrets.token_urlsafe(32)
        with self.lock, self._connect() as db:
            if db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] >= self.MAX_SESSIONS:
                raise AccountError('account_session_limit', 429)
            value = {'csrf': secrets.token_urlsafe(32), 'created': self.clock(), 'principal': principal}
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                       (self._key(token), self.clock() + 3600, json.dumps(value)))
        return token, True

    def _csrf(self, token, csrf):
        value = self._read(token)
        if (not isinstance(csrf, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,100}', csrf)
                or not hmac.compare_digest(value['csrf'], csrf)):
            raise AccountError('account_csrf_invalid', 403)
        return value

    def provider(self):
        path = self.home / 'provider.json'
        if path.is_symlink():
            raise AccountError('account_storage_invalid', 503)
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise AccountError('account_provider_invalid', 503) from None
        return self._validate_provider(value)

    @staticmethod
    def _validate_provider(value):
        if not isinstance(value, dict):
            raise AccountError('account_provider_invalid')
        issuer = str(value.get('issuer', '')).rstrip('/')
        url = urllib.parse.urlsplit(issuer)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise AccountError('account_issuer_requires_https')
        client = str(value.get('client_id', ''))
        if not re.fullmatch(r'[A-Za-z0-9@._:-]{1,200}', client):
            raise AccountError('account_client_id_invalid')
        support = str(value.get('support_email', ''))
        if support and not re.fullmatch(r'[^\s@<>]{1,100}@[^\s@<>]{1,150}', support):
            raise AccountError('account_support_email_invalid')
        account_url = value.get('account_url') or ''
        if account_url:
            account_url = CentralAccounts._endpoint(account_url, issuer)
        return {'issuer': issuer, 'client_id': client, 'support_email': support,
                'account_url': account_url}

    def configure(self, value):
        provider = self._validate_provider(value)
        with self.lock, self._connect() as db:
            # Validate TLS discovery before changing the working provider.
            discovery = self._discover(provider)
            temporary = self.home / ('provider-' + secrets.token_hex(8) + '.tmp')
            try:
                with open(temporary, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
                    json.dump(provider, file)
                os.replace(temporary, self.home / 'provider.json')
            finally:
                temporary.unlink(missing_ok=True)
            db.execute('DELETE FROM sessions')
            self.discovery, self.discovery_until = discovery, self.clock() + 300
        return {'configured': True}

    @staticmethod
    def _endpoint(url, issuer):
        if not isinstance(url, str):
            raise AccountError('account_provider_endpoint_invalid', 503)
        try:
            parsed, base = urllib.parse.urlsplit(url), urllib.parse.urlsplit(issuer)
        except ValueError:
            raise AccountError('account_provider_endpoint_invalid', 503) from None
        if (parsed.scheme != 'https' or parsed.netloc != base.netloc or parsed.username
                or parsed.password or parsed.fragment):
            raise AccountError('account_provider_endpoint_invalid', 503)
        return url

    def _request(self, url, *, form=None, token=None):
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        headers = {'Accept': 'application/json', 'Content-Type': 'application/x-www-form-urlencoded'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise AccountError('account_provider_response_invalid', 503)
            result = json.loads(raw) if raw else {}
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except urllib.error.HTTPError as error:
            try:
                if error.code == 400:
                    try:
                        detail = json.loads(error.read(4096))
                        code = detail.get('error') if isinstance(detail, dict) else None
                    except (ValueError, OSError):
                        code = None
                    if code in ('authorization_pending', 'slow_down', 'access_denied', 'expired_token', 'invalid_grant'):
                        return {'error': code}
            finally:
                error.close()
            raise AccountError('account_provider_unavailable', 503) from None
        except (OSError, ValueError):
            raise AccountError('account_provider_unavailable', 503) from None

    def _discover(self, provider):
        data = self._request(provider['issuer'] + '/.well-known/openid-configuration')
        issuer = data.get('issuer')
        if not isinstance(issuer, str) or issuer.rstrip('/') != provider['issuer']:
            raise AccountError('account_provider_issuer_mismatch', 503)
        data['issuer'] = provider['issuer']
        for key in ('device_authorization_endpoint', 'token_endpoint', 'userinfo_endpoint'):
            data[key] = self._endpoint(data.get(key, ''), provider['issuer'])
        if data.get('revocation_endpoint'):
            data['revocation_endpoint'] = self._endpoint(data['revocation_endpoint'], provider['issuer'])
        return data

    def _ready(self):
        provider = self.provider()
        if not provider:
            raise AccountError('account_service_not_configured', 503)
        if (not self.discovery or self.discovery_until < self.clock()
                or self.discovery.get('issuer') != provider['issuer']):
            self.discovery, self.discovery_until = self._discover(provider), self.clock() + 300
        return provider, self.discovery

    @staticmethod
    def _profile(data):
        if not isinstance(data.get('sub'), str) or not data['sub'] or data.get('email_verified') is not True:
            raise AccountError('account_email_not_verified', 403)
        email = data.get('email')
        if not isinstance(email, str) or not re.fullmatch(r'[^\s@<>]{1,100}@[^\s@<>]{1,150}', email):
            raise AccountError('account_profile_invalid', 503)
        return {'email': email, 'name': str(data.get('name') or email)[:120]}

    def status(self, token):
        with self.lock:
            value, provider = self._read(token), self.provider()
        profile = value.get('profile') if self._bound_provider(value, provider) else None
        return {'configured': bool(provider), 'csrf': value['csrf'],
                'authenticated': bool(profile and value.get('token_until', 0) > self.clock()),
                'profile': profile if value.get('token_until', 0) > self.clock() else None,
                'remember': bool(value.get('remember')), 'issuer': provider.get('issuer', ''),
                'client_id': provider.get('client_id', ''),
                'account_url': provider.get('account_url', ''),
                'support_email': provider.get('support_email', '')}

    def start(self, token, csrf, remember=False):
        with self.lock:
            value = self._csrf(token, csrf)
            if self.clock() - value.get('flow_started', -1000) < 60:
                raise AccountError('account_retry_later', 429)
            provider, discovery = self._ready()
            # The local cookie/DB lifetime controls persistence; a refresh token
            # also keeps an ordinary session alive past a short access-token TTL.
            scope = 'openid email profile offline_access'
            flow = self._request(discovery['device_authorization_endpoint'], form={
                'client_id': provider['client_id'], 'scope': scope})
            code, device = flow.get('user_code'), flow.get('device_code')
            if not isinstance(code, str) or not 1 <= len(code) <= 100 or not isinstance(device, str) or not 1 <= len(device) <= 4096:
                raise AccountError('account_provider_response_invalid', 503)
            interval = self._seconds(flow.get('interval', 5), 5, 30)
            ttl = self._seconds(flow.get('expires_in', 600), 30, 900)
            uri = self._endpoint(flow.get('verification_uri', ''), provider['issuer'])
            complete = self._endpoint(flow.get('verification_uri_complete') or uri, provider['issuer'])
            value.update(flow_started=self.clock(),
                         flow={'code': device, 'until': self.clock() + ttl, 'interval': interval,
                               'issuer': provider['issuer'], 'client_id': provider['client_id'],
                               'remember': bool(remember),
                               'next_poll': self.clock() + interval})
            self._save(token, value)
        return {'user_code': code, 'verification_uri': uri, 'verification_uri_complete': complete,
                'interval': interval, 'expires_in': ttl}

    def poll(self, token, csrf):
        with self.lock:
            value = self._csrf(token, csrf)
            flow = value.get('flow') or {}
            if flow.get('until', 0) < self.clock():
                raise AccountError('account_code_expired', 401)
            if not self._bound_provider(flow, self.provider()):
                raise AccountError('account_provider_changed', 401)
            if flow['next_poll'] > self.clock():
                return {'pending': True, 'interval': flow['interval']}
            flow['next_poll'] = self.clock() + flow['interval']
            self._save(token, value)
            provider, discovery = self._ready()
            tokens = self._request(discovery['token_endpoint'], form={
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                'device_code': flow['code'], 'client_id': provider['client_id']})
            if tokens.get('error') in ('authorization_pending', 'slow_down'):
                if tokens['error'] == 'slow_down':
                    flow['interval'] = min(30, flow['interval'] + 5)
                    flow['next_poll'] = self.clock() + flow['interval']
                self._save(token, value)
                return {'pending': True, 'interval': flow['interval']}
            self._tokens(value, tokens, discovery, provider, fresh=True)
            value['remember'] = flow['remember']
            value.pop('flow', None)
            value.pop('csrf', None)
            value['csrf'] = secrets.token_urlsafe(32)
            self._save(token, value)
            lifetime = 30 * 86400 if value.get('remember') else 12 * 3600
            with self._connect() as db:
                db.execute('UPDATE sessions SET expiry=? WHERE id=?', (self.clock() + lifetime, self._key(token)))
        return {'pending': False, **self.status(token)}

    @staticmethod
    def _bound_provider(value, provider):
        return bool(provider and value.get('issuer') == provider['issuer']
                    and value.get('client_id') == provider['client_id'])

    def _tokens(self, value, tokens, discovery, provider, *, fresh=False):
        access = tokens.get('access_token')
        if tokens.get('error') or not isinstance(access, str) or not 1 <= len(access) <= 16384:
            raise AccountError('account_authorization_denied', 401)
        data = self._request(discovery['userinfo_endpoint'], token=access)
        profile = self._profile(data)
        if not fresh and value.get('subject') and value['subject'] != data['sub']:
            raise AccountError('account_subject_changed', 401)
        refresh = tokens.get('refresh_token', '')
        if not isinstance(refresh, str) or len(refresh) > 16384:
            raise AccountError('account_provider_response_invalid', 503)
        value.update(profile=profile, subject=data['sub'],
                     issuer=provider['issuer'], client_id=provider['client_id'],
                     refresh=refresh or ('' if fresh else value.get('refresh', '')),
                     token_until=self.clock() + self._seconds(tokens.get('expires_in', 300), 10, 3600))

    @staticmethod
    def _seconds(value, minimum, maximum):
        try:
            return min(maximum, max(minimum, int(value)))
        except (ValueError, TypeError, OverflowError):
            raise AccountError('account_provider_response_invalid', 503) from None

    def rotate(self, token):
        value = self._read(token)
        replacement = secrets.token_urlsafe(32)
        with self.lock, self._connect() as db:
            row = db.execute('SELECT expiry FROM sessions WHERE id=?', (self._key(token),)).fetchone()
            if not row:
                raise AccountError('account_session_expired', 401)
            db.execute('DELETE FROM sessions WHERE id=?', (self._key(token),))
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)',
                       (self._key(replacement), row[0], json.dumps(value)))
        return replacement

    def refresh(self, token, csrf):
        with self.lock:
            value = self._csrf(token, csrf)
            if not value.get('profile') and not value.get('refresh'):
                raise AccountError('account_sign_in_required', 401)
            if not self._bound_provider(value, self.provider()):
                raise AccountError('account_provider_changed', 401)
            if value.get('profile') and value.get('token_until', 0) > self.clock() + 30:
                return self.status(token)
            if not value.get('refresh'):
                raise AccountError('account_sign_in_required', 401)
            provider, discovery = self._ready()
            tokens = self._request(discovery['token_endpoint'], form={
                'grant_type': 'refresh_token', 'refresh_token': value['refresh'],
                'client_id': provider['client_id']})
            self._tokens(value, tokens, discovery, provider)
            self._save(token, value)
        return self.status(token)

    def logout(self, token, csrf):
        with self.lock:
            value = self._csrf(token, csrf)
            try:
                provider = self.provider()
            except AccountError:
                provider = {}
            discovery = self.discovery if self.discovery and self.discovery.get('issuer') == provider.get('issuer') else None
            # Delete locally before any network request. Capture the old issuer
            # under the settings lock: concurrent changes cannot receive its token.
            with self._connect() as db:
                db.execute('DELETE FROM sessions WHERE id=?', (self._key(token),))
        if value.get('refresh') and self._bound_provider(value, provider):
            try:
                discovery = discovery or self._discover(provider)
                if discovery.get('revocation_endpoint'):
                    self._request(discovery['revocation_endpoint'], form={
                        'client_id': provider['client_id'], 'token': value['refresh'],
                        'token_type_hint': 'refresh_token'})
            except AccountError:
                pass  # Local logout must not depend on central availability.
        return {'authenticated': False}


_service = None
_service_lock = threading.Lock()


def serve_account(handler, path, home, log=None):
    """HA admins already authorize this optional account settings surface.

    A cloud identity never grants camera/admin permissions or gates recording.
    Standalone NVR authorization is deliberately outside this feature.
    """
    if not path.startswith('/api/account/'):
        return False
    action = path.rsplit('/', 1)[-1]
    if action not in ('status', 'provider', 'device', 'poll', 'refresh', 'logout', 'client.js'):
        action = 'unknown'
    if path == '/api/account/client.js' and handler.command == 'GET':
        source = Path(__file__).parent / 'custom_components/edge/www/edge-accounts.js'
        handler.send_bytes(source.read_bytes(), 'text/javascript', extra_headers={'Cache-Control': 'private, max-age=60'})
        return True
    try:
        peer = handler.client_address[0]
        ingress = peer == '172.30.32.2' and re.fullmatch(
            r'/api/(?:hassio_ingress|hassio/ingress)/[A-Za-z0-9_-]+/?',
            handler.headers.get('X-Ingress-Path', ''))
        loopback = peer in ('127.0.0.1', '::1')
        origin = urllib.parse.urlsplit(handler.headers.get('Origin') or handler.headers.get('X-Edge-Account-Origin', ''))
        local_origin = origin.scheme == 'http' and origin.hostname in ('localhost', '127.0.0.1', '::1')
        if not (ingress or loopback) or not (origin.scheme == 'https' or loopback and local_origin):
            raise AccountError('account_open_ha_https_ingress', 403)
        if ingress:
            user_id = handler.headers.get('X-Remote-User-Id', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', user_id):
                raise AccountError('account_ha_user_missing', 403)
            # Supervisor overwrites this header from the authenticated HA user.
            # Cookies must not carry a linked account across HA users.
            principal = hashlib.sha256(('ha:' + user_id).encode()).hexdigest()
        else:
            principal = 'local-loopback'
        global _service
        with _service_lock:
            if _service is None or _service.home != Path(home) / 'accounts':
                _service = CentralAccounts(home)
            service = _service
        cookies = SimpleCookie()
        try:
            cookies.load(handler.headers.get('Cookie', ''))
        except CookieError:
            raise AccountError('account_cookie_invalid') from None
        token = cookies.get('edge_account').value if 'edge_account' in cookies else ''
        headers = {'Cache-Control': 'no-store', 'Vary': 'Cookie'}
        cookie_path = ingress[0].rstrip('/') + '/' if ingress else '/'

        def cookie(value, age=None):
            jar = SimpleCookie()
            jar['edge_account'] = value
            jar['edge_account']['path'] = cookie_path
            jar['edge_account']['httponly'] = True
            jar['edge_account']['samesite'] = 'Strict'
            if origin.scheme == 'https':
                jar['edge_account']['secure'] = True
            if age is not None:
                jar['edge_account']['max-age'] = age
            headers['Set-Cookie'] = jar.output(header='').strip()

        if handler.command == 'GET' and path == '/api/account/status':
            token, created = service.session(token, principal)
            if created:
                cookie(token)
            result = service.status(token)
        elif handler.command == 'POST':
            if service._read(token).get('principal') != principal:
                raise AccountError('account_ha_user_changed', 403)
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise AccountError('account_json_required', 415)
            if int(handler.headers.get('Content-Length', '0')) > 8192:
                raise AccountError('account_request_too_large', 413)
            body = handler.read_body_json()
            if not isinstance(body, dict):
                raise AccountError('account_request_invalid')
            csrf = handler.headers.get('X-Edge-CSRF', '')
            if path == '/api/account/provider':
                service._csrf(token, csrf)
                result = service.configure(body)
                cookie('', 0)
            elif path == '/api/account/device':
                result = service.start(token, csrf, body.get('remember') is True)
            elif path == '/api/account/poll':
                result = service.poll(token, csrf)
                if not result.get('pending'):
                    token = service.rotate(token)
                    cookie(token, 30 * 86400 if result.get('remember') else None)
            elif path == '/api/account/refresh':
                result = service.refresh(token, csrf)
            elif path == '/api/account/logout':
                result = service.logout(token, csrf)
                cookie('', 0)
            else:
                raise AccountError('account_route_missing', 404)
        else:
            raise AccountError('account_route_missing', 404)
        handler.send_json(result, extra_headers=headers)
        if log and handler.command == 'POST' and not result.get('pending'):
            detail = {'action': action}
            for key in ('authenticated', 'remember'):
                if key in result:
                    detail[key] = bool(result[key])
            if action == 'device':
                detail['remember'] = body.get('remember') is True
            log('account_action', detail)
    except AccountError as error:
        handler.send_json({'error': error.code}, error.status, extra_headers={'Cache-Control': 'no-store'})
        if log:
            log('account_action_failed', {'action': action, 'code': error.code, 'http_status': error.status})
    except (OSError, ValueError, sqlite3.Error):
        handler.send_json({'error': 'account_service_unavailable'}, 503,
                          extra_headers={'Cache-Control': 'no-store'})
        if log:
            log('account_action_failed', {'action': action, 'code': 'account_service_unavailable', 'http_status': 503})
    return True
