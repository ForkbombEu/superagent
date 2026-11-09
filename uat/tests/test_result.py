import copy
import json
import os
import tempfile
import unittest

from uat import result as results
from uat.tests.test_schema import RESULT

META = {key: RESULT[key] for key in results.METADATA}
IDS = ['UAT-1', 'UAT-2']
PASSING = copy.deepcopy(RESULT)
PASSING['verdict'] = 'PASS'
PASSING['scenarios'][1].update(verdict='PASS', observed='HTTP 400; no record')
PASSING['scenarios'][1].pop('reproduction')


def trace(final, *, calls=(('bash', 'curl -o artifacts/uat-1-http.json ...', 'completed'),), end='completed',
          error_code=None, extra=()):
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
        for name in ('uat-1-http.json', 'uat-2-http.json', 'server.log'):
            with open(os.path.join(self.work, 'artifacts', name), 'w') as file:
                file.write('HTTP/1.1 201 Created\n')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.work)

    def evaluate(self, raw, exit_code=0, timed_out=False, ids=IDS):
        return results.evaluate(raw, exit_code, timed_out, META, ids, self.work)

    def assertBlocked(self, outcome, reason):
        self.assertEqual(outcome.verdict, results.BLOCKED, outcome)
        self.assertRegex(' | '.join(outcome.reasons), reason)

    def test_valid_pass(self):
        outcome = self.evaluate(trace(PASSING))
        self.assertEqual((outcome.verdict, outcome.reasons), ('PASS', []))

    def test_fenced_json_answer_is_accepted(self):
        self.assertEqual(self.evaluate(trace(f'```json\n{json.dumps(PASSING)}\n```')).verdict, 'PASS')

    def test_expected_fail(self):
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
        calls = (('bash', 'ls', 'completed'), ('write', '/work/artifacts/uat-1-http.json', 'completed'))
        self.assertBlocked(self.evaluate(trace(PASSING, calls=calls)), 'written with a file tool')

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
