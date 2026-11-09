"""The UAT gate without any GitHub App: requests, runs, verdicts, budgets, invalidation, uat-test and claude-review."""
import copy
import importlib.machinery
import importlib.util
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
ROOT = os.path.dirname(os.path.dirname(TESTS))
CLIENT = os.path.join(ROOT, 'scripts', 'uat-test')
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

    def make_gate(self, state=None, executor=None, **extra):
        """A gate with no GitHub App, only a read token; another one on the same directory is a restart."""
        ctx = controller.Context(REPOSITORY, GitHub(fg.GATE_TOKEN, self.gh.url), controller.Policy(extra))
        return gate.Gate(ctx, state or self.state, os.path.join(self.dir, 'work'),
                         executor=executor or fake_executor(self.verdicts))

    def request(self, sha=None, pr=9, on=None, issue=None):
        return (on or self.gate).request(REPOSITORY, pr, sha or self.gh.prs[pr]['head']['sha'], issue)

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

    def current(self, sha, pr=9, issue=None):
        return self.gate.verify(REPOSITORY, pr, sha, issue)['current']


class RunTest(Gate):
    def test_no_uat_is_explicitly_not_required_and_holds(self):
        self.gh.add_issue(7, '@forkboteu Do X.\n\n## Acceptance criteria\n- [ ] X\n')
        run = self.request()
        self.assertEqual((run['state'], run['verdict'], run['head_sha']), ('COMPLETE', 'NOT_REQUIRED', SHA1))
        self.assertIn('Nothing was tested', run['summary'])
        self.assertTrue(self.current(SHA1))

    def test_pass_is_bound_to_the_exact_head_and_spec(self):
        run = self.request()
        self.assertEqual(run['state'], 'QUEUED')
        self.assertFalse(self.current(SHA1), 'no merge while UAT runs')
        self.drain()
        run = self.gate.view(self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id']))
        self.assertEqual((run['verdict'], run['head_sha'], run['spec_sha256']),
                         ('PASS', SHA1, controller.derive(self.gate.ctx, 9)['spec_sha256']))
        self.assertIn(f'commit `{SHA1}`', run['summary'])
        self.assertTrue(self.current(SHA1))
        self.assertTrue(os.path.exists(os.path.join(self.state, 'runs', run['run_id'], 'app-logs', 'app.log')))
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', run['run_id'])))

    def test_failure_returns_reproduction_and_the_fixed_head_is_tested_again(self):
        self.verdicts[SHA1] = 'FAIL'
        run = self.run_to_end()
        self.assertEqual((run['verdict'], run['failed_rounds']), ('FAIL', 1))
        self.assertIn('Send invalid POST', run['text'])
        self.assertFalse(self.current(SHA1))
        self.gh.push(9, SHA2)  # The fix, or a commit after a failed Claude Code review.
        self.assertFalse(self.current(SHA2))
        run = self.run_to_end()
        self.assertEqual((run['head_sha'], run['verdict'], run['failed_rounds']), (SHA2, 'PASS', 1))
        self.assertTrue(self.current(SHA2))

    def test_new_commit_invalidates_an_earlier_pass(self):
        self.run_to_end()
        self.gh.push(9, SHA2)
        self.assertFalse(self.current(SHA1), 'a PASS of an earlier head must not carry over')
        self.assertFalse(self.current(SHA2))
        with self.assertRaises(gate.Refused):
            self.request(SHA1)

    def test_blocked_never_permits_merging(self):
        self.gh.add_issue(7, ISSUE.replace('UAT-2', 'UAT-1'))
        run = self.request()
        self.assertEqual((run['state'], run['verdict']), ('COMPLETE', 'BLOCKED'))
        self.assertIn('duplicate scenario ids', run['summary'])
        self.assertFalse(self.current(SHA1))
        self.gh.add_issue(7, ISSUE)
        self.verdicts[SHA1] = 'BLOCKED'
        self.assertEqual(self.run_to_end()['verdict'], 'BLOCKED')
        self.assertFalse(self.current(SHA1))

    def test_same_key_is_answered_not_rerun(self):
        first = self.run_to_end()
        self.assertEqual(self.request()['run_id'], first['run_id'])

    def test_runner_failure_is_blocked_and_rerun_on_request(self):
        self.verdicts[SHA1] = 'RAISE'
        self.assertEqual(self.run_to_end()['verdict'], 'BLOCKED')
        self.assertFalse(self.current(SHA1))
        self.verdicts[SHA1] = 'PASS'
        self.assertEqual(self.run_to_end()['verdict'], 'PASS')

    def test_newer_head_supersedes_an_active_run(self):
        old = self.request()
        self.gh.push(9, SHA2)
        new = self.request()
        old_row = self.gate.one('SELECT * FROM runs WHERE id = ?', old['run_id'])
        self.assertEqual((old_row['state'], old_row['verdict']), ('COMPLETE', 'STALE'))
        self.drain()
        self.assertEqual(self.gate.view(self.gate.one('SELECT * FROM runs WHERE id = ?', new['run_id']))['verdict'], 'PASS')

    def test_spec_change_during_the_run_is_stale_never_pass(self):
        self.request()
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        self.drain()
        row = self.gate.one('SELECT * FROM runs ORDER BY created DESC LIMIT 1')
        self.assertEqual(row['verdict'], 'STALE')
        self.assertIn('UAT spec', row['summary'])
        self.assertFalse(self.current(SHA1))

    def test_concurrent_pull_requests_stay_separate(self):
        self.gh.add_issue(8, ISSUE)
        self.gh.add_pr(10, SHA2, closes=[8])
        self.verdicts[SHA2] = 'FAIL'
        first, second = self.request(pr=9), self.request(pr=10)
        self.drain()
        self.assertTrue(self.current(SHA1, pr=9))
        self.assertFalse(self.current(SHA2, pr=10))
        self.assertEqual((self.gate.budget(REPOSITORY, 9), self.gate.budget(REPOSITORY, 10)), (0, 1))
        self.assertEqual(sorted(os.listdir(os.path.join(self.state, 'runs'))), sorted([first['run_id'], second['run_id']]))

    def test_gate_only_reads_from_github(self):
        self.run_to_end()
        self.gh.push(9, SHA2)
        self.verdicts[SHA2] = 'FAIL'
        self.run_to_end()
        self.gate.verify(REPOSITORY, 9, SHA2)
        self.assertEqual([write for write in self.gh.writes if write[2] == fg.GATE_TOKEN], [])


class PersistenceTest(Gate):
    def test_failure_limit_survives_gate_restarts(self):
        for sha in (SHA1, SHA2, SHA3):
            self.gh.push(9, sha)
            self.verdicts[sha] = 'FAIL'
            self.gate = self.make_gate()  # A gate container restart before every round.
            self.assertEqual(self.run_to_end()['verdict'], 'FAIL')
        self.gh.push(9, SHA4)
        self.gate = self.make_gate()
        run = self.request()
        self.assertEqual((run['state'], run['verdict']), ('COMPLETE', 'BLOCKED'))
        self.assertIn('no failing rounds left', run['title'])
        self.assertFalse(self.current(SHA4))

    def test_custom_failure_limit(self):
        self.gate = self.make_gate(SUPERAGENT_UAT_MAX_FAILED_ROUNDS='1')
        self.verdicts[SHA1] = 'FAIL'
        self.run_to_end()
        self.gh.push(9, SHA2)
        self.assertEqual(self.request()['verdict'], 'BLOCKED')

    def test_run_interrupted_by_a_restart_is_run_again_once(self):
        run = self.request()
        self.gate.claim()  # RUNNING, then the gate dies.
        self.gate = self.make_gate()
        self.gate.recover()
        self.drain()
        row = self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id'])
        self.assertEqual((row['verdict'], row['attempts']), ('PASS', 2))

    def test_run_interrupted_twice_is_blocked_not_passed(self):
        run = self.request()
        for _ in range(2):
            self.gate.claim()
            self.gate = self.make_gate()
            self.gate.recover()
        row = self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id'])
        self.assertEqual((row['state'], row['verdict']), ('COMPLETE', 'BLOCKED'))
        self.assertFalse(self.current(SHA1))


class InvalidationTest(Gate):
    def test_spec_edit_after_pass_invalidates_it(self):
        self.run_to_end()
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        answer = self.gate.verify(REPOSITORY, 9, SHA1)
        self.assertFalse(answer['current'])
        self.assertIn('STALE', answer['reason'])
        self.assertEqual(self.run_to_end()['verdict'], 'PASS')
        self.assertTrue(self.current(SHA1))

    def test_removing_the_uat_section_invalidates_a_pass(self):
        self.run_to_end()
        self.gh.add_issue(7, 'No tests any more.')
        self.assertFalse(self.current(SHA1))
        self.assertEqual(self.request()['verdict'], 'NOT_REQUIRED')
        self.assertTrue(self.current(SHA1))

    def test_edit_outside_the_section_keeps_the_pass(self):
        self.run_to_end()
        self.gh.add_issue(7, ISSUE.replace('Implement feature X.', 'Implement feature X now.'))
        self.assertTrue(self.current(SHA1))


class BindingTest(Gate):
    """The verdict is bound to the issue the ticket started from, and request and verify agree on the binding."""

    def test_editing_closes_cannot_bypass_the_ticket_issue(self):
        self.gh.add_issue(10, ISSUE)
        self.gh.add_issue(20, 'Fix the typo.\n\n## Acceptance criteria\n- [ ] no typo\n')
        self.gh.add_pr(11, SHA2, closes=[10])
        self.verdicts[SHA2] = 'FAIL'
        run = self.request(pr=11, issue=10)
        self.drain()
        self.assertEqual(self.gate.one('SELECT verdict FROM runs WHERE id = ?', run['run_id'])['verdict'], 'FAIL')
        self.gh.prs[11]['closes'] = [20]  # `Closes #10` -> `Closes #20`: issue #20 has no UAT.
        for issue in (10, None):  # The worker names its issue, or the gate uses the pinned one.
            with self.subTest(issue=issue):
                answer = self.request(pr=11, issue=issue)
                self.assertEqual((answer['verdict'], answer['issue']), ('FAIL', 10))
                self.assertIn('does not close issue #10', answer['summary'])
                self.assertFalse(self.current(SHA2, pr=11, issue=issue))
        with self.assertRaisesRegex(gate.Refused, 'bound to issue #10'):
            self.request(pr=11, issue=20)
        with self.assertRaisesRegex(gate.Refused, 'bound to issue #10'):
            self.gate.verify(REPOSITORY, 11, SHA2, 20)
        self.gh.prs[11]['closes'] = [10, 20]
        self.assertEqual(self.request(pr=11)['verdict'], 'FAIL', "issue #10's UAT still runs and still fails")

    def test_the_ticket_issue_must_be_an_issue_of_the_repository(self):
        for issue, reason in ((999, 'does not exist'), (9, 'is a pull request')):
            with self.subTest(issue=issue):
                answer = self.request(issue=issue)
                self.assertEqual(answer['verdict'], 'BLOCKED')
                self.assertIn(reason, answer['summary'])
        self.assertIsNone(self.gate.one('SELECT issue FROM origins'), 'nothing unverified is pinned')
        self.assertEqual(self.run_to_end_for(issue=7)['verdict'], 'PASS')
        self.assertTrue(self.current(SHA1, issue=7))
        self.assertTrue(self.current(SHA1), 'the pinned issue applies when the request names none')

    def test_a_mistaken_first_issue_is_not_pinned(self):
        self.gh.add_issue(8, 'Another issue.\n\n## Acceptance criteria\n- [ ] Y\n')
        answer = self.request(issue=8)  # PR #9 closes #7 only.
        self.assertEqual(answer['verdict'], 'FAIL')
        self.assertIn('does not close issue #8', answer['summary'])
        self.assertIsNone(self.gate.one('SELECT issue FROM origins'), 'a failed first request pins nothing')
        self.assertEqual(self.run_to_end_for(issue=7)['verdict'], 'PASS')
        self.assertEqual(self.gate.one('SELECT issue FROM origins')['issue'], 7)
        with self.assertRaisesRegex(gate.Refused, 'bound to issue #7'):
            self.request(issue=8)

    def test_an_unrelated_issue_cannot_replace_the_one_with_uat(self):
        self.gh.add_issue(10, ISSUE)
        self.gh.add_issue(20, 'Fix the typo.\n\n## Acceptance criteria\n- [ ] no typo\n')
        self.gh.add_pr(11, SHA2, closes=[10, 20])
        self.verdicts[SHA2] = 'FAIL'
        run = self.request(pr=11, issue=20)  # The worker names #20, which has no UAT: #10's still runs.
        self.assertEqual((run['state'], run['issue']), ('QUEUED', 10))
        self.drain()
        self.assertFalse(self.current(SHA2, pr=11, issue=20))

    def run_to_end_for(self, issue):
        run = self.request(issue=issue)
        self.drain()
        return self.gate.view(self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id']))

    def test_request_and_verify_agree_after_a_spec_goes_a_b_a(self):
        spec_a, spec_b = ISSUE, ISSUE.replace('returns 400', 'returns 422')
        pass_a = self.run_to_end()
        self.assertEqual(pass_a['verdict'], 'PASS')
        self.gh.add_issue(7, spec_b)
        self.verdicts[SHA1] = 'FAIL'
        self.assertEqual(self.run_to_end()['verdict'], 'FAIL')
        self.assertFalse(self.current(SHA1))
        self.gh.add_issue(7, spec_a)
        again = self.request()
        self.assertEqual((again['run_id'], again['verdict']), (pass_a['run_id'], 'PASS'))
        answer = self.gate.verify(REPOSITORY, 9, SHA1)
        self.assertEqual((answer['current'], answer['run']['run_id']), (True, pass_a['run_id']))
        self.assertEqual(self.gate.budget(REPOSITORY, 9), 1, 'the failed round B still counts')

    def test_protected_policy_changes_invalidate_a_pass(self):
        self.run_to_end()
        preview = {'SUPERAGENT_UAT_ENVIRONMENTS': 'local preview', 'SUPERAGENT_UAT_PREVIEW_ALLOWED_HOSTS': 'example.com',
                   'SUPERAGENT_UAT_PREVIEW_REVISION_PATH': '/revision'}
        for change in ({'SUPERAGENT_UAT_MODEL': 'deepseek-reasoner'},
                       {**preview, 'SUPERAGENT_UAT_PREVIEW_URL': 'https://example.com'},
                       {'SUPERAGENT_UAT_PREVIEW_CREDENTIALS': '{"UAT_PASSWORD": "another-long-secret"}'}):
            with self.subTest(change=sorted(change)):
                self.gate = self.make_gate(**change)
                answer = self.gate.verify(REPOSITORY, 9, SHA1)
                self.assertFalse(answer['current'])
                self.assertIn('STALE', answer['reason'])
        self.gate = self.make_gate(SUPERAGENT_UAT_MAX_FAILED_ROUNDS='5')
        self.assertTrue(self.current(SHA1), 'limits are not part of what a result means')
        self.gate = self.make_gate(SUPERAGENT_UAT_MODEL='deepseek-reasoner')
        self.assertEqual(self.run_to_end()['verdict'], 'PASS')
        self.assertTrue(self.current(SHA1))



class SecurityTest(Gate):
    def test_github_credentials_leave_the_gate_environment(self):
        environ = {'GH_TOKEN': 'ghp_agent', 'SUPERAGENT_UAT_GITHUB_TOKEN': 'github_pat_read', 'GITHUB_TOKEN': 'x',
                   'KEEP': '1'}
        self.assertEqual(gate.github_token(environ), 'github_pat_read')
        self.assertEqual(environ, {'KEEP': '1'})
        environ = {'GH_TOKEN': 'ghp_agent'}
        self.assertEqual(gate.github_token(environ), 'ghp_agent')
        self.assertEqual(environ, {})

    def test_tester_gets_only_model_variables(self):
        policy = controller.Policy({'DEEPSEEK_API_KEY': 'sk-1', 'GH_TOKEN': 'ghp_x', 'SUPERAGENT_UAT_GITHUB_TOKEN': 'g',
                                    'SUPERAGENT_UAT_GATE_TOKEN': TOKEN})
        self.assertEqual(policy.tester_environment, {'DEEPSEEK_API_KEY': 'sk-1'})

    def test_artifact_paths_stay_in_the_run(self):
        run = self.run_to_end()
        self.assertIn(b'POST /records', self.gate.artifact(run['run_id'], 'app-logs/app.log'))
        os.symlink('/etc/passwd', os.path.join(self.state, 'runs', run['run_id'], 'link'))
        for path in ('../gate.db', 'link', '/etc/passwd', 'missing'):
            with self.assertRaises(gate.Refused):
                self.gate.artifact(run['run_id'], path)


class HttpTest(Gate):
    """The API over HTTP with worker threads, the real uat-test client and the claude-review guard."""

    def setUp(self):
        super().setUp()
        self.gate.start()
        self.addCleanup(self.gate.stopping.set)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), gate.handler(self.gate, TOKEN, ['127.0.0.1']))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.config = os.path.join(self.dir, 'uat.json')
        with open(self.config, 'w') as file:
            json.dump({'gate_url': self.url, 'token': TOKEN}, file)
        self.bin = os.path.join(self.dir, 'bin')
        os.makedirs(self.bin)
        os.symlink(os.path.join(TESTS, 'fake_gh.py'), os.path.join(self.bin, 'gh'))
        os.symlink(CLIENT, os.path.join(self.bin, 'uat-test'))
        self.environ = {**os.environ, 'UAT_TEST_GH': os.path.join(TESTS, 'fake_gh.py'), 'FAKE_GITHUB_URL': self.gh.url,
                        'FAKE_GITHUB_TOKEN': fg.CODER_TOKEN, 'UAT_TEST_CONFIG': self.config, 'HOME': self.dir,
                        'UAT_TEST_POLL_SECONDS': '0.1', 'UAT_TEST_TIMEOUT_SECONDS': '30', 'UAT_TEST_OUTAGE_SECONDS': '1',
                        'PATH': f'{self.bin}:{os.environ["PATH"]}', 'DSH_SUPERAGENT_GITHUB_NUMBER': '',
                        'DSH_SUPERAGENT_GITHUB_PR_HEAD': ''}

    def post(self, path, body, token=TOKEN):
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method='POST',
                                         headers={'authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def client(self, *args, **extra):
        return subprocess.run([sys.executable, CLIENT, *args], capture_output=True, text=True,
                              env={**self.environ, **extra}, timeout=60)

    def test_requests_only_never_verdicts(self):
        body = {'repository': REPOSITORY, 'pr': 9, 'head_sha': SHA1}
        self.assertEqual(self.post('/v1/uat/runs', {**body, 'verdict': 'PASS'})[0], 400)
        self.assertEqual(self.post('/v1/uat/runs', {**body, 'pr': '9'})[0], 400)
        for issue in ('7', True, 0, -7):
            self.assertEqual(self.post('/v1/uat/runs', {**body, 'issue': issue})[0], 400, issue)
        self.assertEqual(self.post('/v1/uat/runs', body, token='wrong')[0], 401)
        self.assertEqual(self.post('/v1/uat/runs', {**body, 'repository': 'evil/repo'})[0], 403)
        self.assertEqual(self.gate.query('SELECT * FROM runs'), [])

    def test_uat_test_names_the_issue_the_ticket_started_from(self):
        result = self.client(REPOSITORY, '9', SHA1, DSH_SUPERAGENT_GITHUB_NUMBER='7')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gate.one('SELECT issue FROM origins')['issue'], 7)
        other = self.client('--verify', REPOSITORY, '9', SHA1, DSH_SUPERAGENT_GITHUB_NUMBER='8')
        self.assertEqual(other.returncode, 3)
        self.assertIn('bound to issue #7', other.stderr)
        started_from_the_pr = self.client('--verify', REPOSITORY, '9', SHA1, DSH_SUPERAGENT_GITHUB_NUMBER='9',
                                          DSH_SUPERAGENT_GITHUB_PR_HEAD='refs/pull/9/head')
        self.assertEqual(started_from_the_pr.returncode, 0, started_from_the_pr.stderr)

    def test_only_allowed_clients(self):
        self.server.RequestHandlerClass = gate.handler(self.gate, TOKEN, ['192.0.2.1'])
        self.assertEqual(self.post('/v1/uat/runs', {'repository': REPOSITORY, 'pr': 9, 'head_sha': SHA1})[0], 403)

    def test_exit_codes_and_pr_reports(self):
        result = self.client(REPOSITORY, '9', SHA1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.rstrip().endswith(f'uat-test: PASS for {SHA1}'))
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
        self.gh.push(9, SHA4)
        self.gh.add_issue(7, 'No tests here.')
        self.assertEqual(self.client(REPOSITORY, '9', SHA4).returncode, 0)
        self.assertEqual([(c['pr'], c['token']) for c in self.gh.comments], [(9, fg.CODER_TOKEN)] * 4)
        for sha, verdict, comment in zip((SHA1, SHA2, SHA3, SHA4), ('PASS', 'FAIL', 'BLOCKED', 'NOT_REQUIRED'),
                                         self.gh.comments):
            self.assertIn(f'**DeepSeek UAT** of {sha}: **{verdict}**', comment['body'])

    def test_the_same_run_is_reported_once(self):
        self.client(REPOSITORY, '9', SHA1)
        self.assertEqual(self.client(REPOSITORY, '9', SHA1).returncode, 0)
        self.assertEqual(len(self.gh.comments), 1)

    def test_verify_and_artifact(self):
        result = self.client(REPOSITORY, '9', SHA1)
        run_id = next(line.split()[2] for line in result.stdout.splitlines() if line.startswith('UAT run: '))
        artifact = self.client('--artifact', run_id, 'app-logs/app.log')
        self.assertEqual((artifact.returncode, artifact.stdout), (0, 'POST /records -> 500\n'))
        self.assertEqual(self.client('--verify', REPOSITORY, '9', SHA1).returncode, 0)
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        verify = self.client('--verify', REPOSITORY, '9', SHA1)
        self.assertEqual(verify.returncode, 1)
        self.assertIn('STALE', verify.stdout)

    def test_pass_that_no_longer_holds_is_tested_again(self):
        self.client(REPOSITORY, '9', SHA1)
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        result = self.client(REPOSITORY, '9', SHA1)
        self.assertEqual(result.returncode, 0, result.stderr)
        runs = self.gate.query('SELECT spec_sha256 FROM runs ORDER BY created')
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0]['spec_sha256'], runs[1]['spec_sha256'])
        self.assertEqual(len(self.gh.comments), 2)

    def test_no_verdict_without_the_gate_or_for_an_old_head(self):
        with open(self.config, 'w') as file:
            json.dump({'gate_url': 'http://127.0.0.1:9', 'token': TOKEN}, file)
        result = self.client(REPOSITORY, '9', SHA1)
        self.assertEqual(result.returncode, 3)
        self.assertIn('unreachable', result.stderr)
        result = self.client(REPOSITORY, '9', SHA2)
        self.assertEqual(result.returncode, 3)
        self.assertIn('the PR head is', result.stderr)

    def review(self):
        """claude-review on a checkout whose HEAD is the PR head, up to where it would call Claude Code."""
        checkout = os.path.join(self.dir, 'checkout')
        subprocess.run(['git', 'init', '-q', checkout], check=True)
        subprocess.run(['git', '-C', checkout, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q',
                        '--allow-empty', '-m', 'feat: x'], check=True)
        sha = subprocess.run(['git', '-C', checkout, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        self.gh.push(9, sha)
        self.gh.contents[('.superagent/uat.yaml', sha)] = CONFIG
        return sha, lambda: subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'claude-review'), REPOSITORY,
                                            '9', sha], capture_output=True, text=True, cwd=checkout, env=self.environ,
                                           timeout=60)

    def test_claude_review_runs_only_after_uat_succeeds(self):
        sha, review = self.review()
        refused = review()
        self.assertEqual(refused.returncode, 4)
        self.assertIn('UAT has not passed', refused.stderr)
        self.verdicts[sha] = 'FAIL'
        self.assertEqual(self.client(REPOSITORY, '9', sha).returncode, 1)
        self.assertEqual(review().returncode, 4)
        self.gh.add_issue(7, 'No tests here.')
        self.assertEqual(self.client(REPOSITORY, '9', sha).returncode, 0)
        allowed = review()  # Past the UAT guard, it goes on to fetch the base branch, which this checkout lacks.
        self.assertNotEqual(allowed.returncode, 4)
        self.assertNotIn('UAT has not passed', allowed.stderr)
        self.assertIn('git fetch', allowed.stderr)


class ConfigureTest(unittest.TestCase):
    """superagent-configure turns the UAT procedure on with SUPERAGENT_UAT_ENABLED alone."""

    def delivery(self, **environ):
        loader = importlib.machinery.SourceFileLoader('configure', os.path.join(ROOT, 'scripts', 'superagent-configure'))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader('configure', loader))
        loader.exec_module(module)
        module.DELIVERY = os.path.join(ROOT, 'prompts', 'delivery.md')
        module.DELIVERY_UAT = os.path.join(ROOT, 'prompts', 'delivery-uat.md')
        view = {'sources': [{'id': 'github', 'value': {'config': {'repository': REPOSITORY, 'cli': module.GH_CLI}},
                             'revision': 1}],
                'blocks': {'policy': {'value': {'sources': {}}, 'revision': 1},
                           'executor': {'value': {'prepare': module.PREPARE}, 'revision': 1}}}
        prompts = {'current': {'config': {'delivery': ''}, 'revision': 1}}

        def call(method, path, body=None):
            if method == 'GET':
                return 200, view if path == 'settings' else prompts
            prompts['current']['config']['delivery'] = body['config']['delivery']
            return 200, {}
        module.call = call
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update({'GITHUB_REPOSITORY': REPOSITORY, **environ})
        try:
            self.assertEqual(module.main(), 0)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return prompts['current']['config']['delivery']

    def test_uat_procedure_needs_no_github_app(self):
        with open(os.path.join(ROOT, 'prompts', 'delivery.md')) as file:
            base = file.read().strip()
        with open(os.path.join(ROOT, 'prompts', 'delivery-uat.md')) as file:
            uat = file.read().strip()
        self.assertEqual(self.delivery(), base)
        self.assertEqual(self.delivery(SUPERAGENT_UAT_ENABLED='false'), base)
        self.assertEqual(self.delivery(SUPERAGENT_UAT_ENABLED='true'), f'{base}\n\n{uat}')


if __name__ == '__main__':
    unittest.main()
