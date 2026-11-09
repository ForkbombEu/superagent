"""Turn one tester run into a verdict, trusting nothing the model says without checking it.

DSH exiting 0 only means the agent's turn completed. A run passes only when:
the NDJSON trace opens with `session`, ends its turn `completed` with a `final`
event and has no `error` event; the final answer is exactly one JSON object
valid against result.schema.json; it repeats the controller's metadata
(repository, issue, PR, head SHA, spec hash, environment, revision); it covers
exactly the spec's scenario ids; its overall verdict agrees with the scenario
verdicts; the tester executed at least one shell command; and every PASS or
FAIL scenario cites evidence files in its own fresh artifacts directory, each
captured from the application by a completed command (curl or chromium
addressing only the application, writing the file itself or through a tee, or
a copy of its log under /app-logs/) and written by no other command or file
tool, with every HTTP status the scenario reports in the status lines of its
own responses, and reproduction steps for each failure. Anything else is
BLOCKED, never PASS.
"""
import json
import os
import re
import shlex
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
# The tester's capture tools (prompts/uat-tester.md): an HTTP client and a browser that write what the
# application answered, and readers that copy its log.
CLIENTS = {'curl', 'chromium'}
LOG_READERS = {'cp', 'cat', 'tail', 'head'}
# Commands that only read the files they name; any other command naming an evidence file may write it.
READERS = {'cat', 'head', 'tail', 'grep', 'jq', 'less', 'wc', 'ls', 'file', 'stat', 'sha256sum', 'md5sum', 'test',
           '[', 'xxd', 'od', 'diff', 'cmp'}
PREFIXES = {'env', 'command', 'nice', 'time'}
# Shell tokens that end a simple command; `|` also pipes its output into the next one.
OPERATORS = {';', '&&', '||', '|', '&', '(', ')', '{', '}'}
REDIRECTS = {'>', '>>', '>|', '&>', '&>>', '<', '>&', '<&'}
# curl --write-out may add only curl's own variables and separators, never a forged status line.
WRITE_OUT = re.compile(r'(%\{[a-z_]+\}|[A-Za-z _=:,-]|\\[nt])*')
HTTP_STATUS_LINE = re.compile(rb'^HTTP/[0-9.]+ ([1-5][0-9]{2})\b', re.MULTILINE)
# A status code `observed` reports: after HTTP/status/code (a list too: "HTTP 201, then 200") or before a
# reason phrase ("404 Not Found"); "500 records" is a quantity.
CLAIM = re.compile(r'\b(?:HTTP(?:/[0-9.]+)?|status(?:\s+code)?|code)\s*:?\s*([1-5][0-9]{2}(?:(?:\s*(?:,|/|\band\b|\bthen\b|'
                   r'\bor\b))+\s*[1-5][0-9]{2}\b)*)|\b([1-5][0-9]{2})\s+(?:OK|Created|Accepted|No Content|Moved|Found|See Other|'
                   r'Not Modified|Bad Request|Unauthorized|Forbidden|Not Found|Method Not Allowed|Conflict|Gone|'
                   r'Unprocessable|Too Many Requests|Internal Server Error|Bad Gateway|Service Unavailable|'
                   r'Gateway Timeout)\b', re.IGNORECASE)
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


def shell_text(command):
    """The command without comments (`#` starting a word, outside quotes, to the end of its line), with
    line continuations joined and the other unquoted newlines made `;`, as the shell reads them."""
    kept, quote, index = [], None, 0
    while index < len(command):
        char = command[index]
        if quote:
            quote = None if char == quote else quote
        elif char in '\'"':
            quote = char
        elif char == '\\':
            kept.append('' if command[index + 1:index + 2] == '\n' else command[index:index + 2])
            index += 2
            continue
        elif char == '#' and (index == 0 or command[index - 1] in ' \t\n;|&()'):
            index = command.find('\n', index)
            if index < 0:
                break
            continue
        elif char == '\n':
            char = ';'
        kept.append(char)
        index += 1
    return ''.join(kept)


def segments(command):
    """The simple commands of a shell command line: (words, {redirection target: operator}, piped into the
    next one). An unparsable command has none."""
    lexer = shlex.shlex(shell_text(command), posix=True, punctuation_chars=True)
    lexer.whitespace_split, lexer.commenters = True, ''
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    found, words, targets, redirect = [], [], {}, None
    for token in [*tokens, ';']:
        if redirect:
            targets[token], redirect = redirect, None
        elif token in REDIRECTS:
            redirect = token
        elif token in OPERATORS:
            found.append((words, targets, token == '|'))
            words, targets = [], {}
        else:
            words.append(token)
    return [segment for segment in found if segment[0] or segment[1]]


def program(words):
    """The command a simple command runs and its arguments, past assignments and wrappers (env, timeout ...)."""
    index = 0
    while index < len(words) and (re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', words[index]) or words[index] in PREFIXES):
        index += 1
    if index < len(words) and words[index] == 'timeout':
        index += 1
        while index < len(words) and (words[index].startswith('-') or re.fullmatch(r'[0-9.]+[smhd]?', words[index])):
            index += 1
    return (os.path.basename(words[index]), words[index + 1:]) if index < len(words) else ('', [])


def names(word, path):
    """Whether a word names the evidence file (`artifacts/x`, `/work/artifacts/x`, `--opt=artifacts/x`)."""
    return word == path or word.endswith(('/' + path, '=' + path))


def application_hosts(origins):
    """host:port of each application origin, 127.0.0.1 and localhost alike."""
    hosts = set()
    for origin in origins:
        url = urllib.parse.urlsplit(origin)
        port = url.port or (443 if url.scheme == 'https' else 80)
        for host in {url.hostname, *(('127.0.0.1', 'localhost') if url.hostname in ('127.0.0.1', 'localhost') else ())}:
            hosts.add(f'{host}:{port}')
    return hosts


def from_application(segment, hosts):
    """Whether a simple command outputs only what the application answered: a client addressing the
    application and nothing else (no file:, data: or other host, no option file, no forged write-out), or
    a reader of the application's log and nothing else."""
    name, arguments = program(segment[0])
    if name in CLIENTS:
        for word, following in zip(arguments, [*arguments[1:], '']):
            if word in ('-K', '--config') or word.startswith('--config='):
                return False
            if word in ('-w', '--write-out') and not WRITE_OUT.fullmatch(following):
                return False
        urls = [value for value in (word.split('=', 1)[-1] for word in arguments)
                if '://' in value or re.match(r'(file|data|javascript|blob|about):', value)]
        for url in urls:
            parts = urllib.parse.urlsplit(url)
            if parts.scheme not in ('http', 'https') or (
                    f'{parts.hostname}:{parts.port or (443 if parts.scheme == "https" else 80)}' not in hosts):
                return False
        return bool(urls)
    if name in LOG_READERS:
        sources = [word for word in (arguments[:-1] if name == 'cp' else arguments) if not word.startswith('-')]
        return bool(sources) and all(word.startswith(APP_LOGS) or word.isdigit() for word in sources) and any(
            word.startswith(APP_LOGS) for word in sources)
    return False


def writes(segment, path):
    """Whether a simple command may write the evidence file: a redirection or tee into it, or any command
    but a known reader that names it (a client's output option, cp, sed -i, an interpreter ...)."""
    words, targets, _ = segment
    name, arguments = program(words)
    if any(names(target, path) and operator not in ('<', '>&', '<&') for target, operator in targets.items()):
        return True
    named = [index for index, word in enumerate(arguments) if names(word, path)]
    if name == 'cp' and named:
        return named[-1] == len(arguments) - 1
    return bool(named) and name not in READERS


def captures(simple, index, hosts):
    """Whether simple command `index`, which writes the evidence file, writes what the application answered:
    it outputs only that, or it is a tee fed by such a command."""
    if program(simple[index][0])[0] == 'tee':
        return index > 0 and simple[index - 1][2] and from_application(simple[index - 1], hosts)
    return from_application(simple[index], hosts)


def provenance_problems(scenario, commands, hosts, work_dir):
    """Why a PASS or FAIL scenario's evidence does not show what the application did, if it does not.

    `commands` are (command, completed) of every shell call. Each cited file must be captured from the
    application (captures) by a completed command and written by nothing else; each HTTP status `observed`
    reports must be in the status lines of its cited responses (or a bare curl %{http_code} capture)."""
    problems, statuses = [], set()
    parsed = [(segments(command), completed) for command, completed in commands]
    for path in scenario['evidence']:
        captured = forged = code_only = False
        for simple, completed in parsed:
            for index, segment in enumerate(simple):
                if not writes(segment, path):
                    continue
                if completed and captures(simple, index, hosts):
                    captured = True
                    source = simple[index - 1] if program(segment[0])[0] == 'tee' else segment
                    code_only |= any(variable in word for word in source[0]
                                     for variable in ('%{http_code}', '%{response_code}'))
                else:
                    forged = True
        if forged:
            problems.append(f'{path} is written by a command that failed or does not capture it from the application')
        elif not captured:
            problems.append(f'{path} was not captured from the application ({", ".join(sorted(hosts))}) or its log '
                            f'by a command that names it')
        try:
            with open(os.path.join(work_dir, path), 'rb') as file:
                content = file.read(MAX_EVIDENCE_BYTES)
        except OSError:
            continue  # evidence_problem reports it.
        statuses |= {int(code) for code in HTTP_STATUS_LINE.findall(content)}
        if code_only and re.fullmatch(rb'\s*[1-5][0-9]{2}\s*', content):
            statuses.add(int(content))
    claimed = {int(code) for match in CLAIM.finditer(scenario['observed'])
               for code in re.findall(r'[1-5][0-9]{2}', match.group(1) or match.group(2))}
    if claimed - statuses:
        problems.append(f'observed reports HTTP {", ".join(map(str, sorted(claimed - statuses)))}, but its evidence '
                        'holds ' + (f'only the responses {", ".join(map(str, sorted(statuses)))}' if statuses else
                                    'no HTTP status line (capture responses with curl -i)'))
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
    commands = [(arguments.get('command', '') if isinstance(arguments, dict) else json.dumps(arguments),
                 status == 'completed') for tool, arguments, status in calls if tool in SHELL_TOOLS]
    if not any(completed for _, completed in commands):
        reasons.append('the tester executed no command')
    hand_written = {path for tool, arguments, _ in calls if tool in FILE_WRITE_TOOLS
                    for path in re.findall(r'artifacts/[A-Za-z0-9._/-]+', json.dumps(arguments))}
    hosts = application_hosts(origins)
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
        reasons += [f'{scenario["id"]}: {problem}' for problem in provenance_problems(scenario, commands, hosts, work_dir)]
    if not reasons:
        outcome.verdict = derived
        if derived == BLOCKED:
            outcome.reasons = [f'{scenario["id"]}: {scenario["observed"]}' for scenario in result['scenarios']
                               if scenario['verdict'] == BLOCKED]
    return outcome
