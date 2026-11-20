"""superagent check --dry-run: resolve repo/PR/head and list stored results for that head.

Seam under test: the scripts/superagent CLI (exit status, stdout/stderr). Does not run
uat-test or claude-review.
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


class SuperagentCheckDryRunTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, 'home')
        self.bin = os.path.join(self.dir, 'bin')
        os.makedirs(self.home)
        os.makedirs(self.bin)
        os.symlink(os.path.join(TESTS, 'fake_gh.py'), os.path.join(self.bin, 'gh'))
        self._forbid('uat-test')
        self._forbid('claude-review')
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

    def run_check(self, *args, **extra):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True, cwd=self.ticket,
            env={**self.environ, **extra}, timeout=30,
        )

    def _history(self, check):
        path = os.path.join(self.home, '.local', 'state', check, REPOSITORY, 'pr-9')
        os.makedirs(path, exist_ok=True)
        return path

    def test_no_pr_exits_precondition(self):
        result = self.run_check('check', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('no pull request', result.stderr.lower())

    def test_local_head_behind_pr_names_git_pull(self):
        subprocess.run(['git', '-C', self.ticket, '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-q', '--allow-empty', '-m', 'feat: ahead on PR'], check=True)
        pr_head = subprocess.run(['git', '-C', self.ticket, 'rev-parse', 'HEAD'],
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.gh.add_pr(9, pr_head, closes=[7], title=TITLE, head_ref=BRANCH)
        subprocess.run(['git', '-C', self.ticket, 'reset', '--hard', '-q', self.sha], check=True)
        result = self.run_check('check', '--dry-run')
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn('git pull', result.stderr)

    def test_dry_run_reports_missing_results(self):
        self.gh.add_pr(9, self.sha, closes=[7], title=TITLE, head_ref=BRANCH)
        result = self.run_check('check', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f'superagent check: {REPOSITORY}#9 at {self.sha}')
        self.assertIn(f'uat-test: no result for {self.sha}', lines)
        self.assertIn(f'claude-review: no result for {self.sha}', lines)

    def test_dry_run_lists_stored_results_without_running_checks(self):
        self.gh.add_pr(9, self.sha, closes=[7], title=TITLE, head_ref=BRANCH)
        uat_dir = self._history('uat-test')
        review_dir = self._history('claude-review')
        uat_name = f'20260101T000000Z-{self.sha[:12]}.md'
        with open(os.path.join(uat_dir, uat_name), 'w', encoding='utf-8') as file:
            file.write('# UAT report\n')
        with open(os.path.join(review_dir, f'{self.sha}-deadbeefcafe.json'), 'w', encoding='utf-8') as file:
            json.dump({'sha': self.sha, 'title': TITLE, 'verdict': 'PASS', 'answer': 'ok'}, file)
        result = self.run_check('check', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f'superagent check: {REPOSITORY}#9 at {self.sha}')
        self.assertIn(f'uat-test: result for {self.sha} ({uat_name})', lines)
        self.assertIn(f'claude-review: PASS for {self.sha}', lines)
        self.assertNotIn('unexpected call', result.stderr)


if __name__ == '__main__':
    unittest.main()
