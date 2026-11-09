"""An in-process stand-in for the GitHub API endpoints the UAT gate and uat-test use.

It serves the PR, its issues, its files and `.superagent/uat.yaml` to readers,
takes PR comments (uat-test's reports, as claude-review posts its own) and
records every write with the token that made it, so tests can check that the
gate only reads.
"""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GATE_TOKEN, CODER_TOKEN = 'github_pat_gate_read_only', 'ghp_coder_personal'


class FakeGitHub:
    def __init__(self, repository='ForkbombEu/sample'):
        self.repository = repository
        self.prs, self.issues, self.contents = {}, {}, {}
        self.comments, self.writes = [], []
        self.lock = threading.Lock()
        handler = type('Handler', (Handler,), {'fake': self})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # Test setup.
    def add_issue(self, number, body):
        self.issues[number] = {'number': number, 'body': body, 'updated_at': f'2026-11-09T10:00:{number % 60:02d}Z'}

    def add_pr(self, number, sha, closes, fork=False):
        self.prs[number] = {'number': number, 'state': 'open', 'closes': list(closes),
                            'head': {'sha': sha, 'repo': {'full_name': 'someone/fork' if fork else self.repository}}}

    def push(self, number, sha):
        self.prs[number]['head']['sha'] = sha

    def answer(self, method, path, query, token, body):
        repo = f'repos/{self.repository}/'
        if path == 'graphql':
            return 200, self.graphql(body)
        if not path.startswith(repo):
            return 404, {'message': 'Not Found'}
        if method != 'GET':
            self.writes.append((method, path, token))
        parts = path[len(repo):].split('/')
        if method == 'GET' and parts[0] == 'pulls' and len(parts) == 2:
            pr = self.prs[int(parts[1])]
            return 200, {'number': pr['number'], 'state': pr['state'], 'head': pr['head'], 'title': 'feat: x'}
        if method == 'GET' and parts[0] == 'issues':
            number = int(parts[1])
            if number in self.prs:  # GitHub's issues API answers pull requests too, marked as such.
                return 200, {'number': number, 'body': '', 'pull_request': {'url': f'{repo}pulls/{number}'}}
            return (200, self.issues[number]) if number in self.issues else (404, {'message': 'Not Found'})
        if method == 'POST' and parts[0] == 'issues' and parts[2] == 'comments':
            self.comments.append({'pr': int(parts[1]), 'body': body['body'], 'token': token})
            return 201, {'id': len(self.comments)}
        if method == 'GET' and parts[0] == 'contents':
            key = ('/'.join(parts[1:]), query.get('ref'))
            return (200, self.contents[key]) if key in self.contents else (404, {'message': 'Not Found'})
        return 404, {'message': f'no fake for {method} {path}'}

    def graphql(self, body):
        pr = self.prs[body['variables']['number']]
        reference = lambda number: {'number': number, 'repository': {'nameWithOwner': self.repository}}
        return {'data': {'repository': {'pullRequest': {
            'closingIssuesReferences': {'nodes': [reference(n) for n in pr['closes']]}}}}}


class Handler(BaseHTTPRequestHandler):
    fake = None

    def log_message(self, *args):
        pass

    def handle_any(self):
        url = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        length = int(self.headers.get('content-length') or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        token = (self.headers.get('authorization') or '').split(' ')[-1]
        with self.fake.lock:
            status, answer = self.fake.answer(self.command, url.path.strip('/'), query, token, body)
        raw = 'raw' in (self.headers.get('accept') or '') and isinstance(answer, (bytes, str))
        data = (answer.encode() if isinstance(answer, str) else answer) if raw else \
            (b'' if answer is None else json.dumps(answer).encode())
        self.send_response(status)
        self.send_header('content-length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = handle_any
