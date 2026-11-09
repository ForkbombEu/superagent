"""Test a pull request's preview deployment, after proving that it runs the pull request's head.

Everything about the preview comes from the UAT gate's policy, never from the
pull request or the issue: the URL template (`{pr}` is the PR number), the
allowed host patterns (`example.com` exactly, `*.example.com` any subdomain),
the revision path and the test credentials. `.superagent/uat.yaml` may only
name a readiness path on that host.

Before the tester starts, the controller (outside every sandbox):
- refuses a URL whose host is not allowed, and follows redirects only to
  allowed hosts;
- waits for the readiness path to answer 2xx (unavailable: BLOCKED);
- requires the revision path to answer with the full 40-character head SHA
  (any other revision is a stale preview: BLOCKED). The deployment platform
  supplies that revision, so a preview of older code cannot claim the head.
The tester then reaches only the model API and the preview host through the
allowlisting proxy, so a redirect elsewhere fails there too. Test credentials
are a read-only file in the tester container and are redacted from every
artifact and from the published check.
"""
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

from uat import result as results
from uat import run, sandbox

MAX_BODY = 1024 * 1024


class Refused(Exception):
    """The preview cannot be tested: BLOCKED with this message."""


def host_allowed(host, patterns):
    host = (host or '').lower().rstrip('.')
    for pattern in patterns:
        pattern = pattern.lower().rstrip('.')
        if pattern.startswith('*.') and host.endswith(pattern[1:]) and len(host) > len(pattern) - 1:
            return True
        if host == pattern:
            return True
    return False


def preview_url(template, pr, patterns):
    """The preview origin for a PR, refused unless http(s) on an allowed host."""
    url = urllib.parse.urlsplit(template.replace('{pr}', str(int(pr))))
    if url.scheme not in ('http', 'https') or not url.hostname:
        raise Refused(f'the preview URL {template!r} is not an http(s) URL')
    if not host_allowed(url.hostname, patterns):
        raise Refused(f'the preview host {url.hostname} is not one of the allowed preview hosts')
    return f'{url.scheme}://{url.netloc}'


def opener(patterns):
    class AllowedRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, new_url):
            host = urllib.parse.urlsplit(new_url).hostname
            if not host_allowed(host, patterns):
                raise Refused(f'the preview redirects to {host}, which is not an allowed preview host')
            return super().redirect_request(request, response, code, message, headers, new_url)
    # No proxy from the environment: these checks go straight to the preview.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), AllowedRedirects)


def fetch(url, patterns, timeout=15):
    """(status, body) of a GET, refusing redirects off the allowed hosts."""
    request = urllib.request.Request(url, headers={'User-Agent': 'superagent-uat', 'Cache-Control': 'no-cache'})
    try:
        with opener(patterns).open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BODY)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_BODY)


def verify(origin, sha, readiness_path, revision_path, patterns, timeout):
    """Wait for the preview and prove it serves `sha`; raise Refused otherwise."""
    deadline, problem = time.monotonic() + timeout, 'no answer'
    while time.monotonic() < deadline:
        try:
            status, _ = fetch(origin + readiness_path, patterns)
            if 200 <= status < 300:
                break
            problem = f'HTTP {status}'
        except (urllib.error.URLError, OSError) as error:
            problem = str(getattr(error, 'reason', error))
        time.sleep(3)
    else:
        raise Refused(f'the preview {origin + readiness_path} is unavailable ({problem}) after {timeout} s')
    try:
        status, body = fetch(origin + revision_path, patterns)
    except (urllib.error.URLError, OSError) as error:
        raise Refused(f'the preview revision {origin + revision_path} is unavailable: {error}') from None
    text = body.decode('utf-8', 'replace').strip()
    if status != 200 or sha not in text:
        raise Refused(f'the preview at {origin} does not run {sha} (revision answer: HTTP {status} '
                      f'{text[:80]!r}): it is stale or not yet deployed')


def credentials_file(raw, directory):
    """Write the JSON object of test credentials for the tester; (path or None, secret values)."""
    if not raw:
        return None, []
    try:
        values = json.loads(raw)
    except ValueError:
        values = None
    if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
        raise Refused('UAT_PREVIEW_CREDENTIALS must be a JSON object of string values')
    if any(len(value) < 8 for value in values.values()):
        # Shorter values cannot be redacted from artifacts and reports without mangling them.
        raise Refused('every UAT_PREVIEW_CREDENTIALS value must be at least 8 characters long')
    path = os.path.join(directory, 'credentials.json')
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), 'w', encoding='utf-8') as file:
        json.dump(values, file)
    return path, list(values.values())


def run_preview(meta, spec, entry, settings, policy, out_dir, checkout=None):
    """The Outcome of testing the PR's verified preview; `policy` holds the gate's preview settings."""
    patterns = policy['allowed_hosts']
    p, environment = settings.prefix, dict(os.environ if settings.environment is None else settings.environment)
    private = os.path.join(os.path.dirname(out_dir.rstrip('/')), f'{p}-private')
    os.makedirs(private, exist_ok=True)
    try:
        origin = preview_url(policy['url'], meta['pr_number'], patterns)
        verify(origin, meta['head_sha'], entry.get('readiness_path', '/'), policy['revision_path'], patterns,
               policy.get('ready_timeout', 300))
        credentials, _ = credentials_file(policy.get('credentials'), private)
    except Refused as error:
        return results.Outcome(results.BLOCKED, [str(error)])
    url = urllib.parse.urlsplit(origin)
    host = f'{url.hostname}:{url.port or (443 if url.scheme == "https" else 80)}'
    fixtures = f'uat-{meta["pr_number"]}-{meta["head_sha"][:8]}-{p[-6:]}'
    notes = (f'Name every record, user or file the scenarios create with the prefix `{fixtures}`, and delete them '
             'when you are done.')
    mounts = []
    if credentials:
        mounts = ['-v', f'{credentials}:/uat-credentials.json:ro']
        notes += ' Test credentials for the scenarios are in /uat-credentials.json (never put them in your answer).'
    try:
        sandbox.docker('network', 'create', f'{p}-egress')
        sandbox.docker('network', 'create', '--internal', f'{p}-internal')
        sandbox.start_proxy(settings, extra_allow=(host,))
        outcome, _ = run.run(meta, spec, image=settings.tester_image, network=f'{p}-internal', proxy=f'{p}-proxy:3128',
                             work_root=os.path.join(out_dir, 'tester-work'), out_dir=os.path.join(out_dir, 'tester'),
                             base_url=origin, repo_dir=checkout, timeout=settings.tester_timeout,
                             environment=environment, name_prefix=p, extra_mounts=mounts, kind='preview', notes=notes)
        return outcome
    finally:
        sandbox.remove(p, out_dir)
        shutil.rmtree(private, ignore_errors=True)
