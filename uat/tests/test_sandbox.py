"""The local sandbox: configuration, then real containers, DSH and the sample application."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from uat import sandbox, spec
from uat.tests import docker_support as ds
from uat.tests.test_dsh_tester import CREATE, INVALID
from uat.tests.test_spec import ISSUE

TESTS = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(TESTS, 'fixtures', 'app')
REPOSITORY = os.path.dirname(os.path.dirname(TESTS))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.checkout = tempfile.mkdtemp()
        shutil.copytree(FIXTURE, self.checkout, dirs_exist_ok=True)
        self.path = os.path.join(self.checkout, sandbox.CONFIG_PATH)

    def tearDown(self):
        shutil.rmtree(self.checkout)

    def write(self, text):
        with open(self.path, 'w') as file:
            file.write(text)

    def test_valid_config(self):
        config, problem = sandbox.load_config(self.checkout)
        self.assertIsNone(problem)
        self.assertEqual(sandbox.environment_entry('local', config)['ready'], 'http://127.0.0.1:3000/health')
        self.assertEqual(sandbox.environment_name(None, config), 'local')
        self.assertEqual(sandbox.environment_name('preview', config), 'preview')
        self.assertEqual(sandbox.environment_entry('preview', config), {'type': 'preview'})
        self.assertIsNone(sandbox.environment_entry('staging', config))

    def test_absent_config(self):
        os.remove(self.path)
        self.assertEqual(sandbox.load_config(self.checkout), (None, None))
        self.assertEqual(sandbox.environment_name(None, None), 'local')
        self.assertIsNone(sandbox.environment_entry('local', None))

    def test_invalid_configs(self):
        for text, reason in (('version: 1\nenvironments: {}\n', 'invalid'),
                             ('version: 1\nenvironments:\n  local: {type: local, start: x, ready: "http://evil:1/"}\n', 'invalid'),
                             ('version: 1\ndefault_environment: staging\nenvironments:\n  preview: {type: preview}\n', 'default_environment'),
                             ('[unclosed', 'not valid YAML'),
                             ('!!python/object/apply:os.system ["true"]', 'not valid YAML')):
            with self.subTest(text):
                self.write(text)
                config, problem = sandbox.load_config(self.checkout)
                self.assertIsNone(config)
                self.assertIn(reason, problem)

    def test_symlinked_config_is_refused(self):
        os.remove(self.path)
        os.symlink('/etc/passwd', self.path)
        self.assertIn('symbolic link', sandbox.load_config(self.checkout)[1])

    def test_prune_keeps_artifacts_under_the_limit(self):
        directory = tempfile.mkdtemp()
        for name, size in (('big', 600), ('mid', 300), ('small', 100)):
            with open(os.path.join(directory, name), 'wb') as file:
                file.write(b'x' * size)
        os.symlink('/etc/passwd', os.path.join(directory, 'link'))
        sandbox.prune(directory, limit=500)
        self.assertEqual(sorted(os.listdir(directory)), ['mid', 'small'])
        shutil.rmtree(directory)


class CheckoutTest(unittest.TestCase):
    def test_checkout_is_exactly_the_sha_without_credentials_or_hooks(self):
        root = tempfile.mkdtemp()
        origin = os.path.join(root, 'ForkbombEu', 'sample')
        git = lambda *args, cwd=origin: subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)
        os.makedirs(origin)
        git('init', '-q')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'one')
        first = git('rev-parse', 'HEAD').stdout.strip()
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'two')
        git('config', 'uploadpack.allowAnySHA1InWant', 'true')
        os.rename(os.path.join(origin, '.git'), origin + '.git')
        destination = os.path.join(root, 'checkout')
        previous = os.environ.get('GITHUB_SERVER_URL')
        os.environ['GITHUB_SERVER_URL'] = f'file://{root}'
        try:
            sandbox.checkout('ForkbombEu/sample', first, destination, token='ghs_secret')
            with self.assertRaises(subprocess.CalledProcessError):
                sandbox.checkout('ForkbombEu/sample', 'f' * 40, os.path.join(root, 'missing'))
        finally:
            os.environ.pop('GITHUB_SERVER_URL') if previous is None else os.environ.update(GITHUB_SERVER_URL=previous)
        self.assertEqual(git('rev-parse', 'HEAD', cwd=destination).stdout.strip(), first)
        with open(os.path.join(destination, '.git', 'config')) as file:
            self.assertNotIn('ghs_secret', file.read())
        self.assertFalse(os.path.exists(os.path.join(destination, '.git', 'hooks')))
        shutil.rmtree(root)


def result(meta, verdict, uat2='PASS', observed2='HTTP 400, no record'):
    second = {'id': 'UAT-2', 'verdict': uat2, 'expected': 'HTTP 400 and no record', 'observed': observed2,
              'evidence': ['artifacts/uat-2-invalid.txt']}
    if uat2 == 'FAIL':
        second['reproduction'] = [INVALID]
    return json.dumps({'schema_version': 1, **meta, 'verdict': verdict, 'scenarios': [
        {'id': 'UAT-1', 'verdict': 'PASS', 'expected': 'HTTP 201 and record retrievable', 'observed': '201, 200',
         'evidence': ['artifacts/uat-1-create.txt', 'artifacts/uat-1-get.txt', 'artifacts/app.log']}, second]})


def meta(pr):
    parsed = spec.parse(ISSUE)
    return {'repository': 'ForkbombEu/sample', 'issue_number': 7, 'pr_number': pr, 'head_sha': 'a' * 40,
            'uat_spec_sha256': spec.spec_hash(parsed, 'local'), 'environment': 'local', 'environment_revision': 'a' * 40}


class LocalSandboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        ds.build(ds.TESTER_IMAGE, 'tester/Dockerfile')
        ds.build(ds.SANDBOX_IMAGE, 'sandbox/Dockerfile')
        cls.prefix = f'uat-sbx-test-{os.getpid()}'
        cls.dir = tempfile.mkdtemp()
        os.chmod(cls.dir, 0o755)
        shutil.copy(os.path.join(TESTS, 'mock_model.py'), cls.dir)
        cls.script_path = os.path.join(cls.dir, 'script.json')
        cls.models = f'{cls.prefix}-models'
        ds.docker('network', 'create', '--internal', cls.models)
        ds.docker('run', '-d', '--name', f'{cls.models}-mock', '--network', cls.models, '-v', f'{cls.dir}:/m:ro',
                  ds.TESTER_IMAGE, 'python3', '/m/mock_model.py', '8080', '/m/script.json')
        cls.environment = {**os.environ, 'DEEPSEEK_API_KEY': 'sk-uat-test-key',
                           'DEEPSEEK_BASE_URL': f'http://{cls.models}-mock:8080',
                           'GH_TOKEN': 'ghp_controller_secret', 'SUPERAGENT_UAT_APP_PRIVATE_KEY': 'app-private-key'}
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        ds.docker('rm', '-f', f'{cls.models}-mock', check=False)
        ds.docker('network', 'rm', cls.models, check=False)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def settings(self, **overrides):
        type(self).counter += 1
        return sandbox.Settings(tester_image=ds.TESTER_IMAGE, sandbox_image=ds.SANDBOX_IMAGE,
                                prefix=f'{self.prefix}-{self.counter}', allow=(f'{self.models}-mock:8080',),
                                environment=self.environment, proxy_networks=(self.models,), tester_timeout=120,
                                **overrides)

    def checkout(self, force_500=False, config=None):
        directory = tempfile.mkdtemp(dir=self.dir)
        shutil.copytree(FIXTURE, directory, dirs_exist_ok=True)
        if force_500:
            open(os.path.join(directory, 'force-500'), 'w').close()
        if config:
            with open(os.path.join(directory, sandbox.CONFIG_PATH), 'w') as file:
                file.write(config)
        return directory

    def script(self, steps=None, by_pr=None):
        with open(self.script_path, 'w') as file:
            json.dump({'by_pr': by_pr} if by_pr else {'steps': steps}, file)

    def run_local(self, checkout, pr=9, settings=None):
        settings = settings or self.settings()
        config, problem = sandbox.load_config(checkout)
        self.assertIsNone(problem)
        out = tempfile.mkdtemp(dir=self.dir)
        outcome = sandbox.run_local(meta(pr), spec.parse(ISSUE), checkout, sandbox.environment_entry('local', config),
                                    settings, out)
        self.assertEqual(ds.leftovers(settings.prefix + '-'), [])
        return outcome, out

    def passing_steps(self, pr=9):
        return [{'bash': CREATE}, {'bash': INVALID}, {'bash': 'cp /app-logs/app.log artifacts/app.log'},
                {'final': result(meta(pr), 'PASS')}]

    def test_happy_path_against_the_real_api(self):
        self.script(self.passing_steps())
        checkout = self.checkout()
        outcome, out = self.run_local(checkout)
        self.assertEqual((outcome.verdict, outcome.reasons), ('PASS', []))
        self.assertTrue(os.path.exists(os.path.join(checkout, 'setup.marker')))
        self.assertTrue(os.path.exists(os.path.join(out, 'app-logs', 'cleanup.marker')))
        with open(os.path.join(out, 'tester-work', 'attempt-1', 'artifacts', 'app.log')) as file:
            self.assertIn('POST /records -> 201', file.read())
        with open(os.path.join(out, 'proxy.log')) as file:
            self.assertIn(f'ALLOW POST {self.models}-mock:8080', file.read())

    def test_negative_scenario_against_a_real_500(self):
        steps = self.passing_steps()
        steps[-1] = {'final': result(meta(9), 'FAIL', uat2='FAIL', observed2='HTTP 500')}
        self.script(steps)
        outcome, out = self.run_local(self.checkout(force_500=True))
        self.assertEqual(outcome.verdict, 'FAIL', outcome.reasons)
        with open(os.path.join(out, 'tester-work', 'attempt-1', 'artifacts', 'uat-2-invalid.txt')) as file:
            self.assertIn('500 Internal Server Error', file.read())

    def test_isolation_of_credentials_network_and_docker(self):
        probe = ('env > artifacts/tester-env.txt; '
                 'curl -sS -m 10 -o /dev/null -w "%{http_code}" https://api.github.com > artifacts/github.txt 2>&1; '
                 'ls -la /var/run/docker.sock > artifacts/socket.txt 2>&1; true')
        self.script([{'bash': probe}, {'final': '{}'}])
        config = ('version: 1\nenvironments:\n  local:\n    type: local\n    start: >-\n'
                  '      env > /uat-logs/app-env.txt;\n'
                  '      (curl -sS -m 10 https://example.com > /uat-logs/app-egress.txt 2>&1 || echo blocked >> /uat-logs/app-egress.txt);\n'
                  '      (ls /var/run/docker.sock >> /uat-logs/app-egress.txt 2>&1 || echo no-socket >> /uat-logs/app-egress.txt);\n'
                  '      exec python3 server.py 3000\n'
                  '    ready: http://127.0.0.1:3000/health\n')
        outcome, out = self.run_local(self.checkout(config=config))
        self.assertEqual(outcome.verdict, 'BLOCKED')
        artifacts = os.path.join(out, 'tester-work', 'attempt-1', 'artifacts')
        with open(os.path.join(artifacts, 'tester-env.txt')) as file:
            tester_env = file.read()
        # DSH strips *_KEY/*_TOKEN variables from the tester's shell; only the dsh process holds the key.
        self.assertNotIn('sk-uat-test-key', tester_env)
        self.assertIn('HTTPS_PROXY=', tester_env)
        for secret in ('ghp_controller_secret', 'app-private-key', 'GH_TOKEN', 'GITHUB_TOKEN'):
            self.assertNotIn(secret, tester_env)
        with open(os.path.join(artifacts, 'github.txt')) as file:
            self.assertNotIn('200', file.read())
        with open(os.path.join(artifacts, 'socket.txt')) as file:
            self.assertIn('No such file', file.read())
        with open(os.path.join(out, 'app-logs', 'app-env.txt')) as file:
            app_env = file.read()
        for secret in ('sk-uat-test-key', 'DEEPSEEK', 'ghp_controller_secret', 'app-private-key'):
            self.assertNotIn(secret, app_env)
        with open(os.path.join(out, 'app-logs', 'app-egress.txt')) as file:
            egress = file.read()
        self.assertIn('blocked', egress)
        self.assertIn('no-socket', egress)
        with open(os.path.join(out, 'proxy.log')) as file:
            self.assertIn('DENY CONNECT api.github.com:443', file.read())

    def test_simultaneous_pull_requests_stay_separate(self):
        failing = self.passing_steps(10)
        failing[-1] = {'final': result(meta(10), 'FAIL', uat2='FAIL', observed2='HTTP 500')}
        self.script(by_pr={'9': self.passing_steps(9), '10': failing})
        outcomes = {}
        runs = {9: (self.checkout(), self.settings()), 10: (self.checkout(force_500=True), self.settings())}

        def test(pr):
            outcomes[pr] = self.run_local(runs[pr][0], pr=pr, settings=runs[pr][1])[0]
        threads = [threading.Thread(target=test, args=(pr,)) for pr in runs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes[9].verdict, 'PASS', outcomes[9].reasons)
        self.assertEqual(outcomes[10].verdict, 'FAIL', outcomes[10].reasons)

    def test_setup_failure_fails_with_its_log(self):
        config = ('version: 1\nenvironments:\n  local:\n    type: local\n    setup: echo dependency missing; exit 3\n'
                  '    start: python3 server.py 3000\n    ready: http://127.0.0.1:3000/health\n')
        outcome, _ = self.run_local(self.checkout(config=config))
        self.assertEqual(outcome.verdict, 'FAIL')
        self.assertRegex(outcome.reasons[0], r'exited with status 3(.|\n)*dependency missing')

    def test_application_that_never_gets_ready_fails(self):
        config = ('version: 1\nenvironments:\n  local:\n    type: local\n    start: echo starting; sleep 600\n'
                  '    ready: http://127.0.0.1:3000/health\n')
        outcome, _ = self.run_local(self.checkout(config=config), settings=self.settings(ready_timeout=6))
        self.assertEqual(outcome.verdict, 'FAIL')
        self.assertRegex(outcome.reasons[0], r'did not answer within 6 s(.|\n)*starting')
        config = config.replace('sleep 600', 'exit 4')
        outcome, _ = self.run_local(self.checkout(config=config))
        self.assertRegex(outcome.reasons[0], 'exited with status 4')

    def test_cancellation_removes_everything(self):
        self.script([{'sleep': 120, 'final': '{}'}])
        settings = self.settings()
        checkout, out = self.checkout(), tempfile.mkdtemp(dir=self.dir)
        code = f'''
import os, sys
sys.path.insert(0, {REPOSITORY!r})
from uat import sandbox, spec
from uat.tests.test_sandbox import meta, ISSUE
sandbox.exit_on_signals()
settings = sandbox.Settings(**{json.dumps({key: value for key, value in vars(settings).items() if key != 'environment'})})
settings.environment = dict(os.environ)
config, _ = sandbox.load_config({checkout!r})
sandbox.run_local(meta(9), spec.parse(ISSUE), {checkout!r}, sandbox.environment_entry('local', config), settings, {out!r})
'''
        process = subprocess.Popen([sys.executable, '-c', code], env=self.environment)
        deadline = time.monotonic() + 120
        while not [name for name in ds.leftovers(settings.prefix + '-') if 'tester' in name]:
            self.assertLess(time.monotonic(), deadline, 'the tester never started')
            self.assertIsNone(process.poll())
            time.sleep(1)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=120), 128 + signal.SIGTERM)
        self.assertEqual(ds.leftovers(settings.prefix + '-'), [])


if __name__ == '__main__':
    unittest.main()
