"""The gate end to end on one machine: request, the real runner (checkout, images, sandbox, DSH), the check.

The local Docker daemon stands in for uat-runner; a scripted model and the
sample application stand in for DeepSeek and the pull request.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from uat import controller, gate
from uat.tests import docker_support as ds
from uat.tests.test_dsh_tester import INVALID
from uat.tests.test_gate import CONFIG, Gate
from uat.tests.test_sandbox import FIXTURE, TESTS, result
from uat.tests.test_spec import ISSUE

PREVIEW_ISSUE = ISSUE.replace('Environment: local', 'Environment: preview')
CREATE = ("curl -sS -i -X POST -H 'content-type: application/json' -d '{\"name\":\"alice\"}' "
          "{origin}/records | tee artifacts/uat-1-create.txt && curl -sS -i {origin}/records/1 | tee artifacts/uat-1-get.txt")


class RunnerGate(Gate):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        cls.network = f'uat-pipeline-test-{os.getpid()}'
        cls.model_dir = tempfile.mkdtemp()
        os.chmod(cls.model_dir, 0o755)
        shutil.copy(os.path.join(TESTS, 'mock_model.py'), cls.model_dir)
        ds.build(ds.TESTER_IMAGE, 'tester/Dockerfile')
        ds.docker('network', 'create', cls.network)
        ds.docker('run', '-d', '--name', f'{cls.network}-mock', '--network', cls.network, '-v', f'{cls.model_dir}:/m:ro',
                  ds.TESTER_IMAGE, 'python3', '/m/mock_model.py', '8080', '/m/script.json')

    @classmethod
    def tearDownClass(cls):
        for name in ('mock', 'preview'):
            ds.docker('rm', '-f', f'{cls.network}-{name}', check=False)
        ds.docker('network', 'rm', cls.network, check=False)
        for kind in ('tester', 'sandbox'):
            ds.docker('rmi', f'superagent-uat-{kind}:gate', check=False)
        shutil.rmtree(cls.model_dir, ignore_errors=True)

    def setUp(self):
        super().setUp()
        # The sample application as the pull request's repository, served to `git fetch` from disk.
        self.server = tempfile.mkdtemp(dir=self.dir)
        origin = os.path.join(self.server, 'ForkbombEu', 'sample')
        shutil.copytree(FIXTURE, origin)
        git = lambda *args: subprocess.run(['git', *args], cwd=origin, check=True, capture_output=True, text=True).stdout
        git('init', '-q')
        git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'feat: records api')
        git('config', 'uploadpack.allowAnySHA1InWant', 'true')
        self.sha = git('rev-parse', 'HEAD').strip()
        os.rename(os.path.join(origin, '.git'), origin + '.git')
        self.gh.add_pr(9, self.sha, closes=[7])
        self.gh.contents[('.superagent/uat.yaml', self.sha)] = CONFIG

    def run_gate(self, steps, **policy):
        """Request a run of the head, script the model with the run's own metadata, run it; (run, kept files)."""
        self.gate = self.make_gate(
            executor=gate.Runner(lambda: 'ghs_checkout_token_123456', f'file://{self.server}'),
            DEEPSEEK_API_KEY='sk-pipeline-secret-key', DEEPSEEK_BASE_URL=f'http://{self.network}-mock:8080',
            SUPERAGENT_UAT_PROXY_NETWORKS=self.network, SUPERAGENT_UAT_TESTER_TIMEOUT_MINUTES='3', **policy)
        run = self.request()
        self.assertEqual(run['state'], 'QUEUED', run)
        meta = json.loads(self.gate.one('SELECT snapshot FROM runs WHERE id = ?', run['run_id'])['snapshot'])['meta']
        if steps[-1].get('final') == 'RESULT':
            steps[-1]['final'] = result(meta, 'PASS').replace(
                'HTTP 400, no record', 'HTTP 400 for credentials long-enough-preview-secret')
        with open(os.path.join(self.model_dir, 'script.json'), 'w') as file:
            json.dump({'steps': steps}, file)
        self.drain()
        self.assertEqual(ds.leftovers(f'superagent-uat-{run["run_id"]}-'), [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'work', run['run_id'])))
        run = self.gate.view(self.gate.one('SELECT * FROM runs WHERE id = ?', run['run_id']))
        return run, os.path.join(self.state, 'runs', run['run_id'])


class LocalPipelineTest(RunnerGate):
    def test_pass_goes_from_a_real_run_to_a_passing_app_check(self):
        steps = [{'bash': CREATE.replace('{origin}', 'http://127.0.0.1:3000')}, {'bash': INVALID},
                 {'bash': 'cp /app-logs/app.log artifacts/app.log'}, {'final': 'RESULT'}]
        run, kept = self.run_gate(steps)
        self.assertEqual(run['verdict'], 'PASS', run)
        self.assertTrue(self.gh.required_check_passes(self.sha))
        with open(os.path.join(kept, 'tester-work', 'attempt-1', 'artifacts', 'uat-1-create.txt')) as file:
            self.assertIn('201 Created', file.read())
        self.assertTrue(self.gate.verify(self.gh.repository, 9, self.sha)['current'])

    def test_hallucinated_pass_never_reaches_the_check(self):
        run, _ = self.run_gate([{'final': 'RESULT'}])
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertIn('executed no command', run['summary'])
        self.assertFalse(self.gh.required_check_passes(self.sha))

    def test_secrets_never_reach_the_kept_files(self):
        steps = [{'bash': 'echo "$DEEPSEEK_API_KEY $GH_TOKEN" > artifacts/env.txt; cat /proc/1/environ > artifacts/p1 2>&1; '
                          'true'}, {'final': 'RESULT'}]
        run, kept = self.run_gate(steps)
        for root, _, names in os.walk(kept):
            for name in names:
                with open(os.path.join(root, name), 'rb') as file:
                    data = file.read()
                self.assertNotIn(b'sk-pipeline-secret-key', data, name)
                self.assertNotIn(b'ghs_checkout_token_123456', data, name)


class PreviewPipelineTest(RunnerGate):
    def setUp(self):
        super().setUp()
        self.gh.add_issue(7, PREVIEW_ISSUE)

    def tearDown(self):
        ds.docker('rm', '-f', f'{self.network}-preview', check=False)

    def deploy(self, revision):
        ds.docker('run', '-d', '--name', f'{self.network}-preview', '--network', self.network, '-e',
                  f'APP_REVISION={revision}', '-v', f'{FIXTURE}:/app:ro', ds.TESTER_IMAGE, 'python3', '/app/server.py',
                  '3000', '0.0.0.0')
        address = ds.docker('inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}',
                            f'{self.network}-preview').stdout.strip()
        return {'SUPERAGENT_UAT_ENVIRONMENTS': 'preview', 'SUPERAGENT_UAT_PREVIEW_URL': f'http://{address}:3000',
                'SUPERAGENT_UAT_PREVIEW_ALLOWED_HOSTS': address, 'SUPERAGENT_UAT_PREVIEW_REVISION_PATH': '/revision',
                'SUPERAGENT_UAT_PREVIEW_READY_TIMEOUT_SECONDS': '30',
                'SUPERAGENT_UAT_PREVIEW_CREDENTIALS': '{"UAT_PASSWORD": "long-enough-preview-secret"}'}

    def test_current_preview_passes_and_credentials_never_leave(self):
        policy = self.deploy(self.sha)
        origin = policy['SUPERAGENT_UAT_PREVIEW_URL']
        steps = [{'bash': CREATE.replace('{origin}', origin)}, {'bash': INVALID.replace('http://127.0.0.1:3000', origin)},
                 {'bash': 'cp /uat-credentials.json artifacts/app.log && cat /uat-credentials.json'}, {'final': 'RESULT'}]
        run, kept = self.run_gate(steps, **policy)
        self.assertEqual(run['verdict'], 'PASS', run)
        for root, _, names in os.walk(kept):
            for name in names:
                with open(os.path.join(root, name), 'rb') as file:
                    self.assertNotIn(b'long-enough-preview-secret', file.read(), os.path.join(root, name))
        check, marker = self.latest(self.sha)
        self.assertEqual((check['conclusion'], marker['verdict']), ('success', 'PASS'))
        self.assertNotIn('long-enough-preview-secret', json.dumps(check) + json.dumps(run))
        self.assertIn('[REDACTED]', check['output']['summary'])

    def test_stale_preview_is_blocked_before_any_test(self):
        run, kept = self.run_gate([{'final': 'RESULT'}], **self.deploy('d' * 40))
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertIn('stale', run['summary'])
        self.assertFalse(os.path.exists(os.path.join(kept, 'tester')))
        self.assertFalse(self.gh.required_check_passes(self.sha))

    def test_unavailable_preview_is_blocked(self):
        policy = self.deploy(self.sha)
        ds.docker('rm', '-f', f'{self.network}-preview')
        run, _ = self.run_gate([{'final': 'RESULT'}], **{**policy, 'SUPERAGENT_UAT_PREVIEW_READY_TIMEOUT_SECONDS': '3'})
        self.assertEqual(run['verdict'], 'BLOCKED')
        self.assertIn('unavailable', run['summary'])


class RedactionTest(unittest.TestCase):
    def test_secret_values_are_replaced_in_every_file(self):
        directory = tempfile.mkdtemp()
        os.makedirs(os.path.join(directory, 'a'))
        with open(os.path.join(directory, 'a', 'log.txt'), 'w') as file:
            file.write('key=sk-pipeline-secret-key token=ghs_readtoken123 short=abc')
        controller.redact_tree(directory, ['sk-pipeline-secret-key', 'ghs_readtoken123', 'abc', ''])
        with open(os.path.join(directory, 'a', 'log.txt')) as file:
            self.assertEqual(file.read(), 'key=[REDACTED] token=[REDACTED] short=abc')
        shutil.rmtree(directory)
        self.assertEqual(controller.clean('use ghp_' + 'a' * 36 + ' now', 100), 'use [REDACTED] now')


if __name__ == '__main__':
    unittest.main()
