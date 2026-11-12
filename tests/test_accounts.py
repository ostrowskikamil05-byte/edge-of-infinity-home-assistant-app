"""Optional shared identity cannot alter HA/media permissions or leak tokens."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'edge-of-infinity/rootfs/usr/share/edge-of-infinity'))
import edge_accounts as accounts


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000
        self.s = accounts.CentralAccounts(self.temp.name, clock=lambda: self.now)
        self.provider = {'issuer': 'https://accounts.example.test', 'client_id': 'edge-public',
                         'support_email': 'support@example.test'}
        self.discovery = {'issuer': self.provider['issuer'], **{
            key: self.provider['issuer'] + '/' + route for key, route in (
                ('device_authorization_endpoint', 'device'), ('token_endpoint', 'token'),
                ('userinfo_endpoint', 'userinfo'))}}
        self.requests = []
        self.pending = None
        self.verified = True
        self.subject = 'edge-user-123'

        def request(url, *, form=None, token=None):
            self.requests.append((url, form, token))
            if url.endswith('openid-configuration'):
                return self.discovery.copy()
            if url.endswith('/device'):
                return {'device_code': 'private-device-token', 'user_code': 'ABCD-EFGH',
                        'verification_uri': self.provider['issuer'] + '/activate',
                        'verification_uri_complete': self.provider['issuer'] + '/activate?code=ABCD-EFGH',
                        'interval': 5, 'expires_in': 600}
            if url.endswith('/token'):
                return {'error': self.pending} if self.pending else {
                    'access_token': 'private-access-token', 'refresh_token': 'private-refresh-token',
                    'expires_in': 120}
            if url.endswith('/userinfo'):
                self.assertEqual(token, 'private-access-token')
                return {'sub': self.subject, 'email_verified': self.verified,
                        'email': 'user@example.test', 'name': '<not HTML>'}
            self.fail('Unexpected provider route')
        self.s._request = request
        self.s.configure(self.provider)
        self.token, _ = self.s.session('')
        self.csrf = self.s.status(self.token)['csrf']

    def link(self, remember=False):
        result = self.s.start(self.token, self.csrf, remember)
        self.assertNotIn('private', json.dumps(result))
        self.now += 5
        result = self.s.poll(self.token, self.csrf)
        self.csrf = result['csrf']
        self.assertTrue(result['authenticated'])
        self.assertNotIn('private', json.dumps(result))
        return result

    def test_verified_login_keeps_passwords_and_tokens_out_of_public_state(self):
        self.link()
        self.assertEqual(self.s.status(self.token)['profile']['email'], 'user@example.test')
        self.assertEqual((self.s.home / 'sessions.sqlite').stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.token, (self.s.home / 'sessions.sqlite').read_bytes().decode(errors='ignore'))
        self.assertNotIn('password', json.dumps(self.requests))

    def test_email_verification_is_required_and_cannot_be_string_true(self):
        self.s.start(self.token, self.csrf)
        self.now += 5
        for value in (False, 'true', 1, None):
            self.verified = value
            with self.assertRaisesRegex(accounts.AccountError, 'account_email_not_verified'):
                self.s.poll(self.token, self.csrf)
            self.now += 5
        self.assertFalse(self.s.status(self.token)['authenticated'])

    def test_polling_is_bounded_and_slow_down_and_expiry_are_enforced(self):
        self.s.start(self.token, self.csrf)
        before = len(self.requests)
        for _ in range(20):
            self.assertTrue(self.s.poll(self.token, self.csrf)['pending'])
        self.assertEqual(len(self.requests), before)
        self.now += 5
        self.pending = 'slow_down'
        self.assertEqual(self.s.poll(self.token, self.csrf)['interval'], 10)
        self.now += 601
        with self.assertRaisesRegex(accounts.AccountError, 'account_code_expired'):
            self.s.poll(self.token, self.csrf)

    def test_csrf_and_login_rotation_logout_and_session_fixation(self):
        for csrf in ('wrong', 'é' * 43, None, ['invalid']):
            with self.assertRaisesRegex(accounts.AccountError, 'account_csrf_invalid'):
                self.s.start(self.token, csrf)
        old_csrf = self.csrf
        self.link()
        self.assertNotEqual(self.csrf, old_csrf)
        replacement = self.s.rotate(self.token)
        with self.assertRaises(accounts.AccountError):
            self.s.status(self.token)
        self.token = replacement
        self.s.logout(self.token, self.csrf)
        with self.assertRaises(accounts.AccountError):
            self.s._save(self.token, {'csrf': self.csrf})

    def test_remember_is_server_bounded_and_survives_process_restart(self):
        for remember, lifetime in ((False, 12 * 3600), (True, 30 * 86400)):
            self.token, _ = self.s.session('')
            self.csrf = self.s.status(self.token)['csrf']
            self.link(remember)
            with self.s._connect() as db:
                expiry = db.execute('SELECT expiry FROM sessions WHERE id=?', (self.s._key(self.token),)).fetchone()[0]
            self.assertEqual(expiry, self.now + lifetime)
            restarted = accounts.CentralAccounts(self.temp.name, clock=lambda: self.now)
            self.assertTrue(restarted.status(self.token)['authenticated'])
            self.now = expiry + 1
            with self.assertRaises(accounts.AccountError):
                restarted.status(self.token)

    def test_refresh_preserves_identity_and_does_not_return_tokens(self):
        self.link(True)
        self.now += 121
        self.assertFalse(self.s.status(self.token)['authenticated'])
        result = self.s.refresh(self.token, self.csrf)
        self.assertTrue(result['authenticated'])
        self.assertNotIn('private', json.dumps(result))
        self.subject = 'different-user'
        self.now += 121
        with self.assertRaisesRegex(accounts.AccountError, 'account_subject_changed'):
            self.s.refresh(self.token, self.csrf)

    def test_provider_change_revokes_local_sessions_and_rejects_cross_origin_endpoints(self):
        self.discovery['token_endpoint'] = 'https://attacker.example/token'
        with self.assertRaises(accounts.AccountError):
            self.s.configure(self.provider)
        self.assertEqual(self.s.status(self.token)['issuer'], self.provider['issuer'])
        self.discovery['token_endpoint'] = self.provider['issuer'] + '/token'
        self.s.configure(self.provider)
        with self.assertRaises(accounts.AccountError):
            self.s.status(self.token)
        for issuer in ('http://example.test', 'https://user:pass@example.test', 'https://example.test/#x'):
            with self.assertRaises(accounts.AccountError):
                self.s.configure({**self.provider, 'issuer': issuer})
        self.discovery['issuer'] = None
        with self.assertRaisesRegex(accounts.AccountError, 'account_provider_issuer_mismatch'):
            self.s.configure(self.provider)

    def test_invalid_provider_payload_has_fixed_error_and_capacity_is_bounded(self):
        for value in (None, [], 'password'):
            with self.assertRaises(accounts.AccountError):
                self.s.configure(value)
        self.s.MAX_SESSIONS = 2
        self.s.session('')
        with self.assertRaisesRegex(accounts.AccountError, 'account_session_limit'):
            self.s.session('')

    def test_logout_attempts_token_revocation_but_survives_provider_outage(self):
        self.discovery['revocation_endpoint'] = self.provider['issuer'] + '/revoke'
        self.s.configure(self.provider)
        self.token, _ = self.s.session('')
        self.csrf = self.s.status(self.token)['csrf']
        self.link(True)
        request = Mock(side_effect=accounts.AccountError('account_provider_unavailable', 503))
        self.s._request = request
        self.s.logout(self.token, self.csrf)
        self.assertEqual(request.call_args.args[0], self.provider['issuer'] + '/revoke')
        with self.assertRaises(accounts.AccountError):
            self.s.status(self.token)

    def test_account_http_is_separate_from_media_and_restricts_admin_transport(self):
        handler = Mock()
        handler.command = 'GET'
        handler.client_address = ('192.0.2.10', 1234)
        handler.headers = {'X-Edge-Account-Origin': 'https://ha.example.test'}
        self.assertFalse(accounts.serve_account(handler, '/api/recording/status', self.temp.name))
        handler.send_json.assert_not_called()
        self.assertTrue(accounts.serve_account(handler, '/api/account/status', self.temp.name))
        self.assertEqual(handler.send_json.call_args.args[1], 403)
        handler.headers.update({'X-Ingress-Path': '/api/hassio_ingress/valid', 'X-Remote-User-Id': 'admin-one'})
        handler.client_address = ('172.30.32.2', 1234)
        accounts.serve_account(handler, '/api/account/status', self.temp.name)
        cookie = handler.send_json.call_args.kwargs['extra_headers']['Set-Cookie']
        self.assertIn('Secure', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Path=/api/hassio_ingress/valid/', cookie)
        self.assertNotIn('private', json.dumps(handler.send_json.call_args.args[0]))

    def test_complete_http_link_rotates_cookie_and_csrf_and_keeps_tokens_server_side(self):
        handler, log = Mock(), Mock()
        handler.command = 'GET'
        handler.client_address = ('172.30.32.2', 1234)
        handler.headers = {'X-Edge-Account-Origin': 'https://ha.example.test',
                           'X-Ingress-Path': '/api/hassio_ingress/valid/', 'X-Remote-User-Id': 'admin-one'}
        with unittest.mock.patch.object(accounts, '_service', self.s):
            accounts.serve_account(handler, '/api/account/status', self.temp.name, log)
            data = handler.send_json.call_args.args[0]
            jar = accounts.SimpleCookie(handler.send_json.call_args.kwargs['extra_headers']['Set-Cookie'])
            old = jar['edge_account'].value
            handler.command = 'POST'
            handler.headers.update({'Content-Type': 'application/json', 'Content-Length': '2',
                                    'Cookie': 'edge_account=' + old, 'X-Edge-CSRF': data['csrf']})
            handler.read_body_json.return_value = {'remember': True}
            accounts.serve_account(handler, '/api/account/device', self.temp.name, log)
            self.assertEqual(log.call_args.args[1], {'action': 'device', 'remember': True})
            self.assertNotIn('private', json.dumps(handler.send_json.call_args.args[0]))
            self.now += 5
            handler.read_body_json.return_value = {}
            accounts.serve_account(handler, '/api/account/poll', self.temp.name, log)
            result = handler.send_json.call_args.args[0]
            self.assertTrue(result['authenticated'])
            cookie = accounts.SimpleCookie(handler.send_json.call_args.kwargs['extra_headers']['Set-Cookie'])
            replacement = cookie['edge_account'].value
            self.assertNotEqual(old, replacement)
            self.assertEqual(int(cookie['edge_account']['max-age']), 30 * 86400)
            self.assertNotEqual(result['csrf'], data['csrf'])
            self.assertNotIn('private', json.dumps(result))
            with self.assertRaises(accounts.AccountError):
                self.s.status(old)
            handler.headers.update({'Cookie': 'edge_account=' + replacement, 'X-Edge-CSRF': result['csrf']})
            accounts.serve_account(handler, '/api/account/logout', self.temp.name, log)
            self.assertFalse(handler.send_json.call_args.args[0]['authenticated'])
            self.assertNotIn('member@example', str(log.call_args_list))
            self.assertNotIn('private', str(log.call_args_list))
            with self.assertRaises(accounts.AccountError):
                self.s.status(replacement)

    def test_remembered_cookie_cannot_cross_authenticated_ha_users(self):
        first, _ = self.s.session('', 'ha-one')
        self.assertEqual(self.s.session(first, 'ha-one'), (first, False))
        second, created = self.s.session(first, 'ha-two')
        self.assertTrue(created)
        self.assertNotEqual(first, second)
        self.assertEqual(self.s._read(first)['principal'], 'ha-one')
        self.assertEqual(self.s._read(second)['principal'], 'ha-two')

    def test_explicit_new_link_can_switch_identity_without_reusing_old_refresh_token(self):
        self.link(True)
        self.now += 61
        self.subject = 'another-verified-account'
        original_request = self.s._request

        def request(url, **kwargs):
            result = original_request(url, **kwargs)
            if url.endswith('/token'):
                result.pop('refresh_token', None)
            return result
        self.s._request = request
        self.s.start(self.token, self.csrf)
        self.now += 5
        self.assertTrue(self.s.poll(self.token, self.csrf)['authenticated'])
        value = self.s._read(self.token)
        self.assertEqual(value['subject'], self.subject)
        self.assertEqual(value['refresh'], '')

    def test_logout_never_sends_previous_credentials_to_a_changed_provider(self):
        self.discovery['revocation_endpoint'] = self.provider['issuer'] + '/revoke'
        self.s.configure(self.provider)
        self.token, _ = self.s.session('')
        self.csrf = self.s.status(self.token)['csrf']
        self.link(True)
        original_csrf = self.s._csrf

        def csrf_then_provider_change(token, csrf):
            value = original_csrf(token, csrf)
            changed = {**self.provider, 'issuer': 'https://replacement.example.test'}
            (self.s.home / 'provider.json').write_text(json.dumps(changed))
            self.s.discovery = {**self.discovery, 'issuer': changed['issuer'],
                                'revocation_endpoint': changed['issuer'] + '/revoke'}
            return value
        self.s._csrf = csrf_then_provider_change
        self.s._request = Mock(return_value={})
        self.s.logout(self.token, self.csrf)
        self.s._request.assert_not_called()

    def test_pending_device_code_stays_bound_to_its_original_provider(self):
        self.s.start(self.token, self.csrf)
        (self.s.home / 'provider.json').write_text(json.dumps({
            **self.provider, 'issuer': 'https://replacement.example.test'}))
        self.s.discovery = {**self.discovery, 'issuer': 'https://replacement.example.test',
                            'token_endpoint': 'https://replacement.example.test/token'}
        self.s._request = Mock(return_value={})
        self.now += 5
        with self.assertRaisesRegex(accounts.AccountError, 'account_provider_changed'):
            self.s.poll(self.token, self.csrf)
        self.s._request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
