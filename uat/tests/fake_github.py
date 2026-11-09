"""An in-process stand-in for the GitHub API endpoints the UAT controller and uat-test use.

It mirrors the permission facts the gate relies on: only the GitHub App's
installation token can create or update check runs (a personal access token
gets 403, as on GitHub), anyone with write access can post commit statuses,
an App JWT buys an installation token, and `required_check_passes` evaluates a required check
pinned to the App as a source-pinned ruleset does: only the App's newest
`superagent/uat` check run on the commit counts.
"""
import itertools
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_ID = 4242
APP_TOKEN, READ_TOKEN, CODER_TOKEN = 'ghs_app_installation', 'ghs_actions_read', 'ghp_coder_personal'


class FakeGitHub:
    def __init__(self, repository='ForkbombEu/sample'):
        self.repository = repository
        self.prs, self.issues, self.contents = {}, {}, {}
        self.check_runs, self.statuses = [], []
        self.ids = itertools.count(1000)
        self.token_requests = []
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

    def add_pr(self, number, sha, closes, files=('src/app.py',), fork=False):
        self.prs[number] = {'number': number, 'state': 'open', 'closes': list(closes), 'files': list(files),
                            'commits': [sha], 'befores': [],
                            'head': {'sha': sha, 'repo': {'full_name': 'someone/fork' if fork else self.repository}}}

    def push(self, number, sha, force=False):
        pr = self.prs[number]
        if force:
            pr['befores'].append(pr['head']['sha'])
            pr['commits'] = [sha]
        else:
            pr['commits'].append(sha)
        pr['head']['sha'] = sha

    def app_runs(self, sha):
        return sorted((run for run in self.check_runs if run['head_sha'] == sha and run['app']['id'] == APP_ID),
                      key=lambda run: run['id'], reverse=True)

    def required_check_passes(self, sha, name='superagent/uat'):
        """A ruleset requiring `name` from the App: its newest check run on sha must have succeeded."""
        runs = [run for run in self.app_runs(sha) if run['name'] == name]
        return bool(runs) and runs[0]['status'] == 'completed' and runs[0]['conclusion'] in ('success', 'neutral', 'skipped')

    def answer(self, method, path, query, token, body):
        repo = f'repos/{self.repository}/'
        is_jwt = token.count('.') == 2
        if path == f'repos/{self.repository}/installation':
            return (200, {'id': 77}) if is_jwt else (401, {'message': 'A JSON web token is required'})
        if method == 'POST' and path == 'app/installations/77/access_tokens':
            if not is_jwt:
                return 401, {'message': 'A JSON web token is required'}
            self.token_requests.append(body)
            return 201, {'token': APP_TOKEN, 'expires_at': '2099-01-01T00:00:00Z'}
        if not path.startswith(repo) and path != 'graphql':
            return 404, {'message': 'Not Found'}
        rest = path[len(repo):]
        parts = rest.split('/')
        if path == 'graphql':
            return 200, self.graphql(body)
        if method == 'GET' and parts[0] == 'pulls' and len(parts) == 2:
            pr = self.prs[int(parts[1])]
            return 200, {'number': pr['number'], 'state': pr['state'], 'head': pr['head'], 'title': 'feat: x'}
        if method == 'GET' and parts[0] == 'pulls' and parts[2] == 'files':
            return 200, [{'filename': name} for name in self.page(self.prs[int(parts[1])]['files'], query)]
        if method == 'GET' and parts[0] == 'pulls' and parts[2] == 'commits':
            return 200, [{'sha': sha} for sha in self.page(self.prs[int(parts[1])]['commits'], query)]
        if method == 'GET' and parts[0] == 'issues':
            return (200, self.issues[int(parts[1])]) if int(parts[1]) in self.issues else (404, {'message': 'Not Found'})
        if method == 'GET' and parts[0] == 'contents':
            key = ('/'.join(parts[1:]), query.get('ref'))
            return (200, self.contents[key]) if key in self.contents else (404, {'message': 'Not Found'})
        if method == 'GET' and parts[0] == 'commits' and parts[2] == 'check-runs':
            runs = [run for run in self.check_runs if run['head_sha'] == parts[1]
                    and run['name'] == query.get('check_name', run['name'])
                    and ('app_id' not in query or run['app']['id'] == int(query['app_id']))]
            return 200, {'total_count': len(runs), 'check_runs': self.page(runs, query)}
        if parts[0] == 'check-runs':
            if token != APP_TOKEN:
                return 403, {'message': 'Resource not accessible by personal access token'}
            if method == 'POST':
                run = {**body, 'id': next(self.ids), 'app': {'id': APP_ID}}
                run.setdefault('conclusion', None)
                self.check_runs.append(run)
                return 201, run
            run = next(run for run in self.check_runs if run['id'] == int(parts[1]))
            run.update(body)
            return 200, run
        if method == 'POST' and parts[0] == 'statuses':
            self.statuses.append({'sha': parts[1], **body, 'token': token})
            return 201, body
        return 404, {'message': f'no fake for {method} {path}'}

    @staticmethod
    def page(items, query):
        size, page = int(query.get('per_page', 30)), int(query.get('page', 1))
        return items[(page - 1) * size:page * size]

    def graphql(self, body):
        variables = body['variables']
        reference = lambda number: {'number': number, 'repository': {'nameWithOwner': self.repository}}
        if 'pullRequests(' in body['query']:
            nodes = [{'number': pr['number'], 'closingIssuesReferences': {'nodes': [reference(n) for n in pr['closes']]}}
                     for pr in self.prs.values() if pr['state'] == 'open']
            return {'data': {'repository': {'pullRequests': {'nodes': nodes}}}}
        pr = self.prs[variables['number']]
        return {'data': {'repository': {'pullRequest': {
            'closingIssuesReferences': {'nodes': [reference(n) for n in pr['closes']]},
            'timelineItems': {'nodes': [{'beforeCommit': {'oid': sha}} for sha in pr['befores']]}}}}}


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
