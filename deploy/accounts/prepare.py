"""Write private deployment settings without printing or accepting passwords."""
import argparse
import os
from pathlib import Path
import re
import secrets


def prepare(directory, domain, mail_domain, admin_email):
    for value in (domain, mail_domain):
        if (not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value)
                or value.endswith(('.invalid', '.test', '.localhost'))):
            raise ValueError('Use the real public domain; no URL, path or port.')
    if not re.fullmatch(r'[^\s@<>]{1,100}@[^\s@<>]{1,150}', admin_email):
        raise ValueError('Invalid operator email')
    destination = Path(directory) / '.env'
    data = {'EDGE_ACCOUNT_DOMAIN': domain, 'EDGE_ADMIN_EMAIL': admin_email,
            'EDGE_ADMIN_PASSWORD': 'A9!' + secrets.token_urlsafe(32),
            'EDGE_MASTER_KEY': secrets.token_hex(16), 'EDGE_DB_PASSWORD': secrets.token_hex(32),
            'EDGE_SMTP_HOST': 'smtp.gmail.com:587', 'EDGE_SMTP_USER': f'verification@{mail_domain}',
            'EDGE_SMTP_PASSWORD': '', 'EDGE_VERIFICATION_EMAIL': f'verification@{mail_domain}',
            'EDGE_SUPPORT_EMAIL': f'support@{mail_domain}'}
    # Exclusive creation preserves the encryption key and existing deployment.
    with open(destination, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        handle.write('\n'.join(f'{key}={value}' for key, value in data.items()) + '\n')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True)
    parser.add_argument('--mail-domain', required=True)
    parser.add_argument('--admin-email', required=True)
    args = parser.parse_args()
    path = prepare(Path(__file__).parent, args.domain, args.mail_domain, args.admin_email)
    print(f'Created {path.name} with mode 0600. Fill SMTP credentials privately before starting.')
