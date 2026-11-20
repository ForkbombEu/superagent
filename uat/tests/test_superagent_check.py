"""superagent check: dry-run plan and run-path for the next check on a PR head.

Seam under test: the scripts/superagent CLI (exit status, stdout/stderr, whether
uat-test / claude-review are invoked). Fakes stand in for those commands.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from uat.tests import fake_github as fg

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, 'scripts', 'superagent')
REPOSITORY = 'ForkbombEu/sample'
BRANCH = 'agent/ticket'
TITLE = 'feat: sample change'


class SuperagentCheckHarness(unittest.TestCase):
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

class SuperagentCheckDryRunTest(SuperagentCheckHarness):
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


class SuperagentCheckRunTest(SuperagentCheckHarness):
    """F2.2: lock, run the next missing check, aggregate with next:, budget failed reviews."""

    def setUp(self):
        super().setUp()
        self.uat_log = os.path.join(self.dir, 'uat-calls')
        self.review_log = os.path.join(self.dir, 'review-calls')
        self.uat_config = os.path.join(self.dir, 'uat.json')
        with open(self.uat_config, 'w', encoding='utf-8') as file:
            json.dump({'gate_url': 'http://127.0.0.1:9', 'token': 't'}, file)
        self.environ['UAT_TEST_CONFIG'] = self.uat_config
        self.gh.add_pr(9, self.sha, closes=[7], title=TITLE, head_ref=BRANCH)
        self._install_uat(verdict='PASS')
        self._install_review(verdict='PASS')

    def _install_uat(self, verdict='PASS', hold_path=None, exit_code=None):
        if exit_code is None:
            exit_code = {'PASS': 0, 'NOT_REQUIRED': 0, 'FAIL': 1, 'BLOCKED': 2, 'RETRY': 3}.get(verdict, 1)
        hold_literal = 'None' if hold_path is None else json.dumps(hold_path)
        path = os.path.join(self.bin, 'uat-test')
        script = f'''#!/usr/bin/env python3
import json, os, sys, time
log = os.environ["SUPERAGENT_UAT_LOG"]
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
hold = {hold_literal}
if hold:
    with open(hold, "w", encoding="utf-8") as marker:
        marker.write("held\\n")
    while os.path.exists(hold):
        time.sleep(0.05)
repository, number, sha = sys.argv[1], sys.argv[2], sys.argv[3]
home = os.environ["HOME"]
directory = os.path.join(home, ".local", "state", "uat-test", repository, f"pr-{{number}}")
os.makedirs(directory, exist_ok=True)
name = f"20260101T000000Z-{{sha[:12]}}.md"
report = os.path.join(directory, name)
with open(report, "w", encoding="utf-8") as handle:
    handle.write("# UAT {verdict}\\n")
with open(os.path.join(directory, f"{{sha}}.json"), "w", encoding="utf-8") as handle:
    json.dump({{"sha": sha, "verdict": "{verdict}"}}, handle)
    handle.write("\\n")
print(f"uat-test: detail {{report}}")
print(f"uat-test: {verdict} for {{sha}}")
raise SystemExit({exit_code})
'''
        with open(path, 'w', encoding='utf-8') as file:
            file.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.environ['SUPERAGENT_UAT_LOG'] = self.uat_log

    def _install_review(self, verdict='PASS', exit_code=None):
        if exit_code is None:
            exit_code = {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2, 'RETRY': 3}.get(verdict, 1)
        path = os.path.join(self.bin, 'claude-review')
        script = f'''#!/usr/bin/env python3
import json, os, sys
log = os.environ["SUPERAGENT_REVIEW_LOG"]
with open(log, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
repository, number, sha = sys.argv[1], sys.argv[2], sys.argv[3]
home = os.environ["HOME"]
directory = os.path.join(home, ".local", "state", "claude-review", repository, f"pr-{{number}}")
os.makedirs(directory, exist_ok=True)
stem = f"{{sha}}-deadbeefcafe"
detail = os.path.join(directory, f"{{stem}}.md")
with open(detail, "w", encoding="utf-8") as handle:
    handle.write("review\\n")
with open(os.path.join(directory, f"{{stem}}.json"), "w", encoding="utf-8") as handle:
    json.dump({{"sha": sha, "title": {TITLE!r}, "verdict": "{verdict}", "answer": "ok"}}, handle)
    handle.write("\\n")
if "{verdict}" == "FAIL":
    print(f"claude-review: detail {{detail}}")
    print("claude-review: rounds 1 of 3")
    print("claude-review: next failed reviews so far: {{stem}}.md")
print(f"claude-review: {verdict} for {{sha}}")
raise SystemExit({exit_code})
'''
        with open(path, 'w', encoding='utf-8') as file:
            file.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.environ['SUPERAGENT_REVIEW_LOG'] = self.review_log

    def _calls(self, log):
        if not os.path.exists(log):
            return []
        with open(log, encoding='utf-8') as file:
            return [json.loads(line) for line in file if line.strip()]

    def _seed_uat(self, verdict='PASS'):
        directory = self._history('uat-test')
        name = f'20260101T000000Z-{self.sha[:12]}.md'
        with open(os.path.join(directory, name), 'w', encoding='utf-8') as file:
            file.write(f'# UAT {verdict}\\n')
        with open(os.path.join(directory, f'{self.sha}.json'), 'w', encoding='utf-8') as file:
            json.dump({'sha': self.sha, 'verdict': verdict}, file)
            file.write('\n')
        return name

    def _seed_review(self, verdict='PASS', sha=None, stem_suffix='deadbeefcafe'):
        sha = sha or self.sha
        directory = self._history('claude-review')
        stem = f'{sha}-{stem_suffix}'
        with open(os.path.join(directory, f'{stem}.md'), 'w', encoding='utf-8') as file:
            file.write('review\\n')
        with open(os.path.join(directory, f'{stem}.json'), 'w', encoding='utf-8') as file:
            json.dump({'sha': sha, 'title': TITLE, 'verdict': verdict, 'answer': 'ok'}, file)
            file.write('\n')
        return stem

    def test_uat_fail_stops_before_review(self):
        self._install_uat(verdict='FAIL')
        result = self.run_check('check')
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertEqual(len(self._calls(self.uat_log)), 1)
        self.assertEqual(self._calls(self.review_log), [])
        self.assertIn('superagent: next ', result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f'superagent: FAIL for {self.sha}'))

    def test_uat_pass_then_review_runs(self):
        self._install_uat(verdict='PASS')
        self._install_review(verdict='PASS')
        first = self.run_check('check')
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(len(self._calls(self.uat_log)), 1)
        self.assertEqual(self._calls(self.review_log), [])
        second = self.run_check('check')
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertEqual(len(self._calls(self.uat_log)), 1)
        self.assertEqual(len(self._calls(self.review_log)), 1)
        self.assertIn('superagent: next ', second.stdout)
        self.assertTrue(second.stdout.rstrip().endswith(f'superagent: PASS for {self.sha}'))

    def test_third_run_reuses_both_stored_results(self):
        self._install_uat(verdict='PASS')
        self._install_review(verdict='PASS')
        self.assertEqual(self.run_check('check').returncode, 0)
        self.assertEqual(self.run_check('check').returncode, 0)
        self.assertEqual(len(self._calls(self.uat_log)), 1)
        self.assertEqual(len(self._calls(self.review_log)), 1)
        third = self.run_check('check')
        self.assertEqual(third.returncode, 0, third.stderr + third.stdout)
        self.assertEqual(len(self._calls(self.uat_log)), 1)
        self.assertEqual(len(self._calls(self.review_log)), 1)
        self.assertIn('superagent: next ', third.stdout)
        self.assertTrue(third.stdout.rstrip().endswith(f'superagent: PASS for {self.sha}'))

    def test_concurrent_second_call_waits_on_lock(self):
        hold = os.path.join(self.dir, 'uat-hold')
        self._install_uat(verdict='PASS', hold_path=hold)
        self._install_review(verdict='PASS')
        first = subprocess.Popen(
            [sys.executable, SCRIPT, 'check'],
            cwd=self.ticket, env=self.environ,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(100):
            if os.path.exists(hold):
                break
            time.sleep(0.05)
        else:
            first.kill()
            self.fail('uat-test never took the hold')
        second = subprocess.Popen(
            [sys.executable, SCRIPT, 'check'],
            cwd=self.ticket, env=self.environ,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        time.sleep(0.3)
        self.assertIsNone(second.poll(), 'second check should still be waiting on the lock')
        os.remove(hold)
        first_code = first.wait(timeout=10)
        second_code = second.wait(timeout=10)
        first_err = first.stderr.read() if first.stderr else ''
        second_err = second.stderr.read() if second.stderr else ''
        if first.stdout:
            first.stdout.close()
        if first.stderr:
            first.stderr.close()
        if second.stdout:
            second.stdout.close()
        if second.stderr:
            second.stderr.close()
        self.assertEqual(first_code, 0, first_err)
        self.assertEqual(second_code, 0, second_err)
        self.assertEqual(len(self._calls(self.uat_log)), 1)
        self.assertEqual(len(self._calls(self.review_log)), 1)

    def test_fourth_failed_review_is_blocked(self):
        self._seed_uat('PASS')
        for index in range(3):
            other = f'{"%040d" % (index + 1)}'
            self._seed_review('FAIL', sha=other, stem_suffix=f'fail{index}')
        self._forbid('claude-review')
        result = self.run_check('check')
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn('superagent: next ', result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith(f'superagent: BLOCKED for {self.sha}'))
        self.assertNotIn('unexpected call', result.stderr)


if __name__ == '__main__':
    unittest.main()
