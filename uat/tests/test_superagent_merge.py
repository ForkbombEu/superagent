"""superagent merge --dry-run: every Delivery merge condition as an exit code.

Seam under test: the scripts/superagent CLI `merge --dry-run` (exit status,
stdout compact result / next:, whether uat-test --verify runs). fake_gh stands
in for pr view / checks / ready / reviews; a PATH stub stands in for uat-test.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

from uat.tests import fake_github as fg

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, 'scripts', 'superagent')
REPOSITORY = 'ForkbombEu/sample'
BRANCH = 'agent/ticket'
TITLE = 'feat: sample change'


class SuperagentMergeHarness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, 'home')
        self.bin = os.path.join(self.dir, 'bin')
        os.makedirs(self.home)
        os.makedirs(self.bin)
        os.symlink(os.path.join(TESTS, 'fake_gh.py'), os.path.join(self.bin, 'gh'))
        self._forbid('uat-test')
        self.gh = fg.FakeGitHub(REPOSITORY)
        self.addCleanup(self.gh.close)
        self.ticket, self.sha = self._make_ticket(branch=BRANCH)
        self.environ = {
            **os.environ,
            'HOME': self.home,
            'PATH': f'{self.bin}:{os.environ["PATH"]}',
            'FAKE_GITHUB_URL': self.gh.url,
            'FAKE_GITHUB_TOKEN': fg.CODER_TOKEN,
            'SUPERAGENT_GH': os.path.join(self.bin, 'gh'),
            'DSH_SUPERAGENT_GITHUB_REPOSITORY': REPOSITORY,
        }
        # UAT off by default unless a test turns it on.
        self.environ.pop('UAT_TEST_CONFIG', None)

    def _forbid(self, name):
        path = os.path.join(self.bin, name)
        with open(path, 'w', encoding='utf-8') as file:
            file.write(textwrap.dedent(f'''\
                #!/bin/sh
                echo "unexpected call to {name}" >&2
                exit 99
            '''))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def _make_ticket(self, branch=BRANCH):
        origin = os.path.join(self.dir, 'origin.git')
        seed = os.path.join(self.dir, 'seed')
        ticket = os.path.join(self.dir, 'ticket')
        subprocess.run(['git', 'init', '-q', seed], check=True)
        subprocess.run(['git', '-C', seed, 'branch', '-M', 'main'], check=True)
        subprocess.run(['git', '-C', seed, '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-q', '--allow-empty', '-m', 'feat: sample'], check=True)
        sha = subprocess.run(['git', '-C', seed, 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(['git', 'clone', '-q', '--bare', seed, origin], check=True)
        subprocess.run(['git', 'clone', '-q', origin, ticket], check=True)
        subprocess.run(['git', '-C', ticket, 'checkout', '-q', '-b', branch], check=True)
        return ticket, sha

    def run_merge(self, *args, **extra):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, cwd=self.ticket,
            env={**self.environ, **extra}, timeout=30,
        )

    def _history(self, check):
        path = os.path.join(self.home, '.local', 'state', check, REPOSITORY, 'pr-9')
        os.makedirs(path, exist_ok=True)
        return path

    def _seed_review(self, verdict='PASS', sha=None, title=TITLE, stem_suffix='deadbeefcafe'):
        sha = sha or self.sha
        directory = self._history('claude-review')
        stem = f'{sha}-{stem_suffix}'
        with open(os.path.join(directory, f'{stem}.md'), 'w', encoding='utf-8') as file:
            file.write('review\n')
        with open(os.path.join(directory, f'{stem}.json'), 'w', encoding='utf-8') as file:
            json.dump({'sha': sha, 'title': title, 'verdict': verdict, 'answer': 'ok'}, file)
            file.write('\n')
        return stem

    def _add_ready_pr(self, **kwargs):
        defaults = dict(number=9, sha=self.sha, closes=[7], title=TITLE, head_ref=BRANCH, draft=False)
        defaults.update(kwargs)
        number = defaults.pop('number')
        sha = defaults.pop('sha')
        closes = defaults.pop('closes')
        self.gh.add_pr(number, sha, closes, **defaults)
        self.gh.set_checks(number, [{'name': 'ci', 'state': 'pass', 'required': True}])
        self.gh.set_reviews(number, [{'state': 'APPROVED', 'author': 'reviewer'}])

    def _enable_uat(self):
        config = os.path.join(self.dir, 'uat.json')
        with open(config, 'w', encoding='utf-8') as file:
            json.dump({'gate_url': 'http://127.0.0.1:9', 'token': 't'}, file)
        self.environ['UAT_TEST_CONFIG'] = config
        return config

    def _install_uat_verify(self, exit_code=0):
        path = os.path.join(self.bin, 'uat-test')
        log = os.path.join(self.dir, 'uat-verify.log')
        script = f'''#!/usr/bin/env python3
import json, os, sys
log = {log!r}
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] != "--verify":
    print("uat-test: unexpected non-verify call", file=sys.stderr)
    raise SystemExit(99)
print(f"uat-test: current for {{sys.argv[4]}}: ok")
raise SystemExit({exit_code})
'''
        with open(path, 'w', encoding='utf-8') as file:
            file.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.uat_verify_log = log
        return log

    def _verify_calls(self):
        if not os.path.exists(self.uat_verify_log):
            return []
        with open(self.uat_verify_log, encoding='utf-8') as file:
            return [json.loads(line) for line in file if line.strip()]


class SuperagentMergeDryRunTest(SuperagentMergeHarness):
    def test_draft_names_mark_ready(self):
        self._add_ready_pr(draft=True)
        self._seed_review('PASS')
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        self.assertIn('superagent: next mark ready', result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f'superagent: PRECONDITION for {self.sha}'))

    def test_missing_passing_review_is_named(self):
        self._add_ready_pr()
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        self.assertIn('superagent: next passing review', result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f'superagent: PRECONDITION for {self.sha}'))

    def test_title_mismatch_needs_passing_review(self):
        self._add_ready_pr(title='feat: retitled')
        self._seed_review('PASS', title=TITLE)
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        self.assertIn('superagent: next passing review', result.stdout)

    def test_uat_verify_failure_is_named(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        self._enable_uat()
        self._install_uat_verify(exit_code=1)
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        self.assertIn('superagent: next uat-test --verify', result.stdout)
        self.assertEqual(self._verify_calls(), [['--verify', REPOSITORY, '9', self.sha]])

    def test_pending_checks_exit_retry(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        self.gh.set_checks(9, [{'name': 'ci', 'state': 'pending', 'required': True}])
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 3, result.stderr + result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f'superagent: RETRY for {self.sha}'))

    def test_failed_checks_are_named(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        self.gh.set_checks(9, [{'name': 'ci', 'state': 'fail', 'required': True}])
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        self.assertIn('superagent: next checks', result.stdout)

    def test_changes_requested_is_named(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        self.gh.set_reviews(9, [{'state': 'CHANGES_REQUESTED', 'author': 'reviewer'}])
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        self.assertIn('superagent: next changes requested', result.stdout)

    def test_all_conditions_pass_with_uat_verify(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        self._enable_uat()
        self._install_uat_verify(exit_code=0)
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('superagent: next merge', result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f'superagent: PASS for {self.sha}'))
        self.assertEqual(self._verify_calls(), [['--verify', REPOSITORY, '9', self.sha]])

    def test_uat_off_skips_verify(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('superagent: next merge', result.stdout)
        self.assertNotIn('unexpected call to uat-test', result.stderr)

    def test_no_required_checks_uses_all_checks(self):
        self._add_ready_pr()
        self._seed_review('PASS')
        self.gh.set_checks(9, [
            {'name': 'ci', 'state': 'pass', 'required': False},
            {'name': 'lint', 'state': 'skipping', 'required': False},
        ])
        result = self.run_merge('merge', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('superagent: next merge', result.stdout)


if __name__ == '__main__':
    unittest.main()
