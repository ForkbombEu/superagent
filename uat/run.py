"""Run the DeepSeek tester on one UAT spec and evaluate what it did.

Each attempt is a fresh `dsh --profile superagent-uat --json -` session in its
own container, given the trusted prompt (prompts/uat-tester.md) on stdin and a
fresh /work. The trace, stderr, exit status and timing are recorded outside
the container, and uat.result.evaluate decides the verdict. An attempt that
failed on one of DSH's transient model errors is retried once.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import time

from uat import result as results

PROMPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prompts', 'uat-tester.md')
ENVIRONMENTS = {
    'local': 'the pull request\'s code was built and started in a disposable sandbox that shares its network '
             'with you and has no other network access; its console output is in /app-logs/app.log (read-only: '
             'copy it into /work/artifacts when it is evidence).',
    'preview': 'the pull request\'s preview deployment, verified to run this commit; use only test data.',
}
# Variables the tester container receives from the controller's environment, by name.
PASSED_ENVIRONMENT = ('DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'SUPERAGENT_UAT_PROVIDER', 'SUPERAGENT_UAT_MODEL')


def result_template(meta, scenario_ids):
    return {
        'schema_version': 1,
        **{key: meta[key] for key in results.METADATA},
        'verdict': 'PASS | FAIL | BLOCKED',
        'summary': '...',
        'scenarios': [{'id': scenario_id, 'verdict': 'PASS | FAIL | BLOCKED', 'expected': '...', 'observed': '...',
                       'reproduction': ['...'], 'evidence': ['artifacts/...']} for scenario_id in scenario_ids],
    }


def render_prompt(meta, spec, base_url, template_path=PROMPT, kind='local', notes=''):
    """The tester's prompt; spec text sits between per-run random boundary lines it cannot forge."""
    boundary = f'=====UAT-SPEC-{secrets.token_hex(8)}====='
    ids = [scenario.id for scenario in spec.scenarios]
    values = {
        '<repository>': meta['repository'], '<pr>': str(meta['pr_number']), '<issue>': str(meta['issue_number']),
        '<sha>': meta['head_sha'], '<environment>': meta['environment'],
        '<environment description>': ENVIRONMENTS[kind] + (f' {notes}' if notes else ''),
        '<base url>': base_url,
        '<boundary>': boundary, '<preamble>': spec.preamble,
        '<scenarios>': '\n\n'.join(f'### {s.id}: {s.title}\n{s.steps}' for s in spec.scenarios),
        '<scenario ids>': ', '.join(ids),
        '<result template>': json.dumps(result_template(meta, ids), indent=2),
    }
    with open(template_path, encoding='utf-8') as file:
        # One pass, so text from the issue is never substituted again.
        return re.sub('|'.join(map(re.escape, values)), lambda match: values[match.group(0)], file.read())


def tester_command(name, image, network, work_dir, repo_dir, proxy, environment, extra_mounts=()):
    """`docker run` arguments for one tester attempt (the prompt goes on stdin)."""
    passed = [item for key in PASSED_ENVIRONMENT if environment.get(key) for item in ('-e', key)]
    return [
        'docker', 'run', '--rm', '-i', '--name', name, '--network', network,
        '--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        # DSH loads native bindings it unpacks in /tmp: exec is required.
        '--pids-limit', '1024', '--memory', '4g', '--cpus', '2', '--tmpfs', '/tmp:rw,exec,size=1g',
        '-v', f'{work_dir}:/work', *(['-v', f'{repo_dir}:/repo:ro'] if repo_dir else []), *extra_mounts,
        # curl reads only the lowercase http_proxy; Node and others the uppercase forms.
        *[item for name, value in (('HTTPS_PROXY', f'http://{proxy}'), ('HTTP_PROXY', f'http://{proxy}'),
                                   ('NO_PROXY', '127.0.0.1,localhost'))
          for item in ('-e', f'{name}={value}', '-e', f'{name.lower()}={value}')],
        '-e', 'NODE_USE_ENV_PROXY=1', '-e', 'DSH_TELEMETRY_MODE=DISABLED', *passed,
        image, 'dsh', '--profile', 'superagent-uat', '--json', '-',
    ]


def attempt(command, name, prompt, timeout, out_dir, environment):
    """Run one tester container; (raw trace, exit code, timed out, started, finished)."""
    started = time.time()
    trace_path, stderr_path = os.path.join(out_dir, 'trace.ndjson'), os.path.join(out_dir, 'dsh-stderr.log')
    with open(trace_path, 'wb') as trace, open(stderr_path, 'wb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=trace, stderr=stderr, env=environment)
        try:
            process.communicate(prompt.encode('utf-8'), timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            # Stopping the docker client does not stop its container.
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
            process.kill()
            process.wait()
    with open(trace_path, 'rb') as trace:
        return trace.read(results.MAX_TRACE_BYTES + 1), process.returncode, timed_out, started, time.time()


def run(meta, spec, *, image, network, proxy, work_root, out_dir, base_url, repo_dir=None, timeout=1800,
        environment=None, name_prefix='superagent-uat', extra_mounts=(), kind='local', notes=''):
    """Test the spec, retrying a transient failure once; (Outcome, path of the work dir that produced it)."""
    environment = dict(os.environ if environment is None else environment)
    prompt = render_prompt(meta, spec, base_url, kind=kind, notes=notes)
    ids = [scenario.id for scenario in spec.scenarios]
    records = []
    for number in (1, 2):
        work_dir = os.path.join(work_root, f'attempt-{number}')
        attempt_out = os.path.join(out_dir, f'attempt-{number}')
        os.makedirs(os.path.join(work_dir, 'artifacts'))
        os.makedirs(attempt_out)
        with open(os.path.join(attempt_out, 'prompt.md'), 'w', encoding='utf-8') as file:
            file.write(prompt)
        name = f'{name_prefix}-tester-{number}'
        command = tester_command(name, image, network, work_dir, repo_dir, proxy, environment, extra_mounts)
        raw, exit_code, timed_out, started, finished = attempt(command, name, prompt, timeout, attempt_out, environment)
        outcome = results.evaluate(raw, exit_code, timed_out, meta, ids, work_dir)
        records.append({'attempt': number, 'started_at': started, 'finished_at': finished, 'exit_code': exit_code,
                        'timed_out': timed_out, 'tool_calls': outcome.tool_calls, 'verdict': outcome.verdict,
                        'reasons': outcome.reasons, 'transient': outcome.transient})
        with open(os.path.join(out_dir, 'attempts.json'), 'w', encoding='utf-8') as file:
            json.dump(records, file, indent=2)
        if not outcome.transient:
            break
    return outcome, work_dir


def remove_tree(path, image):
    """Delete a work tree whose files a container may have created."""
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        subprocess.run(['docker', 'run', '--rm', '-v', f'{path}:/target', '--user', '0', '--entrypoint', 'rm', image,
                        '-rf', '/target'], capture_output=True)
        shutil.rmtree(path, ignore_errors=True)
