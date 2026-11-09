"""GitHub App installation tokens for the UAT gate, on the standard library and `openssl`.

The gate alone holds the App's private key (a file only the gate process can
read). It signs a short-lived JWT with `openssl dgst -sha256 -sign`, exchanges
it for an installation token limited to the target repository and to the
permissions the gate uses, and renews the token before it expires.
"""
import base64
import json
import subprocess
import threading
import time

from uat.github import GitHub

# Checks to publish superagent/uat; read access to the PR, its issue and its code.
PERMISSIONS = {'checks': 'write', 'contents': 'read', 'issues': 'read', 'pull_requests': 'read', 'metadata': 'read'}


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def app_jwt(app_id, key_path, now=None):
    """An RS256 JWT for the App, valid ten minutes (backdated one for clock drift)."""
    now = int(now or time.time())
    header = b64url(json.dumps({'alg': 'RS256', 'typ': 'JWT'}, separators=(',', ':')).encode())
    payload = b64url(json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': str(app_id)}, separators=(',', ':')).encode())
    signed = f'{header}.{payload}'.encode()
    signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', key_path], input=signed,
                               capture_output=True, check=True).stdout
    return f'{header}.{payload}.{b64url(signature)}'


class InstallationToken:
    """A callable giving a current installation token for one repository."""

    def __init__(self, app_id, key_path, repository, api=None, installation_id=None):
        self.app_id, self.key_path, self.repository, self.api = app_id, key_path, repository, api
        self.installation_id = installation_id
        self.token, self.expires = None, 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            if self.token is None or time.time() > self.expires - 600:
                self.renew()
            return self.token

    def renew(self):
        app = GitHub(app_jwt(self.app_id, self.key_path), self.api)
        if not self.installation_id:
            self.installation_id = app.get(f'repos/{self.repository}/installation')['id']
        answer = app.request('POST', f'app/installations/{self.installation_id}/access_tokens',
                             {'repositories': [self.repository.split('/')[1]], 'permissions': PERMISSIONS})
        self.token = answer['token']
        # GitHub tokens last an hour; renew ten minutes early without parsing expires_at.
        self.expires = time.time() + 3600
