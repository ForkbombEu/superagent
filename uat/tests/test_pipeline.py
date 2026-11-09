"""The whole workflow on one machine: start, the real test job (checkout, images, sandbox, DSH) and finish."""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from uat import controller
from uat.tests import docker_support as ds
from uat.tests import fake_github as fg
from uat.tests.test_dsh_tester import INVALID
from uat.tests.test_gate import CONFIG, Gate
from uat.tests.test_sandbox import FIXTURE, TESTS, result


class PipelineTest(Gate):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        cls.models = f'uat-pipeline-test-{os.getpid()}'
        cls.model_dir = tempfile.mkdtemp()
        os.chmod(cls.model_dir, 0o755)
        shutil.copy(os.path.join(TESTS, 'mock_model.py'), cls.model_dir)
        ds.build(ds.TESTER_IMAGE, 'tester/Dockerfile')
        ds.docker('network', 'create', '--internal', cls.models)
        ds.docker('run', '-d', '--name', f'{cls.models}-mock', '--network', cls.models, '-v', f'{cls.model_dir}:/m:ro',
                  ds.TESTER_IMAGE, 'python3', '/m/mock_model.py', '8080', '/m/script.json')

    @classmethod
    def tearDownClass(cls):
        ds.docker('rm', '-f', f'{cls.models}-mock', check=False)
        ds.docker('network', 'rm', cls.models, check=False)
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
        self.run_id = f'{os.getpid()}{len(os.listdir(self.dir))}'

    def tearDown(self):
        for kind in ('tester', 'sandbox'):
            ds.docker('rmi', f'superagent-uat-{kind}:{self.run_id}', check=False)

    def run_test_job(self, outputs, steps):
        snapshot = json.loads(base64.b64decode(outputs['snapshot']))
        final = steps[-1].get('final')
        if final == 'RESULT':
            steps[-1]['final'] = result(snapshot['meta'], 'PASS')
        with open(os.path.join(self.model_dir, 'script.json'), 'w') as file:
            json.dump({'steps': steps}, file)
        ctx = self.context(UAT_SNAPSHOT=outputs['snapshot'], GITHUB_SERVER_URL=f'file://{self.server}',
                           GITHUB_RUN_ID=self.run_id, DEEPSEEK_API_KEY='sk-pipeline-secret-key',
                           DEEPSEEK_BASE_URL=f'http://{self.models}-mock:8080', UAT_PROXY_NETWORKS=self.models,
                           UAT_TESTER_TIMEOUT_MINUTES='3')
        controller.test(ctx)
        self.assertEqual(ds.leftovers(f'superagent-uat-{self.run_id}-'), [])
        with open(os.path.join(ctx.out_dir, 'outcome.json')) as file:
            return json.load(file)

    def test_pass_goes_from_a_real_run_to_a_passing_check(self):
        outputs = self.start()
        create = ("curl -sS -i -X POST -H 'content-type: application/json' -d '{\"name\":\"alice\"}' "
                  "http://127.0.0.1:3000/records | tee artifacts/uat-1-create.txt && "
                  "curl -sS -i http://127.0.0.1:3000/records/1 | tee artifacts/uat-1-get.txt")
        steps = [{'bash': create}, {'bash': INVALID}, {'bash': 'cp /app-logs/app.log artifacts/app.log'},
                 {'final': 'RESULT'}]
        outcome = self.run_test_job(outputs, steps)
        self.assertEqual((outcome['verdict'], outcome['reasons']), ('PASS', []))
        self.finish(outputs)
        run, marker = self.latest(self.sha)
        self.assertEqual((run['conclusion'], marker['verdict']), ('success', 'PASS'), run['output'])
        self.assertTrue(self.gh.required_check_passes(self.sha))

    def test_hallucinated_pass_never_reaches_the_check(self):
        outputs = self.start()
        outcome = self.run_test_job(outputs, [{'final': 'RESULT'}])
        self.assertEqual(outcome['verdict'], 'BLOCKED')
        self.finish(outputs)
        run, marker = self.latest(self.sha)
        self.assertEqual((run['conclusion'], marker['verdict']), ('failure', 'BLOCKED'))
        self.assertIn('executed no command', run['output']['summary'])
        self.assertFalse(self.gh.required_check_passes(self.sha))


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
