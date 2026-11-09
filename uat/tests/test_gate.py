"""The trusted gate end to end against a fake GitHub: controller jobs, check runs, uat-test."""
import base64
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from uat import controller
from uat.tests import fake_github as fg
from uat.tests.test_result import PASSING, RESULT, trace
from uat.tests.test_spec import ISSUE

TESTS = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(os.path.dirname(os.path.dirname(TESTS)), 'scripts', 'uat-test')
with open(os.path.join(TESTS, 'fixtures', 'app', '.superagent', 'uat.yaml')) as _file:
    CONFIG = _file.read()
SHA1, SHA2, SHA3, SHA4 = ('1' * 40, '2' * 40, '3' * 40, '4' * 40)


class Gate(unittest.TestCase):
    def setUp(self):
        self.gh = fg.FakeGitHub()
        self.addCleanup(self.gh.close)
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.gh.add_issue(7, ISSUE)
        self.gh.add_pr(9, SHA1, closes=[7])
        for sha in (SHA1, SHA2, SHA3, SHA4):
            self.gh.contents[('.superagent/uat.yaml', sha)] = CONFIG

    def context(self, event_name='pull_request_target', event=None, **extra):
        """A fresh Context, as each workflow job (and each restart) builds one."""
        directory = tempfile.mkdtemp(dir=self.dir)
        event_path, output = os.path.join(directory, 'event.json'), os.path.join(directory, 'output')
        with open(event_path, 'w') as file:
            json.dump(event if event is not None else {'pull_request': {'number': 9}}, file)
        open(output, 'w').close()
        environ = {'GITHUB_REPOSITORY': self.gh.repository, 'GITHUB_API_URL': self.gh.url,
                   'UAT_READ_TOKEN': fg.READ_TOKEN, 'UAT_CHECK_TOKEN': fg.APP_TOKEN, 'UAT_APP_ID': str(fg.APP_ID),
                   'GITHUB_EVENT_NAME': event_name, 'GITHUB_EVENT_PATH': event_path, 'GITHUB_OUTPUT': output,
                   'GITHUB_RUN_ID': '77', 'UAT_OUT_DIR': os.path.join(self.dir, 'out'), **extra}
        return controller.Context(environ)

    @staticmethod
    def outputs(ctx):
        with open(ctx.env['GITHUB_OUTPUT']) as file:
            return dict(line.rstrip('\n').split('=', 1) for line in file if '=' in line)

    def start(self, **kwargs):
        ctx = self.context(**kwargs)
        controller.start(ctx)
        return self.outputs(ctx)

    def simulate_test_job(self, outputs, verdict):
        """What the test job leaves for finish: outcome.json, and for a run its trace, attempts and evidence."""
        snapshot = json.loads(base64.b64decode(outputs['snapshot']))
        out = os.path.join(self.dir, 'out')
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(os.path.join(out, 'tester', 'attempt-1'))
        artifacts = os.path.join(out, 'tester-work', 'attempt-1', 'artifacts')
        os.makedirs(artifacts)
        result = copy.deepcopy(PASSING if verdict == 'PASS' else RESULT)
        result.update({key: snapshot['meta'][key] for key in snapshot['meta']})
        for name in ('uat-1-http.json', 'uat-2-http.json', 'server.log'):
            with open(os.path.join(artifacts, name), 'w') as file:
                file.write('HTTP/1.1 201 Created\n')
        with open(os.path.join(out, 'tester', 'attempt-1', 'trace.ndjson'), 'wb') as file:
            file.write(trace(result))
        with open(os.path.join(out, 'tester', 'attempts.json'), 'w') as file:
            json.dump([{'attempt': 1, 'exit_code': 0, 'timed_out': False}], file)
        with open(os.path.join(out, 'outcome.json'), 'w') as file:
            json.dump({'verdict': verdict, 'reasons': [], 'result': result}, file)
        return snapshot

    def finish(self, outputs, test_result='success'):
        ctx = self.context(UAT_SNAPSHOT=outputs['snapshot'], UAT_TEST_RESULT=test_result)
        controller.finish(ctx)

    def latest(self, sha):
        runs = self.gh.app_runs(sha)
        return (runs[0], controller.marker_of(runs[0])) if runs else (None, None)

    def full_round(self, verdict, sha=None):
        outputs = self.start()
        self.assertEqual(outputs['run'], 'true')
        self.simulate_test_job(outputs, verdict)
        self.finish(outputs)
        return self.latest(sha or self.gh.prs[9]['head']['sha'])


class ControllerTest(Gate):
    def test_no_uat_is_explicitly_not_required(self):
        self.gh.add_issue(7, '@forkboteu Do X.\n\n## Acceptance criteria\n- [ ] X\n')
        self.assertEqual(self.start()['run'], 'false')
        run, marker = self.latest(SHA1)
        self.assertEqual((run['conclusion'], marker['verdict']), ('success', 'NOT_REQUIRED'))
        self.assertIn('No UAT ran', run['output']['summary'])
        self.assertTrue(self.gh.required_check_passes(SHA1))

    def test_start_publishes_in_progress_before_running(self):
        outputs = self.start()
        run, marker = self.latest(SHA1)
        self.assertEqual((run['status'], marker['verdict'], marker['failed_rounds']), ('in_progress', 'PENDING', 0))
        self.assertFalse(self.gh.required_check_passes(SHA1))
        snapshot = json.loads(base64.b64decode(outputs['snapshot']))
        self.assertEqual(snapshot['meta']['head_sha'], SHA1)
        self.assertEqual([s['id'] for s in snapshot['canonical']['scenarios']], ['UAT-1', 'UAT-2'])
        self.assertEqual(snapshot['entry']['start'], 'python3 server.py 3000')

    def test_pass_is_published_only_after_reverification(self):
        run, marker = self.full_round('PASS')
        self.assertEqual((run['conclusion'], marker['verdict']), ('success', 'PASS'))
        self.assertTrue(self.gh.required_check_passes(SHA1))

    def test_pass_without_trace_or_evidence_is_blocked(self):
        outputs = self.start()
        self.simulate_test_job(outputs, 'PASS')
        shutil.rmtree(os.path.join(self.dir, 'out', 'tester-work'))
        self.finish(outputs)
        run, marker = self.latest(SHA1)
        self.assertEqual((run['conclusion'], marker['verdict']), ('failure', 'BLOCKED'))
        self.assertIn('re-verification', run['output']['summary'])

    def test_pass_claimed_by_a_tampered_outcome_is_blocked(self):
        outputs = self.start()
        self.simulate_test_job(outputs, 'FAIL')
        with open(os.path.join(self.dir, 'out', 'outcome.json'), 'w') as file:
            json.dump({'verdict': 'PASS', 'reasons': [], 'result': RESULT}, file)
        self.finish(outputs)
        self.assertEqual(self.latest(SHA1)[1]['verdict'], 'BLOCKED')

    def test_failure_returns_reproduction_and_counts_a_round(self):
        run, marker = self.full_round('FAIL')
        self.assertEqual((run['conclusion'], marker['verdict'], marker['failed_rounds']), ('failure', 'FAIL', 1))
        self.assertIn('UAT-2', run['output']['title'])
        self.assertIn('Send invalid POST', run['output']['text'])
        self.assertIn('**Observed:** HTTP 500', run['output']['text'])

    def test_fixed_code_gets_fresh_uat_and_new_sha_invalidates_old_pass(self):
        self.full_round('FAIL')
        self.gh.push(9, SHA2)
        self.assertFalse(self.gh.required_check_passes(SHA2))
        run, marker = self.full_round('PASS')
        self.assertEqual((marker['sha'], marker['verdict'], marker['failed_rounds']), (SHA2, 'PASS', 1))
        self.gh.push(9, SHA3)
        self.assertFalse(self.gh.required_check_passes(SHA3), 'the PASS of an earlier head must not carry over')

    def test_failed_test_job_is_blocked(self):
        for result in ('failure', 'cancelled'):
            outputs = self.start()
            self.finish(outputs, test_result=result)
            run, marker = self.latest(SHA1)
            self.assertEqual((run['conclusion'], marker['verdict'], marker['failed_rounds']), ('failure', 'BLOCKED', 0))

    def test_same_key_is_not_run_twice(self):
        self.full_round('PASS')
        count = len(self.gh.check_runs)
        self.assertEqual(self.start()['run'], 'false')
        self.assertEqual(len(self.gh.check_runs), count)

    def test_failure_budget_survives_restarts_and_force_pushes(self):
        self.full_round('FAIL')
        self.gh.push(9, SHA2)
        self.full_round('FAIL')
        self.gh.push(9, SHA3, force=True)  # The old commits leave the PR; their check runs remain.
        run, marker = self.full_round('FAIL')
        self.assertEqual(marker['failed_rounds'], 3)
        self.gh.push(9, SHA4)
        self.assertEqual(self.start()['run'], 'false')
        run, marker = self.latest(SHA4)
        self.assertEqual((run['conclusion'], marker['verdict']), ('failure', 'BLOCKED'))
        self.assertIn('no failing rounds left', run['output']['title'])

    def test_head_change_during_the_run_is_stale(self):
        outputs = self.start()
        self.simulate_test_job(outputs, 'PASS')
        self.gh.push(9, SHA2)
        self.finish(outputs)
        self.assertEqual(self.latest(SHA1)[1]['verdict'], 'STALE')

    def test_spec_edit_during_the_run_is_stale(self):
        outputs = self.start()
        self.simulate_test_job(outputs, 'PASS')
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        self.finish(outputs)
        run, marker = self.latest(SHA1)
        self.assertEqual((run['conclusion'], marker['verdict']), ('failure', 'STALE'))
        self.assertIn('UAT spec', run['output']['summary'])

    def test_issue_edit_withdraws_an_earlier_pass(self):
        self.full_round('PASS')
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        controller.route(self.context('issues', {'issue': {'number': 7}, 'changes': {'body': {'from': ISSUE}}}))
        run, marker = self.latest(SHA1)
        self.assertEqual((run['conclusion'], marker['verdict']), ('failure', 'STALE'))
        self.assertFalse(self.gh.required_check_passes(SHA1))
        outputs = self.start(event_name='repository_dispatch', event={'client_payload': {'pr': 9}})
        self.assertEqual(outputs['run'], 'true')

    def test_issue_edit_outside_the_section_changes_nothing(self):
        self.full_round('PASS')
        count = len(self.gh.check_runs)
        self.gh.add_issue(7, ISSUE.replace('Implement feature X.', 'Implement feature X now.'))
        controller.route(self.context('issues', {'issue': {'number': 7}, 'changes': {'body': {'from': ISSUE}}}))
        self.assertEqual(len(self.gh.check_runs), count)
        self.assertTrue(self.gh.required_check_passes(SHA1))

    def test_policy_failures(self):
        cases = [
            (lambda: self.gh.add_pr(9, SHA1, closes=[]), 'FAIL', 'closes no issue'),
            (lambda: self.gh.add_pr(9, SHA1, closes=[7], files=['.github/workflows/ci.yml']), 'BLOCKED', 'GitHub Actions'),
            (lambda: self.gh.add_pr(9, SHA1, closes=[7], fork=True), 'BLOCKED', 'forks'),
            (lambda: self.gh.add_issue(7, ISSUE.replace('UAT-2', 'UAT-1')), 'BLOCKED', 'duplicate'),
            (lambda: (self.gh.add_issue(8, ISSUE), self.gh.add_pr(9, SHA1, closes=[7, 8])), 'BLOCKED', 'one issue with UAT'),
            (lambda: self.gh.add_issue(7, ISSUE.replace('Environment: local', 'Environment: preview')), 'BLOCKED',
             "'preview' is not enabled"),
            (lambda: self.gh.add_issue(7, ISSUE.replace('Environment: local', 'Environment: staging')), 'BLOCKED',
             "'staging' is not enabled"),
            (lambda: self.gh.contents.pop(('.superagent/uat.yaml', SHA1)), 'FAIL', 'not configured'),
            (lambda: self.gh.contents.update({('.superagent/uat.yaml', SHA1): 'version: 2\n'}), 'FAIL', 'invalid'),
        ]
        for change, verdict, reason in cases:
            with self.subTest(reason):
                self.setUp()
                change()
                self.assertEqual(self.start()['run'], 'false')
                run, marker = self.latest(SHA1)
                self.assertEqual((run['conclusion'], marker['verdict']), ('failure', verdict))
                self.assertIn(reason, run['output']['summary'])

    def test_staging_environment_enabled_by_the_workflow_still_needs_configuration(self):
        self.gh.add_issue(7, ISSUE.replace('Environment: local', 'Environment: staging'))
        self.start(UAT_ENVIRONMENTS='local staging')
        self.assertIn("'staging' is not configured", self.latest(SHA1)[0]['output']['summary'])

    def test_closed_pr_is_left_alone(self):
        self.gh.prs[9]['state'] = 'closed'
        self.assertEqual(self.start()['run'], 'false')
        self.assertEqual(self.gh.check_runs, [])


class SpoofTest(Gate):
    def test_coder_token_cannot_publish_the_check(self):
        from uat.github import GitHub, GitHubError
        coder = GitHub(fg.CODER_TOKEN, self.gh.url)
        coder.request('POST', f'repos/{self.gh.repository}/statuses/{SHA1}',
                      {'state': 'success', 'context': 'superagent/uat', 'description': 'UAT passed'})
        with self.assertRaises(GitHubError) as raised:
            coder.request('POST', f'repos/{self.gh.repository}/check-runs',
                          {'name': 'superagent/uat', 'head_sha': SHA1, 'status': 'completed', 'conclusion': 'success'})
        self.assertEqual(raised.exception.status, 403)
        self.assertFalse(self.gh.required_check_passes(SHA1))


class ClientTest(Gate):
    def client(self, sha=SHA1, timeout='20', background=None):
        config = os.path.join(self.dir, 'uat.json')
        with open(config, 'w') as file:
            json.dump({'app_id': fg.APP_ID}, file)
        environ = {**os.environ, 'UAT_TEST_GH': os.path.join(TESTS, 'fake_gh.py'), 'FAKE_GITHUB_URL': self.gh.url,
                   'FAKE_GITHUB_TOKEN': fg.CODER_TOKEN, 'UAT_TEST_CONFIG': config, 'HOME': self.dir,
                   'UAT_TEST_POLL_SECONDS': '0.1', 'UAT_TEST_GRACE_SECONDS': '0.5', 'UAT_TEST_TIMEOUT_SECONDS': timeout}
        if background:
            threading.Timer(0.5, background).start()
        return subprocess.run([sys.executable, CLIENT, self.gh.repository, '9', sha], capture_output=True, text=True,
                              env=environ, timeout=60)

    def test_exit_codes_follow_the_app_verdict(self):
        self.full_round('PASS')
        result = self.client()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'uat-test: PASS for {SHA1}', result.stdout)
        self.gh.push(9, SHA2)
        self.full_round('FAIL')
        result = self.client(SHA2)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Send invalid POST', result.stdout)
        self.assertIn('failing rounds used: 1 of 3', result.stdout)

    def test_not_required_and_blocked(self):
        self.gh.add_issue(7, 'No tests here.')
        self.start()
        self.assertEqual(self.client().returncode, 0)
        self.gh.add_issue(7, ISSUE.replace('UAT-2', 'UAT-1'))
        self.gh.push(9, SHA2)
        self.start()
        result = self.client(SHA2)
        self.assertEqual(result.returncode, 2)
        self.assertIn('duplicate scenario ids', result.stdout)

    def test_waits_for_a_running_check(self):
        outputs = self.start()

        def complete():
            self.simulate_test_job(outputs, 'PASS')
            self.finish(outputs)
        self.assertEqual(self.client(background=complete).returncode, 0)
        self.assertEqual(self.gh.dispatches, [])

    def test_spoofed_status_and_foreign_check_are_ignored(self):
        from uat.github import GitHub
        GitHub(fg.CODER_TOKEN, self.gh.url).request('POST', f'repos/{self.gh.repository}/statuses/{SHA1}',
                                                    {'state': 'success', 'context': 'superagent/uat'})
        self.gh.check_runs.append({'id': 1, 'head_sha': SHA1, 'name': 'superagent/uat', 'status': 'completed',
                                   'conclusion': 'success', 'app': {'id': 15368},
                                   'output': {'text': '<!-- superagent-uat {"verdict": "PASS", "sha": "%s"} -->' % SHA1}})
        result = self.client(timeout='3')
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.gh.dispatches, [{'event_type': 'superagent-uat', 'client_payload': {'pr': 9}}] * 2)

    def test_stale_result_requests_a_new_run(self):
        self.full_round('PASS')
        self.gh.add_issue(7, ISSUE.replace('returns 400', 'returns 422'))
        controller.route(self.context('issues', {'issue': {'number': 7}, 'changes': {'body': {'from': ISSUE}}}))

        def rerun():
            while not self.gh.dispatches:
                time.sleep(0.05)
            self.full_round('FAIL')
        result = self.client(background=rerun)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(self.gh.dispatches), 1)

    def test_wrong_head_is_refused(self):
        result = self.client(SHA2)
        self.assertEqual(result.returncode, 3)
        self.assertIn('the PR head is', result.stderr)


if __name__ == '__main__':
    unittest.main()
