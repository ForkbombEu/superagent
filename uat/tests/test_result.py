import copy
import json
import os
import tempfile
import unittest

from uat import result as results
from uat.tests.test_schema import RESULT

META = {key: RESULT[key] for key in results.METADATA}
IDS = ['UAT-1', 'UAT-2']
ORIGIN = 'http://127.0.0.1:3000'
PASSING = copy.deepcopy(RESULT)
PASSING['verdict'] = 'PASS'
PASSING['scenarios'][0]['observed'] = 'HTTP 201; GET returned the record'
PASSING['scenarios'][1].update(verdict='PASS', observed='HTTP 400; no record')
PASSING['scenarios'][1].pop('reproduction')
# The commands that captured RESULT's and PASSING's evidence, each naming its file and reaching the application.
CAPTURE = (('bash', f'curl -sS -i -X POST {ORIGIN}/records | tee artifacts/uat-1-http.json', 'completed'),
           ('bash', f"curl -sS -i -X POST -d '{{}}' {ORIGIN}/records | tee /work/artifacts/uat-2-http.json", 'completed'),
           ('bash', 'cp /app-logs/app.log artifacts/server.log', 'completed'))


def trace(final, *, calls=CAPTURE, end='completed', error_code=None, extra=()):
    events = [{'type': 'session', 'sessionId': 'session-1', 'cwd': '/work'},
              {'type': 'status', 'phase': 'turn_start', 'turn': 1}]
    for index, (tool, command, status) in enumerate(calls):
        arguments = {'command': command} if tool == 'bash' else {'path': command, 'content': '{}'}
        events.append({'type': 'tool_call', 'callId': f'c{index}', 'tool': tool, 'input': arguments})
        if status:
            events.append({'type': 'tool_result', 'callId': f'c{index}', 'status': status, 'result': ''})
    events += list(extra)
    reason = {'kind': end, **({'error': {'code': error_code, 'message': 'x'}} if error_code else {})}
    events.append({'type': 'status', 'phase': 'turn_end', 'turn': 1, 'reason': reason})
    if final is not None:
        events.append({'type': 'final', 'text': final if isinstance(final, str) else json.dumps(final)})
    return '\n'.join(json.dumps(event) for event in events).encode()


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.work, 'artifacts'))
        for name, content in (('uat-1-http.json', 'HTTP/1.1 201 Created\r\n\r\n{"id": 1}'),
                              ('uat-2-http.json', 'HTTP/1.1 400 Bad Request\r\n\r\n{"error": "name is required"}'),
                              ('server.log', 'POST /records -> 201\n')):
            self.write(name, content)

    def write(self, name, content):
        with open(os.path.join(self.work, 'artifacts', name), 'w') as file:
            file.write(content)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.work)

    def evaluate(self, raw, exit_code=0, timed_out=False, ids=IDS):
        return results.evaluate(raw, exit_code, timed_out, META, ids, self.work, origins=[ORIGIN])

    def assertBlocked(self, outcome, reason):
        self.assertEqual(outcome.verdict, results.BLOCKED, outcome)
        self.assertRegex(' | '.join(outcome.reasons), reason)

    def test_valid_pass(self):
        outcome = self.evaluate(trace(PASSING))
        self.assertEqual((outcome.verdict, outcome.reasons), ('PASS', []))

    def test_fenced_json_answer_is_accepted(self):
        self.assertEqual(self.evaluate(trace(f'```json\n{json.dumps(PASSING)}\n```')).verdict, 'PASS')

    def test_expected_fail(self):
        self.write('uat-2-http.json', 'HTTP/1.1 500 Internal Server Error\r\n\r\n{}')
        outcome = self.evaluate(trace(RESULT))
        self.assertEqual(outcome.verdict, 'FAIL', outcome.reasons)
        self.assertEqual(outcome.result['scenarios'][1]['observed'], 'HTTP 500')

    def test_scenario_blocked_reports_blocked_with_reason(self):
        blocked = copy.deepcopy(PASSING)
        blocked['verdict'] = 'BLOCKED'
        blocked['scenarios'][1].update(verdict='BLOCKED', observed='fixture user is missing', evidence=[])
        self.assertBlocked(self.evaluate(trace(blocked)), 'UAT-2: fixture user is missing')

    def test_unexpected_exit(self):
        self.assertBlocked(self.evaluate(trace(PASSING), exit_code=1), 'exited 1')
        self.assertBlocked(self.evaluate(b'', exit_code=137), 'no output')

    def test_timeout(self):
        self.assertBlocked(self.evaluate(trace(PASSING), timed_out=True), 'time limit')

    def test_aborted_turn(self):
        self.assertBlocked(self.evaluate(trace(PASSING, end='aborted'), exit_code=1), 'aborted turn end')

    def test_runner_error_event(self):
        raw = b'{"type":"error","message":"unknown option --permission-mode"}\n'
        self.assertBlocked(self.evaluate(raw, exit_code=1), 'does not start with a session')
        raw = trace(None, extra=[{'type': 'error', 'message': 'agent creation failed'}])
        self.assertBlocked(self.evaluate(raw, exit_code=1), 'DSH failed: agent creation failed')

    def test_transient_model_errors_are_marked_for_retry(self):
        outcome = self.evaluate(trace('', end='error', error_code='RATE_LIMIT'), exit_code=1)
        self.assertBlocked(outcome, 'RATE_LIMIT')
        self.assertTrue(outcome.transient)
        outcome = self.evaluate(trace('', end='error', error_code='AUTH'), exit_code=1)
        self.assertFalse(outcome.transient)

    def test_malformed_output(self):
        self.assertBlocked(self.evaluate(b'{"type":"session"}\nnot json\n'), 'malformed DSH output')
        self.assertBlocked(self.evaluate(b'[1,2]\n'), 'not an event')
        self.assertBlocked(self.evaluate(b'\xff\xfe'), 'malformed DSH output')

    def test_missing_final(self):
        self.assertBlocked(self.evaluate(trace(None)), 'final answer')

    def test_malformed_result_json(self):
        self.assertBlocked(self.evaluate(trace('All scenarios pass!')), 'not a result object')
        self.assertBlocked(self.evaluate(trace(json.dumps(PASSING)[:-10])), 'not a result object')
        self.assertBlocked(self.evaluate(trace('Here it is:\n' + json.dumps(PASSING))), 'not a result object')
        self.assertBlocked(self.evaluate(trace('[]')), 'not a JSON object')
        self.assertBlocked(self.evaluate(trace({**PASSING, 'approved': True})), "unexpected property 'approved'")

    def test_missing_evidence(self):
        missing = copy.deepcopy(PASSING)
        missing['scenarios'][0]['evidence'] = ['artifacts/never-written.json']
        self.assertBlocked(self.evaluate(trace(missing)), 'never-written.json does not exist')
        empty = copy.deepcopy(PASSING)
        empty['scenarios'][0]['evidence'] = []
        self.assertBlocked(self.evaluate(trace(empty)), 'UAT-1 is PASS without evidence')
        open(os.path.join(self.work, 'artifacts', 'uat-1-http.json'), 'w').close()
        self.assertBlocked(self.evaluate(trace(PASSING)), 'is empty')

    def test_evidence_outside_artifacts_or_symlinked(self):
        os.symlink('/etc/hostname', os.path.join(self.work, 'artifacts', 'link'))
        linked = copy.deepcopy(PASSING)
        linked['scenarios'][0]['evidence'] = ['artifacts/link']
        self.assertBlocked(self.evaluate(trace(linked)), 'not a file in the artifacts directory')
        os.makedirs(os.path.join(self.work, 'artifacts', 'dir'))
        directory = copy.deepcopy(PASSING)
        directory['scenarios'][0]['evidence'] = ['artifacts/dir']
        self.assertBlocked(self.evaluate(trace(directory)), 'not a regular file')

    def test_partial_scenario_coverage(self):
        partial = copy.deepcopy(PASSING)
        partial['scenarios'] = partial['scenarios'][:1]
        self.assertBlocked(self.evaluate(trace(partial)), 'missing scenarios: UAT-2')
        extra = copy.deepcopy(PASSING)
        extra['scenarios'].append({**extra['scenarios'][0], 'id': 'UAT-3'})
        self.assertBlocked(self.evaluate(trace(extra)), 'unknown scenarios: UAT-3')
        repeated = copy.deepcopy(PASSING)
        repeated['scenarios'][1]['id'] = 'UAT-1'
        self.assertBlocked(self.evaluate(trace(repeated)), 'repeated scenarios: UAT-1')

    def test_hallucinated_pass_without_execution(self):
        self.assertBlocked(self.evaluate(trace(PASSING, calls=())), 'executed no command')
        self.assertBlocked(self.evaluate(trace(PASSING, calls=(('bash', 'curl', 'error'),))), 'executed no command')
        self.assertBlocked(self.evaluate(trace(PASSING, calls=(('read', 'src/app.py', 'completed'),))),
                           'executed no command')

    def test_hallucinated_pass_with_hand_written_evidence(self):
        calls = (*CAPTURE, ('write', '/work/artifacts/uat-1-http.json', 'completed'))
        self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)), 'written with a file tool')

    def test_fabricated_evidence_that_never_reached_the_application(self):
        for fabrication in (f'echo "HTTP/1.1 201 Created" > artifacts/uat-1-http.json  # {ORIGIN}/records',
                            f"printf 'HTTP/1.1 201 Created\\r\\n' | tee artifacts/uat-1-http.json; curl -sS {ORIGIN}/",
                            f'(echo "HTTP/1.1 201 Created"; curl -sS {ORIGIN}/records) | tee artifacts/uat-1-http.json',
                            f'curl -sS -i {ORIGIN}/records -o artifacts/uat-1-http.json\necho "HTTP/1.1 201 Created" '
                            '> artifacts/uat-1-http.json',
                            f"curl -sS -o /dev/null -w 'HTTP/1.1 201 Created' {ORIGIN}/ > artifacts/uat-1-http.json",
                            f'curl -sS -i {ORIGIN}/records file:///work/fake.txt > artifacts/uat-1-http.json',
                            'curl -sS -i https://example.com/ | tee artifacts/uat-1-http.json',
                            f'python3 -c "open(\'artifacts/uat-1-http.json\', \'w\').write(\'HTTP/1.1 201\')"'):
            with self.subTest(fabrication):
                calls = (('bash', fabrication, 'completed'), *CAPTURE[1:])
                self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)),
                                   'UAT-1: artifacts/uat-1-http.json (is written by a command that failed or does not '
                                   'capture it|was not captured from the application)')

    def test_a_capture_altered_afterwards_is_refused(self):
        calls = (*CAPTURE, ('bash', 'sed -i s/500/201/ artifacts/uat-1-http.json', 'completed'))
        self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)), 'uat-1-http.json is written by a command')
        calls = (*CAPTURE[:2], ('bash', 'cat /app-logs/app.log /work/fake.log > artifacts/server.log', 'completed'))
        self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)), 'server.log is written by a command')
        readers = (*CAPTURE, ('bash', 'cat artifacts/uat-1-http.json; jq . artifacts/uat-2-http.json || true', 'completed'))
        self.assertEqual(self.evaluate(trace(PASSING, calls=readers)).verdict, 'PASS')

    def test_evidence_must_be_captured_by_a_completed_command(self):
        self.assertBlocked(self.evaluate(trace(PASSING, calls=CAPTURE[:1])),
                           'UAT-2: artifacts/uat-2-http.json was not captured from the application')
        failed = (CAPTURE[0], (*CAPTURE[1][:2], 'error'), CAPTURE[2])
        self.assertBlocked(self.evaluate(trace(PASSING, calls=failed)), 'uat-2-http.json is written by a command that failed')
        renamed = (CAPTURE[0], ('bash', f'curl -i {ORIGIN}/records | tee artifacts/uat-2-http.json.bak', 'completed'),
                   CAPTURE[2])
        self.assertBlocked(self.evaluate(trace(PASSING, calls=renamed)), 'uat-2-http.json was not captured')

    def test_claimed_status_must_be_in_the_http_evidence(self):
        self.write('uat-1-http.json', 'HTTP/1.1 500 Internal Server Error\r\n\r\n{}')
        self.assertBlocked(self.evaluate(trace(PASSING)),
                           'UAT-1: observed reports HTTP 201, but its evidence holds only the responses 500')
        unrelated = copy.deepcopy(PASSING)  # UAT-2 cites UAT-1's response, which shows no 400.
        unrelated['scenarios'][1]['evidence'] = ['artifacts/uat-1-http.json']
        self.assertBlocked(self.evaluate(trace(unrelated)), 'UAT-2: observed reports HTTP 400')

    def test_claimed_status_needs_a_response_status(self):
        self.write('uat-1-http.json', '{"id": 1}')  # The body alone: curl without -i.
        self.assertBlocked(self.evaluate(trace(PASSING)), 'UAT-1: observed reports HTTP 201, but its evidence holds no '
                                                          'HTTP status line')
        self.write('uat-1-http.json', '201\n')  # curl -w '%{http_code}': the status alone, from curl itself.
        calls = (('bash', f"curl -sS -o /dev/null -w '%{{http_code}}\\n' -X POST {ORIGIN}/records > "
                          'artifacts/uat-1-http.json', 'completed'), *CAPTURE[1:])
        self.assertEqual(self.evaluate(trace(PASSING, calls=calls)).verdict, 'PASS')
        self.write('uat-1-http.json', '201\n')
        calls = (('bash', f'curl -sS -X POST {ORIGIN}/records > artifacts/uat-1-http.json', 'completed'), *CAPTURE[1:])
        self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)), 'holds no HTTP status line')

    def test_a_status_capture_must_hold_curls_write_out_alone(self):
        record = f'-X POST {ORIGIN}/records'
        self.write('uat-1-http.json', '201')  # The body of an HTTP 500 answer, or a write-out of 201.
        for command in (f"curl -sS -o /dev/null -w '%{{http_code}}' {record} > artifacts/uat-1-http.json",
                        f"curl -sS -o /dev/null -w '%{{http_code}}\\n' {record} | tee artifacts/uat-1-http.json",
                        f'curl -sSo/dev/null --write-out=%{{response_code}} {record} >> /work/artifacts/uat-1-http.json'):
            with self.subTest(command):
                self.assertEqual(self.capture(command).verdict, 'PASS', self.capture(command).reasons)
        for command in (f"curl -sS -o artifacts/uat-1-http.json -w '%{{http_code}}' {record}",
                        f"curl -sS -o artifacts/uat-1-http.json -w '%{{http_code}}' {record} > artifacts/status.txt",
                        f"curl -sS --output artifacts/uat-1-http.json -w '%{{http_code}}' {record} > "
                        'artifacts/uat-1-http.json',
                        f"curl -sS -w '%{{http_code}}' {record} > artifacts/uat-1-http.json",
                        f"curl -sS -o - -w '%{{http_code}}' {record} > artifacts/uat-1-http.json",
                        f"curl -sS -o /dev/null -w '%{{http_code}}' {record} {ORIGIN}/ > artifacts/uat-1-http.json",
                        f"curl -sS -o /dev/null -w '%{{http_code}}' -O {record} > artifacts/uat-1-http.json",
                        f"curl -sS -o /dev/null -w '%{{stderr}}%{{http_code}}' {record} &> artifacts/uat-1-http.json",
                        f"curl -sS -o /dev/null -w '%{{http_code}}' {record} > artifacts/uat-1-http.json > /tmp/x",
                        f"curl -sS -o /dev/null -w '%{{http_code}}%{{size_download}}' {record} > "
                        'artifacts/uat-1-http.json'):
            with self.subTest(command):
                self.assertBlocked(self.capture(command), 'UAT-1: observed reports HTTP 201, but its evidence holds no '
                                                          'HTTP status line')
        self.write('uat-1-http.json', 'HTTP/1.1 500 Internal Server Error\r\n\r\n201')  # -i: the headers too.
        self.assertBlocked(self.capture(f"curl -sS -i -o artifacts/uat-1-http.json -w '%{{http_code}}' {record}"),
                           'UAT-1: observed reports HTTP 201, but its evidence holds only the responses 500')

    def test_a_status_capture_and_a_response_together(self):
        self.write('uat-1-status.txt', '201\n')
        self.write('uat-1-get.json', 'HTTP/1.1 200 OK\r\n\r\n{"id": 1}')
        result = copy.deepcopy(PASSING)
        result['scenarios'][0].update(observed='POST returned 201; GET returned 200',
                                      evidence=['artifacts/uat-1-status.txt', 'artifacts/uat-1-get.json'])
        calls = (('bash', f"curl -sS -o /dev/null -w '%{{http_code}}\\n' -X POST {ORIGIN}/records > "
                          f'artifacts/uat-1-status.txt; curl -sS -i {ORIGIN}/records/1 | tee artifacts/uat-1-get.json',
                  'completed'), *CAPTURE[1:])
        self.assertEqual(self.evaluate(trace(result, calls=calls)).verdict, 'PASS')
        result['scenarios'][0]['observed'] = 'POST returned 201; GET returned 404'
        self.assertBlocked(self.evaluate(trace(result, calls=calls)), 'observed reports HTTP 404, but its evidence holds '
                                                                    'only the responses 200, 201')
        later = (*calls, ('bash', f'curl -sS -X POST {ORIGIN}/records > artifacts/uat-1-status.txt', 'completed'))
        result['scenarios'][0]['observed'] = 'POST returned 201; GET returned 200'
        self.assertBlocked(self.evaluate(trace(result, calls=later)), 'observed reports HTTP 201')

    def test_browser_and_log_evidence_and_localhost_alias(self):
        self.write('uat-1.png', '\x89PNG fake image bytes')
        browser = copy.deepcopy(PASSING)
        browser['scenarios'][0].update(observed='the record page lists the new record', evidence=['artifacts/uat-1.png'])
        calls = (('bash', 'chromium --headless --no-sandbox --window-size=1280,800 '
                          '--screenshot=/work/artifacts/uat-1.png http://localhost:3000/records/1', 'completed'),
                 *CAPTURE[1:])
        self.assertEqual(self.evaluate(trace(browser, calls=calls)).verdict, 'PASS')
        inspection = ('bash', 'for f in /work/artifacts/uat-1.png; do identify "$f"; done', 'completed')
        self.assertEqual(self.evaluate(trace(browser, calls=(calls[0], inspection, *CAPTURE[1:]))).verdict, 'PASS')
        for elsewhere in ('chromium --headless --screenshot=artifacts/uat-1.png https://example.com/',
                          'chromium --headless --screenshot=artifacts/uat-1.png "data:text/html,<h1>Saved</h1>"'):
            with self.subTest(elsewhere):
                calls = (('bash', elsewhere, 'completed'), *CAPTURE[1:])
                self.assertBlocked(self.evaluate(trace(browser, calls=calls)), 'UAT-1: artifacts/uat-1.png is written')
        logs = (*CAPTURE[:2], ('bash', 'tail -n 50 /app-logs/app.log | tee artifacts/server.log', 'completed'))
        self.assertEqual(self.evaluate(trace(PASSING, calls=logs)).verdict, 'PASS')

    def test_artifact_can_be_deleted_before_recapture(self):
        command = f'rm -f /work/artifacts/uat-1-http.json; {CAPTURE[0][1]}'
        calls = (('bash', command, 'completed'), *CAPTURE[1:])
        self.assertEqual(self.evaluate(trace(PASSING, calls=calls)).verdict, 'PASS')
        fabricated = f'rm -f artifacts/uat-1-http.json; echo "HTTP/1.1 201 Created" > artifacts/uat-1-http.json'
        calls = (('bash', fabricated, 'completed'), *CAPTURE[1:])
        self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)), 'uat-1-http.json is written')

    def test_browser_stderr_redirection_is_not_a_url(self):
        self.write('uat-1.png', '\x89PNG fake image bytes')
        browser = copy.deepcopy(PASSING)
        browser['scenarios'][0].update(observed='the record page lists the new record', evidence=['artifacts/uat-1.png'])
        for redirection in ('2>/dev/null', '2> /dev/null'):
            with self.subTest(redirection=redirection):
                command = (f'rm -f /work/artifacts/uat-1.png; timeout 60 chromium --headless --no-sandbox '
                           f'--screenshot=/work/artifacts/uat-1.png {ORIGIN}/records {redirection}')
                calls = (('bash', command, 'completed'), *CAPTURE[1:])
                self.assertEqual(self.evaluate(trace(browser, calls=calls)).verdict, 'PASS')
        for refused in (f'chromium --headless --screenshot=/work/artifacts/uat-1.png {ORIGIN}/records 2 >/dev/null',
                        f'chromium --headless --dump-dom {ORIGIN}/records 2>/work/artifacts/uat-1.png',
                        f'curl -sS {ORIGIN}/records 2>>artifacts/uat-1.png'):
            with self.subTest(refused=refused):
                calls = (('bash', refused, 'completed'), *CAPTURE[1:])
                self.assertBlocked(self.evaluate(trace(browser, calls=calls)), 'uat-1.png is written')

    def test_image_tools_read_evidence_and_write_only_their_output(self):
        self.write('uat-1.png', '\x89PNG fake image bytes')
        browser = copy.deepcopy(PASSING)
        browser['scenarios'][0].update(observed='the record page lists the new record', evidence=['artifacts/uat-1.png'])
        capture = ('bash', f'chromium --headless --screenshot=/work/artifacts/uat-1.png {ORIGIN}/records', 'completed')
        for inspection in ('identify /work/artifacts/uat-1.png',
                           'compare -metric AE /work/artifacts/uat-1.png /work/other.png /work/diff.png 2>&1 || true',
                           'convert /work/artifacts/uat-1.png -crop 1280x120+0+0 +repage /work/header.png',
                           'compare -metric AE <(convert /work/artifacts/uat-1.png -crop 10x10+0+0 png:-) '
                           '<(convert /work/other.png png:-) null: 2>&1 || true'):
            with self.subTest(inspection=inspection):
                calls = (capture, ('bash', inspection, 'completed'), *CAPTURE[1:])
                self.assertEqual(self.evaluate(trace(browser, calls=calls)).verdict, 'PASS')
        for overwrite in ('convert /work/fake.png /work/artifacts/uat-1.png',
                          'convert /work/fake.png png:artifacts/uat-1.png',
                          'convert /work/fake.png -write artifacts/uat-1.png /work/copy.png',
                          'compare /work/a.png /work/b.png /work/artifacts/uat-1.png',
                          'cat <(convert /work/fake.png artifacts/uat-1.png)'):
            with self.subTest(overwrite=overwrite):
                calls = (capture, ('bash', overwrite, 'completed'), *CAPTURE[1:])
                self.assertBlocked(self.evaluate(trace(browser, calls=calls)), 'uat-1.png is written')

    def test_status_capture_with_silenced_stderr(self):
        self.write('uat-1-http.json', '201\n')
        command = f"curl -sS -o /dev/null -w '%{{http_code}}' {ORIGIN}/records > artifacts/uat-1-http.json 2>/dev/null"
        self.assertEqual(self.capture(command, observed='POST returned 201').verdict, 'PASS')

    def test_numbers_that_are_not_status_codes(self):
        quantities = copy.deepcopy(PASSING)
        quantities['scenarios'][0]['observed'] = ('HTTP 201 in 350ms on port 3000; the list shows 500 records, 404 '
                                                  'pages and 200 users')
        self.assertEqual(self.evaluate(trace(quantities)).verdict, 'PASS')
        lists = copy.deepcopy(PASSING)
        lists['scenarios'][0]['observed'] = 'HTTP 201, then 200 for the GET'
        self.assertBlocked(self.evaluate(trace(lists)), 'observed reports HTTP 200')
        phrase = copy.deepcopy(PASSING)
        phrase['scenarios'][0]['observed'] = 'the API answered 404 Not Found'
        self.assertBlocked(self.evaluate(trace(phrase)), 'observed reports HTTP 404')

    def test_url_fragments_and_line_continuations_parse_as_the_shell_does(self):
        calls = (('bash', f'curl -sS -i \\\n  {ORIGIN}/records#top | tee artifacts/uat-1-http.json', 'completed'),
                 *CAPTURE[1:])
        self.assertEqual(self.evaluate(trace(PASSING, calls=calls)).verdict, 'PASS')

    def capture(self, command, observed=None):
        """Evaluate PASSING with UAT-1's evidence captured by `command` (and `observed` reported for it)."""
        result = copy.deepcopy(PASSING)
        if observed is not None:
            result['scenarios'][0]['observed'] = observed
        return self.evaluate(trace(result, calls=(('bash', command, 'completed'), *CAPTURE[1:])))

    def test_request_urls_are_told_from_option_values(self):
        target = '> artifacts/uat-1-http.json'
        for command in (f"curl -sS -i '{ORIGIN}/search?q=hello&page=2' {target}",
                        "curl -sS -i -X POST -H 'content-type: application/json' "
                        f"""-d '{{"redirect_uri":"https://client.example/callback"}}' {ORIGIN}/authorize {target}""",
                        f"curl -sS -i -H 'Referer: https://client.example/' -e https://client.example/ {ORIGIN}/ {target}",
                        f'curl -sS -i --url={ORIGIN}/records {target}',
                        f'curl -sS -i --url "{ORIGIN}/records?next=https://client.example/" {target}',
                        f"curl -sS -i -G --data-urlencode 'next=https://client.example/' {ORIGIN}/records {target}",
                        f'curl -sSi -o artifacts/uat-1-http.json {ORIGIN}/records',
                        f'curl -sSiofoo.txt {ORIGIN}/records {target}',
                        f'curl -sS -i 127.0.0.1:3000/records {target}'):
            with self.subTest(command):
                self.assertEqual(self.capture(command).verdict, 'PASS', self.capture(command).reasons)

    def test_requests_elsewhere_are_refused(self):
        target = '> artifacts/uat-1-http.json'
        for command in (f'curl -sS -i --url=https://example.com/ {target}',
                        f'curl -sS -i --url https://example.com/ {ORIGIN}/ {target}',
                        f'curl -sS -i {ORIGIN}/ example.com/fake {target}',
                        f'curl -sS -i {ORIGIN}/ file:///work/fake.txt {target}',
                        f"curl -sS -i {ORIGIN}/ 'data:text/plain,HTTP/1.1 201' {target}",
                        f'curl -sS -i -x http://example.com:8080 {ORIGIN}/ {target}',
                        f'curl -sS -i --proxy=http://example.com:8080 {ORIGIN}/ {target}',
                        f'curl -sS -i --resolve 127.0.0.1:3000:203.0.113.1 {ORIGIN}/ {target}',
                        f'curl -sS -i --connect-to 127.0.0.1:3000:example.com:80 {ORIGIN}/ {target}',
                        f'curl -sSiK /work/options {ORIGIN}/ {target}',
                        f"curl -sS -w 'HTTP/1.1 201 Created' -o /dev/null {ORIGIN}/ {target}",
                        f"curl -sS --write-out='HTTP/1.1 201' -o /dev/null {ORIGIN}/ {target}",
                        f"curl -sS -i 'http://{{127.0.0.1:3000,example.com}}/' {target}",
                        f'curl -sS -i {target}'):
            with self.subTest(command):
                self.assertBlocked(self.capture(command), 'UAT-1: artifacts/uat-1-http.json is written by a command')

    def test_reported_statuses_are_checked_against_every_cited_response(self):
        both = 'HTTP/1.1 201 Created\r\n\r\n{"id": 1}\nHTTP/1.1 200 OK\r\n\r\n{"id": 1}'
        for observed in ('HTTP 201, then 200', 'POST returned 201; GET returned 200', '201, 200',
                         'created (201 Created), then the GET answered 200 OK'):
            with self.subTest(observed):
                self.write('uat-1-http.json', both)  # Two requests, two responses in one capture.
                self.assertEqual(self.capture(CAPTURE[0][1], observed).verdict, 'PASS')
                self.write('uat-1-http.json', 'HTTP/1.1 201 Created\r\n\r\n{"id": 1}')
                self.assertBlocked(self.capture(CAPTURE[0][1], observed),
                                   'UAT-1: observed reports HTTP 200, but its evidence holds only the responses 201')
                self.write('uat-1-http.json', 'HTTP/1.1 500 Internal Server Error\r\n\r\n{}')
                self.assertBlocked(self.capture(CAPTURE[0][1], observed), 'observed reports HTTP 200, 201, but its '
                                                                          'evidence holds only the responses 500')

    def test_statuses_across_several_evidence_files(self):
        self.write('uat-1-get.json', 'HTTP/1.1 200 OK\r\n\r\n{"id": 1}')
        result = copy.deepcopy(PASSING)
        result['scenarios'][0].update(observed='POST returned 201; GET returned 200',
                                      evidence=['artifacts/uat-1-http.json', 'artifacts/uat-1-get.json'])
        calls = (*CAPTURE, ('bash', f'curl -sS -i {ORIGIN}/records/1 -o artifacts/uat-1-get.json', 'completed'))
        self.assertEqual(self.evaluate(trace(result, calls=calls)).verdict, 'PASS')
        result['scenarios'][0]['observed'] = 'POST returned 201; GET returned 404'
        self.assertBlocked(self.evaluate(trace(result, calls=calls)), 'observed reports HTTP 404')

    def test_quantities_ids_ports_durations_and_times_are_not_statuses(self):
        for observed in ('HTTP 201 for 500 records on port 3000 in 200ms', 'HTTP 201; took 200 ms; record id 404',
                         'HTTP 201 at 12:30:45 on 2026-11-09, release v1.500, ticket #404',
                         'HTTP 201 instead of the expected 500', 'HTTP 201: the list shows 300 items, total 500.',
                         'HTTP 201 and the response returned 500 records'):
            with self.subTest(observed):
                self.assertEqual(self.capture(CAPTURE[0][1], observed).verdict, 'PASS')

    def test_measurements_are_not_statuses(self):
        for observed, statuses in (('HTTP 201; latency 200', {201}), ('HTTP 201, latency: 200 ms', {201}),
                                   ('HTTP 201 (duration 300, p95 400, avg 250)', {201}),
                                   ('HTTP 201; response time 200; size 500; 300 bytes', {201}),
                                   ('HTTP 201 and 200 ms later; elapsed 150', {201}),
                                   ('HTTP 201 and 500 records', {201}), ('HTTP 201, then 200', {200, 201}),
                                   ('POST returned 201; GET returned 200', {200, 201}), ('201, 200', {200, 201}),
                                   ('HTTP 201; latency 200; GET returned 404', {201, 404})):
            with self.subTest(observed):
                self.assertEqual(results.reported_statuses(observed, http=True), statuses)
        self.assertEqual(results.reported_statuses('the page lists 120 records, then 300.', http=False), set())
        self.assertEqual(self.capture(CAPTURE[0][1], 'HTTP 201; latency 200').verdict, 'PASS')

    def test_browser_and_log_scenarios_need_no_status(self):
        self.write('uat-1.png', '\x89PNG fake image bytes')
        browser = copy.deepcopy(PASSING)
        browser['scenarios'][0].update(observed='the page lists 120 records, then 300.', evidence=['artifacts/uat-1.png'])
        calls = (('bash', f'chromium --headless --screenshot=artifacts/uat-1.png {ORIGIN}/records', 'completed'),
                 *CAPTURE[1:])
        self.assertEqual(self.evaluate(trace(browser, calls=calls)).verdict, 'PASS')
        logs = copy.deepcopy(PASSING)
        logs['scenarios'][0].update(observed='the log records the new entry 201, 200', evidence=['artifacts/server.log'])
        self.assertEqual(self.evaluate(trace(logs)).verdict, 'PASS')
        browser['scenarios'][0]['observed'] = 'the page answered 404 Not Found'
        self.assertBlocked(self.evaluate(trace(browser, calls=calls)), 'holds no HTTP status line')

    def test_overall_verdict_must_match_scenarios(self):
        lying = copy.deepcopy(RESULT)
        lying['verdict'] = 'PASS'
        self.assertBlocked(self.evaluate(trace(lying)), 'contradicts the scenario verdicts \\(FAIL\\)')

    def test_failure_needs_reproduction(self):
        bare = copy.deepcopy(RESULT)
        bare['scenarios'][1].pop('reproduction')
        self.assertBlocked(self.evaluate(trace(bare)), 'UAT-2 fails without reproduction')

    def test_metadata_must_match_the_controller(self):
        for key, value in (('head_sha', 'f' * 40), ('uat_spec_sha256', 'b' * 64), ('pr_number', 43),
                           ('repository', 'evil/repo'), ('environment', 'preview'), ('environment_revision', 'e' * 40)):
            with self.subTest(key):
                self.assertBlocked(self.evaluate(trace({**PASSING, key: value})), f'{key} is')

    def test_oversized_trace(self):
        self.assertBlocked(self.evaluate(b' ' * (results.MAX_TRACE_BYTES + 1)), 'larger than')


if __name__ == '__main__':
    unittest.main()
