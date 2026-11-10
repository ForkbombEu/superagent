"""Preview environments: trusted URL policy, readiness, SHA proof, redirects, credentials and redaction."""
import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from uat import controller, preview
from uat.tests.test_gate import Gate
from uat.tests.test_spec import ISSUE

SHA = 'c' * 40
PREVIEW_ISSUE = ISSUE.replace('Environment: local', 'Environment: preview')


class Preview(BaseHTTPRequestHandler):
    """A preview whose /revision answers `answers` in turn (the last one from then on)."""
    answers, redirect = [json.dumps({'revision': SHA}).encode()], None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.redirect and self.path == '/health':
            self.send_response(302)
            self.send_header('location', self.redirect)
            self.end_headers()
            return
        status, body = 200, b'{"status": "ok"}'
        if self.path == '/revision':
            body = self.answers[0] if len(self.answers) == 1 else type(self).answers.pop(0)
            status, body = (int(body[5:8]), b'') if body.startswith(b'HTTP') else (200, body)
        self.send_response(status)
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

    def test_url_template_comes_from_the_policy(self):
        self.assertEqual(preview.preview_url('https://pr-{pr}.pr.example.com/app', 12, ['*.pr.example.com']),
                         'https://pr-12.pr.example.com')
        for template in ('https://pr-{pr}.evil.net', 'ftp://pr-{pr}.pr.example.com', 'pr-{pr}.pr.example.com'):
            with self.assertRaises(preview.Refused):
                preview.preview_url(template, 12, ['*.pr.example.com'])

    def test_current_preview_is_accepted(self):
        self.verify()

    def test_stale_preview_is_refused(self):
        self.handler.answers = [json.dumps({'revision': 'd' * 40}).encode()]
        with self.assertRaisesRegex(preview.Refused, f'stale or not yet deployed.*|it runs {"d" * 40}'):
            self.verify()

    def test_exact_revision_answers_are_accepted(self):
        for answer in (SHA, SHA.upper() + '\n', json.dumps({'sha': SHA}), json.dumps({'commit': SHA, 'branch': 'agent/x'})):
            with self.subTest(answer):
                self.handler.answers = [answer.encode()]
                self.verify()

    def test_ambiguous_or_partial_revision_answers_are_refused(self):
        for answer in (f'<html>deployed {SHA} after {"d" * 40}</html>', f'build {SHA}', SHA[:12],
                       json.dumps({'revision': SHA, 'previous': 'd' * 40}), json.dumps({'revision': SHA, 'sha': SHA}),
                       json.dumps({'version': SHA}), json.dumps({'revision': SHA[:7]}), json.dumps([SHA])):
            with self.subTest(answer):
                self.handler.answers = [answer.encode()]
                with self.assertRaisesRegex(preview.Refused, 'does not prove the deployed commit'):
                    self.verify()

    def test_deployment_propagation_is_awaited(self):
        self.handler.answers = [b'HTTP 503', json.dumps({'revision': 'd' * 40}).encode(), json.dumps({'revision': SHA}).encode()]
        self.verify(timeout=10)
        self.handler.answers = [b'HTTP 404']
        with self.assertRaisesRegex(preview.Refused, 'HTTP 404.*stale or not yet deployed'):
            self.verify(timeout=2)

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
        return {'SUPERAGENT_UAT_ENVIRONMENTS': 'local preview',
                'SUPERAGENT_UAT_PREVIEW_URL': 'https://pr-{pr}.preview.example.com',
                'SUPERAGENT_UAT_PREVIEW_ALLOWED_HOSTS': '*.preview.example.com',
                'SUPERAGENT_UAT_PREVIEW_REVISION_PATH': '/revision', **extra}

    def test_preview_needs_the_gate_policy_to_configure_it(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        run = self.request(on=self.make_gate(SUPERAGENT_UAT_ENVIRONMENTS='local preview'))
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertIn('configures no preview URL', run['summary'])

    def test_preview_host_outside_the_allowlist_is_blocked(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        run = self.request(on=self.make_gate(**self.configure(SUPERAGENT_UAT_PREVIEW_URL='https://pr-{pr}.evil.net')))
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertIn('not one of the allowed preview hosts', run['summary'])

    def test_issue_cannot_pick_an_environment_the_policy_does_not_allow(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        run = self.request()
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertIn("'preview' is not enabled", run['summary'])

    def test_configured_preview_runs(self):
        self.gh.add_issue(7, PREVIEW_ISSUE)
        self.gate = self.make_gate(**self.configure())
        run = self.request()
        self.assertEqual(run['state'], 'QUEUED')
        snapshot = json.loads(self.gate.one('SELECT snapshot FROM runs WHERE id = ?', run['run_id'])['snapshot'])
        self.assertEqual(snapshot['entry'], {'type': 'preview', 'readiness_path': '/health'})
        self.assertEqual(controller.derive(self.gate.ctx, 9)['key'], snapshot['key'])


if __name__ == '__main__':
    unittest.main()
