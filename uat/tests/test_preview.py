"""Preview environments: trusted URL policy, readiness, SHA proof, redirects, credentials and redaction."""
import base64
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from uat import controller, preview
from uat.tests import docker_support as ds
from uat.tests.test_dsh_tester import INVALID
from uat.tests.test_gate import Gate
from uat.tests.test_sandbox import FIXTURE, TESTS, result
from uat.tests.test_spec import ISSUE

SHA = 'c' * 40
PREVIEW_ISSUE = ISSUE.replace('Environment: local', 'Environment: preview')


class Preview(BaseHTTPRequestHandler):
    revision, redirect = SHA, None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.redirect and self.path == '/health':
            self.send_response(302)
            self.send_header('location', self.redirect)
            self.end_headers()
            return
        body = json.dumps({'revision': self.revision} if self.path == '/revision' else {'status': 'ok'}).encode()
        self.send_response(200)
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), type('Handler', (Preview,), {}))
        self.handler = self.server.RequestHandlerClass
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def verify(self, patterns=('127.0.0.1',), timeout=3):
        preview.verify(self.origin, SHA, '/health', '/revision', list(patterns), timeout)

    def test_host_patterns(self):
        patterns = ['preview.example.com', '*.pr.example.com']
        for host, allowed in (('preview.example.com', True), ('PREVIEW.example.com.', True), ('a.pr.example.com', True),
                              ('a.b.pr.example.com', True), ('pr.example.com', False), ('evilpr.example.com', False),
                              ('example.com', False), ('preview.example.com.evil.net', False), ('', False)):
            self.assertEqual(preview.host_allowed(host, patterns), allowed, host)

    def test_url_template_comes_from_the_workflow(self):
        self.assertEqual(preview.preview_url('https://pr-{pr}.pr.example.com/app', 12, ['*.pr.example.com']),
                         'https://pr-12.pr.example.com')
        for template in ('https://pr-{pr}.evil.net', 'ftp://pr-{pr}.pr.example.com', 'pr-{pr}.pr.example.com'):
            with self.assertRaises(preview.Refused):
                preview.preview_url(template, 12, ['*.pr.example.com'])

    def test_current_preview_is_accepted(self):
        self.verify()

    def test_stale_preview_is_refused(self):
        self.handler.revision = 'd' * 40
        with self.assertRaisesRegex(preview.Refused, 'stale'):
            self.verify()

    def test_redirect_to_a_disallowed_host_is_refused(self):
        self.handler.redirect = f'http://localhost:{self.server.server_port}/ready'
        with self.assertRaisesRegex(preview.Refused, 'redirects to localhost'):
            self.verify()
        self.verify(patterns=('127.0.0.1', 'localhost'))

    def test_unavailable_preview_is_refused(self):
        self.origin = 'http://127.0.0.1:9'
        with self.assertRaisesRegex(preview.Refused, 'unavailable'):
            self.verify(timeout=1)

    def test_credentials_must_be_redactable(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.assertEqual(preview.credentials_file('', directory), (None, []))
        for raw in ('not json', '["a"]', '{"user": 1}', '{"password": "short"}'):
            with self.assertRaises(preview.Refused):
                preview.credentials_file(raw, directory)
        path, values = preview.credentials_file('{"UAT_PASSWORD": "long-enough-secret"}', directory)
        self.assertEqual(values, ['long-enough-secret'])
        self.assertEqual(controller.credential_values('{"UAT_PASSWORD": "long-enough-secret"}'), values)


class PreviewGateTest(Gate):
    def configure(self, **extra):
        return {'UAT_ENVIRONMENTS': 'local preview', 'UAT_PREVIEW_URL': 'https://pr-{pr}.preview.example.com',
                'UAT_PREVIEW_ALLOWED_HOSTS': '*.preview.example.com', 'UAT_PREVIEW_REVISION_PATH': '/revision', **extra}

    def test_preview_needs_the_workflow_to_configure_it(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        self.start(UAT_ENVIRONMENTS='local preview')
        self.assertIn('configures no preview URL', self.latest('1' * 40)[0]['output']['summary'])

    def test_preview_host_outside_the_allowlist_is_blocked(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        self.start(**self.configure(UAT_PREVIEW_URL='https://pr-{pr}.evil.net'))
        run, marker = self.latest('1' * 40)
        self.assertEqual(marker['verdict'], 'BLOCKED')
        self.assertIn('not one of the allowed preview hosts', run['output']['summary'])

    def test_configured_preview_runs(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        outputs = self.start(**self.configure())
        self.assertEqual(outputs['run'], 'true')
        snapshot = json.loads(base64.b64decode(outputs['snapshot']))
        self.assertEqual(snapshot['entry'], {'type': 'preview', 'readiness_path': '/health'})
        # finish derives with the same policy: the key still matches.
        found = controller.derive(self.context(**self.configure()), 9)
        self.assertEqual(found['key'], snapshot['key'])


class PreviewPipelineTest(Gate):
    """controller.test against a real preview container, with real DSH through the proxy."""

    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        ds.build(ds.TESTER_IMAGE, 'tester/Dockerfile')
        cls.network = f'uat-preview-test-{os.getpid()}'
        cls.model_dir = tempfile.mkdtemp()
        os.chmod(cls.model_dir, 0o755)
        shutil.copy(os.path.join(TESTS, 'mock_model.py'), cls.model_dir)
        ds.docker('network', 'create', cls.network)
        ds.docker('run', '-d', '--name', f'{cls.network}-mock', '--network', cls.network, '-v',
                  f'{cls.model_dir}:/m:ro', ds.TESTER_IMAGE, 'python3', '/m/mock_model.py', '8080', '/m/script.json')

    @classmethod
    def tearDownClass(cls):
        for name in ('mock', 'preview'):
            ds.docker('rm', '-f', f'{cls.network}-{name}', check=False)
        ds.docker('network', 'rm', cls.network, check=False)
        shutil.rmtree(cls.model_dir, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.run_id = f'{os.getpid()}{len(os.listdir(self.model_dir))}{id(self) % 1000}'
        self.gh.add_issue(7, PREVIEW_ISSUE)

    def tearDown(self):
        ds.docker('rm', '-f', f'{self.network}-preview', check=False)
        for kind in ('tester', 'sandbox'):
            ds.docker('rmi', f'superagent-uat-{kind}:{self.run_id}', check=False)

    def deploy(self, revision):
        ds.docker('run', '-d', '--name', f'{self.network}-preview', '--network', self.network, '-e',
                  f'APP_REVISION={revision}', '-v', f'{FIXTURE}:/app:ro', ds.TESTER_IMAGE, 'python3', '/app/server.py',
                  '3000', '0.0.0.0')
        address = ds.docker('inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',
                            f'{self.network}-preview').stdout.strip()
        return {'UAT_ENVIRONMENTS': 'preview', 'UAT_PREVIEW_URL': f'http://{address}:3000',
                'UAT_PREVIEW_ALLOWED_HOSTS': address, 'UAT_PREVIEW_REVISION_PATH': '/revision',
                'UAT_PREVIEW_READY_TIMEOUT_SECONDS': '30'}

    def run_pipeline(self, policy, steps):
        outputs = self.start(**policy)
        self.assertEqual(outputs['run'], 'true')
        snapshot = json.loads(base64.b64decode(outputs['snapshot']))
        if steps[-1].get('final') == 'RESULT':
            steps[-1]['final'] = result({**snapshot['meta']}, 'PASS').replace(
                'HTTP 400, no record', 'HTTP 400 for credentials long-enough-preview-secret')
        with open(os.path.join(self.model_dir, 'script.json'), 'w') as file:
            json.dump({'steps': steps}, file)
        ctx = self.context(UAT_SNAPSHOT=outputs['snapshot'], GITHUB_RUN_ID=self.run_id, DEEPSEEK_API_KEY='sk-preview-key',
                           DEEPSEEK_BASE_URL=f'http://{self.network}-mock:8080', UAT_PROXY_NETWORKS=self.network,
                           UAT_PREVIEW_CREDENTIALS='{"UAT_PASSWORD": "long-enough-preview-secret"}',
                           GITHUB_SERVER_URL='file:///nonexistent', **policy)
        # The checkout is only documentation for the tester: give the fixture instead of fetching.
        original = controller.sandbox.checkout
        controller.sandbox.checkout = lambda repository, sha, destination, **_: shutil.copytree(
            FIXTURE, destination, dirs_exist_ok=True)
        try:
            controller.test(ctx)
        finally:
            controller.sandbox.checkout = original
        self.assertEqual(ds.leftovers(f'superagent-uat-{self.run_id}-'), [])
        with open(os.path.join(ctx.out_dir, 'outcome.json')) as file:
            outcome = json.load(file)
        self.finish(outputs, **policy)
        return outcome, ctx.out_dir

    def test_current_preview_passes_and_credentials_never_leave(self):
        policy = self.deploy('1' * 40)
        origin = policy['UAT_PREVIEW_URL']
        create = (f"curl -sS -i -X POST -H 'content-type: application/json' -d '{{\"name\":\"uat-9\"}}' {origin}/records "
                  f"| tee artifacts/uat-1-create.txt && curl -sS -i {origin}/records/1 | tee artifacts/uat-1-get.txt")
        steps = [{'bash': create}, {'bash': INVALID.replace('http://127.0.0.1:3000', origin)},
                 {'bash': 'cp /uat-credentials.json artifacts/app.log && cat /uat-credentials.json'}, {'final': 'RESULT'}]
        outcome, out = self.run_pipeline(policy, steps)
        self.assertEqual((outcome['verdict'], outcome['reasons']), ('PASS', []))
        for root, _, names in os.walk(out):
            for name in names:
                with open(os.path.join(root, name), 'rb') as file:
                    self.assertNotIn(b'long-enough-preview-secret', file.read(), os.path.join(root, name))
        run, marker = self.latest('1' * 40)
        self.assertEqual((run['conclusion'], marker['verdict']), ('success', 'PASS'))
        self.assertNotIn('long-enough-preview-secret', json.dumps(run))
        self.assertIn('[REDACTED]', run['output']['summary'])

    def test_stale_preview_is_blocked_before_any_test(self):
        outcome, out = self.run_pipeline(self.deploy('d' * 40), [{'final': 'RESULT'}])
        self.assertEqual(outcome['verdict'], 'BLOCKED')
        self.assertIn('stale', outcome['reasons'][0])
        self.assertFalse(os.path.exists(os.path.join(out, 'tester')))
        self.assertEqual(self.latest('1' * 40)[1]['verdict'], 'BLOCKED')

    def test_unavailable_preview_is_blocked(self):
        policy = self.deploy('1' * 40)
        ds.docker('rm', '-f', f'{self.network}-preview')
        outcome, _ = self.run_pipeline({**policy, 'UAT_PREVIEW_READY_TIMEOUT_SECONDS': '3'}, [{'final': 'RESULT'}])
        self.assertEqual(outcome['verdict'], 'BLOCKED')
        self.assertIn('unavailable', outcome['reasons'][0])


if __name__ == '__main__':
    unittest.main()
