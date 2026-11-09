"""The trusted UAT gate against a fake GitHub: requests, runs, checks, budgets, invalidation and uat-test."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from uat import controller, gate, result as results
from uat.github import GitHub
from uat.tests import fake_github as fg
from uat.tests.test_result import PASSING, RESULT, trace
from uat.tests.test_spec import ISSUE

TESTS = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(os.path.dirname(os.path.dirname(TESTS)), 'scripts', 'uat-test')
with open(os.path.join(TESTS, 'fixtures', 'app', '.superagent', 'uat.yaml')) as _file:
    CONFIG = _file.read()
SHA1, SHA2, SHA3, SHA4 = ('1' * 40, '2' * 40, '3' * 40, '4' * 40)
TOKEN = 'gate-request-token-' + 'x' * 32
REPOSITORY = 'ForkbombEu/sample'


def fake_executor(verdicts):
    """An executor that leaves what a real run leaves, with the verdict `verdicts[head sha]` (default PASS)."""
    def execute(ctx, snapshot, run_dir):
        verdict = verdicts.get(snapshot['sha'], 'PASS')
        if verdict == 'RAISE':
            raise RuntimeError('the runner fell over')
        out = os.path.join(run_dir, 'out')
        artifacts = os.path.join(out, 'tester-work', 'attempt-1', 'artifacts')
        os.makedirs(artifacts)
        os.makedirs(os.path.join(out, 'app-logs'))
        with open(os.path.join(out, 'app-logs', 'app.log'), 'w') as file:
            file.write('POST /records -> 500\n')
        result = copy.deepcopy(PASSING if verdict == 'PASS' else RESULT)
        result.update(snapshot['meta'])
        with open(os.path.join(out, 'tester-work', 'attempt-1', 'trace.ndjson'), 'wb') as file:
            file.write(trace(result))
        if verdict == 'BLOCKED':
            return results.Outcome('BLOCKED', ['the model API was unreachable'], result=None)
        return results.Outcome(verdict, [], result=result)
    return execute


class Gate(unittest.TestCase):
    def setUp(self):
        self.gh = fg.FakeGitHub(REPOSITORY)
        self.addCleanup(self.gh.close)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.gh.add_issue(7, ISSUE)
        self.gh.add_pr(9, SHA1, closes=[7])
        for sha in (SHA1, SHA2, SHA3, SHA4):
            self.gh.contents[('.superagent/uat.yaml', sha)] = CONFIG
        self.verdicts = {}
        self.state = os.path.join(self.dir, 'state')
        self.gate = self.make_gate()

    def policy(self, **extra):
        return controller.Policy({'SUPERAGENT_UAT_APP_ID': str(fg.APP_ID), **extra})

    def make_gate(self, state=None, executor=None, **extra):
        """A gate on the state directory; making another one on the same directory is a restart."""
        ctx = controller.Context(REPOSITORY, GitHub(fg.APP_TOKEN, self.gh.url), self.policy(**extra))
        return gate.Gate(ctx, state or self.state, os.path.join(self.dir, 'work'),
                         executor=executor or fake_executor(self.verdicts))

    def request(self, sha=None, pr=9, on=None):
        return (on or self.gate).request(REPOSITORY, pr, sha or self.gh.prs[pr]['head']['sha'])

    def drain(self, on=None):
        on = on or self.gate
        while True:
            row = on.claim()
            if not row:
                return
            on.process(row)

    def run_to_end(self, sha=None, pr=9):
        run = self.request(sha, pr)
        self.drain()
        return self.gate.view(self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id']))

    def latest(self, sha):
        runs = self.gh.app_runs(sha)
        return (runs[0], controller.marker_of(runs[0])) if runs else (None, None)


class RunTest(Gate):
    def test_no_uat_is_explicitly_not_required(self):
        self.gh.add_issue(7, '@forkboteu Do X.\n\n## Acceptance criteria\n- [ ] X\n')
        run = self.request()
        self.assertEqual((run['state'], run['verdict']), ('COMPLETE', 'NOT_REQUIRED'))
        self.assertIn('Nothing was tested', run['summary'])
        check, marker = self.latest(SHA1)
        self.assertEqual((check['conclusion'], marker['verdict'], marker['run_id']), ('success', 'NOT_REQUIRED', run['run_id']))
        self.assertTrue(self.gh.required_check_passes(SHA1))

    def test_pass_publishes_in_progress_then_success_on_the_exact_head(self):
        run = self.request()
        self.assertEqual(run['state'], 'QUEUED')
        check, marker = self.latest(SHA1)
        self.assertEqual((check['status'], marker['verdict']), ('in_progress', 'PENDING'))
        self.assertFalse(self.gh.required_check_passes(SHA1))
        self.drain()
        run = self.gate.view(self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id']))
        self.assertEqual((run['state'], run['verdict']), ('COMPLETE', 'PASS'))
        self.assertTrue(self.gh.required_check_passes(SHA1))
        self.assertTrue(os.path.exists(os.path.join(self.state, 'runs', run['run_id'], 'app-logs', 'app.log')))
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', run['run_id'])))

    def test_failure_returns_reproduction_and_the_fixed_head_is_tested_again(self):
        self.verdicts[SHA1] = 'FAIL'
        run = self.run_to_end()
        self.assertEqual((run['verdict'], run['failed_rounds']), ('FAIL', 1))
        self.assertIn('Send invalid POST', run['text'])
        self.assertIn(f'uat-test --artifact {run["run_id"]}', run['summary'])
        self.gh.push(9, SHA2)  # The fix, or a commit after a failed Claude Code review.
        self.assertFalse(self.gh.required_check_passes(SHA2))
        run = self.run_to_end()
        self.assertEqual((run['head_sha'], run['verdict'], run['failed_rounds']), (SHA2, 'PASS', 1))
        self.gh.push(9, SHA3)
        self.assertFalse(self.gh.required_check_passes(SHA3), 'a PASS of an earlier head must not carry over')

    def test_empty_or_ambiguous_uat_is_blocked(self):
        self.gh.add_issue(7, ISSUE.replace('UAT-2', 'UAT-1'))
        run = self.request()
        self.assertEqual((run['state'], run['verdict']), ('COMPLETE', 'BLOCKED'))
        self.assertIn('duplicate scenario ids', run['summary'])
        self.assertFalse(self.gh.required_check_passes(SHA1))

    def test_same_key_is_answered_not_rerun(self):
        first = self.run_to_end()
        count = len(self.gh.check_runs)
        self.assertEqual(self.request()['run_id'], first['run_id'])
        self.assertEqual(len(self.gh.check_runs), count)

    def test_blocked_infrastructure_is_rerun_on_request(self):
        self.verdicts[SHA1] = 'RAISE'
        run = self.run_to_end()
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertFalse(self.gh.required_check_passes(SHA1))
        self.verdicts[SHA1] = 'PASS'
        self.assertEqual(self.run_to_end()['verdict'], 'PASS')

    def test_newer_head_supersedes_an_active_run(self):
        old = self.request()
        self.gh.push(9, SHA2)
        new = self.request()
        self.assertNotEqual(old['run_id'], new['run_id'])
        old_row = self.gate.one('SELECT * FROM runs WHERE id = ?', old['run_id'])
        self.assertEqual((old_row['state'], old_row['verdict']), ('COMPLETE', 'STALE'))
        self.assertEqual(self.latest(SHA1)[1]['verdict'], 'STALE')
        self.drain()
        self.assertTrue(self.gh.required_check_passes(SHA2))
        self.assertFalse(self.gh.required_check_passes(SHA1))

    def test_request_for_an_old_head_is_refused(self):
        self.gh.push(9, SHA2)
        with self.assertRaises(gate.Refused) as refused:
            self.request(SHA1)
        self.assertEqual(refused.exception.status, 409)

    def test_spec_change_during_the_run_is_stale_never_pass(self):
        self.request()
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        self.drain()
        check, marker = self.latest(SHA1)
        self.assertEqual((check['conclusion'], marker['verdict']), ('failure', 'STALE'))
        self.assertIn('UAT spec', check['output']['summary'])

    def test_concurrent_pull_requests_stay_separate(self):
        self.gh.add_issue(8, ISSUE)
        self.gh.add_pr(10, SHA2, closes=[8])
        self.verdicts[SHA2] = 'FAIL'
        first, second = self.request(pr=9), self.request(pr=10)
        self.drain()
        self.assertTrue(self.gh.required_check_passes(SHA1))
        self.assertFalse(self.gh.required_check_passes(SHA2))
        self.assertEqual((self.gate.budget(REPOSITORY, 9), self.gate.budget(REPOSITORY, 10)), (0, 1))
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertEqual(sorted(os.listdir(os.path.join(self.state, 'runs'))), sorted([first['run_id'], second['run_id']]))


class PersistenceTest(Gate):
    def test_budget_survives_restarts_and_force_pushes(self):
        for sha, force in ((SHA1, False), (SHA2, False), (SHA3, True)):
            if sha != SHA1:
                self.gh.push(9, sha, force=force)
            self.verdicts[sha] = 'FAIL'
            self.gate = self.make_gate()  # A gate restart before every round.
            self.assertEqual(self.run_to_end()['verdict'], 'FAIL')
        self.gh.push(9, SHA4)
        self.gate = self.make_gate()
        run = self.request()
        self.assertEqual((run['state'], run['verdict']), ('COMPLETE', 'BLOCKED'))
        self.assertIn('no failing rounds left', run['title'])

    def test_budget_comes_from_the_app_check_runs_when_the_database_is_lost(self):
        """Also how the rounds the old GitHub Actions controller counted carry over to the gate."""
        for sha in (SHA1, SHA2, SHA3):
            if sha != SHA1:
                self.gh.push(9, sha)
            self.verdicts[sha] = 'FAIL'
            self.run_to_end()
        self.gh.push(9, SHA4)
        self.gate = self.make_gate(state=os.path.join(self.dir, 'new-state'))
        self.assertEqual(self.request()['verdict'], 'BLOCKED')

    def test_run_interrupted_by_a_restart_is_run_again_once(self):
        run = self.request()
        self.gate.claim()  # RUNNING, then the gate dies.
        restarted = self.make_gate()
        restarted.recover()
        self.drain(restarted)
        row = restarted.one('SELECT * FROM runs WHERE id = ?', run['run_id'])
        self.assertEqual((row['verdict'], row['attempts']), ('PASS', 2))
        self.assertEqual(len([r for r in self.gh.app_runs(SHA1) if r['conclusion'] == 'success']), 1)

    def test_run_interrupted_twice_is_blocked_not_passed(self):
        run = self.request()
        for _ in range(2):
            self.gate.claim()
            self.gate = self.make_gate()
            self.gate.recover()
        row = self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id'])
        self.assertEqual((row['state'], row['verdict']), ('COMPLETE', 'BLOCKED'))
        self.assertEqual(self.latest(SHA1)[1]['verdict'], 'BLOCKED')

    def test_gate_failure_mid_run_is_never_a_pass(self):
        self.verdicts[SHA1] = 'BLOCKED'
        self.assertEqual(self.run_to_end()['verdict'], 'BLOCKED')
        self.assertFalse(self.gh.required_check_passes(SHA1))


class InvalidationTest(Gate):
    def test_issue_edit_after_pass_is_published_stale(self):
        self.run_to_end()
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        self.gate.observe()
        check, marker = self.latest(SHA1)
        self.assertEqual((check['conclusion'], marker['verdict']), ('failure', 'STALE'))
        self.assertFalse(self.gh.required_check_passes(SHA1))
        self.assertEqual(self.run_to_end()['verdict'], 'PASS')

    def test_issue_removing_uat_after_pass_is_stale_then_not_required(self):
        self.run_to_end()
        self.gh.add_issue(7, 'No tests any more.')
        self.gate.observe()
        self.assertFalse(self.gh.required_check_passes(SHA1))
        self.assertEqual(self.request()['verdict'], 'NOT_REQUIRED')

    def test_edit_outside_the_section_changes_nothing(self):
        self.run_to_end()
        count = len(self.gh.check_runs)
        self.gh.add_issue(7, ISSUE.replace('Implement feature X.', 'Implement feature X now.'))
        self.gate.observe()
        self.assertEqual(len(self.gh.check_runs), count)
        self.assertTrue(self.gh.required_check_passes(SHA1))

    def test_verify_before_merge(self):
        self.assertFalse(self.gate.verify(REPOSITORY, 9, SHA1)['current'])
        self.run_to_end()
        self.assertTrue(self.gate.verify(REPOSITORY, 9, SHA1)['current'])
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        answer = self.gate.verify(REPOSITORY, 9, SHA1)
        self.assertFalse(answer['current'])
        self.assertIn('STALE', answer['reason'])
        self.assertFalse(self.gh.required_check_passes(SHA1))


class SecurityTest(Gate):
    def test_coder_token_cannot_publish_the_check(self):
        from uat.github import GitHubError
        coder = GitHub(fg.CODER_TOKEN, self.gh.url)
        coder.request('POST', f'repos/{REPOSITORY}/statuses/{SHA1}', {'state': 'success', 'context': 'superagent/uat'})
        with self.assertRaises(GitHubError) as raised:
            coder.request('POST', f'repos/{REPOSITORY}/check-runs',
                          {'name': 'superagent/uat', 'head_sha': SHA1, 'status': 'completed', 'conclusion': 'success'})
        self.assertEqual(raised.exception.status, 403)
        self.assertFalse(self.gh.required_check_passes(SHA1))

    def test_old_actions_success_does_not_satisfy_the_app_pinned_check(self):
        self.verdicts[SHA1] = 'FAIL'
        self.run_to_end()
        self.gh.check_runs.append({'id': 99999, 'head_sha': SHA1, 'name': 'superagent/uat', 'status': 'completed',
                                   'conclusion': 'success', 'app': {'id': 15368}, 'output': {}})  # github-actions
        self.assertFalse(self.gh.required_check_passes(SHA1))

    def test_private_key_and_token_leave_the_environment(self):
        environ = {'SUPERAGENT_UAT_APP_PRIVATE_KEY': '-----BEGIN KEY-----\\nabc\\n-----END KEY-----', 'X': '1'}
        path = gate.private_key_file(environ, os.path.join(self.dir, 'secrets'))
        self.assertEqual(environ, {'X': '1'})
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), '0o600')
        with open(path) as file:
            self.assertEqual(file.read(), '-----BEGIN KEY-----\nabc\n-----END KEY-----\n')

    def test_tester_gets_only_model_variables(self):
        policy = controller.Policy({'SUPERAGENT_UAT_APP_ID': '1', 'DEEPSEEK_API_KEY': 'sk-1', 'GH_TOKEN': 'ghp_x',
                                    'SUPERAGENT_UAT_APP_PRIVATE_KEY': 'k', 'SUPERAGENT_UAT_GATE_TOKEN': TOKEN})
        self.assertEqual(policy.tester_environment, {'DEEPSEEK_API_KEY': 'sk-1'})

    def test_artifact_paths_stay_in_the_run(self):
        run = self.run_to_end()
        self.assertIn(b'POST /records', self.gate.artifact(run['run_id'], 'app-logs/app.log'))
        os.symlink('/etc/passwd', os.path.join(self.state, 'runs', run['run_id'], 'link'))
        for path in ('../gate.db', 'link', '/etc/passwd', 'missing'):
            with self.assertRaises(gate.Refused):
                self.gate.artifact(run['run_id'], path)


class HttpTest(Gate):
    """The API over HTTP, with worker threads, and the real uat-test client."""

    def setUp(self):
        super().setUp()
        self.gate.start(interval=3600)
        self.addCleanup(self.gate.stopping.set)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), gate.handler(self.gate, TOKEN, ['127.0.0.1']))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def post(self, path, body, token=TOKEN):
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method='POST',
                                         headers={'authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_requests_only_never_verdicts(self):
        body = {'repository': REPOSITORY, 'pr': 9, 'head_sha': SHA1}
        self.assertEqual(self.post('/v1/uat/runs', {**body, 'verdict': 'PASS'})[0], 400)
        self.assertEqual(self.post('/v1/uat/runs', {**body, 'pr': '9'})[0], 400)
        self.assertEqual(self.post('/v1/uat/runs', body, token='wrong')[0], 401)
        self.assertEqual(self.post('/v1/uat/runs', {**body, 'repository': 'evil/repo'})[0], 403)
        self.assertEqual(self.post('/v1/uat/runs/x', body)[0], 404)
        self.assertEqual(self.gh.check_runs, [])

    def test_only_allowed_clients(self):
        self.server.RequestHandlerClass = gate.handler(self.gate, TOKEN, ['192.0.2.1'])
        self.assertEqual(self.post('/v1/uat/runs', {'repository': REPOSITORY, 'pr': 9, 'head_sha': SHA1})[0], 403)

    def client(self, *args, gate_url=None, timeout='30'):
        config = os.path.join(self.dir, 'uat.json')
        with open(config, 'w') as file:
            json.dump({'app_id': fg.APP_ID, 'gate_url': gate_url or self.url, 'token': TOKEN}, file)
        environ = {**os.environ, 'UAT_TEST_GH': os.path.join(TESTS, 'fake_gh.py'), 'FAKE_GITHUB_URL': self.gh.url,
                   'FAKE_GITHUB_TOKEN': fg.CODER_TOKEN, 'UAT_TEST_CONFIG': config, 'HOME': self.dir,
                   'UAT_TEST_POLL_SECONDS': '0.1', 'UAT_TEST_TIMEOUT_SECONDS': timeout, 'UAT_TEST_OUTAGE_SECONDS': '1'}
        return subprocess.run([sys.executable, CLIENT, *args], capture_output=True, text=True, env=environ, timeout=60)

    def test_client_exit_codes(self):
        result = self.client(REPOSITORY, '9', SHA1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'uat-test: PASS for {SHA1}', result.stdout)
        self.gh.push(9, SHA2)
        self.verdicts[SHA2] = 'FAIL'
        result = self.client(REPOSITORY, '9', SHA2)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Send invalid POST', result.stdout)
        self.assertIn('failing rounds used: 1 of 3', result.stdout)
        self.gh.push(9, SHA3)
        self.gh.add_issue(7, ISSUE.replace('UAT-2', 'UAT-1'))
        result = self.client(REPOSITORY, '9', SHA3)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('duplicate scenario ids', result.stdout)

    def test_client_artifact_and_verify(self):
        result = self.client(REPOSITORY, '9', SHA1)
        run_id = next(line.split()[2] for line in result.stdout.splitlines() if line.startswith('UAT run: '))
        artifact = self.client('--artifact', run_id, 'app-logs/app.log')
        self.assertEqual((artifact.returncode, artifact.stdout), (0, 'POST /records -> 500\n'))
        self.assertEqual(self.client('--verify', REPOSITORY, '9', SHA1).returncode, 0)
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        verify = self.client('--verify', REPOSITORY, '9', SHA1)
        self.assertEqual(verify.returncode, 1)
        self.assertIn('STALE', verify.stdout)

    def test_client_refuses_a_verdict_github_does_not_show(self):
        self.gh.add_issue(7, 'No tests here.')
        original = self.gh.answer

        def hide_checks(method, path, query, token, body):  # The gate says NOT_REQUIRED; GitHub has no App check.
            if '/check-runs' in path and method == 'GET':
                return 200, {'total_count': 0, 'check_runs': []}
            return original(method, path, query, token, body)
        self.gh.answer = hide_checks
        result = self.client(REPOSITORY, '9', SHA1)
        self.assertEqual(result.returncode, 3)
        self.assertIn('not trusting it', result.stderr)

    def test_client_without_gate_has_no_verdict(self):
        result = self.client(REPOSITORY, '9', SHA1, gate_url='http://127.0.0.1:9')
        self.assertEqual(result.returncode, 3)
        self.assertIn('unreachable', result.stderr)

    def test_client_refuses_an_old_head(self):
        result = self.client(REPOSITORY, '9', SHA2)
        self.assertEqual(result.returncode, 3)
        self.assertIn('the PR head is', result.stderr)


if __name__ == '__main__':
    unittest.main()
