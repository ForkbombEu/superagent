"""Shared exit codes, compact results, PR comments, history paths and head checks.

Used by uat-test, claude-review and later superagent check/merge. Callers keep
their own gate/Claude logic; this module owns the check contract surface.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

# GitHub rejects comments over 65536 characters.
MAX_COMMENT = 60000
DISCLOSURE = 'This is by an automated agent, not a human.'
GH = os.environ.get('SUPERAGENT_GH', os.environ.get('UAT_TEST_GH', 'gh'))

Exit = SimpleNamespace(
    PASS=0,
    FAIL=1,
    BLOCKED=2,
    RETRY=3,
    PRECONDITION=4,
)

_VERDICT_EXIT = {
    'PASS': Exit.PASS,
    'NOT_REQUIRED': Exit.PASS,
    'FAIL': Exit.FAIL,
    'BLOCKED': Exit.BLOCKED,
    'RETRY': Exit.RETRY,
    'PRECONDITION': Exit.PRECONDITION,
}


class Result:
    """Compact check outcome for stdout and later orchestrators."""

    __slots__ = ('check', 'verdict', 'sha', 'rounds_used', 'rounds_allowed', 'next_step', 'detail_file', 'model')

    def __init__(self, check, verdict, sha, rounds_used, rounds_allowed, next_step, detail_file, model=None):
        self.check = check
        self.verdict = verdict
        self.sha = sha
        self.rounds_used = rounds_used
        self.rounds_allowed = rounds_allowed
        self.next_step = next_step
        self.detail_file = detail_file
        self.model = model


def history_dir(check, repository, number):
    return os.path.join(os.path.expanduser('~/.local/state'), check, repository, f'pr-{number}')


def gh(*args, stdin=None):
    return subprocess.run([GH, *args], input=stdin, capture_output=True, text=True)


def print_result(result):
    if result.detail_file:
        print(f'{result.check}: detail {result.detail_file}')
    if result.rounds_used is not None:
        allowed = result.rounds_allowed if result.rounds_allowed is not None else '?'
        print(f'{result.check}: rounds {result.rounds_used} of {allowed}')
    if result.next_step:
        print(f'{result.check}: next {result.next_step}')
    if result.model:
        print(f'{result.check}: model {result.model}')
    print(f'{result.check}: {result.verdict} for {result.sha}')
    return _VERDICT_EXIT.get(result.verdict, Exit.RETRY)


def _truncate(text):
    if len(text) <= MAX_COMMENT:
        return text
    return text[:MAX_COMMENT] + '\n\n(Truncated to fit a GitHub comment.)'


def pr_comment(repository, number, body, *, directory, marker, gh=gh):
    """Post `body` on the PR once for `marker`. Adds disclosure and truncates.

    Returns True when the comment is on the PR (newly posted or already marked).
    Returns False when `gh` fails to post.
    """
    os.makedirs(directory, exist_ok=True)
    posted_marker = os.path.join(directory, f'posted-{marker}')
    if os.path.exists(posted_marker):
        return True
    comment = f'{DISCLOSURE}\n\n{_truncate(body)}'
    result = gh('pr', 'comment', str(number), '--repo', repository, '--body-file', '-', stdin=comment)
    if result.returncode:
        return False
    open(posted_marker, 'w').close()
    return True


def require_head(repository, number, sha, *, gh=gh, check='check'):
    """Exit with PRECONDITION when the PR head is not exactly `sha`."""
    view = gh('pr', 'view', str(number), '--repo', repository, '--json', 'headRefOid')
    if view.returncode:
        print(f'{check}: {GH} pr view failed: {view.stderr.strip()}', file=sys.stderr)
        sys.exit(Exit.PRECONDITION)
    head = json.loads(view.stdout)['headRefOid']
    if head != sha:
        print(f'{check}: the PR head is {head}, not {sha}: run against the current head', file=sys.stderr)
        sys.exit(Exit.PRECONDITION)
