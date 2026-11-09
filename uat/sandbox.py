"""Build, start and test a pull request's application in a disposable sandbox.

One run creates, under a unique prefix, and always removes:

- `<p>-egress`: a normal network, and `<p>-internal`: an internal one with no
  route out;
- `<p>-home`: a volume for the sandbox user's home (mise toolchains, caches),
  shared by setup and start;
- `<p>-setup`: runs `mise install` and the configured setup command in the
  checkout, on the egress network (dependency downloads), then exits;
- `<p>-proxy`: the allowlisting egress proxy, on both networks;
- `<p>-pod`: holds the network namespace, on the internal network only, that
  `<p>-app` (the start command) and the tester containers share, so the
  application is at 127.0.0.1 for both and can reach nothing but the proxy.

The sandbox containers run as the controller's uid without capabilities, with
memory, CPU and process limits, and never get a credential or the Docker
socket; only the tester gets the DeepSeek API key (uat.run). Commands from
.superagent/uat.yaml are PR-controlled and run only inside these containers.
A pull request whose application cannot be configured, set up, started or
reached fails UAT, with the logs: the agent can fix those.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass

from uat import result as results
from uat import run, schema

CONFIG_PATH = '.superagent/uat.yaml'
MAX_CONFIG_BYTES = 65536
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
LOG_TAIL = 3000
SETUP_SCRIPT = ('set -eu; if [ -f mise.toml ] || [ -f .mise.toml ] || [ -f .tool-versions ]; then mise install; fi; '
                'if [ -n "${UAT_SETUP:-}" ]; then sh -c "$UAT_SETUP"; fi')
LIMITS = ['--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '2048', '--memory', '6g',
          '--cpus', '2']


@dataclass
class Settings:
    tester_image: str
    sandbox_image: str
    prefix: str
    allow: tuple = ('api.deepseek.com:443',)
    setup_timeout: int = 1800
    ready_timeout: int = 300
    tester_timeout: int = 1800
    environment: dict | None = None
    # Networks the proxy joins besides egress, to reach a model gateway on a private network.
    proxy_networks: tuple = ()


def load_config(checkout):
    """(config, None); (None, None) without .superagent/uat.yaml; (None, problem) when it is unusable."""
    path = os.path.join(checkout, CONFIG_PATH)
    if os.path.islink(path) or os.path.islink(os.path.dirname(path)):
        return None, f'{CONFIG_PATH} must not be a symbolic link'
    if not os.path.exists(path):
        return None, None
    if os.path.getsize(path) > MAX_CONFIG_BYTES:
        return None, f'{CONFIG_PATH} is larger than {MAX_CONFIG_BYTES} bytes'
    import yaml  # The runner's python3-yaml; uat/action.yml installs it when missing.
    try:
        with open(path, encoding='utf-8') as file:
            config = yaml.safe_load(file)
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        return None, f'{CONFIG_PATH} is not valid YAML: {error}'
    errors = schema.errors(config, schema.load('config'))
    if not errors and config.get('default_environment', next(iter(config['environments']))) not in config['environments']:
        errors = [f'default_environment {config["default_environment"]!r} is not one of the environments']
    return (None, f'{CONFIG_PATH} is invalid: {"; ".join(errors[:5])}') if errors else (config, None)


def environment_name(spec_environment, config):
    """The issue's Environment line, else the configuration's default, else local."""
    return spec_environment or (config or {}).get('default_environment') or 'local'


def environment_entry(name, config):
    """The configured entry for an environment name; `preview` needs none."""
    entry = (config or {}).get('environments', {}).get(name)
    return entry or ({'type': 'preview'} if name == 'preview' else None)


def docker(*args, timeout=120, check=True):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout, check=check)


def tail(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as file:
            return file.read()[-LOG_TAIL:].strip()
    except OSError:
        return ''


def run_logged(command, log_path, timeout, environment):
    """(exit code, timed out) of a foreground container whose output goes to log_path."""
    with open(log_path, 'wb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   env=environment)
        try:
            return process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            return None, True
        finally:
            subprocess.run(['docker', 'rm', '-f', command[command.index('--name') + 1]], capture_output=True)
            process.kill()
            process.wait()


def app_state(name):
    state = json.loads(docker('inspect', '-f', '{{json .State}}', name).stdout)
    return state['Running'], state['ExitCode']


def wait_ready(prefix, ready_url, timeout):
    """None once the ready URL answers 2xx inside the pod, else why not."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running, exit_code = app_state(f'{prefix}-app')
        if not running:
            return f'the application exited with status {exit_code} before {ready_url} answered'
        probe = docker('exec', f'{prefix}-pod', 'curl', '-fsS', '-o', '/dev/null', '--max-time', '5', ready_url,
                       check=False, timeout=30)
        if probe.returncode == 0:
            return None
        time.sleep(2)
    return f'{ready_url} did not answer within {timeout} s'


def run_local(meta, spec, checkout, entry, settings, out_dir):
    """The Outcome of testing the checkout's local environment `entry`; artifacts stay in out_dir."""
    p, environment = settings.prefix, dict(os.environ if settings.environment is None else settings.environment)
    user = ['--user', f'{os.getuid()}:{os.getgid()}']
    mounts = ['-v', f'{checkout}:/workspace', '-v', f'{p}-home:/home/sandbox']
    logs = os.path.join(out_dir, 'app-logs')
    os.makedirs(logs)
    os.chmod(logs, 0o777)
    try:
        docker('network', 'create', f'{p}-egress')
        docker('network', 'create', '--internal', f'{p}-internal')
        docker('volume', 'create', f'{p}-home')
        setup_log = os.path.join(out_dir, 'setup.log')
        code, timed_out = run_logged(
            ['docker', 'run', '--rm', '--name', f'{p}-setup', '--network', f'{p}-egress', *user, *LIMITS, *mounts,
             '-e', f'UAT_SETUP={entry.get("setup", "")}', settings.sandbox_image, 'sh', '-c', SETUP_SCRIPT],
            setup_log, settings.setup_timeout, environment)
        if timed_out or code:
            reason = f'did not finish within {settings.setup_timeout} s' if timed_out else f'exited with status {code}'
            return results.Outcome(results.FAIL, [f'the sandbox setup ({entry.get("setup") or "mise install"}) {reason}; '
                                                  f'last output:\n{tail(setup_log)}'])
        allowed = [item for host in settings.allow for item in ('--allow', host)]
        docker('run', '-d', '--name', f'{p}-proxy', '--network', f'{p}-egress', *LIMITS, settings.tester_image,
               'python3', '/usr/local/lib/superagent-uat/proxy.py', *allowed)
        for network in (f'{p}-internal', *settings.proxy_networks):
            docker('network', 'connect', network, f'{p}-proxy')
        docker('run', '-d', '--name', f'{p}-pod', '--network', f'{p}-internal', *user, *LIMITS,
               settings.sandbox_image, 'sleep', 'infinity')
        docker('run', '-d', '--init', '--name', f'{p}-app', '--network', f'container:{p}-pod', *user, *LIMITS, *mounts,
               '-v', f'{logs}:/uat-logs', '-e', f'UAT_START={entry["start"]}', settings.sandbox_image,
               'sh', '-c', 'exec sh -c "$UAT_START" > /uat-logs/app.log 2>&1')
        problem = wait_ready(p, entry['ready'], settings.ready_timeout)
        if problem:
            return results.Outcome(results.FAIL, [f'{problem}; application output:\n{tail(os.path.join(logs, "app.log"))}'])
        url = urllib.parse.urlsplit(entry['ready'])
        outcome, _ = run.run(meta, spec, image=settings.tester_image, network=f'container:{p}-pod',
                             proxy=f'{p}-proxy:3128', work_root=os.path.join(out_dir, 'tester-work'),
                             out_dir=os.path.join(out_dir, 'tester'), base_url=f'http://{url.netloc}',
                             repo_dir=checkout, timeout=settings.tester_timeout, environment=environment,
                             name_prefix=p, extra_mounts=['-v', f'{logs}:/app-logs:ro'])
        if entry.get('cleanup'):
            # Best effort: the containers, networks and volume go away regardless.
            with open(os.path.join(out_dir, 'cleanup.log'), 'wb') as log:
                try:
                    subprocess.run(['docker', 'exec', '-e', f'UAT_CLEANUP={entry["cleanup"]}', f'{p}-app', 'sh', '-c',
                                    'sh -c "$UAT_CLEANUP"'], stdout=log, stderr=subprocess.STDOUT, timeout=300)
                except subprocess.TimeoutExpired:
                    log.write(b'cleanup did not finish within 300 s\n')
        return outcome
    finally:
        remove(p, out_dir)


def remove(prefix, out_dir=None):
    """Remove every container, network and volume of a run; keep the proxy's decisions first."""
    if out_dir:
        with open(os.path.join(out_dir, 'proxy.log'), 'w') as log:
            subprocess.run(['docker', 'logs', f'{prefix}-proxy'], stdout=log, stderr=subprocess.STDOUT, check=False)
    for name in ('app', 'pod', 'proxy', 'setup'):
        subprocess.run(['docker', 'rm', '-f', f'{prefix}-{name}'], capture_output=True)
    for network in ('internal', 'egress'):
        subprocess.run(['docker', 'network', 'rm', f'{prefix}-{network}'], capture_output=True)
    subprocess.run(['docker', 'volume', 'rm', '-f', f'{prefix}-home'], capture_output=True)


def prune(directory, limit=MAX_UPLOAD_BYTES):
    """Drop the largest files until the tree fits the artifact limit (after the verdict)."""
    files = sorted(((os.path.getsize(os.path.join(root, name)), os.path.join(root, name))
                    for root, _, names in os.walk(directory) for name in names
                    if not os.path.islink(os.path.join(root, name))), reverse=True)
    total = sum(size for size, _ in files)
    for size, path in files:
        if total <= limit and size <= results.MAX_EVIDENCE_BYTES:
            continue
        os.remove(path)
        total -= size
    for root, _, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            if os.path.islink(path):
                os.remove(path)


def checkout(repository, sha, destination, token=None):
    """A detached checkout of `sha` with no stored credential; raises unless HEAD is exactly sha."""
    os.makedirs(destination)
    server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com').rstrip('/')
    environment = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    if token:
        import base64
        basic = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
        # Through the environment for these commands only: never in .git/config or argv.
        environment.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0=f'http.{server}/.extraheader',
                           GIT_CONFIG_VALUE_0=f'AUTHORIZATION: basic {basic}')
    for command in (['git', 'init', '-q'],
                    ['git', 'fetch', '-q', '--depth', '1', '--no-tags', f'{server}/{repository}.git', sha],
                    ['git', 'checkout', '-q', '--detach', 'FETCH_HEAD']):
        subprocess.run(command, cwd=destination, env=environment, check=True, capture_output=True, timeout=600)
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=destination, capture_output=True, text=True, check=True)
    if head.stdout.strip() != sha:
        raise RuntimeError(f'checked out {head.stdout.strip()}, not {sha}')
    shutil.rmtree(os.path.join(destination, '.git', 'hooks'), ignore_errors=True)


def exit_on_signals():
    """Make SIGTERM, SIGINT and SIGHUP (a cancelled workflow) unwind through the cleanup `finally` blocks."""
    import signal

    def stop(number, _):
        raise SystemExit(128 + number)
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, stop)
