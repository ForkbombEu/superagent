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
own responses or in a status-only curl capture (status_only), and
reproduction steps for each failure. Anything else is BLOCKED, never PASS.
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
# Commands that do not write the contents of files they name; any other command naming an evidence file
# may write it. `rm` can remove evidence, which evidence_problem detects, but cannot fabricate its contents.
NON_WRITERS = {'cat', 'head', 'tail', 'grep', 'jq', 'less', 'wc', 'ls', 'file', 'stat', 'sha256sum', 'md5sum', 'test',
               '[', 'xxd', 'od', 'diff', 'cmp', 'rm'}
PREFIXES = {'env', 'command', 'nice', 'time'}
# Shell tokens that end a simple command; `|` also pipes its output into the next one.
OPERATORS = {';', '&&', '||', '|', '&', '(', ')', '{', '}'}
REDIRECTS = {'>', '>>', '>|', '&>', '&>>', '<', '>&', '<&'}
# curl --write-out may add only curl's own variables and separators, never a forged status line.
WRITE_OUT = re.compile(r'(%\{[a-z_]+\}|[A-Za-z _=:,-]|\\[nt])*')
# A write-out that prints the response status alone, and curl options that send a body elsewhere than -o.
STATUS_WRITE_OUT = re.compile(r'(?:\s|\\[nr])*%\{(?:http_code|response_code)\}(?:\s|\\[nr])*')
CURL_OTHER_OUTPUTS = {'--remote-name', '--remote-name-all', '--output-dir'}
CURL_ALIASES = {'-o': '--output', '-w': '--write-out', '-O': '--remote-name'}
# curl options that take a value: the next word, the rest of a short option cluster (`-ofile`), or `=value`.
# Any other word that is not an option is a URL curl requests, like the value of --url.
CURL_VALUE_SHORT = set('AbcCdDeEFHmoPQrtTuUwXyYz')
CURL_VALUE_LONG = {
    '--data', '--data-ascii', '--data-binary', '--data-raw', '--data-urlencode', '--json', '--form', '--form-string',
    '--header', '--proxy-header', '--request', '--request-target', '--output', '--output-dir', '--write-out', '--user',
    '--user-agent', '--referer', '--cookie', '--cookie-jar', '--upload-file', '--max-time', '--connect-timeout',
    '--retry', '--retry-delay', '--retry-max-time', '--range', '--dump-header', '--cacert', '--capath', '--cert',
    '--cert-type', '--key', '--key-type', '--pass', '--url', '--url-query', '--limit-rate', '--max-filesize',
    '--max-redirs', '--oauth2-bearer', '--continue-at', '--speed-limit', '--speed-time', '--time-cond', '--trace',
    '--trace-ascii', '--stderr', '--variable', '--expect100-timeout', '--keepalive-time', '--aws-sigv4', '--ciphers',
    '--interface', '--local-port', '--netrc-file', '--proto', '--proto-redir'}
# Options that make curl connect elsewhere than the URL names, or read options from a file: refused.
CURL_REROUTE = {'-x', '-K', '--proxy', '--preproxy', '--connect-to', '--resolve', '--unix-socket',
                '--abstract-unix-socket', '--socks4', '--socks4a', '--socks5', '--socks5-hostname', '--doh-url',
                '--proto-default', '--config'}
HTTP_STATUS_LINE = re.compile(rb'^HTTP/[0-9.]+ ([1-5][0-9]{2})\b', re.MULTILINE)
# Reading the HTTP statuses `observed` reports (reported_statuses). A three-digit number not part of a
# longer token (200ms, 12:30:45, 2026-11-09, #201, v1.201) is a status after one of STATUS_WORDS or
# ANSWER_VERBS ("POST returned 201"), before a reason phrase ("404 Not Found"), continuing a list of
# statuses ("HTTP 201, then 200") or, when the scenario's evidence holds HTTP responses, alone ("201, 200").
# It is a quantity, an id, a measurement or a contrast after one of NOT_STATUS ("id 201", "port 300",
# "latency 200", "p95 300", "shows 500", "expected 201") or before any word but those of KEEPS_STATUS
# ("500 records", "200 ms"), even within a list ("HTTP 201 and 200 ms later").
NUMBER = re.compile(r'(?<![\w#.:/-])[1-5][0-9]{2}(?![\w:/-]|\.[0-9])')
STATUS_WORDS = {'http', 'status', 'statuses', 'code', 'codes'}
ANSWER_VERBS = {'returned', 'returns', 'return', 'returning', 'responded', 'responds', 'respond', 'answered', 'answers',
                'answer', 'got', 'gets', 'get', 'received', 'receives', 'gave', 'gives', 'replied', 'replies',
                'yielded', 'yields', 'with'}
NOT_STATUS = {'id', 'ids', 'no', 'number', 'numbers', 'record', 'records', 'item', 'items', 'row', 'rows', 'page',
              'pages', 'port', 'version', 'line', 'lines', 'user', 'users', 'of', 'total', 'count', 'size', 'length',
              'limit', 'offset', 'top', 'first', 'last', 'expected', 'expecting', 'expect', 'expects', 'not', 'than',
              'should', 'be', 'instead', 'shows', 'show', 'showed', 'lists', 'list', 'listed', 'contains', 'contain',
              'has', 'have', 'had', 'displays', 'display', 'displayed', 'took', 'takes', 'lasted', 'within', 'over',
              'under', 'about', 'around', 'approximately', 'after', 'in', 'at', 'every', 'each', 'latency',
              'latencies', 'duration', 'durations', 'time', 'times', 'timing', 'timings', 'elapsed', 'delay', 'rtt',
              'ttfb', 'ms', 'bytes', 'sizes', 'lengths', 'counts', 'amount', 'quantity', 'avg', 'average', 'mean',
              'median', 'min', 'max', 'p50', 'p90', 'p95', 'p99'}
KEEPS_STATUS = {'for', 'on', 'in', 'with', 'when', 'after', 'because', 'as', 'to', 'from', 'and', 'then', 'but',
                'while', 'at', 'so', 'since', 'instead', 'again', 'both', 'each', 'via', 'by', 'once', 'twice', 'if',
                'or', 'not', 'response', 'responses', 'error', 'errors', 'status', 'code', 'reply', 'answer'}
ARTICLES = {'a', 'an', 'the'}
REASON = re.compile(r'(?:OK|Created|Accepted|No Content|Moved|Found|See Other|Not Modified|Bad Request|Unauthorized|'
                    r'Forbidden|Not Found|Method Not Allowed|Conflict|Gone|Unprocessable|Too Many Requests|'
                    r'Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout)\b', re.IGNORECASE)
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
    line continuations joined and the other unquoted newlines made `;`, as the shell reads them. Unquoted
    IO numbers attached to redirections are omitted: they select a file descriptor, not a command argument."""
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
        elif char.isdigit() and (index == 0 or command[index - 1] in ' \t\n;|&(){}'):
            end = index + 1
            while end < len(command) and command[end].isdigit():
                end += 1
            if any(command.startswith(operator, end) for operator in REDIRECTS):
                index = end
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


def request_urls(name, arguments, options=None):
    """The URLs a curl or chromium command requests, or None when it may also reach something else
    (a rerouting or option-file curl option, a forged write-out). Values of options (headers, bodies,
    referers) are not requests, whatever they contain. `options`, a dict, collects the values of each
    curl option ('' for a flag) under its long name for -o, -w and -O, else as written."""
    urls, index = [], 0
    while index < len(arguments):
        word = arguments[index]
        index += 1
        if name == 'chromium':  # Its flags are all --flag[=value]; every other word is a page it opens.
            if not word.startswith('-'):
                urls.append(word)
            continue
        if not word.startswith('-') or word == '-':
            urls.append(word)
            continue
        if word == '--':
            urls += arguments[index:]
            break
        if word.startswith('--'):
            option, has_value, value = word.partition('=')
            if option in CURL_REROUTE:
                return None
            if option in CURL_VALUE_LONG and not has_value:
                value, index = (arguments[index], index + 1) if index < len(arguments) else ('', index)
        else:
            option = value = None
            for position, letter in enumerate(word[1:], 2):
                if f'-{letter}' in CURL_REROUTE:
                    return None
                if letter in CURL_VALUE_SHORT:
                    option, value = f'-{letter}', word[position:]
                    if not value:
                        value, index = (arguments[index], index + 1) if index < len(arguments) else ('', index)
                    break
                if options is not None:
                    options.setdefault(CURL_ALIASES.get(f'-{letter}', f'-{letter}'), []).append('')
        if options is not None and option:
            options.setdefault(CURL_ALIASES.get(option, option), []).append(value)
        if option == '--url':
            urls.append(value)
        elif option in ('-w', '--write-out') and not WRITE_OUT.fullmatch(value):
            return None
    return urls


def addresses_application(url, hosts):
    """Whether a requested URL is http(s) on the application. Like curl, a URL without a scheme is http;
    `data:`, `file:`, `about:` ... are other schemes, refused."""
    if '://' not in url:
        if re.match(r'[A-Za-z][A-Za-z0-9+.-]*:(?![0-9])', url):
            return False
        url = 'http://' + url
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:  # An invalid or globbed port.
        return False
    return parts.scheme in ('http', 'https') and f'{parts.hostname}:{port}' in hosts


def from_application(segment, hosts):
    """Whether a simple command outputs only what the application answered: a client requesting the
    application's URLs and nothing else (no file:, data: or other host, no rerouting, no option file, no
    forged write-out), or a reader of the application's log and nothing else."""
    name, arguments = program(segment[0])
    if name in CLIENTS:
        urls = request_urls(name, arguments)
        return bool(urls) and all(addresses_application(url, hosts) for url in urls)
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
    return bool(named) and name not in NON_WRITERS


def captures(simple, index, hosts):
    """Whether simple command `index`, which writes the evidence file, writes what the application answered:
    it outputs only that, or it is a tee fed by such a command."""
    if program(simple[index][0])[0] == 'tee':
        return index > 0 and simple[index - 1][2] and from_application(simple[index - 1], hosts)
    return from_application(simple[index], hosts)


def status_only(simple, index, path):
    """Whether simple command `index`, which captures the evidence file, writes into it the response status
    alone: curl's standard output, redirected (`>`, `>>`, `>|`) or tee'd into the file and nowhere else,
    holds only its single write-out, one status variable, because the body of its single, unglobbed request
    goes to `-o /dev/null`. A body written to the file (`-o file`) or to standard output is not a status."""
    tee = program(simple[index][0])[0] == 'tee'
    words, targets, _ = simple[index - 1] if tee else simple[index]
    stdout = [(target, operator) for target, operator in targets.items() if operator in ('>', '>>', '>|', '&>', '&>>')]
    if tee:
        if stdout:
            return False
    elif len(stdout) != 1 or not names(stdout[0][0], path) or stdout[0][1] not in ('>', '>>', '>|'):
        return False
    name, arguments = program(words)
    options = {}
    urls = request_urls(name, arguments, options) if name == 'curl' else None
    write_out = options.get('--write-out', [])
    return (urls is not None and len(urls) == 1 and not re.search(r'[][{}]', urls[0]) and len(write_out) == 1 and
            bool(STATUS_WRITE_OUT.fullmatch(write_out[0])) and options.get('--output') == ['/dev/null'] and
            not CURL_OTHER_OUTPUTS & options.keys() and not any(names(word, path) for word in arguments))


def reported_statuses(text, http):
    """The HTTP status codes a scenario's `observed` text reports (see NUMBER); bare numbers count only
    when `http`, the scenario's evidence holding HTTP responses."""
    reported, previous_end, previous_reported = set(), None, False
    for match in NUMBER.finditer(text):
        words = re.findall(r'[A-Za-z][A-Za-z0-9]*', text[:match.start()])[-2:]
        before = (words[0] if len(words) == 2 and words[1].lower() in ARTICLES else (words or [''])[-1]).lower()
        after = re.match(r'\s*([A-Za-z]+)?', text[match.end():]).group(1) or ''
        continues = previous_reported and re.fullmatch(r'(?:\s*(?:,|/|\band\b|\bthen\b|\bor\b))+\s*',
                                                       text[previous_end:match.start()], re.IGNORECASE)
        if before in STATUS_WORDS or REASON.match(text[match.end():].lstrip()):
            status = True
        elif before in NOT_STATUS or (after and after.lower() not in KEEPS_STATUS):
            status = False
        else:
            status = bool(continues) or before in ANSWER_VERBS or http
        if status:
            reported.add(int(match.group()))
        previous_end, previous_reported = match.end(), status
    return reported


def provenance_problems(scenario, commands, hosts, work_dir):
    """Why a PASS or FAIL scenario's evidence does not show what the application did, if it does not.

    `commands` are (command, completed) of every shell call. Each cited file must be captured from the
    application (captures) by a completed command and written by nothing else; each HTTP status `observed`
    reports must be in the status lines of its cited responses (or a status_only curl capture)."""
    problems, statuses = [], set()
    parsed = [(segments(command), completed) for command, completed in commands]
    for path in scenario['evidence']:
        captured = forged = bodies = False
        for simple, completed in parsed:
            for index, segment in enumerate(simple):
                if not writes(segment, path):
                    continue
                if completed and captures(simple, index, hosts):
                    captured = True
                    bodies |= not status_only(simple, index, path)
                else:
                    forged = True
        if forged:
            problems.append(f'{path} is written by a command that failed or does not capture it from the application')
        elif not captured:
            problems.append(f'{path} was not captured from the application ({", ".join(sorted(hosts))}) or its log '
                            f'by a command that names it')
        code_only = captured and not bodies
        try:
            with open(os.path.join(work_dir, path), 'rb') as file:
                content = file.read(MAX_EVIDENCE_BYTES)
        except OSError:
            continue  # evidence_problem reports it.
        statuses |= {int(code) for code in HTTP_STATUS_LINE.findall(content)}
        if code_only and re.fullmatch(rb'\s*[1-5][0-9]{2}\s*', content):
            statuses.add(int(content))
    claimed = reported_statuses(scenario['observed'], http=bool(statuses))
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
