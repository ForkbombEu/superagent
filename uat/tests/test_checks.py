"""Shared check module: exit codes, compact results, PR comments, history paths, head checks."""
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHA = 'a' * 40
OTHER = 'b' * 40
REPOSITORY = 'ForkbombEu/sample'


def load_checks():
    path = os.path.join(ROOT, 'scripts', 'superagent_checks.py')
    loader = importlib.machinery.SourceFileLoader('superagent_checks', path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('superagent_checks', loader))
    sys.modules['superagent_checks'] = module
    loader.exec_module(module)
    return module


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.checks = load_checks()
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, 'home')
        os.makedirs(self.home)
        self._home = mock.patch.dict(os.environ, {'HOME': self.home})
        self._home.start()
        self.addCleanup(self._home.stop)

    def test_exit_codes(self):
        self.assertEqual(
            (self.checks.Exit.PASS, self.checks.Exit.FAIL, self.checks.Exit.BLOCKED,
             self.checks.Exit.RETRY, self.checks.Exit.PRECONDITION),
            (0, 1, 2, 3, 4),
        )

    def test_print_result_ends_with_verdict_for_sha(self):
        result = self.checks.Result(
            check='uat-test', verdict='FAIL', sha=SHA,
            rounds_used=1, rounds_allowed=3,
            next_step='fix and push',
            detail_file='/tmp/report.md',
        )
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.checks.print_result(result)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(lines[-1], f'uat-test: FAIL for {SHA}')
        self.assertIn('uat-test: detail /tmp/report.md', lines)
        self.assertIn('uat-test: rounds 1 of 3', lines)
        self.assertIn('uat-test: next fix and push', lines)

    def test_history_dir_is_under_local_state(self):
        path = self.checks.history_dir('uat-test', REPOSITORY, 9)
        self.assertEqual(path, os.path.join(self.home, '.local', 'state', 'uat-test', REPOSITORY, 'pr-9'))

    def test_pr_comment_truncates_long_bodies(self):
        directory = self.checks.history_dir('uat-test', REPOSITORY, 9)
        os.makedirs(directory)
        bodies = []

        def gh(*args, stdin=None):
            bodies.append(stdin)
            return subprocess.CompletedProcess(args, 0, '', '')

        long_body = 'x' * (self.checks.MAX_COMMENT + 500)
        posted = self.checks.pr_comment(
            REPOSITORY, 9, long_body, directory=directory, marker='run-1', gh=gh,
        )
        self.assertTrue(posted)
        self.assertEqual(len(bodies), 1)
        comment = bodies[0]
        self.assertTrue(comment.startswith(self.checks.DISCLOSURE + '\n\n'))
        self.assertIn('(Truncated to fit a GitHub comment.)', comment)
        self.assertLessEqual(len(comment), self.checks.MAX_COMMENT + len(self.checks.DISCLOSURE) + 80)

    def test_pr_comment_posts_once_per_marker(self):
        directory = self.checks.history_dir('claude-review', REPOSITORY, 9)
        os.makedirs(directory)
        calls = []

        def gh(*args, stdin=None):
            calls.append(stdin)
            return subprocess.CompletedProcess(args, 0, '', '')

        self.assertTrue(self.checks.pr_comment(
            REPOSITORY, 9, '**review**\n\nok', directory=directory, marker='abc', gh=gh,
        ))
        self.assertTrue(self.checks.pr_comment(
            REPOSITORY, 9, '**review**\n\nok', directory=directory, marker='abc', gh=gh,
        ))
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.exists(os.path.join(directory, 'posted-abc')))
        self.assertIn(self.checks.DISCLOSURE, calls[0])
        self.assertIn('**review**', calls[0])

    def test_require_head_exits_precondition_on_mismatch(self):
        def gh(*args, stdin=None):
            self.assertEqual(args[:2], ('pr', 'view'))
            return subprocess.CompletedProcess(
                args, 0, json.dumps({'headRefOid': OTHER}), '',
            )

        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            self.checks.require_head(REPOSITORY, 9, SHA, gh=gh, check='uat-test')
        self.assertEqual(raised.exception.code, self.checks.Exit.PRECONDITION)
        self.assertIn('the PR head is', stderr.getvalue())
        self.assertIn(OTHER, stderr.getvalue())

    def test_require_head_accepts_matching_sha(self):
        def gh(*args, stdin=None):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({'headRefOid': SHA}), '',
            )

        self.checks.require_head(REPOSITORY, 9, SHA, gh=gh, check='claude-review')


if __name__ == '__main__':
    unittest.main()
