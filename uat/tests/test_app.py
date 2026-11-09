"""GitHub App authentication of the gate: JWT signed with openssl, installation token scoped down."""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from uat import app
from uat.github import GitHub
from uat.tests import fake_github as fg


def unb64(part):
    return base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))


class AppTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.key = os.path.join(self.dir, 'app.pem')
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', self.key],
                       check=True, capture_output=True)

    def test_jwt_is_rs256_signed_by_the_app_key(self):
        token = app.app_jwt(4242, self.key, now=1_000_000)
        header, payload, signature = token.split('.')
        self.assertEqual(json.loads(unb64(header)), {'alg': 'RS256', 'typ': 'JWT'})
        self.assertEqual(json.loads(unb64(payload)), {'iat': 999_940, 'exp': 1_000_540, 'iss': '4242'})
        public = os.path.join(self.dir, 'public.pem')
        subprocess.run(['openssl', 'pkey', '-in', self.key, '-pubout', '-out', public], check=True, capture_output=True)
        with open(os.path.join(self.dir, 'signature'), 'wb') as file:
            file.write(unb64(signature))
        verified = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', public, '-signature',
                                   os.path.join(self.dir, 'signature')], input=f'{header}.{payload}'.encode(),
                                  capture_output=True)
        self.assertEqual(verified.returncode, 0, verified.stderr)

    def test_installation_token_is_limited_to_the_repository_and_permissions(self):
        gh = fg.FakeGitHub('ForkbombEu/sample')
        self.addCleanup(gh.close)
        token = app.InstallationToken(4242, self.key, 'ForkbombEu/sample', api=gh.url)
        client = GitHub(token, gh.url)
        gh.add_pr(9, '1' * 40, closes=[])
        self.assertEqual(client.get('repos/ForkbombEu/sample/pulls/9')['number'], 9)
        client.get('repos/ForkbombEu/sample/pulls/9')
        self.assertEqual(gh.token_requests, [{'repositories': ['sample'], 'permissions': app.PERMISSIONS}])
        self.assertEqual(token.installation_id, 77)
        self.assertEqual(app.PERMISSIONS['checks'], 'write')
        self.assertTrue(all(level == 'read' for name, level in app.PERMISSIONS.items() if name != 'checks'))


if __name__ == '__main__':
    unittest.main()
