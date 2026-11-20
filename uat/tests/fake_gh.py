#!/usr/bin/env python3
"""The `gh pr view` and `gh pr comment` subset uat-test and claude-review use, against FAKE_GITHUB_URL."""
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
    }
    return {name: fields[name] for name in names}


args = sys.argv[1:]
repository = args[args.index('--repo') + 1] if '--repo' in args else None
if args[:2] == ['pr', 'view']:
    json_fields = args[args.index('--json') + 1].split(',') if '--json' in args else []
    # `gh pr view --json ...` with no number uses the current branch.
    if len(args) > 2 and args[2] not in ('--repo', '--json'):
        number = args[2]
        pr = json.loads(call('GET', f'repos/{repository}/pulls/{number}'))
    else:
        branch = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                capture_output=True, text=True, check=True).stdout.strip()
        listing = json.loads(call('GET', f'repos/{repository}/pulls?head={repository}:{branch}'))
        if not listing:
            sys.stderr.write(f'gh: no pull requests found for branch "{branch}"\n')
            sys.exit(1)
        pr = listing[0]
    print(json.dumps(pr_fields(pr, json_fields)))
elif args[:2] == ['pr', 'comment'] and '--body-file' in args:
    if os.environ.get('FAKE_GH_FAIL_COMMENT'):
        sys.stderr.write('gh: failed to post comment\n')
        sys.exit(1)
    call('POST', f'repos/{repository}/issues/{args[2]}/comments', {'body': sys.stdin.read()})
else:
    sys.exit(f'fake gh: unsupported {args}')
