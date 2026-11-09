"""The trusted UAT controller: `python3 -m uat.controller start|test|finish|route`.

It runs in the target repository's superagent-uat workflow (templates/superagent-uat.yml),
from this repository at a pinned commit, never from the pull request:

- start (App token): derive the PR head, linked issue, UAT spec, environment and
  failure budget from GitHub alone, publish `superagent/uat` in progress on the
  head (withdrawing any earlier result there) and hand a snapshot to `test`;
  publish the verdict at once when there is nothing to run (no UAT, blocked,
  budget used up) or skip a key that already has a PASS or FAIL.
- test (no GitHub write access; DeepSeek key only): check out the head and run
  the sandbox and the tester; write outcome.json and the artifacts.
- finish (App token): re-verify a PASS against the snapshot's trusted metadata,
  re-derive the key so a head, spec or environment change makes the result STALE,
  count the failed round and publish the verdict.
- route (App token, on issue edits): mark the UAT result of every open PR that
  closes the issue STALE when the edit changed what it would test.

Each check run carries a marker (a JSON comment in its text) that the App alone
can write: its verdict, idempotency key and the PR's failed rounds so far.
"""
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse

from uat import result as results
from uat import sandbox, spec
from uat.github import GitHub, GitHubError

CHECK_NAME = 'superagent/uat'
NOT_REQUIRED, STALE, PENDING = 'NOT_REQUIRED', 'STALE', 'PENDING'
PASS, FAIL, BLOCKED = results.PASS, results.FAIL, results.BLOCKED
MARKER = re.compile(r'<!-- superagent-uat (\{.*?\}) -->')
PRIVILEGED_PATHS = ('.github/workflows/', '.github/actions/')
UAT = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATTERN = re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})\b')
LINKS = '''query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    closingIssuesReferences(first: 20) { nodes { number repository { nameWithOwner } } }
    timelineItems(itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT], first: 100) {
      nodes { ... on HeadRefForcePushedEvent { beforeCommit { oid } } } } } } }'''
OPEN_PRS = '''query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) { pullRequests(states: OPEN, first: 100) { nodes {
    number closingIssuesReferences(first: 20) { nodes { number repository { nameWithOwner } } } } } } }'''


class Context:
    def __init__(self, environ=os.environ):
        self.env = environ
        self.repository = environ['GITHUB_REPOSITORY']
        self.owner, self.name = self.repository.split('/')
        self.read = GitHub(environ.get('UAT_READ_TOKEN', ''), environ.get('GITHUB_API_URL'))
        self.checks = GitHub(environ.get('UAT_CHECK_TOKEN', ''), environ.get('GITHUB_API_URL'))
        self.app_id = int(environ.get('UAT_APP_ID') or 0)
        self.environments = set(re.split(r'[\s,]+', environ.get('UAT_ENVIRONMENTS', 'local').strip())) - {''}
        self.max_failed_rounds = int(environ.get('UAT_MAX_FAILED_ROUNDS') or 3)
        server = environ.get('GITHUB_SERVER_URL', 'https://github.com')
        self.run_url = f'{server}/{self.repository}/actions/runs/{environ.get("GITHUB_RUN_ID", "0")}'
        self.out_dir = environ.get('UAT_OUT_DIR') or os.path.join(environ.get('RUNNER_TEMP', '/tmp'), 'superagent-uat', 'out')

    def event(self):
        with open(self.env['GITHUB_EVENT_PATH'], encoding='utf-8') as file:
            return json.load(file)

    def output(self, **values):
        with open(self.env['GITHUB_OUTPUT'], 'a', encoding='utf-8') as file:
            for key, value in values.items():
                file.write(f'{key}={value}\n')


def log(message):
    print(f'superagent-uat: {message}', flush=True)


# Deriving what a PR must be tested against, from GitHub only.

def derive(ctx, number):
    """Everything the verdict is bound to, as a dict whose `status` is READY, NOT_REQUIRED, BLOCKED, FAIL or CLOSED."""
    pr = ctx.read.get(f'repos/{ctx.repository}/pulls/{number}')
    found = {'pr': number, 'sha': pr['head']['sha'], 'issue': None, 'environment': None, 'spec_sha256': None,
             'befores': []}
    if pr['state'] != 'open':
        return {**found, 'status': 'CLOSED', 'reason': 'the pull request is not open'}
    links = ctx.read.graphql(LINKS, owner=ctx.owner, name=ctx.name, number=number)['repository']['pullRequest']
    found['befores'] = [node['beforeCommit']['oid'] for node in links['timelineItems']['nodes']
                        if node and node.get('beforeCommit')]

    def verdict(status, reason, **more):
        found.update(more)
        found['key'] = f'{ctx.repository}/{number}/{found["sha"]}/{found["spec_sha256"] or status}/{found["environment"] or "-"}'
        return {**found, 'status': status, 'reason': reason}

    if (pr['head'].get('repo') or {}).get('full_name') != ctx.repository:
        return verdict(BLOCKED, 'pull requests from forks are not tested')
    files = [item['filename'] for item in ctx.read.paginate(f'repos/{ctx.repository}/pulls/{number}/files')]
    privileged = sorted(name for name in files if name.startswith(PRIVILEGED_PATHS))
    if privileged:
        return verdict(BLOCKED, f'the pull request changes GitHub Actions code that can reach the UAT controller\'s '
                                f'credentials ({", ".join(privileged[:5])}); a person must review and merge it')
    issues = sorted(node['number'] for node in links['closingIssuesReferences']['nodes']
                    if node['repository']['nameWithOwner'].lower() == ctx.repository.lower())
    if not issues:
        return verdict(FAIL, 'the pull request closes no issue of this repository: name the issue it resolves '
                             'with a `Closes #<n>` line in its description')
    specs = {}
    for issue in issues:
        body = ctx.read.get(f'repos/{ctx.repository}/issues/{issue}')
        specs[issue] = (spec.parse(body.get('body')), body.get('updated_at'))
    blocked = [(issue, parsed.reason) for issue, (parsed, _) in specs.items() if parsed.status == spec.BLOCKED]
    if blocked:
        return verdict(BLOCKED, '; '.join(f'issue #{issue}: {reason}' for issue, reason in blocked), issue=blocked[0][0])
    ready = [issue for issue, (parsed, _) in specs.items() if parsed.status == spec.READY]
    if not ready:
        return verdict(NOT_REQUIRED, f'issue {", ".join(f"#{i}" for i in issues)} has no User Acceptance Tests section',
                       issue=issues[0])
    if len(ready) > 1:
        return verdict(BLOCKED, f'issues {", ".join(f"#{i}" for i in ready)} each have User Acceptance Tests: '
                                'one pull request resolves one issue with UAT', issue=ready[0])
    issue = ready[0]
    parsed, updated_at = specs[issue]
    try:
        raw = ctx.read.request('GET', f'repos/{ctx.repository}/contents/{sandbox.CONFIG_PATH}',
                               params={'ref': found['sha']}, raw=True)
        config, problem = sandbox.parse_config(raw)
    except GitHubError as error:
        if error.status != 404:
            raise
        config, problem = None, None
    if problem:
        return verdict(FAIL, problem, issue=issue)
    name = sandbox.environment_name(parsed.environment, config)
    entry = sandbox.environment_entry(name, config)
    if name not in ctx.environments:
        return verdict(BLOCKED, f'environment {name!r} is not enabled in this repository\'s superagent-uat workflow '
                                f'(enabled: {", ".join(sorted(ctx.environments))})', issue=issue)
    if entry is None:
        return verdict(FAIL, f'environment {name!r} is not configured: add it to {sandbox.CONFIG_PATH} '
                             '(type local with start and ready)', issue=issue)
    return verdict(spec.READY, '', issue=issue, environment=name, spec_sha256=spec.spec_hash(parsed, name),
                   spec=parsed, entry=entry, issue_updated_at=updated_at)


# Check runs and their markers.

def marker_of(check_run):
    match = MARKER.search((check_run.get('output') or {}).get('text') or '')
    return json.loads(match.group(1)) if match else None


def app_check_runs(ctx, sha):
    """This App's superagent/uat check runs on a commit, newest (highest id) first."""
    runs = ctx.read.paginate(f'repos/{ctx.repository}/commits/{sha}/check-runs', key='check_runs',
                             check_name=CHECK_NAME, app_id=ctx.app_id, filter='all')
    runs = [run for run in runs if (run.get('app') or {}).get('id') == ctx.app_id and run['name'] == CHECK_NAME]
    return sorted(runs, key=lambda run: run['id'], reverse=True)


def prior_failed_rounds(ctx, found):
    """The PR's failed UAT rounds so far: the largest count any of the App's check runs on its commits carries."""
    shas = {commit['sha'] for commit in ctx.read.paginate(f'repos/{ctx.repository}/pulls/{found["pr"]}/commits', limit=250)}
    shas |= set(found['befores']) | {found['sha']}
    counts = [marker.get('failed_rounds', 0) for sha in sorted(shas) for run in app_check_runs(ctx, sha)
              if (marker := marker_of(run)) and marker.get('pr') == found['pr']]
    return max(counts, default=0)


def clean(text, limit):
    text = TOKEN_PATTERN.sub('[REDACTED]', text)
    return text if len(text) <= limit else text[:limit - 40] + '\n\n(truncated)'


def publish(ctx, found, verdict, title, summary, failed_rounds, text='', check_id=None):
    """Create or complete the check run on found['sha']; return its id."""
    marker = {'v': 1, 'verdict': verdict, 'key': found['key'], 'pr': found['pr'], 'issue': found['issue'],
              'sha': found['sha'], 'spec_sha256': found['spec_sha256'], 'environment': found['environment'],
              'failed_rounds': failed_rounds, 'max_failed_rounds': ctx.max_failed_rounds, 'run_url': ctx.run_url}
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    body = {'name': CHECK_NAME, 'details_url': ctx.run_url, 'external_id': found['key'][:255],
            'output': {'title': clean(title, 255), 'summary': clean(summary, 60000),
                       'text': clean(text, 60000) + f'\n\n<!-- superagent-uat {json.dumps(marker)} -->'}}
    if verdict == PENDING:
        body.update(status='in_progress', started_at=now)
    else:
        body.update(status='completed', completed_at=now, conclusion='success' if verdict in (PASS, NOT_REQUIRED) else 'failure')
    if check_id:
        ctx.checks.request('PATCH', f'repos/{ctx.repository}/check-runs/{check_id}', body)
        log(f'{verdict} published on {found["sha"]} (check run {check_id})')
        return check_id
    created = ctx.checks.request('POST', f'repos/{ctx.repository}/check-runs', {**body, 'head_sha': found['sha']})
    log(f'{verdict} published on {found["sha"]} (check run {created["id"]})')
    return created['id']


# Commands.

def event_pr(ctx):
    event = ctx.event()
    if ctx.env['GITHUB_EVENT_NAME'] == 'pull_request_target':
        return event['pull_request']['number']
    if ctx.env['GITHUB_EVENT_NAME'] == 'repository_dispatch':
        number = (event.get('client_payload') or {}).get('pr')
        if isinstance(number, int) and not isinstance(number, bool) and number > 0:
            return number
        raise SystemExit('superagent-uat: the dispatch needs client_payload.pr, a pull request number')
    raise SystemExit(f'superagent-uat: start does not handle {ctx.env["GITHUB_EVENT_NAME"]} events')


def start(ctx):
    found = derive(ctx, event_pr(ctx))
    ctx.output(run='false')
    if found['status'] == 'CLOSED':
        return log(f'PR #{found["pr"]}: {found["reason"]}; nothing to do')
    latest = next(iter(app_check_runs(ctx, found['sha'])), None)
    marker = marker_of(latest) if latest else None
    if marker and marker['key'] == found['key'] and latest['status'] == 'completed' \
            and marker['verdict'] in (PASS, FAIL, NOT_REQUIRED, BLOCKED if found['status'] != spec.READY else None):
        return log(f'{found["key"]} already has {marker["verdict"]}; nothing to do')
    prior = prior_failed_rounds(ctx, found)
    budget = f'{prior} of {ctx.max_failed_rounds} failed UAT rounds used'
    if found['status'] in (FAIL, spec.READY) and prior >= ctx.max_failed_rounds:
        return publish(ctx, found, BLOCKED, 'UAT blocked: no failing rounds left',
                       f'This pull request has used all {ctx.max_failed_rounds} failing UAT rounds; '
                       'UAT does not run on it again and it must not merge.', prior)
    if found['status'] == NOT_REQUIRED:
        return publish(ctx, found, NOT_REQUIRED, 'UAT not required',
                       f'No UAT ran: {found["reason"]}. Nothing was tested.', prior)
    if found['status'] == BLOCKED:
        return publish(ctx, found, BLOCKED, 'UAT blocked', f'UAT cannot run: {found["reason"]}.', prior)
    if found['status'] == FAIL:
        return publish(ctx, found, FAIL, 'UAT failed: the pull request cannot be tested',
                       f'{found["reason"]}.\n\nThis counts as a failing round ({prior + 1} of {ctx.max_failed_rounds}).',
                       prior + 1)
    check_id = publish(ctx, found, PENDING, 'UAT running',
                       f'Testing {len(found["spec"].scenarios)} scenarios of issue #{found["issue"]} in the '
                       f'`{found["environment"]}` environment ({budget}).', prior)
    snapshot = {'pr': found['pr'], 'sha': found['sha'], 'issue': found['issue'], 'key': found['key'],
                'environment': found['environment'], 'entry': found['entry'], 'check_id': check_id,
                'failed_rounds': prior, 'issue_updated_at': found['issue_updated_at'],
                'canonical': spec.canonical(found['spec'], found['environment']),
                'meta': {'repository': ctx.repository, 'issue_number': found['issue'], 'pr_number': found['pr'],
                         'head_sha': found['sha'], 'uat_spec_sha256': found['spec_sha256'],
                         'environment': found['environment'], 'environment_revision': found['sha']}}
    ctx.output(run='true', snapshot=base64.b64encode(json.dumps(snapshot).encode()).decode())


def load_snapshot(ctx):
    return json.loads(base64.b64decode(ctx.env['UAT_SNAPSHOT']))


def redact_tree(directory, secrets):
    """Replace every secret value in the files a run leaves for upload."""
    secrets = [secret.encode() for secret in secrets if secret and len(secret) >= 8]
    for root, _, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            if os.path.islink(path) or not secrets:
                continue
            with open(path, 'rb') as file:
                data = file.read()
            redacted = data
            for secret in secrets:
                redacted = redacted.replace(secret, b'[REDACTED]')
            if redacted != data:
                with open(path, 'wb') as file:
                    file.write(redacted)


def build_images(tag):
    images = {}
    for kind in ('tester', 'sandbox'):
        images[kind] = f'superagent-uat-{kind}:{tag}'
        subprocess.run(['docker', 'build', '-q', '-t', images[kind], '-f', os.path.join(UAT, kind, 'Dockerfile'), UAT],
                       check=True, capture_output=True, timeout=1800)
    return images


def test(ctx):
    sandbox.exit_on_signals()
    snapshot = load_snapshot(ctx)
    os.makedirs(ctx.out_dir, exist_ok=True)
    root = os.path.dirname(ctx.out_dir)
    outcome = results.Outcome(BLOCKED, ['the UAT run did not finish'])
    try:
        checkout = os.path.join(root, 'checkout')
        sandbox.checkout(ctx.repository, snapshot['sha'], checkout, token=ctx.env.get('UAT_READ_TOKEN'),
                         server=ctx.env.get('GITHUB_SERVER_URL', 'https://github.com'))
        config, problem = sandbox.load_config(checkout)
        entry = sandbox.environment_entry(snapshot['environment'], config)
        if problem or entry != snapshot['entry']:
            outcome = results.Outcome(BLOCKED, [f'the checkout\'s {sandbox.CONFIG_PATH} differs from the one read '
                                                f'through GitHub: {problem or "different environment"}'])
        else:
            images = build_images(ctx.env.get('GITHUB_RUN_ID', 'local'))
            minutes = int(ctx.env.get('UAT_TESTER_TIMEOUT_MINUTES') or 30)
            settings = sandbox.Settings(tester_image=images['tester'], sandbox_image=images['sandbox'],
                                        prefix=f'superagent-uat-{ctx.env.get("GITHUB_RUN_ID", os.getpid())}',
                                        allow=tuple(model_hosts(ctx.env)), tester_timeout=minutes * 60,
                                        proxy_networks=tuple(ctx.env.get('UAT_PROXY_NETWORKS', '').split()),
                                        environment=dict(ctx.env))
            outcome = sandbox.run_local(snapshot['meta'], spec.from_canonical(snapshot['canonical']), checkout,
                                        entry, settings, ctx.out_dir)
    except Exception as error:  # Infrastructure, not the PR: blocked, never passed.
        outcome = results.Outcome(BLOCKED, [f'the UAT run failed: {type(error).__name__}: {error}'])
        log(f'test failed: {error!r}')
    finally:
        with open(os.path.join(ctx.out_dir, 'outcome.json'), 'w', encoding='utf-8') as file:
            json.dump({'verdict': outcome.verdict, 'reasons': outcome.reasons, 'transient': outcome.transient,
                       'result': outcome.result}, file, indent=2)
        redact_tree(ctx.out_dir, [ctx.env.get(name, '') for name in ('DEEPSEEK_API_KEY', 'UAT_READ_TOKEN')])
        sandbox.prune(ctx.out_dir)
        log(f'{outcome.verdict}: {"; ".join(outcome.reasons)[:2000]}')


def model_hosts(environ):
    """The model API the proxy lets the tester reach: DEEPSEEK_BASE_URL's host, else api.deepseek.com."""
    url = urllib.parse.urlsplit(environ.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com')
    return [f'{url.hostname}:{url.port or (443 if url.scheme == "https" else 80)}']


def reverify(ctx, snapshot):
    """Re-evaluate a PASS from the uploaded trace and evidence with the snapshot's trusted metadata."""
    tester = os.path.join(ctx.out_dir, 'tester')
    with open(os.path.join(tester, 'attempts.json'), encoding='utf-8') as file:
        last = json.load(file)[-1]
    with open(os.path.join(tester, f'attempt-{last["attempt"]}', 'trace.ndjson'), 'rb') as file:
        raw = file.read(results.MAX_TRACE_BYTES + 1)
    ids = [scenario['id'] for scenario in snapshot['canonical']['scenarios']]
    work = os.path.join(ctx.out_dir, 'tester-work', f'attempt-{last["attempt"]}')
    return results.evaluate(raw, last['exit_code'], last['timed_out'], snapshot['meta'], ids, work)


def report(snapshot, outcome, failed_rounds, max_rounds):
    """(title, summary, text) of a finished run for the check run."""
    result = outcome.result or {}
    scenarios = result.get('scenarios') or []
    cell = lambda value: str(value).replace('|', '\\|').replace('\n', ' ')[:300]
    table = '\n'.join(['| Scenario | Verdict | Observed |', '| --- | --- | --- |',
                       *(f'| {s["id"]} | {s["verdict"]} | {cell(s["observed"])} |' for s in scenarios)]) if scenarios else ''
    where = (f'Issue #{snapshot["issue"]}, environment `{snapshot["environment"]}`, commit `{snapshot["sha"]}`, '
             f'spec `{snapshot["meta"]["uat_spec_sha256"][:12]}`. Evidence and logs: the run\'s `superagent-uat` artifact.')
    reasons = '\n'.join(f'- {reason}' for reason in outcome.reasons)
    details = []
    for s in scenarios:
        if s['verdict'] == PASS:
            continue
        details.append(f'### {s["id"]}: {s["verdict"]}\n\n**Expected:** {s["expected"]}\n\n**Observed:** {s["observed"]}')
        if s.get('reproduction'):
            details.append('**Reproduction:**\n' + '\n'.join(f'{n}. {step}' for n, step in enumerate(s['reproduction'], 1)))
        if s.get('evidence'):
            details.append('**Evidence:** ' + ', '.join(f'`{path}`' for path in s['evidence']))
    text = '\n\n'.join(details)
    if outcome.verdict == PASS:
        return f'UAT passed: {len(scenarios)} scenarios', f'{where}\n\n{table}', text
    if outcome.verdict == FAIL:
        failing = [s['id'] for s in scenarios if s['verdict'] == FAIL] or ['the application']
        return (f'UAT failed: {", ".join(failing)} (failing round {failed_rounds} of {max_rounds})',
                f'{where}\n\n{table}\n\n{reasons}'.strip(), text)
    if outcome.verdict == STALE:
        return 'UAT result is stale', f'{where}\n\n{reasons}\n\nRun `uat-test` (or push) to test the current spec.', text
    return 'UAT blocked', f'{where}\n\n{reasons}\n\n{table}'.strip(), text


def finish(ctx):
    snapshot = load_snapshot(ctx)
    outcome_path = os.path.join(ctx.out_dir, 'outcome.json')
    test_result = ctx.env.get('UAT_TEST_RESULT', '')
    if test_result != 'success' or not os.path.exists(outcome_path):
        outcome = results.Outcome(BLOCKED, [f'the test job ended `{test_result or "unknown"}` without a result'])
    else:
        with open(outcome_path, encoding='utf-8') as file:
            data = json.load(file)
        outcome = results.Outcome(data['verdict'], data['reasons'], data.get('transient', False), data.get('result'))
        if outcome.verdict not in (PASS, FAIL, BLOCKED):
            outcome = results.Outcome(BLOCKED, [f'unknown verdict {outcome.verdict!r}'])
        if outcome.verdict == PASS:
            try:
                check = reverify(ctx, snapshot)
            except (OSError, ValueError, KeyError, IndexError) as error:
                check = results.Outcome(BLOCKED, [f'the run\'s trace or evidence is missing: {error}'])
            if check.verdict != PASS:
                outcome = results.Outcome(BLOCKED, ['re-verification of the PASS failed: ' + '; '.join(check.reasons)],
                                          result=outcome.result)
    found = derive(ctx, snapshot['pr'])
    if found.get('key') != snapshot['key']:
        changes = [label for label, now, then in (('head', found['sha'], snapshot['sha']),
                                                  ('UAT spec', found.get('spec_sha256'), snapshot['meta']['uat_spec_sha256']),
                                                  ('environment', found.get('environment'), snapshot['environment']))
                   if now != then] or [f'status {found["status"]}']
        outcome = results.Outcome(STALE, [f'the {", ".join(changes)} changed while UAT ran; this result applies to '
                                          f'nothing current'], result=outcome.result)
    failed_rounds = snapshot['failed_rounds'] + (1 if outcome.verdict == FAIL else 0)
    title, summary, text = report(snapshot, outcome, failed_rounds, ctx.max_failed_rounds)
    bound = {'pr': snapshot['pr'], 'sha': snapshot['sha'], 'issue': snapshot['issue'], 'key': snapshot['key'],
             'spec_sha256': snapshot['meta']['uat_spec_sha256'], 'environment': snapshot['environment']}
    publish(ctx, bound, outcome.verdict, title, summary, failed_rounds, text, check_id=snapshot['check_id'])


def route(ctx):
    event = ctx.event()
    if 'body' not in (event.get('changes') or {}):
        return log('the issue description did not change; nothing to do')
    issue = event['issue']['number']
    prs = ctx.read.graphql(OPEN_PRS, owner=ctx.owner, name=ctx.name)['repository']['pullRequests']['nodes']
    for pr in prs:
        if not any(node['number'] == issue and node['repository']['nameWithOwner'].lower() == ctx.repository.lower()
                   for node in pr['closingIssuesReferences']['nodes']):
            continue
        found = derive(ctx, pr['number'])
        latest = next(iter(app_check_runs(ctx, found['sha'])), None)
        marker = marker_of(latest) if latest else None
        if not marker or marker['verdict'] in (STALE, PENDING) or marker['key'] == found.get('key'):
            continue
        bound = {**{key: marker.get(key) for key in ('pr', 'sha', 'issue', 'key', 'spec_sha256', 'environment')}}
        publish(ctx, bound, STALE, 'UAT result is stale',
                f'Issue #{issue} changed what UAT tests since this {marker["verdict"]} result; it no longer applies. '
                'Run `uat-test` (or push) to test the current spec.', marker.get('failed_rounds', 0))


def main(argv):
    commands = {'start': start, 'test': test, 'finish': finish, 'route': route}
    if len(argv) != 2 or argv[1] not in commands:
        raise SystemExit(f'usage: python3 -m uat.controller {"|".join(commands)}')
    commands[argv[1]](Context())


if __name__ == '__main__':
    main(sys.argv)
