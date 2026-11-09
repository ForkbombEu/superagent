#!/usr/bin/env python3
"""The `gh api` and `gh pr view --json headRefOid` subset uat-test uses, against FAKE_GITHUB_URL."""
import json
import os
import sys
import urllib.error
import urllib.request


def call(method, path, body=None):
    request = urllib.request.Request(f'{os.environ["FAKE_GITHUB_URL"]}/{path.lstrip("/")}', method=method,
                                     data=None if body is None else body.encode(),
                                     headers={'authorization': f'token {os.environ["FAKE_GITHUB_TOKEN"]}'})
    try:
        with urllib.request.urlopen(request) as response:
            return response.read().decode()
    except urllib.error.HTTPError as error:
        sys.exit(f'gh: HTTP {error.code}: {error.read().decode()}')


args = sys.argv[1:]
if args[0] == 'api':
    method = args[args.index('--method') + 1] if '--method' in args else 'GET'
    path = next(arg for arg in args[1:] if arg.startswith('repos/'))
    print(call(method, path, sys.stdin.read() if '--input' in args else None))
elif args[:2] == ['pr', 'view']:
    pr = json.loads(call('GET', f'repos/{args[args.index("--repo") + 1]}/pulls/{args[2]}'))
    print(json.dumps({'headRefOid': pr['head']['sha']}))
else:
    sys.exit(f'fake gh: unsupported {args}')
