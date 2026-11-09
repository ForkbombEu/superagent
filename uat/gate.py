"""The UAT gate service: `python3 -m uat.gate` (the uat-gate service in compose.yaml).

DSH Superagent stays the only orchestrator: its worker asks for a test of a
PR head with scripts/uat-test and decides what to do with the answer, as it
does with claude-review. The gate only runs and keeps the run it is asked for:

  POST /v1/uat/runs            {"repository", "pr", "head_sha"} -> the run (new or the same key's)
  GET  /v1/uat/runs/<id>                                         -> the run and, once complete, its report
  GET  /v1/uat/runs/<id>/files/<path>                            -> one kept artifact (redacted, capped)
  POST /v1/uat/verify          {"repository", "pr", "head_sha"} -> whether a PASS still holds (pre-merge)
  GET  /healthz

A request carries no verdict and the API accepts no other field. Requests need
the shared bearer token and must come from an allowed client host (the
superagent service), and everything a run is bound to (PR head, issue, spec
hash, environment) is re-read from GitHub (uat.controller.derive) with a
GitHub read credential that never reaches a container. The gate publishes
nothing on GitHub: the verdict reaches the PR through uat-test, and the merge
is enforced by the Delivery requirements, as for the Claude Code review.

State is a SQLite database on a gate-only volume: runs, at most one active run
per PR (a unique index), the PR failure budgets and each run's kept artifacts;
it survives restarts. PR code runs only on the runner, the Docker daemon
DOCKER_HOST names (uat-runner), never in this container.
"""
import collections
import hmac
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from uat import controller, run, sandbox
from uat import result as results
from uat.github import GitHub, GitHubError

ACTIVE, COMPLETE = ('QUEUED', 'RUNNING'), 'COMPLETE'
# A key with one of these is answered again rather than re-run; a READY spec's BLOCKED or STALE is re-run.
FINAL = (controller.PASS, controller.FAIL, controller.NOT_REQUIRED)
MAX_FILE_BYTES = 1024 * 1024
RATE_WINDOW, RATE_LIMIT = 600, 20
SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, repository TEXT NOT NULL, pr INTEGER NOT NULL, head_sha TEXT NOT NULL,
  key TEXT NOT NULL, issue INTEGER, spec_sha256 TEXT, environment TEXT, status TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('QUEUED', 'RUNNING', 'COMPLETE')), verdict TEXT,
  failed_rounds INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  snapshot TEXT, title TEXT, summary TEXT, text TEXT,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_pr ON runs (repository, pr) WHERE state != 'COMPLETE';
CREATE INDEX IF NOT EXISTS runs_by_key ON runs (key, created);
CREATE TABLE IF NOT EXISTS budgets (
  repository TEXT NOT NULL, pr INTEGER NOT NULL, failed_rounds INTEGER NOT NULL, PRIMARY KEY (repository, pr));
CREATE TABLE IF NOT EXISTS origins (
  repository TEXT NOT NULL, pr INTEGER NOT NULL, issue INTEGER NOT NULL, PRIMARY KEY (repository, pr));
'''


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Gate:
    def __init__(self, ctx, state_dir, work_dir, executor=None, concurrency=2, retention_days=30):
        self.ctx, self.state_dir, self.work_dir = ctx, state_dir, work_dir
        self.executor = executor
        self.concurrency, self.retention = concurrency, retention_days * 86400
        os.makedirs(os.path.join(state_dir, 'runs'), exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(state_dir, 'gate.db'), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.pr_locks = collections.defaultdict(threading.Lock)
        self.wake = threading.Condition()
        self.requests = collections.defaultdict(collections.deque)
        self.stopping = threading.Event()

    # Storage.

    def query(self, sql, *args):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql, *args):
        rows = self.query(sql, *args)
        return rows[0] if rows else None

    def budget(self, repository, pr):
        row = self.one('SELECT failed_rounds FROM budgets WHERE repository = ? AND pr = ?', repository, pr)
        return row['failed_rounds'] if row else 0

    def raise_budget(self, repository, pr, failed_rounds):
        self.query('INSERT INTO budgets VALUES (?, ?, ?) ON CONFLICT (repository, pr) DO UPDATE '
                   'SET failed_rounds = max(failed_rounds, excluded.failed_rounds)', repository, pr, failed_rounds)

    def finish_run(self, run_id, verdict, failed_rounds, title, summary, text, expected_state):
        """Complete a run unless it was completed meanwhile (superseded); True when this call did."""
        with self.lock:
            changed = self.db.execute(
                'UPDATE runs SET state = ?, verdict = ?, failed_rounds = ?, title = ?, summary = ?, text = ?, '
                'updated = ? WHERE id = ? AND state = ?',
                (COMPLETE, verdict, failed_rounds, title, summary, text, time.time(), run_id, expected_state)).rowcount
        return changed == 1

    def view(self, row):
        """A run as uat-test sees it: its binding (exact head SHA, spec hash, environment) and verdict."""
        answer = {'run_id': row['id'], 'repository': row['repository'], 'pr': row['pr'], 'head_sha': row['head_sha'],
                  'issue': row['issue'], 'spec_sha256': row['spec_sha256'], 'environment': row['environment'],
                  'state': row['state'], 'verdict': row['verdict'], 'failed_rounds': row['failed_rounds'],
                  'max_failed_rounds': self.ctx.policy.max_failed_rounds}
        if row['state'] == COMPLETE:
            answer.update(title=row['title'], summary=row['summary'], text=row['text'])
        return answer

    # Requests.

    def allowed(self, repository, pr, head_sha):
        if repository.lower() != self.ctx.repository.lower():
            raise Refused(403, f'the gate tests {self.ctx.repository} only')
        with self.lock:
            now, recent = time.time(), self.requests[(repository, pr)]
            while recent and recent[0] < now - RATE_WINDOW:
                recent.popleft()
            if len(recent) >= RATE_LIMIT:
                raise Refused(429, f'more than {RATE_LIMIT} requests for PR #{pr} in {RATE_WINDOW // 60} minutes')
            recent.append(now)

    def binding(self, repository, pr, issue):
        """The PR's current binding (controller.derive) for the issue its ticket started from: the one pinned
        for the PR, else the one the request names. An issue is pinned only once bound (an issue of the
        repository that the PR closes), so a mistaken first request pins nothing, and no later request can
        change it. A request that names no issue, while none is pinned, is bound by `Closes` lines alone: the
        gate cannot learn a ticket's issue from anything the worker could not alter (README, Limits)."""
        row = self.one('SELECT issue FROM origins WHERE repository = ? AND pr = ?', repository, pr)
        if row and issue is not None and issue != row['issue']:
            raise Refused(409, f'PR #{pr} is bound to issue #{row["issue"]}, which its ticket started from, not #{issue}')
        origin = row['issue'] if row else issue
        found = controller.derive(self.ctx, pr, origin)
        if not row and found['origin_bound']:
            self.query('INSERT INTO origins VALUES (?, ?, ?)', repository, pr, origin)
        return found

    def request(self, repository, pr, head_sha, issue=None):
        """The run for this PR head: the same key's when it already has an answer, else a new one."""
        self.allowed(repository, pr, head_sha)
        ctx = self.ctx
        with self.pr_locks[(repository, pr)]:
            found = self.binding(repository, pr, issue)
            if found['status'] == 'CLOSED':
                raise Refused(409, 'the pull request is not open')
            if found['sha'] != head_sha:
                raise Refused(409, f'the PR head is {found["sha"]}, not {head_sha}: test the current head')
            same = self.one('SELECT * FROM runs WHERE key = ? ORDER BY created DESC LIMIT 1', found['key'])
            if same and (same['state'] in ACTIVE or same['verdict'] in FINAL
                         or (same['verdict'] == controller.BLOCKED and found['status'] != 'READY')):
                return self.view(same)
            for active in self.query('SELECT * FROM runs WHERE repository = ? AND pr = ? AND state != ?',
                                     repository, pr, COMPLETE):
                self.supersede(active)
            prior = self.budget(repository, pr)
            run_id = uuid.uuid4().hex[:16]
            verdict, failed, title, summary, snapshot = controller.admit(ctx, found, prior, run_id)
            now = time.time()
            final = snapshot is None
            self.query('INSERT INTO runs (id, repository, pr, head_sha, key, issue, spec_sha256, environment, status, '
                       'state, verdict, failed_rounds, snapshot, title, summary, text, created, updated) '
                       'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       run_id, repository, pr, head_sha, found['key'], found['issue'], found['spec_sha256'],
                       found['environment'], found['status'], COMPLETE if final else 'QUEUED',
                       verdict if final else None, failed, None if final else json.dumps(snapshot),
                       title if final else None, summary if final else None, '' if final else None, now, now)
            self.raise_budget(repository, pr, failed)
        with self.wake:
            self.wake.notify_all()
        return self.view(self.one('SELECT * FROM runs WHERE id = ?', run_id))

    def supersede(self, active):
        """A newer request for the PR replaces an active run: STALE, and its containers go."""
        title, summary = 'UAT result is stale', (
            f'A request for a newer head or spec replaced UAT run `{active["id"]}`; this result applies to nothing '
            'current.')
        if self.finish_run(active['id'], controller.STALE, active['failed_rounds'], title, summary, '', active['state']):
            sandbox.remove(f'superagent-uat-{active["id"]}')

    def verify(self, repository, pr, head_sha, issue=None):
        """Before merging: is the answer for the current binding (the key `request` would use now) a PASS or
        NOT_REQUIRED? A result for another issue, spec, environment or policy does not count."""
        self.allowed(repository, pr, head_sha)
        with self.pr_locks[(repository, pr)]:
            found = self.binding(repository, pr, issue)
            if found['status'] == 'CLOSED' or found['sha'] != head_sha:
                return {'current': False, 'run': None, 'reason': 'the pull request is not open'
                        if found['status'] == 'CLOSED' else f'the PR head moved to {found["sha"]}'}
            row = self.one('SELECT * FROM runs WHERE key = ? AND state = ? ORDER BY created DESC LIMIT 1',
                           found['key'], COMPLETE)
            if row and row['verdict'] in (controller.PASS, controller.NOT_REQUIRED):
                return {'current': True, 'reason': 'the result holds for the current head, issue, spec and policy',
                        'run': self.view(row)}
            if row:
                return {'current': False, 'reason': f'the result for the current binding is {row["verdict"]}',
                        'run': self.view(row)}
            earlier = self.one('SELECT * FROM runs WHERE repository = ? AND pr = ? AND head_sha = ? AND state = ? '
                               'ORDER BY created DESC LIMIT 1', repository, pr, head_sha, COMPLETE)
            return {'current': False, 'run': earlier and self.view(earlier),
                    'reason': 'no UAT result for the current binding' + (
                        ': the issue, its UAT, the environment or the UAT policy changed since the last result for '
                        'this head, which is STALE for it' if earlier else '')}

    def artifact(self, run_id, path):
        root = os.path.realpath(os.path.join(self.state_dir, 'runs', run_id))
        full = os.path.join(root, path)
        if not self.one('SELECT 1 FROM runs WHERE id = ?', run_id) or os.path.realpath(full) != os.path.abspath(full) \
                or not os.path.abspath(full).startswith(root + os.sep) or not os.path.isfile(full):
            raise Refused(404, 'no such artifact')
        with open(full, 'rb') as file:
            return file.read(MAX_FILE_BYTES)

    # Work.

    def claim(self):
        with self.lock:
            if self.db.execute('SELECT count(*) FROM runs WHERE state = ?', ('RUNNING',)).fetchone()[0] >= self.concurrency:
                return None
            row = self.db.execute("SELECT * FROM runs WHERE state = 'QUEUED' ORDER BY created LIMIT 1").fetchone()
            if row:
                self.db.execute("UPDATE runs SET state = 'RUNNING', attempts = attempts + 1, updated = ? WHERE id = ?",
                                (time.time(), row['id']))
            return row

    def work(self):
        while not self.stopping.is_set():
            row = self.claim()
            if not row:
                with self.wake:
                    self.wake.wait(5)
                continue
            try:
                self.process(row)
            except Exception as error:  # Never leave a run RUNNING because of a gate bug or GitHub outage.
                controller.log(f'run {row["id"]} failed in the gate: {error!r}')
                self.finish_run(row['id'], controller.BLOCKED, row['failed_rounds'], 'UAT blocked',
                                f'The UAT gate failed: {error}', '', 'RUNNING')
            with self.wake:
                self.wake.notify_all()

    def process(self, row):
        snapshot = json.loads(row['snapshot'])
        run_dir = os.path.join(self.work_dir, row['id'])
        try:
            outcome = self.executor(self.ctx, snapshot, run_dir)
        except Exception as error:  # The runner, not the PR: BLOCKED, published like any verdict.
            controller.log(f'run {row["id"]}: the executor failed: {error!r}')
            outcome = results.Outcome(controller.BLOCKED, [f'the UAT runner failed: {type(error).__name__}: {error}'])
        finally:
            kept = os.path.join(self.state_dir, 'runs', row['id'])
            if os.path.isdir(os.path.join(run_dir, 'out')):
                shutil.rmtree(kept, ignore_errors=True)
                shutil.copytree(os.path.join(run_dir, 'out'), kept, symlinks=True)
            remove_dir(run_dir)
        current = self.one('SELECT state FROM runs WHERE id = ?', row['id'])
        if current['state'] != 'RUNNING':
            return  # Superseded meanwhile: already STALE.
        verdict, failed, title, summary, text = controller.conclude(self.ctx, snapshot, outcome)
        if self.finish_run(row['id'], verdict, failed, title, summary, text, 'RUNNING'):
            self.raise_budget(row['repository'], row['pr'], failed)

    def recover(self):
        """After a gate restart: runs left RUNNING lost their process. Re-run each once; then BLOCKED.

        Whatever any run of this gate left on the runner goes first (tester attempts included), found by its
        label: nothing runs now, and a superseded run may have been mid-way too."""
        mine = {f'superagent-uat-{row["id"]}' for row in self.query('SELECT id FROM runs')}
        for prefix in sandbox.owners() & mine:
            sandbox.remove(prefix)
        for row in self.query("SELECT * FROM runs WHERE state = 'RUNNING'"):
            remove_dir(os.path.join(self.work_dir, row['id']))
            if row['attempts'] < 2:
                self.query("UPDATE runs SET state = 'QUEUED', updated = ? WHERE id = ?", time.time(), row['id'])
                continue
            title, summary = 'UAT blocked', 'The UAT gate restarted during this run twice; it was not completed.'
            self.finish_run(row['id'], controller.BLOCKED, row['failed_rounds'], title, summary, '', 'RUNNING')

    def cleanup(self):
        """Drop the kept files of runs older than the retention period (their records stay)."""
        for row in self.query('SELECT id FROM runs WHERE state = ? AND updated < ?', COMPLETE, time.time() - self.retention):
            shutil.rmtree(os.path.join(self.state_dir, 'runs', row['id']), ignore_errors=True)

    def start(self, interval=3600):
        for _ in range(self.concurrency):
            threading.Thread(target=self.work, daemon=True).start()

        def cleaning():
            while not self.stopping.wait(interval):
                try:
                    self.cleanup()
                except OSError as error:
                    controller.log(f'cleanup failed: {error!r}')
        threading.Thread(target=cleaning, daemon=True).start()


def remove_dir(path):
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):  # Files a container left with other permissions.
        run.remove_tree(path, os.environ.get('SUPERAGENT_UAT_CLEANUP_IMAGE', 'superagent-uat-sandbox:gate'))


class Runner:
    """The default executor: build the images once on the runner, then controller.execute.
    `token` is the gate's GitHub read credential, used only for the checkout and redacted from kept files."""

    def __init__(self, token, server):
        self.token, self.server, self.images, self.lock = token, server, None, threading.Lock()

    def __call__(self, ctx, snapshot, run_dir):
        try:
            with self.lock:
                if self.images is None:
                    self.images = controller.build_images('gate')
        except Exception as error:
            return results.Outcome(controller.BLOCKED, [f'the UAT runner is unavailable: {type(error).__name__}: {error}'])
        return controller.execute(ctx, snapshot, run_dir, self.images, token=self.token, server=self.server)


# HTTP.

def client_addresses(names):
    addresses = set()
    for name in names:
        try:
            addresses |= {info[4][0] for info in socket.getaddrinfo(name, None)}
        except OSError:
            pass
    return addresses


def handler(gate, token, clients):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            controller.log(f'{self.client_address[0]} {format % args}')

        def answer(self, status, body, content_type='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('content-type', content_type)
            self.send_header('content-length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if self.client_address[0] not in client_addresses(clients):
                self.answer(403, {'error': 'not an allowed client'})
                return False
            given = (self.headers.get('authorization') or '').removeprefix('Bearer ')
            if not token or not hmac.compare_digest(given.encode(), token.encode()):
                self.answer(401, {'error': 'missing or wrong token'})
                return False
            return True

        def body(self):
            length = int(self.headers.get('content-length') or 0)
            if length > 4096:
                raise Refused(413, 'request too large')
            try:
                data = json.loads(self.rfile.read(length) or b'null')
            except ValueError:
                raise Refused(400, 'the body is not JSON') from None
            if not isinstance(data, dict) or not {'repository', 'pr', 'head_sha'} <= set(data) <= {
                    'repository', 'pr', 'head_sha', 'issue'}:
                raise Refused(400, 'the body is exactly {"repository", "pr", "head_sha"} and, for a ticket started '
                                   'from an issue, "issue": a request, never a verdict')
            repository, pr, sha, issue = data['repository'], data['pr'], data['head_sha'], data.get('issue')
            if not (isinstance(repository, str) and isinstance(pr, int) and not isinstance(pr, bool) and pr > 0
                    and isinstance(sha, str) and len(sha) == 40 and all(c in '0123456789abcdef' for c in sha)
                    and (issue is None or isinstance(issue, int) and not isinstance(issue, bool) and issue > 0)):
                raise Refused(400, 'repository must be owner/repo, pr and issue numbers and head_sha a full '
                                   '40-character SHA')
            return repository, pr, sha, issue

        def do_GET(self):
            if self.path == '/healthz':
                return self.answer(200, {'ok': True, 'configured': gate is not None})
            if not self.authorized():
                return
            if gate is None:
                return self.answer(503, {'error': 'UAT is not configured in this gate'})
            parts = self.path.split('?')[0].strip('/').split('/')
            try:
                if parts[:3] == ['v1', 'uat', 'runs'] and len(parts) == 4:
                    row = gate.one('SELECT * FROM runs WHERE id = ?', parts[3])
                    return self.answer(200, gate.view(row)) if row else self.answer(404, {'error': 'no such run'})
                if parts[:3] == ['v1', 'uat', 'runs'] and len(parts) > 5 and parts[4] == 'files':
                    return self.answer(200, gate.artifact(parts[3], '/'.join(parts[5:])), 'text/plain; charset=utf-8')
            except Refused as refused:
                return self.answer(refused.status, {'error': str(refused)})
            self.answer(404, {'error': 'not found'})

        def do_POST(self):
            if not self.authorized():
                return
            if gate is None:
                return self.answer(503, {'error': 'UAT is not configured in this gate'})
            try:
                if self.path == '/v1/uat/runs':
                    return self.answer(200, gate.request(*self.body()))
                if self.path == '/v1/uat/verify':
                    return self.answer(200, gate.verify(*self.body()))
            except Refused as refused:
                return self.answer(refused.status, {'error': str(refused)})
            except GitHubError as error:
                return self.answer(502, {'error': f'GitHub: {error}'})
            self.answer(404, {'error': 'not found'})
    return Handler


def github_token(environ):
    """The gate's GitHub read credential; every variable that may hold one leaves the environment, so no
    child process (docker, git) inherits it."""
    tokens = [environ.pop(name, '') for name in ('SUPERAGENT_UAT_GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN')]
    return next((token for token in tokens if token), '')


def main():
    environ = os.environ
    github = github_token(environ)
    token = environ.pop('SUPERAGENT_UAT_GATE_TOKEN', '')
    repository = environ.get('GITHUB_REPOSITORY', '')
    policy = controller.Policy(environ)
    gate = None
    if github and repository and len(token) >= 32:
        ctx = controller.Context(repository, GitHub(github), policy)
        gate = Gate(ctx, environ.get('SUPERAGENT_UAT_STATE_DIR', '/var/lib/uat-gate'),
                    environ.get('SUPERAGENT_UAT_WORK_DIR', '/var/lib/uat-work'),
                    executor=Runner(github, environ.get('GITHUB_SERVER_URL', 'https://github.com')),
                    concurrency=int(environ.get('SUPERAGENT_UAT_CONCURRENCY') or 2),
                    retention_days=int(environ.get('SUPERAGENT_UAT_RETENTION_DAYS') or 30))
        gate.recover()
        gate.start()
    else:
        controller.log('not configured (a GitHub read token in SUPERAGENT_UAT_GITHUB_TOKEN or GH_TOKEN, '
                       'GITHUB_REPOSITORY and a SUPERAGENT_UAT_GATE_TOKEN of 32+ characters): answering 503')
    clients = (environ.get('SUPERAGENT_UAT_GATE_CLIENTS') or 'superagent').split()
    server = ThreadingHTTPServer(('0.0.0.0', int(environ.get('SUPERAGENT_UAT_GATE_PORT') or 8090)),
                                 handler(gate, token, clients))
    sandbox.exit_on_signals()
    controller.log(f'listening on {server.server_address[1]} for {", ".join(clients)}')
    try:
        server.serve_forever()
    finally:
        if gate:
            gate.stopping.set()


if __name__ == '__main__':
    sys.exit(main())
