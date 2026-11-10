import re
import unittest

from uat import spec

ISSUE = '''@forkboteu Implement feature X.

## Acceptance criteria
- [ ] User can do X

## User Acceptance Tests
Environment: local

### UAT-1: Successful operation
Given a test user exists
When they perform X
Then the API returns 201
And the resulting record can be retrieved

### UAT-2: Failure behavior
Given invalid input
When they perform X
Then the API returns 400
And no record is created

Depends on #12
'''


class ParseTest(unittest.TestCase):
    def test_absent_section_is_not_required(self):
        for body in (None, '', '@forkboteu Do X.\n\n## Acceptance criteria\n- [ ] X\n\nDepends on #3'):
            self.assertEqual(spec.parse(body).status, spec.NOT_REQUIRED)

    def test_valid_section(self):
        parsed = spec.parse(ISSUE)
        self.assertEqual(parsed.status, spec.READY)
        self.assertEqual(parsed.environment, 'local')
        self.assertEqual([s.id for s in parsed.scenarios], ['UAT-1', 'UAT-2'])
        self.assertEqual(parsed.scenarios[0].title, 'Successful operation')
        self.assertTrue(parsed.scenarios[1].steps.endswith('And no record is created'))

    def test_depends_on_stays_out_of_the_section(self):
        parsed = spec.parse(ISSUE)
        self.assertNotIn('Depends on', parsed.scenarios[-1].steps)
        self.assertEqual(spec.spec_hash(parsed, 'local'),
                         spec.spec_hash(spec.parse(ISSUE.replace('Depends on #12', 'Depends on #12, #14')), 'local'))
        # The worker keeps reading the line from the unchanged issue body.
        self.assertRegex(ISSUE, re.compile(r'^Depends on #12$', re.MULTILINE))

    def test_section_ends_at_next_level_two_heading(self):
        body = ISSUE.replace('Depends on #12', '## Notes\nNot a test step')
        self.assertNotIn('Not a test step', spec.parse(body).scenarios[-1].steps)

    def test_environment_is_optional(self):
        parsed = spec.parse(ISSUE.replace('Environment: local\n', ''))
        self.assertEqual((parsed.status, parsed.environment), (spec.READY, None))

    def test_preamble_is_kept_as_context(self):
        parsed = spec.parse(ISSUE.replace('Environment: local\n', 'Environment: local\nUse the admin fixture.\n'))
        self.assertEqual(parsed.preamble, 'Use the admin fixture.')

    def test_headings_and_environment_inside_fences_are_steps(self):
        body = ISSUE.replace('And the resulting record can be retrieved',
                             '```\n### UAT-9: not a scenario\n## Not the end\nEnvironment: preview\n```')
        parsed = spec.parse(body)
        self.assertEqual([s.id for s in parsed.scenarios], ['UAT-1', 'UAT-2'])
        self.assertIn('### UAT-9: not a scenario', parsed.scenarios[0].steps)

    def test_heading_inside_fence_before_section_is_ignored(self):
        body = '```md\n## User Acceptance Tests\n```\nNo real section.'
        self.assertEqual(spec.parse(body).status, spec.NOT_REQUIRED)

    def assertBlocked(self, body, reason):
        parsed = spec.parse(body)
        self.assertEqual(parsed.status, spec.BLOCKED, parsed)
        self.assertRegex(parsed.reason, reason)

    def test_empty_section_is_blocked(self):
        self.assertBlocked('Do X.\n\n## User Acceptance Tests\n\n## Notes\n', 'no "### UAT')
        self.assertBlocked('Do X.\n\n## User Acceptance Tests\n', 'no "### UAT')
        self.assertBlocked('## User Acceptance Tests\nEnvironment: local\nJust prose.', 'no "### UAT')

    def test_duplicate_ids_are_blocked(self):
        self.assertBlocked(ISSUE.replace('UAT-2', 'UAT-1'), 'duplicate scenario ids: UAT-1')

    def test_malformed_sections_are_blocked(self):
        self.assertBlocked(ISSUE.replace('### UAT-2: Failure behavior', '### UAT-2 Failure behavior'), 'not a scenario')
        self.assertBlocked(ISSUE.replace('### UAT-2: Failure behavior', '### UAT-0: Zero'), 'not a scenario')
        self.assertBlocked(ISSUE.replace('### UAT-2: Failure behavior', '### Extra notes'), 'not a scenario')
        self.assertBlocked(ISSUE.replace('### UAT-2: Failure behavior', '### UAT-2:'), 'not a scenario')
        self.assertBlocked(ISSUE.replace('Environment: local', 'Environment: Local Box'), 'not a valid environment')
        self.assertBlocked(ISSUE.replace('Environment: local', 'Environment: local\nEnvironment: preview'),
                           'more than one Environment')
        self.assertBlocked(ISSUE + '\n## User Acceptance Tests\n### UAT-3: More\nStep', '2 .* sections')

    def test_scenario_without_steps_is_blocked(self):
        body = ISSUE.replace('Given invalid input\nWhen they perform X\nThen the API returns 400\nAnd no record is created\n', '')
        self.assertBlocked(body, 'without steps: UAT-2')

    def test_near_miss_heading_is_blocked_not_skipped(self):
        self.assertBlocked(ISSUE.replace('## User Acceptance Tests', '## User acceptance tests'), 'exactly')
        self.assertBlocked(ISSUE.replace('## User Acceptance Tests', '## User Acceptance Test'), 'exactly')

    def test_oversized_section_is_blocked(self):
        self.assertBlocked(ISSUE + 'x' * spec.MAX_SECTION_CHARS, 'longer than')


class HashTest(unittest.TestCase):
    def setUp(self):
        self.parsed = spec.parse(ISSUE)
        self.digest = spec.spec_hash(self.parsed, 'local')

    def test_hash_is_full_sha256_of_canonical_json(self):
        import hashlib
        import json
        canonical = json.dumps(spec.canonical(self.parsed, 'local'), sort_keys=True, separators=(',', ':'))
        self.assertEqual(self.digest, hashlib.sha256(canonical.encode()).hexdigest())
        self.assertRegex(self.digest, '^[0-9a-f]{64}$')

    def test_whitespace_only_edits_keep_the_hash(self):
        edited = ISSUE.replace('\n', '\r\n').replace('returns 201', 'returns 201   ').replace(
            '### UAT-2: Failure behavior\n', '### UAT-2: Failure behavior\n\n\n')
        self.assertEqual(spec.spec_hash(spec.parse(edited), 'local'), self.digest)

    def test_edits_outside_the_section_keep_the_hash(self):
        edited = ISSUE.replace('Implement feature X.', 'Implement feature X quickly.')
        self.assertEqual(spec.spec_hash(spec.parse(edited), 'local'), self.digest)

    def test_meaningful_edits_change_the_hash(self):
        for edited in (ISSUE.replace('returns 400', 'returns 422'),
                       ISSUE.replace('Successful operation', 'Happy path'),
                       ISSUE.replace('Environment: local\n', 'Environment: local\nAs an admin.\n'),
                       ISSUE.replace('### UAT-2', '### UAT-3')):
            self.assertNotEqual(spec.spec_hash(spec.parse(edited), 'local'), self.digest, edited)

    def test_environment_changes_the_hash(self):
        self.assertNotEqual(spec.spec_hash(self.parsed, 'preview'), self.digest)

    def test_only_ready_specs_hash(self):
        with self.assertRaises(ValueError):
            spec.spec_hash(spec.parse(''), 'local')


if __name__ == '__main__':
    unittest.main()
