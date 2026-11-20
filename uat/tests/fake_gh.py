#!/usr/bin/env python3
"""The `gh` subset superagent / uat-test / claude-review use, against FAKE_GITHUB_URL."""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def call(method, path, body=None):
    request = urllib.request.Request(f'{os.environ["FAKE_GITHUB_URL"]}/{path.lstrip("/")}', method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={'authorization': f'token {os.environ["FAKE_GITHUB_TOKEN"]}'})
    try:
        with urllib.request.urlopen(request) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        sys.exit(f'gh: HTTP {error.code}: {error.read().decode()}')


def pr_fields(pr, names):
    fields = {
        'headRefOid': pr['head']['sha'],
        'baseRefName': 'main',
        'title': pr['title'],
        'number': pr['number'],
        'isDraft': bool(pr.get('draft', False)),
        'reviews': [
            {'id': str(index), 'state': review.get('state', 'COMMENTED'),
             'author': {'login': review.get('author', 'reviewer')}}
            for index, review in enumerate(pr.get('reviews') or [])
        ],
    }
    return {name: fields[name] for name in names}


def load_pr(repository, number=None):
    if number is not None:
        return json.loads(call('GET', f'repos/{repository}/pulls/{number}'))
    branch = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                            capture_output=True, text=True, check=True).stdout.strip()
    listing = json.loads(call('GET', f'repos/{repository}/pulls?head={repository}:{branch}'))
    if not listing:
        sys.stderr.write(f'gh: no pull requests found for branch "{branch}"\n')
        sys.exit(1)
    # Listing is thin; reload full PR for draft/checks/reviews.
    return json.loads(call('GET', f'repos/{repository}/pulls/{listing[0]["number"]}'))


def checks_exit(checks, required_only):
    selected = [c for c in checks if c.get('required')] if required_only else list(checks)
    if required_only and not selected:
        sys.stderr.write('no required checks on this pull request\n')
        return 0
    buckets = []
    for check in selected:
        bucket = check.get('bucket') or {
            'pass': 'pass', 'success': 'pass',
            'fail': 'fail', 'failure': 'fail',
            'pending': 'pending', 'queued': 'pending', 'in_progress': 'pending',
            'skipping': 'skipping', 'skipped': 'skipping', 'neutral': 'skipping',
            'cancel': 'cancel', 'cancelled': 'cancel',
        }.get(str(check.get('state', 'pending')).lower(), 'pending')
        name = check.get('name', 'check')
        print(f'{name}\t{bucket}\t0\t\t')
        buckets.append(bucket)
    if any(b == 'pending' for b in buckets):
        return 8
    if any(b == 'fail' for b in buckets):
        return 1
    return 0


args = sys.argv[1:]
repository = args[args.index('--repo') + 1] if '--repo' in args else None
if args[:2] == ['pr', 'view']:
    json_fields = args[args.index('--json') + 1].split(',') if '--json' in args else []
    # `gh pr view --json ...` with no number uses the current branch.
    if len(args) > 2 and args[2] not in ('--repo', '--json'):
        number = args[2]
        pr = load_pr(repository, number)
    else:
        pr = load_pr(repository)
    print(json.dumps(pr_fields(pr, json_fields)))
elif args[:2] == ['pr', 'comment'] and '--body-file' in args:
    if os.environ.get('FAKE_GH_FAIL_COMMENT'):
        sys.stderr.write('gh: failed to post comment\n')
        sys.exit(1)
    call('POST', f'repos/{repository}/issues/{args[2]}/comments', {'body': sys.stdin.read()})
elif args[:2] == ['pr', 'checks']:
    number = None
    skip_next = False
    value_flags = {'--repo', '--json', '--template', '--jq', '--interval', '-R', '-t', '-q', '-i'}
    for arg in args[2:]:
        if skip_next:
            skip_next = False
            continue
        if arg in value_flags:
            skip_next = True
            continue
        if arg.startswith('-'):
            continue
        number = arg
        break
    pr = load_pr(repository, number)
    required = '--required' in args
    sys.exit(checks_exit(pr.get('checks') or [], required))
elif args[:2] == ['pr', 'ready']:
    number = args[2]
    call('POST', f'repos/{repository}/pulls/{number}/ready', {})
else:
    sys.exit(f'fake gh: unsupported {args}')
