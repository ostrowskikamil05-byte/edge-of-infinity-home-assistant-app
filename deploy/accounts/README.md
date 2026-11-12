# Shared Edge accounts — deployment preparation

This stack runs **once on a separate server**, not on every Home Assistant or
the HP t530. It uses ZITADEL 4.20.2, PostgreSQL 17.10 and Traefik 3.7.18.
Registration, email verification codes, passwords, recovery and TOTP authenticator
enrollment belong to this identity service. Edge links a verified email through
OAuth device authorization; no camera address, camera password or video is sent
to the identity service. HA continues to authorize cameras/settings.

**Not deployed:** a real domain, server, Workspace sender/SMTP credentials and
end-to-end delivery checks are still required. The app has no default account
issuer until that service is ready. Existing HA access, Live and recording work
without it. Standalone NVR login is deferred.

## Domain and Workspace

1. Create an account hostname such as `accounts.your-domain.example` and point
   DNS A/AAAA records to the server that will run this stack. Allow 80/443 for TLS.
2. In Google Workspace create `verification@your-domain.example` (or an authorized
   sender alias) and `support@your-domain.example` (mailbox/group). These files
   **do not create Google mailboxes or buy a domain**. Set Workspace MX/SPF/DKIM
   and DMARC from its admin console. Verify the sender is allowed to send as the
   configured From address.
3. Configure Workspace SMTP relay or an SMTP credential supported by the Workspace
   account/policy; do not use the main Google account password. The template uses
   authenticated STARTTLS on port 587. Workspace limits depend on the plan and
   relay configuration; size and test delivery before public signup traffic.

## Prepare and start

Run on the account server from this repository:

```sh
python3 deploy/accounts/prepare.py --domain accounts.YOUR-DOMAIN --mail-domain YOUR-DOMAIN --admin-email OPERATOR-EMAIL
cd deploy/accounts
# Edit .env privately and set EDGE_SMTP_PASSWORD / SMTP relay settings.
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --wait
```

Use the actual lowercase domains/email. `prepare.py` refuses to overwrite `.env`,
generates distinct secrets and sets mode 0600. Empty required SMTP settings block
startup. Do not post `.env`, expanded `docker compose config` output or tokens.
The encryption master key and database must be backed up together. Database and
identity listeners have no public host ports; the gateway publishes only 80/443.
It limits POSTs by source IP; tune for measured traffic and any trusted upstream
proxy. Access logs are disabled so authorization URLs/codes do not enter them.

Open the account hostname, log in with the generated operator credentials from
the private `.env`, change the password and enroll an authenticator. Keep recovery
methods under operator control. The configured release uses the built-in login
UI; upgrading to a provider release with different login requirements needs its
documented migration and a staging test.

## Create the public Edge client

In the identity admin console create an **Edge of Infinity** project and an
OIDC **native/public client**, authentication method **none**, allowing **Device
Code** and **Refresh Token** grants. Permit `openid email profile offline_access`.
Do not enable password/direct grants or wildcard redirect URIs; device linking
does not require a redirect URI for each HA installation. Save the public client
ID. Enable account self-registration and OTP/TOTP as an available second factor;
email must be verified before the Edge client accepts a profile.

Customize the registration/VerifyEmail message in Polish and English: say that
an Edge account is being created, include the provider's code placeholder and
expiry, and provide the support address. Keep the other password-reset/recovery
templates. Ready-to-review field values are in
[`mail-texts/verifyemail.pl.json`](mail-texts/verifyemail.pl.json) and
[`mail-texts/verifyemail.en.json`](mail-texts/verifyemail.en.json). Apply these
to the provider's VerifyEmail fields for the matching language; do not replace
the entire provider message catalog. The actual sender and Reply-To come from
the SMTP configuration, not these templates. Upload the **existing original Edge logo** from the app's brand assets;
no replacement logo is introduced.

In **Edge → Settings → Account and access method**, set the HTTPS issuer and
public client ID. This settings action is restricted to HA admin Ingress (or
local loopback), uses CSRF protection and validates discovery over trusted TLS.
Client secrets and SMTP passwords are never entered into the Edge panel.
Set the optional account/MFA settings URL to
`https://YOUR-ACCOUNT-HOST/ui/console/users/me` so the signed-in card offers a
direct link to password, authenticator and recovery settings. It must use the
same HTTPS origin as the issuer.
Once the official endpoint is deployed and verified, a later release can supply
these two **public** defaults so users do not configure an identity provider.

## User flow and release checks

Users choose **Sign in or create an account**, open the central page and approve
the displayed device code. Registration verifies the email code there; TOTP uses
an authenticator app there. After linking, Edge shows **Nabu Casa / Local HTTPS**.
These are access preferences/guides, not automatic network or certificate changes.

“Keep me signed in” uses an HttpOnly/SameSite cookie with at most 30 days;
unchecked uses a browser-session cookie and a 12-hour server limit. Refresh tokens
stay in a private local SQLite file; no tokens go to localStorage or device logs.
Remembered sessions are bound to the authenticated HA user reported by Supervisor;
switching HA users does not expose the previous user's linked account.
Device codes and refresh tokens are also bound to their original issuer/client;
changing provider settings cannot forward the old credentials to a new server.
Changing the provider invalidates local sessions. Local logout works while the
identity server is offline. It does not log out unrelated central browser sessions.

Before enabling public accounts, verify actual registration/mail delivery,
expired/wrong codes, duplicate emails, TOTP enrollment/recovery, device consent,
refresh/remember behavior, revocation and login on iOS/Android. Verify a cloud outage
does not interrupt Live/DVR. Exercise backup/restore and measure server/database
load and SMTP limits with realistic signup/login concurrency. App unit/browser
fixtures do not establish mail deliverability or a capacity guarantee for thousands
of concurrent users.

Upstream implementation/configuration references:
[ZITADEL](https://github.com/zitadel/zitadel/tree/v4.20.2/deploy/compose),
[Traefik](https://doc.traefik.io/traefik/),
[OAuth device authorization](https://www.rfc-editor.org/rfc/rfc8628).
