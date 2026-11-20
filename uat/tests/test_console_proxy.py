"""Caddy /console* gate: session cookie required, cross-site rejected, rest unchanged."""
import os
import tempfile
import time
import unittest

from uat.tests import docker_support as ds

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CADDYFILE = os.path.join(ROOT, 'dashboard', 'Caddyfile')
COOKIE = 'dsh-session=test-console-cookie'

# One process on the superagent alias: forward_auth on /, management API, and UI.
SUPERAGENT = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer

COOKIE = %r

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/?"):
            ok = self.headers.get("Cookie") == COOKIE
            self.send_response(200 if ok else 401)
            self.end_headers()
            self.wfile.write(b"ok" if ok else b"no")
            return
        if self.path.startswith("/orchestrator/api/"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"dsh-ui")
    def log_message(self, *a):
        pass

HTTPServer(("0.0.0.0", 3083), H).serve_forever()
''' % COOKIE

CONSOLE = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(("console:%s" % self.path).encode())
    def log_message(self, *a):
        pass

HTTPServer(("0.0.0.0", 3000), H).serve_forever()
'''


class ConsoleProxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        cls.prefix = f'console-proxy-{os.getpid()}'
        cls.network = cls.prefix
        cls.dir = tempfile.mkdtemp(prefix=cls.prefix + '-')
        with open(os.path.join(cls.dir, 'superagent.py'), 'w') as f:
            f.write(SUPERAGENT)
        with open(os.path.join(cls.dir, 'console.py'), 'w') as f:
            f.write(CONSOLE)
        ds.docker('network', 'create', cls.network)
        ds.docker('run', '-d', '--name', f'{cls.prefix}-superagent',
                  '--network', cls.network, '--network-alias', 'superagent',
                  '-v', f'{cls.dir}:/app:ro', 'python:3.11-alpine',
                  'python3', '/app/superagent.py')
        ds.docker('run', '-d', '--name', f'{cls.prefix}-console',
                  '--network', cls.network, '--network-alias', 'console',
                  '-v', f'{cls.dir}:/app:ro', 'python:3.11-alpine',
                  'python3', '/app/console.py')
        ds.docker('run', '-d', '--name', f'{cls.prefix}-dashboard',
                  '--network', cls.network,
                  '-v', f'{CADDYFILE}:/etc/caddy/Caddyfile:ro',
                  'caddy:2.10-alpine')
        deadline = time.time() + 60
        while time.time() < deadline:
            probe = ds.docker('exec', f'{cls.prefix}-dashboard',
                              'wget', '-qO-', 'http://127.0.0.1:8080/healthz', check=False)
            if probe.returncode == 0 and 'ok' in probe.stdout:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError('dashboard did not become ready')

    @classmethod
    def tearDownClass(cls):
        for name in ('dashboard', 'console', 'superagent'):
            ds.docker('rm', '-f', f'{cls.prefix}-{name}', check=False)
        ds.docker('network', 'rm', cls.network, check=False)

    def curl(self, path, headers=None, cookie=None):
        cmd = ['exec', f'{self.prefix}-dashboard', 'wget', '-S', '-O-']
        for key, value in (headers or {}).items():
            cmd += ['--header', f'{key}: {value}']
        if cookie:
            cmd += ['--header', f'Cookie: {cookie}']
        cmd.append(f'http://127.0.0.1:8080{path}')
        result = ds.docker(*cmd, check=False)
        status = None
        for line in (result.stderr or '').splitlines():
            line = line.strip()
            if line.startswith('HTTP/') and ' ' in line:
                status = int(line.split()[1])
        return status, result.stdout, result.stderr

    def test_console_requires_cookie(self):
        status, body, _ = self.curl('/console/')
        self.assertEqual(status, 401, body)

    def test_console_allows_cookie(self):
        status, body, _ = self.curl('/console/', cookie=COOKIE)
        self.assertEqual(status, 200, body)
        self.assertIn('console:/console/', body)

    def test_console_rejects_cross_site(self):
        status, body, err = self.curl(
            '/console/',
            cookie=COOKIE,
            headers={'Sec-Fetch-Site': 'cross-site'},
        )
        self.assertEqual(status, 403, err or body)

    def test_healthz_still_open(self):
        status, body, _ = self.curl('/healthz')
        self.assertEqual(status, 200, body)
        self.assertEqual(body.strip(), 'ok')

    def test_management_still_gated(self):
        status, _, _ = self.curl('/orchestrator/api/state')
        self.assertEqual(status, 401)
        status, body, _ = self.curl('/orchestrator/api/state', cookie=COOKIE)
        self.assertEqual(status, 200, body)
        self.assertIn('"ok":true', body)


if __name__ == '__main__':
    unittest.main()
