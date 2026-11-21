#!/usr/bin/env python3
"""Validate write-issues fixture bodies against AGENTS.md template checks."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from uat import spec  # noqa: E402

ISSUES = Path(__file__).resolve().parent / 'fixtures' / 'issues'

MENTION = re.compile(r'@forkboteu\b')
EFFORT = re.compile(r'^- Effort :: ~\d+k\s*$', re.MULTILINE)
MODEL = re.compile(r'^- Model :: (low|mid|high)\s*$', re.MULTILINE)
CONTEXT = re.compile(r'^- Context :: \S.*$', re.MULTILINE)
BRANCH = re.compile(r'^- Branch after :: \S.*$', re.MULTILINE)
ACCEPT = re.compile(r'^## Acceptance criteria\s*$', re.MULTILINE)
CHECK = re.compile(r'^- \[[ x]\] \S', re.MULTILINE)
DEPENDS = re.compile(r'^Depends on #\d+(?:[ \t]*,[ \t]*#\d+)*\s*$', re.MULTILINE)
DEPENDS_ANY = re.compile(r'^Depends on\b', re.MULTILINE)
UAT_HEADING = re.compile(r'^## User Acceptance Tests\s*$', re.MULTILINE)


def check(body: str, name: str) -> list[str]:
    errors: list[str] = []
    if not MENTION.search(body):
        errors.append('missing @forkboteu mention')
    if not EFFORT.search(body):
        errors.append('missing Effort line')
    if not MODEL.search(body):
        errors.append('missing Model line')
    if not CONTEXT.search(body):
        errors.append('missing Context line')
    if not BRANCH.search(body):
        errors.append('missing Branch after line')
    if not ACCEPT.search(body):
        errors.append('missing ## Acceptance criteria')
    elif len(CHECK.findall(body)) < 1:
        errors.append('acceptance criteria need checklist items')
    depends_lines = [line for line in body.splitlines() if DEPENDS_ANY.match(line)]
    if depends_lines and not all(DEPENDS.match(line) for line in depends_lines):
        errors.append('Depends on line must be `Depends on #<n>(, #<n>)*`')
    if len(depends_lines) > 1:
        errors.append('more than one Depends on line')
    parsed = spec.parse(body)
    if UAT_HEADING.search(body) or re.search(r'(?im)^## +user +acceptance +tests?\s*$', body):
        if parsed.status != spec.READY:
            errors.append(f'UAT {parsed.status}: {parsed.reason}')
    elif parsed.status not in (spec.NOT_REQUIRED,):
        errors.append(f'unexpected UAT status {parsed.status}: {parsed.reason}')
    return errors


def main() -> int:
    files = sorted(ISSUES.glob('*.md'))
    if not files:
        print(f'no fixture issues under {ISSUES}', file=sys.stderr)
        return 1
    failed = 0
    for path in files:
        errors = check(path.read_text(encoding='utf-8'), path.name)
        if errors:
            failed += 1
            print(f'FAIL {path.name}:')
            for error in errors:
                print(f'  - {error}')
        else:
            print(f'ok   {path.name}')
    if failed:
        print(f'{failed}/{len(files)} fixture(s) failed template checks', file=sys.stderr)
        return 1
    print(f'{len(files)} fixture(s) passed template checks')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
