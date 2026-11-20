"""claude-review through the shared check contract: storage, reuse, exit codes, compact fail output."""
import importlib.machinery
import importlib.util
from pathlib import Path
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
SCRIPT = os.path.join(ROOT, 'scripts', 'claude-review')
REPOSITORY = 'ForkbombEu/sample'
TITLE = 'feat: sample change'


def answer_for(sha, verdict='PASS', summary='Looks good.', fix='None.'):
    return textwrap.dedent(f'''\
        ## Summary
        {summary}

        ## Review
        No blocking findings.

        ## Fix plan
        {fix}

        VERDICT: {verdict}
        REVIEWED_SHA: {sha}
    ''')


class ClaudeReviewHarness(unittest.TestCase):
    """Shared fake gh/claude ticket fixture for claude-review tests."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.home = os.path.join(self.dir, 'home')
        self.bin = os.path.join(self.dir, 'bin')
        self.checkouts = os.path.join(self.dir, 'checkouts')
        self.prompt = os.path.join(self.dir, 'prompt.md')
        self.config = os.path.join(self.dir, 'claude-review.json')
        self.claude_log = os.path.join(self.dir, 'claude-calls')
        self.claude_answer = os.path.join(self.dir, 'claude-answer.txt')
        os.makedirs(self.home)
        os.makedirs(self.bin)
        os.makedirs(self.checkouts)
        with open(self.prompt, 'w', encoding='utf-8') as file:
            file.write('Review <repository> #<number> "<title>" at <sha> base <base branch>.\n<history>\n')
        with open(self.config, 'w', encoding='utf-8') as file:
            json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key'}, 'model': 'test-model'}, file)
        self._write_claude()
        os.symlink(os.path.join(TESTS, 'fake_gh.py'), os.path.join(self.bin, 'gh'))
        self.gh = fg.FakeGitHub(REPOSITORY)
        self.addCleanup(self.gh.close)
        self.ticket, self.sha = self._make_ticket()
        self.gh.add_pr(9, self.sha, closes=[7], title=TITLE)
        self.environ = {
            **os.environ,
            'HOME': self.home,
            'PATH': f'{self.bin}:{os.environ["PATH"]}',
            'FAKE_GITHUB_URL': self.gh.url,
            'FAKE_GITHUB_TOKEN': fg.CODER_TOKEN,
            'SUPERAGENT_GH': os.path.join(self.bin, 'gh'),
            'CLAUDE_REVIEW_PROMPT': self.prompt,
            'CLAUDE_REVIEW_CONFIG': self.config,
            'CLAUDE_REVIEW_CHECKOUTS': self.checkouts,
            'UAT_TEST_CONFIG': os.path.join(self.dir, 'missing-uat.json'),
            'CLAUDE_FAKE_LOG': self.claude_log,
            'CLAUDE_FAKE_ANSWER': self.claude_answer,
        }

    def _write_claude(self):
        path = os.path.join(self.bin, 'claude')
        with open(path, 'w', encoding='utf-8') as file:
            file.write(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json, os, sys
                with open(os.environ['CLAUDE_FAKE_LOG'], 'a', encoding='utf-8') as log:
                    log.write(json.dumps(sys.argv[1:]) + '\\n')
                with open(os.environ['CLAUDE_FAKE_ANSWER'], encoding='utf-8') as answer:
                    text = answer.read()
                print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text}))
            '''))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def _make_ticket(self):
        origin = os.path.join(self.dir, 'origin.git')
        seed = os.path.join(self.dir, 'seed')
        ticket = os.path.join(self.dir, 'ticket')
        subprocess.run(['git', 'init', '-q', seed], check=True)
        subprocess.run(['git', '-C', seed, 'branch', '-M', 'main'], check=True)
        subprocess.run(['git', '-C', seed, '-c', 'user.name=t', '-c', 'user.email=t@t',
                        'commit', '-q', '--allow-empty', '-m', 'feat: sample change'], check=True)
        sha = subprocess.run(['git', '-C', seed, 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(['git', 'clone', '-q', '--bare', seed, origin], check=True)
        subprocess.run(['git', 'clone', '-q', origin, ticket], check=True)
        return ticket, sha

    def set_answer(self, verdict='PASS', summary='Looks good.', fix='None.'):
        with open(self.claude_answer, 'w', encoding='utf-8') as file:
            file.write(answer_for(self.sha, verdict=verdict, summary=summary, fix=fix))

    def set_raw_answer(self, text):
        with open(self.claude_answer, 'w', encoding='utf-8') as file:
            file.write(text)

    def claude_calls(self):
        if not os.path.exists(self.claude_log):
            return []
        with open(self.claude_log, encoding='utf-8') as file:
            return [json.loads(line) for line in file if line.strip()]

    def run_review(self, **extra):
        return subprocess.run(
            [sys.executable, SCRIPT, REPOSITORY, '9', self.sha],
            capture_output=True, text=True, cwd=self.ticket,
            env={**self.environ, **extra}, timeout=120,
        )

    def stored_reviews(self):
        directory = os.path.join(self.home, '.local', 'state', 'claude-review', REPOSITORY, 'pr-9')
        if not os.path.isdir(directory):
            return []
        reviews = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith('.json'):
                continue
            with open(os.path.join(directory, name), encoding='utf-8') as file:
                reviews.append(json.load(file))
        return reviews

    def write_review_config(self, **fields):
        payload = {'env': {'ANTHROPIC_API_KEY': 'test-key'}, 'model': 'opus', **fields}
        with open(self.config, 'w', encoding='utf-8') as file:
            json.dump(payload, file)

    def commit_lines(self, count, name='change.txt'):
        """Add a commit with `count` lines; keep origin/main as the base behind HEAD."""
        path = os.path.join(self.ticket, name)
        with open(path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(f'line-{i}' for i in range(count)) + '\n')
        subprocess.run(['git', '-C', self.ticket, 'add', name], check=True)
        subprocess.run(
            ['git', '-C', self.ticket, '-c', 'user.name=t', '-c', 'user.email=t@t',
             'commit', '-q', '-m', f'add {count} lines'],
            check=True,
        )
        self.sha = subprocess.run(
            ['git', '-C', self.ticket, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        parent = subprocess.run(
            ['git', '-C', self.ticket, 'rev-parse', 'HEAD~1'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(
            ['git', '-C', self.ticket, 'push', '-q', '--force', 'origin', f'{parent}:refs/heads/main'],
            check=True,
        )
        subprocess.run(
            ['git', '-C', self.ticket, 'push', '-q', '--force', 'origin', f'{self.sha}:refs/heads/head'],
            check=True,
        )
        self.gh.prs[9]['head']['sha'] = self.sha
        return self.sha

class ClaudeReviewTest(ClaudeReviewHarness):
    def test_pass_stores_review_and_prints_compact_result(self):
        self.set_answer('PASS')
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.rstrip().endswith(f'claude-review: PASS for {self.sha}'))
        self.assertNotIn('## Fix plan', result.stdout)
        reviews = self.stored_reviews()
        self.assertEqual(len(reviews), 1)
        self.assertEqual(
            (reviews[0]['sha'], reviews[0]['title'], reviews[0]['verdict']),
            (self.sha, TITLE, 'PASS'),
        )
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertEqual(len(self.gh.comments), 1)
        self.assertIn('**Claude Code review**', self.gh.comments[0]['body'])
        self.assertIn('**PASS**', self.gh.comments[0]['body'])

    def test_fail_names_fix_plan_file_and_failed_reviews(self):
        self.set_answer('FAIL', summary='Blocking finding.', fix='1. Fix the bug.')
        result = self.run_review()
        self.assertEqual(result.returncode, 1, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], f'claude-review: FAIL for {self.sha}')
        detail = next(line for line in lines if line.startswith('claude-review: detail '))
        path = detail.split(' ', 2)[2]
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding='utf-8') as file:
            self.assertIn('## Fix plan', file.read())
        self.assertIn('claude-review: rounds 1 of 3', lines)
        failed = next(line for line in lines if line.startswith('claude-review: next '))
        self.assertIn('failed reviews so far:', failed)
        self.assertIn(path.rsplit('/', 1)[-1], failed)
        self.assertEqual(len(self.claude_calls()), 1)

    def test_malformed_answer_exits_retry(self):
        self.set_raw_answer('not a review at all')
        result = self.run_review()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(self.stored_reviews(), [])
        self.assertEqual(self.gh.comments, [])
        self.assertEqual(len(self.claude_calls()), 1)

    def test_post_failure_then_rerun_reposts_without_calling_claude(self):
        self.set_answer('PASS')
        first = self.run_review(FAKE_GH_FAIL_COMMENT='1')
        self.assertEqual(first.returncode, 3, first.stderr)
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertEqual(self.gh.comments, [])
        self.assertEqual(len(self.stored_reviews()), 1)
        second = self.run_review()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertEqual(len(self.gh.comments), 1)
        self.assertTrue(second.stdout.rstrip().endswith(f'claude-review: PASS for {self.sha}'))

    def test_title_change_runs_a_new_review(self):
        self.set_answer('PASS', summary='First title.')
        first = self.run_review()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.gh.prs[9]['title'] = 'feat: retitled'
        self.set_answer('FAIL', summary='Second title.', fix='1. Adjust.')
        second = self.run_review()
        self.assertEqual(second.returncode, 1, second.stderr)
        self.assertEqual(len(self.claude_calls()), 2)
        reviews = self.stored_reviews()
        self.assertEqual(len(reviews), 2)
        self.assertEqual({review['title'] for review in reviews}, {TITLE, 'feat: retitled'})

    def test_two_runs_on_one_commit_call_claude_once(self):
        self.set_answer('PASS')
        first = self.run_review()
        second = self.run_review()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.claude_calls()), 1)
        self.assertEqual(len(self.gh.comments), 1)
        self.assertEqual(len(self.stored_reviews()), 1)


if __name__ == '__main__':
    unittest.main()


def load_claude_review():
    path = SCRIPT
    loader = importlib.machinery.SourceFileLoader('claude_review', path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('claude_review', loader))
    sys.modules['claude_review'] = module
    loader.exec_module(module)
    return module


class ReviewerModelSelectionTest(unittest.TestCase):
    """Unit: pick small vs large reviewer from config and changed-line count."""

    def setUp(self):
        self.review = load_claude_review()

    def test_unset_small_model_keeps_single_model(self):
        self.assertEqual(
            self.review.select_reviewer_model({'model': 'opus'}, 10),
            'opus',
        )
        self.assertEqual(
            self.review.select_reviewer_model({'model': 'opus', 'small_model': ''}, 10),
            'opus',
        )
        self.assertEqual(
            self.review.select_reviewer_model({'model': ''}, 500),
            '',
        )

    def test_small_diff_uses_small_model_at_default_threshold(self):
        config = {'model': 'opus', 'small_model': 'haiku', 'small_diff_lines': 200}
        self.assertEqual(self.review.select_reviewer_model(config, 0), 'haiku')
        self.assertEqual(self.review.select_reviewer_model(config, 200), 'haiku')
        self.assertEqual(self.review.select_reviewer_model(config, 201), 'opus')

    def test_missing_threshold_defaults_to_200(self):
        config = {'model': 'opus', 'small_model': 'haiku'}
        self.assertEqual(self.review.select_reviewer_model(config, 200), 'haiku')
        self.assertEqual(self.review.select_reviewer_model(config, 201), 'opus')

    def test_custom_threshold(self):
        config = {'model': 'opus', 'small_model': 'haiku', 'small_diff_lines': 50}
        self.assertEqual(self.review.select_reviewer_model(config, 50), 'haiku')
        self.assertEqual(self.review.select_reviewer_model(config, 51), 'opus')

    def test_count_changed_lines_sums_numstat(self):
        origin = os.path.join(tempfile.mkdtemp(), 'repo')
        self.addCleanup(shutil.rmtree, os.path.dirname(origin), True)
        subprocess.run(['git', 'init', '-q', origin], check=True)
        subprocess.run(['git', '-C', origin, 'branch', '-M', 'main'], check=True)
        subprocess.run(
            ['git', '-C', origin, '-c', 'user.name=t', '-c', 'user.email=t@t',
             'commit', '-q', '--allow-empty', '-m', 'base'],
            check=True,
        )
        base = subprocess.run(
            ['git', '-C', origin, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        path = os.path.join(origin, 'file.txt')
        with open(path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(f'line-{i}' for i in range(12)) + '\n')
        subprocess.run(['git', '-C', origin, 'add', 'file.txt'], check=True)
        subprocess.run(
            ['git', '-C', origin, '-c', 'user.name=t', '-c', 'user.email=t@t',
             'commit', '-q', '-m', 'add lines'],
            check=True,
        )
        sha = subprocess.run(
            ['git', '-C', origin, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        cwd = os.getcwd()
        try:
            os.chdir(origin)
            self.assertEqual(self.review.count_changed_lines(base, sha), 12)
        finally:
            os.chdir(cwd)


class EntrypointClaudeReviewConfigTest(unittest.TestCase):
    """superagent-entrypoint.sh writes small-model routing into claude-review.json."""

    def write_config(self, **environ):
        """Run the entrypoint's claude-review.json writer with the given env."""
        script = Path(ROOT) / 'scripts' / 'superagent-entrypoint.sh'
        source = script.read_text(encoding='utf-8')
        start = source.index("python3 > /run/superagent-secrets/claude-review.json <<'PY'")
        start = source.index('\n', start) + 1
        end = source.index('\nPY\n', start)
        code = source[start:end]
        env = {
            'PATH': os.environ.get('PATH', ''),
            'HOME': os.environ.get('HOME', ''),
            **environ,
        }
        result = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_unset_small_model_keeps_today_json_shape(self):
        data = self.write_config(ANTHROPIC_API_KEY='k', SUPERAGENT_REVIEWER_MODEL='opus')
        self.assertEqual(data['model'], 'opus')
        self.assertEqual(data['env'], {'ANTHROPIC_API_KEY': 'k'})
        self.assertNotIn('small_model', data)
        self.assertNotIn('small_diff_lines', data)

    def test_small_model_and_default_threshold_go_into_json(self):
        data = self.write_config(
            ANTHROPIC_API_KEY='k',
            SUPERAGENT_REVIEWER_MODEL='opus',
            SUPERAGENT_REVIEWER_SMALL_MODEL='haiku',
        )
        self.assertEqual(data['model'], 'opus')
        self.assertEqual(data['small_model'], 'haiku')
        self.assertEqual(data['small_diff_lines'], 200)

    def test_custom_small_diff_lines(self):
        data = self.write_config(
            CLAUDE_CODE_OAUTH_TOKEN='tok',
            SUPERAGENT_REVIEWER_MODEL='opus',
            SUPERAGENT_REVIEWER_SMALL_MODEL='haiku',
            SUPERAGENT_REVIEWER_SMALL_DIFF_LINES='75',
        )
        self.assertEqual(data['small_diff_lines'], 75)
        self.assertEqual(data['env'], {'CLAUDE_CODE_OAUTH_TOKEN': 'tok'})


class ReviewerModelRoutingTest(ClaudeReviewHarness):
    """Route Claude by diff size: --model, compact result, comment, reuse by model."""

    def test_single_model_passed_to_claude_and_shown(self):
        self.write_review_config(model='test-model')
        self.set_answer('PASS')
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.claude_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn('--model', calls[0])
        self.assertEqual(calls[0][calls[0].index('--model') + 1], 'test-model')
        self.assertIn('claude-review: model test-model', result.stdout.splitlines())
        self.assertIn('test-model', self.gh.comments[0]['body'])
        self.assertEqual(self.stored_reviews()[0].get('model'), 'test-model')

    def test_small_diff_uses_small_model(self):
        self.write_review_config(model='opus', small_model='haiku', small_diff_lines=200)
        self.commit_lines(10)
        self.set_answer('PASS')
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.claude_calls()[0]
        self.assertEqual(args[args.index('--model') + 1], 'haiku')
        self.assertIn('claude-review: model haiku', result.stdout.splitlines())
        self.assertIn('haiku', self.gh.comments[0]['body'])

    def test_large_diff_uses_large_model(self):
        self.write_review_config(model='opus', small_model='haiku', small_diff_lines=200)
        self.commit_lines(201)
        self.set_answer('PASS')
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.claude_calls()[0]
        self.assertEqual(args[args.index('--model') + 1], 'opus')
        self.assertIn('claude-review: model opus', result.stdout.splitlines())

    def test_reuse_only_for_same_model_choice(self):
        self.write_review_config(model='opus', small_model='haiku', small_diff_lines=200)
        self.commit_lines(10)
        self.set_answer('PASS', summary='small model review')
        first = self.run_review()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(self.claude_calls()), 1)
        # Same sha/title but force the large model via a lower threshold → new review.
        self.write_review_config(model='opus', small_model='haiku', small_diff_lines=5)
        self.set_answer('FAIL', summary='large model review', fix='1. Fix.')
        second = self.run_review()
        self.assertEqual(second.returncode, 1, second.stderr)
        self.assertEqual(len(self.claude_calls()), 2)
        models = {row.get('model') for row in self.stored_reviews()}
        self.assertEqual(models, {'haiku', 'opus'})
        # Re-run with the small threshold again → reuse haiku, no third Claude call.
        self.write_review_config(model='opus', small_model='haiku', small_diff_lines=200)
        third = self.run_review()
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(len(self.claude_calls()), 2)
        self.assertIn('claude-review: model haiku', third.stdout.splitlines())

    def test_empty_model_omits_model_line(self):
        self.write_review_config(model='')
        self.set_answer('PASS')
        result = self.run_review()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(line.startswith('claude-review: model ') for line in result.stdout.splitlines()))
        args = self.claude_calls()[0]
        self.assertNotIn('--model', args)
