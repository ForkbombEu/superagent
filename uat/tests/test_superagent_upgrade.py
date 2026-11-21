"""superagent upgrade: refresh planning skills and write a work order.

Seams under test: scripts/superagent CLI `upgrade <dir>` (exit status,
filesystem writes under the target). Template content comes from the repo
templates/ tree (independent source of truth). Work-order expectations are
literal markers the CLI must emit, not recomputed from the implementation.
"""
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, 'scripts', 'superagent')
TEMPLATES = os.path.join(ROOT, 'templates')
SKILLS = os.path.join('.agents', 'skills')
UPGRADE_MD = os.path.join('.superagent', 'UPGRADE.md')


def _file_list(directory):
    paths = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for name in filenames:
            paths.append(os.path.relpath(os.path.join(dirpath, name), directory))
    return sorted(paths)


class SuperagentUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.target = os.path.join(self.dir, 'target')
        os.makedirs(self.target)

    def run_upgrade(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, 'upgrade', *args],
            capture_output=True, text=True, cwd=self.dir, timeout=30,
        )

    def seed_stale_skill(self):
        """Leave a stale write-issues skill so upgrade has something to refresh."""
        dest = os.path.join(self.target, SKILLS, 'write-issues')
        os.makedirs(dest)
        with open(os.path.join(dest, 'SKILL.md'), 'w', encoding='utf-8') as file:
            file.write('# stale write-issues\n')

    def assert_skills_match_templates(self):
        dest = os.path.join(self.target, SKILLS)
        src = os.path.join(TEMPLATES, SKILLS)
        self.assertTrue(os.path.isdir(dest), dest)
        self.assertEqual(_file_list(src), _file_list(dest), SKILLS)
        for name in _file_list(src):
            self.assertTrue(
                filecmp.cmp(
                    os.path.join(src, name),
                    os.path.join(dest, name),
                    shallow=False,
                ),
                f'{SKILLS}/{name} differs from templates/',
            )

    def test_upgrade_refreshes_skills_from_templates(self):
        self.seed_stale_skill()
        result = self.run_upgrade(self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_skills_match_templates()

    def test_upgrade_writes_work_order_describing_changes(self):
        self.seed_stale_skill()
        # Extra target-only file: upgrade must note removal (or leave and document).
        orphan = os.path.join(self.target, SKILLS, 'write-issues', 'orphan.md')
        with open(orphan, 'w', encoding='utf-8') as file:
            file.write('local only\n')
        result = self.run_upgrade(self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        work_order = os.path.join(self.target, UPGRADE_MD)
        self.assertTrue(os.path.isfile(work_order), work_order)
        with open(work_order, encoding='utf-8') as file:
            body = file.read()
        # Literal work-order markers (independent of how the CLI formats lists).
        self.assertIn('# Superagent upgrade', body)
        self.assertIn('## Changed', body)
        self.assertIn('## Steps', body)
        self.assertIn('.agents/skills/write-issues/SKILL.md', body)
        self.assertRegex(body, r'(?i)updated|refreshed')
        self.assertIn('.agents/skills/architect/', body)
        # Completion-oriented steps an agent can check off.
        self.assertIn('Review', body)
        self.assertIn('Commit', body)
