"""Turn one tester run into a verdict, trusting nothing the model says without checking it.

DSH exiting 0 only means the agent's turn completed. A run passes only when:
the NDJSON trace opens with `session`, ends its turn `completed` with a `final`
event and has no `error` event; the final answer is exactly one JSON object
valid against result.schema.json; it repeats the controller's metadata
(repository, issue, PR, head SHA, spec hash, environment, revision); it covers
exactly the spec's scenario ids; its overall verdict agrees with the scenario
verdicts; the tester executed at least one shell command; and every PASS or
FAIL scenario cites evidence files that the run's commands produced in its own
fresh artifacts directory (not written by hand with a file tool), with
reproduction steps for each failure. Anything else is BLOCKED, never PASS.
"""
import json
import os
import re
import stat
import urllib.parse
from dataclasses import dataclass, field

from uat import schema

PASS, FAIL, BLOCKED = 'PASS', 'FAIL', 'BLOCKED'
# Tool names of the pinned DSH (uat/tester/profile.patch.yml disables the
# subagent and workflow tools, so every call is in the session's own trace).
SHELL_TOOLS = {'bash'}
FILE_WRITE_TOOLS = {'write', 'edit'}
# DSH's own retryable model-request failures (dsh-llm DEFAULT_RETRYABLE_CODES),
# still failing after DSH retried them: the controller retries the run once.
TRANSIENT_CODES = {'EMPTY_RESPONSE', 'RATE_LIMIT', 'SERVER', 'TIMEOUT', 'TRANSPORT'}
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_BYTES = 25 * 1024 * 1024
# Where an application observation can come from besides its URL: the local application's console log.
APP_LOGS = '/app-logs/'
HTTP_STATUS_LINE = re.compile(rb'^HTTP/[0-9.]+ ([1-5][0-9]{2})\b', re.MULTILINE)
NUMBER = re.compile(r'(?<![\w.:/-])([1-5][0-9]{2})(?![\w.:/-])')
METADATA = ('repository', 'issue_number', 'pr_number', 'head_sha', 'uat_spec_sha256', 'environment',
            'environment_revision')


@dataclass
class Outcome:
    verdict: str
    reasons: list = field(default_factory=list)
    transient: bool = False
    result: dict | None = None
    tool_calls: int = 0


def read_trace(raw):
    """The trace's events, or raise ValueError for output that is not well-formed NDJSON."""
    if len(raw) > MAX_TRACE_BYTES:
        raise ValueError(f'the trace is larger than {MAX_TRACE_BYTES} bytes')
    events = []
    for number, line in enumerate(raw.decode('utf-8', errors='strict').splitlines(), 1):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ValueError(f'trace line {number} is not an event object')
        events.append(event)
    return events


def final_answer(events, exit_code, timed_out):
    """(final text, None, False) for a completed run, else (None, problem, transient)."""
    if timed_out:
        return None, 'the tester did not finish within its time limit', False
    if not events:
        return None, f'DSH produced no output (exit {exit_code})', False
    if events[0]['type'] != 'session':
        return None, 'the trace does not start with a session event', False
    errors = [event for event in events if event['type'] == 'error']
    if errors:
        message = str(errors[0].get('message') or errors[0].get('code') or 'unknown error')
        return None, f'DSH failed: {message[:500]}', False
    ends = [event for event in events if event['type'] == 'status' and event.get('phase') == 'turn_end']
    reason = ends[-1].get('reason') if ends else None
    kind = reason.get('kind') if isinstance(reason, dict) else None
    if kind != 'completed':
        code = (reason.get('error') or {}).get('code') if isinstance(reason, dict) else None
        detail = f'{kind or "no"} turn end' + (f' ({code})' if code else '')
        return None, f'the tester turn did not complete: {detail}', code in TRANSIENT_CODES
    if exit_code != 0:
        return None, f'DSH exited {exit_code} after a completed turn', False
    if events[-1]['type'] != 'final' or not isinstance(events[-1].get('text'), str):
        return None, 'the trace does not end with a final answer', False
    return events[-1]['text'], None, False


def parse_result(text):
    """The JSON object of a final answer that is the object alone, optionally in one ```json fence."""
    stripped = text.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*)\n```', stripped, re.DOTALL)
    document = json.loads(fenced.group(1) if fenced else stripped)
    if not isinstance(document, dict):
        raise ValueError('the final answer is not a JSON object')
    return document


def tool_calls(events):
    """[(tool, input, status)] of every tool call, status None when it never returned."""
    results = {event.get('callId'): event.get('status') for event in events if event['type'] == 'tool_result'}
    return [(event.get('tool'), event.get('input'), results.get(event.get('callId')))
            for event in events if event['type'] == 'tool_call']


def evidence_problem(path, work_dir, hand_written):
    artifacts = os.path.realpath(os.path.join(work_dir, 'artifacts'))
    full = os.path.join(work_dir, path)
    if os.path.realpath(full) != os.path.abspath(full) or not os.path.realpath(full).startswith(artifacts + os.sep):
        return f'{path} is not a file in the artifacts directory'
    try:
        info = os.lstat(full)
    except OSError:
        return f'{path} does not exist'
    if not stat.S_ISREG(info.st_mode):
        return f'{path} is not a regular file'
    if info.st_size == 0:
        return f'{path} is empty'
    if info.st_size > MAX_EVIDENCE_BYTES:
        return f'{path} is larger than {MAX_EVIDENCE_BYTES} bytes'
    if path in hand_written:
        return f'{path} was written with a file tool, not captured from a command'
    return None


def application_needles(origins):
    """The strings by which a command addresses the application: host:port of each origin (127.0.0.1 and
    localhost alike), the bare host when the port is the scheme's default, and its log directory."""
    needles = {APP_LOGS}
    for origin in origins:
        url = urllib.parse.urlsplit(origin)
        hosts = {url.hostname, *(('127.0.0.1', 'localhost') if url.hostname in ('127.0.0.1', 'localhost') else ())}
        needles |= {f'{host}:{url.port}' if url.port else host for host in hosts}
    return needles


def provenance_problems(scenario, commands, needles, work_dir):
    """Why a PASS or FAIL scenario's evidence does not show it was observed on the application, if it does not.

    Each cited file must be named by an executed shell command, at least one of them by a command that
    addressed the application, and the HTTP status codes `observed` names must be in the cited responses."""
    problems, from_application, statuses = [], False, set()
    for path in scenario['evidence']:
        named = re.compile(re.escape(path) + r'(?![A-Za-z0-9._/-])')
        producers = [command for command in commands if named.search(command)]
        if not producers:
            problems.append(f'{path} is named by no executed command')
        from_application |= any(needle in command for command in producers for needle in needles)
        try:
            with open(os.path.join(work_dir, path), 'rb') as file:
                statuses |= {int(code) for code in HTTP_STATUS_LINE.findall(file.read(MAX_EVIDENCE_BYTES))}
        except OSError:
            pass  # evidence_problem reports it.
    if scenario['evidence'] and not from_application:
        problems.append('none of its evidence comes from a command that reached the application')
    claimed = {int(code) for code in NUMBER.findall(scenario['observed'])}
    if statuses and claimed - statuses:
        problems.append(f'observed names HTTP {", ".join(map(str, sorted(claimed - statuses)))}, but its evidence '
                        f'shows {", ".join(map(str, sorted(statuses)))}')
    return problems


def evaluate(raw_trace, exit_code, timed_out, expected, scenario_ids, work_dir, origins):
    """The Outcome of one run; `expected` holds the controller's own METADATA values and `origins` the
    application's base URLs."""
    try:
        events = read_trace(raw_trace)
    except (ValueError, UnicodeDecodeError) as error:
        return Outcome(BLOCKED, [f'malformed DSH output: {error}'])
    text, problem, transient = final_answer(events, exit_code, timed_out)
    calls = tool_calls(events)
    if problem:
        return Outcome(BLOCKED, [problem], transient, tool_calls=len(calls))
    try:
        result = parse_result(text)
    except ValueError as error:
        return Outcome(BLOCKED, [f'the final answer is not a result object: {error}'], tool_calls=len(calls))
    outcome = Outcome(BLOCKED, [], result=result, tool_calls=len(calls))
    outcome.reasons = schema.errors(result, schema.load('result'))
    if outcome.reasons:
        return outcome
    reasons = outcome.reasons
    for key in METADATA:
        if result[key] != expected[key]:
            reasons.append(f'{key} is {result[key]!r}, not {expected[key]!r}')
    ids = [scenario['id'] for scenario in result['scenarios']]
    missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in ids]
    extra = sorted(set(ids) - set(scenario_ids))
    repeated = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    for label, values in (('missing', missing), ('unknown', extra), ('repeated', repeated)):
        if values:
            reasons.append(f'{label} scenarios: {", ".join(values)}')
    verdicts = {scenario['verdict'] for scenario in result['scenarios']}
    derived = FAIL if FAIL in verdicts else BLOCKED if BLOCKED in verdicts else PASS
    if result['verdict'] != derived:
        reasons.append(f'overall verdict {result["verdict"]} contradicts the scenario verdicts ({derived})')
    commands = [arguments.get('command', '') if isinstance(arguments, dict) else json.dumps(arguments)
                for tool, arguments, status in calls if tool in SHELL_TOOLS and status == 'completed']
    if not commands:
        reasons.append('the tester executed no command')
    hand_written = {path for tool, arguments, _ in calls if tool in FILE_WRITE_TOOLS
                    for path in re.findall(r'artifacts/[A-Za-z0-9._/-]+', json.dumps(arguments))}
    needles = application_needles(origins)
    for scenario in result['scenarios']:
        if scenario['verdict'] == BLOCKED:
            continue
        if not scenario['evidence']:
            reasons.append(f'{scenario["id"]} is {scenario["verdict"]} without evidence')
        for path in scenario['evidence']:
            problem = evidence_problem(path, work_dir, hand_written)
            if problem:
                reasons.append(f'{scenario["id"]}: {problem}')
        if scenario['verdict'] == FAIL and not scenario.get('reproduction'):
            reasons.append(f'{scenario["id"]} fails without reproduction steps')
        reasons += [f'{scenario["id"]}: {problem}' for problem in provenance_problems(scenario, commands, needles, work_dir)]
    if not reasons:
        outcome.verdict = derived
        if derived == BLOCKED:
            outcome.reasons = [f'{scenario["id"]}: {scenario["observed"]}' for scenario in result['scenarios']
                               if scenario['verdict'] == BLOCKED]
    return outcome
