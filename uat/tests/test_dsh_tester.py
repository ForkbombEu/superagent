"""Real DSH headless runs (the pinned tester image) against a scripted model and a live sample app."""
import json
import os
import shutil
import tempfile
import unittest

from uat import run, spec
from uat.tests import docker_support as ds
from uat.tests.test_spec import ISSUE

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'app')
CREATE = ("curl -sS -i -X POST -H 'content-type: application/json' -d '{\"name\":\"alice\"}' "
          "http://127.0.0.1:3000/records | tee artifacts/uat-1-create.txt && "
          "curl -sS -i http://127.0.0.1:3000/records/1 | tee artifacts/uat-1-get.txt")
INVALID = ("curl -sS -i -X POST -H 'content-type: application/json' -d '{}' http://127.0.0.1:3000/records "
           "| tee artifacts/uat-2-invalid.txt")


class DshTesterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        ds.build(ds.TESTER_IMAGE, 'tester/Dockerfile')
        cls.prefix = f'uat-dsh-test-{os.getpid()}'
        cls.dir = tempfile.mkdtemp()
        os.chmod(cls.dir, 0o755)
        cls.app_dir = os.path.join(cls.dir, 'app')
        shutil.copytree(FIXTURE, cls.app_dir)
        shutil.copy(os.path.join(os.path.dirname(FIXTURE), '..', 'mock_model.py'), cls.dir)
        cls.script = os.path.join(cls.dir, 'script.json')
        p = cls.prefix
        try:
            ds.docker('network', 'create', '--internal', f'{p}-internal')
            ds.docker('network', 'create', f'{p}-egress')
            ds.docker('run', '-d', '--name', f'{p}-mock', '--network', f'{p}-internal', '-v', f'{cls.dir}:/m:ro',
                      ds.TESTER_IMAGE, 'python3', '/m/mock_model.py', '8080', '/m/script.json')
            ds.docker('run', '-d', '--name', f'{p}-proxy', '--network', f'{p}-egress', ds.TESTER_IMAGE, 'python3',
                      '/usr/local/lib/superagent-uat/proxy.py', '--allow', f'{p}-mock:8080')
            ds.docker('network', 'connect', f'{p}-internal', f'{p}-proxy')
            ds.docker('run', '-d', '--name', f'{p}-app', '--network', f'{p}-internal', '-v', f'{cls.app_dir}:/app:ro',
                      ds.TESTER_IMAGE, 'python3', '/app/server.py', '3000')
        except Exception:
            cls.tearDownClass()
            raise
        cls.spec = spec.parse(ISSUE)
        cls.meta = {'repository': 'ForkbombEu/sample', 'issue_number': 7, 'pr_number': 9, 'head_sha': 'a' * 40,
                    'uat_spec_sha256': spec.spec_hash(cls.spec, 'local'), 'environment': 'local',
                    'environment_revision': 'a' * 40}

    @classmethod
    def tearDownClass(cls):
        for name in ('mock', 'proxy', 'app'):
            ds.docker('rm', '-f', f'{cls.prefix}-{name}', check=False)
        for name in ('internal', 'egress'):
            ds.docker('network', 'rm', f'{cls.prefix}-{name}', check=False)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.work = tempfile.mkdtemp(dir=self.dir)
        force = os.path.join(self.app_dir, 'force-500')
        if os.path.exists(force):
            os.remove(force)

    def result(self, verdict, uat1='PASS', uat2='PASS', observed2='HTTP 400, no record'):
        second = {'id': 'UAT-2', 'verdict': uat2, 'expected': 'HTTP 400 and no record', 'observed': observed2,
                  'evidence': ['artifacts/uat-2-invalid.txt']}
        if uat2 == 'FAIL':
            second['reproduction'] = [INVALID]
        return json.dumps({'schema_version': 1, **self.meta, 'verdict': verdict, 'scenarios': [
            {'id': 'UAT-1', 'verdict': uat1, 'expected': 'HTTP 201 and record retrievable', 'observed': '201, 200',
             'evidence': ['artifacts/uat-1-create.txt', 'artifacts/uat-1-get.txt']}, second]})

    def run_script(self, steps, timeout=120):
        with open(self.script, 'w') as file:
            json.dump({'steps': steps}, file)
        environment = {**os.environ, 'DEEPSEEK_API_KEY': 'sk-test', 'DEEPSEEK_BASE_URL': f'http://{self.prefix}-mock:8080'}
        run_dir = tempfile.mkdtemp(dir=self.work)
        out = os.path.join(run_dir, 'out')
        os.makedirs(out)
        outcome, work_dir = run.run(self.meta, self.spec, image=ds.TESTER_IMAGE, network=f'container:{self.prefix}-app',
                                    proxy=f'{self.prefix}-proxy:3128', work_root=os.path.join(run_dir, 'w'),
                                    out_dir=out, base_url='http://127.0.0.1:3000', timeout=timeout,
                                    environment=environment, name_prefix=f'{self.prefix}-{self.id().rsplit(".", 1)[1]}')
        with open(os.path.join(out, 'attempts.json')) as file:
            return outcome, work_dir, json.load(file)

    def test_valid_pass_executes_against_the_app(self):
        outcome, work_dir, attempts = self.run_script([{'bash': CREATE}, {'bash': INVALID}, {'final': self.result('PASS')}])
        self.assertEqual((outcome.verdict, outcome.reasons), ('PASS', []))
        with open(os.path.join(work_dir, 'artifacts', 'uat-1-create.txt')) as file:
            self.assertIn('201 Created', file.read())
        with open(os.path.join(work_dir, 'artifacts', 'uat-2-invalid.txt')) as file:
            self.assertIn('400 Bad Request', file.read())
        self.assertEqual(len(attempts), 1)

    def test_expected_fail_from_a_real_500(self):
        open(os.path.join(self.app_dir, 'force-500'), 'w').close()
        outcome, work_dir, _ = self.run_script([{'bash': CREATE}, {'bash': INVALID},
                                                {'final': self.result('FAIL', uat1='FAIL', observed2='HTTP 500')}])
        # UAT-1 says FAIL without reproduction steps: an unsupported failure is not trusted either.
        self.assertEqual(outcome.verdict, 'BLOCKED')
        with open(os.path.join(work_dir, 'artifacts', 'uat-1-create.txt')) as file:
            self.assertIn('500 Internal Server Error', file.read())
        outcome, _, _ = self.run_script([{'bash': CREATE}, {'bash': INVALID},
                                         {'final': self.result('FAIL', uat2='FAIL', observed2='HTTP 500')}])
        self.assertEqual(outcome.verdict, 'FAIL', outcome.reasons)

    def test_hallucinated_pass_without_commands(self):
        outcome, _, _ = self.run_script([{'final': self.result('PASS')}])
        self.assertEqual(outcome.verdict, 'BLOCKED')
        self.assertIn('the tester executed no command', outcome.reasons)

    def test_malformed_answer(self):
        outcome, _, _ = self.run_script([{'bash': CREATE}, {'final': 'Both scenarios pass.'}])
        self.assertEqual(outcome.verdict, 'BLOCKED')
        self.assertRegex(outcome.reasons[0], 'not a result object')

    def test_timeout_stops_the_container(self):
        outcome, _, attempts = self.run_script([{'sleep': 30, 'final': self.result('PASS')}], timeout=8)
        self.assertEqual(outcome.verdict, 'BLOCKED')
        self.assertIn('time limit', outcome.reasons[0])
        self.assertEqual(len(attempts), 1)
        self.assertEqual([name for name in ds.leftovers(self.prefix) if 'tester' in name], [])

    def test_transient_model_failure_is_retried_once(self):
        outcome, _, attempts = self.run_script([{'status': 500}], timeout=180)
        self.assertEqual(outcome.verdict, 'BLOCKED')
        self.assertTrue(outcome.transient)
        self.assertEqual(len(attempts), 2)
        self.assertRegex(attempts[0]['reasons'][0], r'\(SERVER\)')


if __name__ == '__main__':
    unittest.main()
