"""The UAT logic the gate service (uat/gate.py) runs. It orchestrates nothing.

DSH Superagent decides when a PR head is tested and what happens next; it asks
the gate through scripts/uat-test. For one requested run this module:

- derive: reads the PR head, the issue it closes, the UAT spec and
  .superagent/uat.yaml from GitHub (read access only, never from the
  requester) and checks them against the protected policy;
- admit: decides what the request leads to, either a verdict at once (no UAT,
  blocked, budget used up) or a run;
- execute: checks out the head and runs the sandbox and the DeepSeek tester on
  the isolated runner, then evaluates their output (uat.result);
- conclude: re-derives the binding, so a new head, spec, environment, ticket
  issue or policy makes the result STALE, counts the failed round and writes
  the report.

The binding key (derive) names the head, the issue the ticket started from,
the spec, the environment and the policy fingerprint: the gate answers both
"run" and "verify" with the latest complete run of exactly that key.

Results are plain data (verdict, binding, title, summary, text) that the gate
keeps and gives to uat-test; nothing here publishes to GitHub, so a trusted
publisher can be added later without touching this module.
"""
import hashlib
import json
import os
import re
import subprocess
import urllib.parse

from uat import preview, sandbox, spec
from uat import result as results
from uat.github import GitHubError

NOT_REQUIRED, STALE, PENDING = 'NOT_REQUIRED', 'STALE', 'PENDING'
PASS, FAIL, BLOCKED = results.PASS, results.FAIL, results.BLOCKED
UAT = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATTERN = re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,})\b')
LINKS = '''query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    closingIssuesReferences(first: 20) { nodes { number repository { nameWithOwner } } } } } }'''


class Policy:
    """The protected policy: the gate's own environment, never the PR, the issue or the requester."""

    def __init__(self, environ):
        self.environments = set(re.split(r'[\s,]+', (environ.get('SUPERAGENT_UAT_ENVIRONMENTS') or 'local').strip())) - {''}
        self.max_failed_rounds = int(environ.get('SUPERAGENT_UAT_MAX_FAILED_ROUNDS') or 3)
        self.tester_timeout = int(environ.get('SUPERAGENT_UAT_TESTER_TIMEOUT_MINUTES') or 30) * 60
        self.preview = {'url': environ.get('SUPERAGENT_UAT_PREVIEW_URL', '').strip(),
                        'allowed_hosts': environ.get('SUPERAGENT_UAT_PREVIEW_ALLOWED_HOSTS', '').split(),
                        'revision_path': environ.get('SUPERAGENT_UAT_PREVIEW_REVISION_PATH', '').strip(),
                        'ready_timeout': int(environ.get('SUPERAGENT_UAT_PREVIEW_READY_TIMEOUT_SECONDS') or 300),
                        'credentials': environ.get('SUPERAGENT_UAT_PREVIEW_CREDENTIALS', '')}
        # Networks the runner's proxy also joins, to reach a model gateway on a private network.
        self.proxy_networks = tuple(environ.get('SUPERAGENT_UAT_PROXY_NETWORKS', '').split())
        # Only these variables reach the runner's containers, by name (uat.run.PASSED_ENVIRONMENT).
        self.tester_environment = {key: environ[key] for key in ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL',
                                                                  'SUPERAGENT_UAT_PROVIDER', 'SUPERAGENT_UAT_MODEL')
                                   if environ.get(key)}
        # Part of every binding key: a change to what a run means (environments, preview deployment and
        # credentials, model) makes earlier results no longer apply. Secrets enter only as a hash.
        relevant = {'environments': sorted(self.environments),
                    'preview': {**self.preview, 'allowed_hosts': sorted(self.preview['allowed_hosts']),
                                'credentials': hashlib.sha256(self.preview['credentials'].encode()).hexdigest()},
                    'model': [environ.get(key, '') for key in ('DEEPSEEK_BASE_URL', 'SUPERAGENT_UAT_PROVIDER',
                                                               'SUPERAGENT_UAT_MODEL')]}
        self.fingerprint = hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


class Context:
    """One repository: its GitHub client (read access) and the policy."""

    def __init__(self, repository, github, policy):
        self.repository, self.github, self.policy = repository, github, policy
        self.owner, self.name = repository.split('/')


def log(message):
    print(f'superagent-uat: {message}', flush=True)


# Deriving what a PR must be tested against, from GitHub only.

def derive(ctx, number, origin=None):
    """Everything the verdict is bound to, as a dict whose `status` is READY, NOT_REQUIRED, BLOCKED, FAIL or CLOSED.

    `origin` is the issue the ticket started from (DSH_SUPERAGENT_GITHUB_NUMBER, as the worker's uat-test
    names it: neither DSH's state nor GitHub holds a record of it the worker could not alter), which the PR
    description cannot replace: it must be a real issue of the repository and the PR must close it. Its UAT
    is tested with any other issue's the PR closes. `origin_bound` says both held, so the gate may pin it."""
    gh, policy = ctx.github, ctx.policy
    pr = gh.get(f'repos/{ctx.repository}/pulls/{number}')
    found = {'pr': number, 'sha': pr['head']['sha'], 'origin': origin, 'issue': None, 'environment': None,
             'spec_sha256': None, 'origin_bound': False}
    if pr['state'] != 'open':
        return {**found, 'status': 'CLOSED', 'reason': 'the pull request is not open'}
    links = gh.graphql(LINKS, owner=ctx.owner, name=ctx.name, number=number)['repository']['pullRequest']

    def verdict(status, reason, **more):
        found.update(more)
        found['key'] = (f'{ctx.repository}/{number}/{found["sha"]}/#{origin or "-"}/{found["spec_sha256"] or status}/'
                        f'{found["environment"] or "-"}/{policy.fingerprint}')
        return {**found, 'status': status, 'reason': reason}

    if (pr['head'].get('repo') or {}).get('full_name') != ctx.repository:
        return verdict(BLOCKED, 'pull requests from forks are not tested')
    issues = sorted(node['number'] for node in links['closingIssuesReferences']['nodes']
                    if node['repository']['nameWithOwner'].lower() == ctx.repository.lower())
    bodies = {}
    if origin is not None:
        try:
            bodies[origin] = gh.get(f'repos/{ctx.repository}/issues/{origin}')
        except GitHubError as error:
            if error.status != 404:
                raise
            return verdict(BLOCKED, f'issue #{origin}, which the ticket started from, does not exist in {ctx.repository}')
        if bodies[origin].get('pull_request'):
            return verdict(BLOCKED, f'#{origin}, which the ticket started from, is a pull request, not an issue')
        if origin not in issues:
            return verdict(FAIL, f'the pull request does not close issue #{origin}, which its ticket started from, so '
                                 f'that issue\'s UAT would not run: name it with a `Closes #{origin}` line in its '
                                 'description', issue=origin)
        found['origin_bound'] = True
    if not issues:
        return verdict(FAIL, 'the pull request closes no issue of this repository: name the issue it resolves '
                             'with a `Closes #<n>` line in its description')
    specs = {}
    for issue in issues:
        body = bodies.get(issue) or gh.get(f'repos/{ctx.repository}/issues/{issue}')
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
        raw = gh.request('GET', f'repos/{ctx.repository}/contents/{sandbox.CONFIG_PATH}',
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
    if name not in policy.environments:
        return verdict(BLOCKED, f'environment {name!r} is not enabled in the UAT gate\'s policy '
                                f'(enabled: {", ".join(sorted(policy.environments))})', issue=issue)
    if entry is None:
        return verdict(FAIL, f'environment {name!r} is not configured: add it to {sandbox.CONFIG_PATH} '
                             '(type local with start and ready)', issue=issue)
    if entry['type'] == 'preview':
        if not (policy.preview['url'] and policy.preview['allowed_hosts'] and policy.preview['revision_path'].startswith('/')):
            return verdict(BLOCKED, f'environment {name!r} is a preview, and the UAT gate\'s policy configures no '
                                    'preview URL, allowed hosts and revision path', issue=issue)
        try:
            preview.preview_url(policy.preview['url'], number, policy.preview['allowed_hosts'])
        except preview.Refused as error:
            return verdict(BLOCKED, str(error), issue=issue)
    return verdict(spec.READY, '', issue=issue, environment=name, spec_sha256=spec.spec_hash(parsed, name),
                   spec=parsed, entry=entry, issue_updated_at=updated_at)


def clean(text, limit):
    text = TOKEN_PATTERN.sub('[REDACTED]', text)
    return text if len(text) <= limit else text[:limit - 40] + '\n\n(truncated)'


def bound(found):
    return {key: found.get(key) for key in ('pr', 'sha', 'origin', 'issue', 'key', 'spec_sha256', 'environment')}


# One requested run.

def admit(ctx, found, prior, run_id):
    """What a request leads to: (verdict, failed rounds, title, summary, snapshot). The snapshot is None
    when the verdict is final; else the verdict is PENDING and execute runs the snapshot."""
    rounds = ctx.policy.max_failed_rounds
    if found['status'] in (FAIL, spec.READY) and prior >= rounds:
        verdict, failed, title, summary = BLOCKED, prior, 'UAT blocked: no failing rounds left', (
            f'This pull request has used all {rounds} failing UAT rounds; UAT does not run on it again and it must '
            'not merge.')
    elif found['status'] == NOT_REQUIRED:
        verdict, failed, title, summary = NOT_REQUIRED, prior, 'UAT not required', (
            f'No UAT ran: {found["reason"]}. Nothing was tested.')
    elif found['status'] == BLOCKED:
        verdict, failed, title, summary = BLOCKED, prior, 'UAT blocked', f'UAT cannot run: {found["reason"]}.'
    elif found['status'] == FAIL:
        verdict, failed, title, summary = FAIL, prior + 1, 'UAT failed: the pull request cannot be tested', (
            f'{found["reason"]}.\n\nThis counts as a failing round ({prior + 1} of {rounds}).')
    else:
        verdict, failed, title, summary = PENDING, prior, 'UAT running', (
            f'Testing {len(found["spec"].scenarios)} scenarios of issue #{found["issue"]} in the '
            f'`{found["environment"]}` environment ({prior} of {rounds} failing rounds used).')
    log(f'PR #{found["pr"]} at {found["sha"]}: {verdict}')
    if verdict != PENDING:
        return verdict, failed, title, summary, None
    snapshot = {**bound(found), 'entry': found['entry'], 'failed_rounds': prior, 'run_id': run_id,
                'issue_updated_at': found['issue_updated_at'],
                'canonical': spec.canonical(found['spec'], found['environment']),
                'meta': {'repository': ctx.repository, 'issue_number': found['issue'], 'pr_number': found['pr'],
                         'head_sha': found['sha'], 'uat_spec_sha256': found['spec_sha256'],
                         'environment': found['environment'], 'environment_revision': found['sha']}}
    return PENDING, prior, title, summary, snapshot


def redact_tree(directory, secrets):
    """Replace every secret value in the files a run keeps."""
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
    """Build the tester and sandbox images on the runner (the Docker daemon DOCKER_HOST names)."""
    images = {}
    for kind in ('tester', 'sandbox'):
        images[kind] = f'superagent-uat-{kind}:{tag}'
        subprocess.run(['docker', 'build', '-q', '-t', images[kind], '-f', os.path.join(UAT, kind, 'Dockerfile'), UAT],
                       check=True, capture_output=True, timeout=1800)
    return images


def credential_values(raw):
    """The values of the preview test credentials (a JSON object), for redaction."""
    try:
        values = json.loads(raw) if raw else {}
    except ValueError:
        return [raw]
    return [value for value in values.values() if isinstance(value, str)] if isinstance(values, dict) else [raw]


def model_hosts(environment):
    """The model API the proxy lets the tester reach: DEEPSEEK_BASE_URL's host, else api.deepseek.com."""
    url = urllib.parse.urlsplit(environment.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com')
    return [f'{url.hostname}:{url.port or (443 if url.scheme == "https" else 80)}']


def execute(ctx, snapshot, run_dir, images, token=None, server='https://github.com'):
    """Check out the head in run_dir and test it on the runner; the Outcome, with outcome.json and the
    artifacts in run_dir/out. Nothing here runs the PR's code outside the runner's containers."""
    out = os.path.join(run_dir, 'out')
    os.makedirs(out, exist_ok=True)
    outcome = results.Outcome(BLOCKED, ['the UAT run did not finish'])
    policy = ctx.policy
    try:
        checkout = os.path.join(run_dir, 'checkout')
        sandbox.checkout(ctx.repository, snapshot['sha'], checkout, token=token, server=server)
        config, problem = sandbox.load_config(checkout)
        entry = sandbox.environment_entry(snapshot['environment'], config)
        if problem or entry != snapshot['entry']:
            outcome = results.Outcome(BLOCKED, [f'the checkout\'s {sandbox.CONFIG_PATH} differs from the one read '
                                                f'through GitHub: {problem or "different environment"}'])
        else:
            settings = sandbox.Settings(tester_image=images['tester'], sandbox_image=images['sandbox'],
                                        prefix=f'superagent-uat-{snapshot["run_id"]}',
                                        allow=tuple(model_hosts(policy.tester_environment)),
                                        tester_timeout=policy.tester_timeout, proxy_networks=policy.proxy_networks,
                                        environment={**os.environ, **policy.tester_environment})
            run_spec = spec.from_canonical(snapshot['canonical'])
            if entry['type'] == 'preview':
                outcome = preview.run_preview(snapshot['meta'], run_spec, entry, settings, policy.preview, out, checkout)
            else:
                outcome = sandbox.run_local(snapshot['meta'], run_spec, checkout, entry, settings, out)
    except Exception as error:  # Infrastructure, not the PR: blocked, never passed.
        outcome = results.Outcome(BLOCKED, [f'the UAT run failed: {type(error).__name__}: {error}'])
        log(f'run {snapshot["run_id"]} failed: {error!r}')
    finally:
        with open(os.path.join(out, 'outcome.json'), 'w', encoding='utf-8') as file:
            json.dump({'verdict': outcome.verdict, 'reasons': outcome.reasons, 'transient': outcome.transient,
                       'result': outcome.result}, file, indent=2)
        redact_tree(out, [policy.tester_environment.get('DEEPSEEK_API_KEY', ''), token or '']
                    + credential_values(policy.preview['credentials']))
        sandbox.prune(out)
        # The report published on GitHub and given to the worker comes from the redacted file, never memory.
        with open(os.path.join(out, 'outcome.json'), encoding='utf-8') as file:
            kept = json.load(file)
        outcome = results.Outcome(kept['verdict'], kept['reasons'], kept['transient'], kept['result'], outcome.tool_calls)
        log(f'run {snapshot["run_id"]}: {outcome.verdict}: {"; ".join(outcome.reasons)[:2000]}')
    return outcome


def report(snapshot, outcome, failed_rounds, max_rounds):
    """(title, summary, text) of a finished run, for uat-test and the PR comment it posts."""
    result = outcome.result or {}
    scenarios = result.get('scenarios') or []
    cell = lambda value: str(value).replace('|', '\\|').replace('\n', ' ')[:300]
    table = '\n'.join(['| Scenario | Verdict | Observed |', '| --- | --- | --- |',
                       *(f'| {s["id"]} | {s["verdict"]} | {cell(s["observed"])} |' for s in scenarios)]) if scenarios else ''
    where = (f'Issue #{snapshot["issue"]}, environment `{snapshot["environment"]}`, commit `{snapshot["sha"]}`, '
             f'spec `{snapshot["meta"]["uat_spec_sha256"][:12]}`, UAT run `{snapshot["run_id"]}`. Evidence and logs: '
             f'`uat-test --artifact {snapshot["run_id"]} <path>` (`outcome.json`, `app-logs/app.log`, '
             '`tester-work/attempt-1/artifacts/...`, `tester/attempt-1/trace.ndjson`).')
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
        return 'UAT result is stale', f'{where}\n\n{reasons}\n\nRun `uat-test` for the current head.', text
    return 'UAT blocked', f'{where}\n\n{reasons}\n\n{table}'.strip(), text


def conclude(ctx, snapshot, outcome):
    """Re-derive the binding and write the run's report. (verdict, failed rounds, title, summary, text)."""
    if outcome.verdict not in (PASS, FAIL, BLOCKED, STALE):
        outcome = results.Outcome(BLOCKED, [f'unknown verdict {outcome.verdict!r}'])
    if outcome.verdict != STALE:
        found = derive(ctx, snapshot['pr'], snapshot.get('origin'))
        if found.get('key') != snapshot['key']:
            changes = [label for label, now, then in (('head', found['sha'], snapshot['sha']),
                                                      ('UAT spec', found.get('spec_sha256'), snapshot['spec_sha256']),
                                                      ('environment', found.get('environment'), snapshot['environment']))
                       if now != then] or [f'status {found["status"]}']
            outcome = results.Outcome(STALE, [f'the {", ".join(changes)} changed while UAT ran; this result applies '
                                              'to nothing current'], result=outcome.result)
    failed_rounds = snapshot['failed_rounds'] + (1 if outcome.verdict == FAIL else 0)
    title, summary, text = report(snapshot, outcome, failed_rounds, ctx.policy.max_failed_rounds)
    log(f'run {snapshot["run_id"]} on {snapshot["sha"]}: {outcome.verdict}')
    return outcome.verdict, failed_rounds, clean(title, 255), clean(summary, 60000), clean(text, 60000)
