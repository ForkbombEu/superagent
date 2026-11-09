#!/usr/bin/env python3
"""The `gh pr view` and `gh pr comment` subset uat-test and claude-review use, against FAKE_GITHUB_URL."""
import json
import os
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


args = sys.argv[1:]
repository = args[args.index('--repo') + 1] if '--repo' in args else None
if args[:2] == ['pr', 'view']:
    pr = json.loads(call('GET', f'repos/{repository}/pulls/{args[2]}'))
    fields = {'headRefOid': pr['head']['sha'], 'baseRefName': 'main', 'title': pr['title']}
    print(json.dumps({name: fields[name] for name in args[args.index('--json') + 1].split(',')}))
elif args[:2] == ['pr', 'comment'] and '--body-file' in args:
    call('POST', f'repos/{repository}/issues/{args[2]}/comments', {'body': sys.stdin.read()})
else:
    sys.exit(f'fake gh: unsupported {args}')
