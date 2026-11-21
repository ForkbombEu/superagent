"""superagent init: scaffold target-owned templates into a directory.

Seam under test: scripts/superagent CLI `init <dir>` / `init <dir> --force`
(exit status, stderr, filesystem writes under the target). Template content
comes from the repo templates/ tree (independent source of truth).
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

# Target-owned paths init must write (ADR 0002).
INIT_FILES = (
    'AGENTS.md',
    os.path.join('.superagent', 'uat.yaml'),
)
INIT_TREES = (
    os.path.join('.agents', 'skills'),
)


def _file_list(directory):
    paths = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for name in filenames:
            paths.append(os.path.relpath(os.path.join(dirpath, name), directory))
    return sorted(paths)


class SuperagentInitTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.target = os.path.join(self.dir, 'target')
        os.makedirs(self.target)

    def run_init(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, 'init', *args],
            capture_output=True, text=True, cwd=self.dir, timeout=30,
        )

    def assert_matches_templates(self):
        for rel in INIT_FILES:
            dest = os.path.join(self.target, rel)
            src = os.path.join(TEMPLATES, rel)
            self.assertTrue(os.path.isfile(dest), dest)
            self.assertTrue(
                filecmp.cmp(src, dest, shallow=False),
                f'{rel} differs from templates/',
            )
        for rel in INIT_TREES:
            dest = os.path.join(self.target, rel)
            src = os.path.join(TEMPLATES, rel)
            self.assertTrue(os.path.isdir(dest), dest)
            self.assertEqual(_file_list(src), _file_list(dest), rel)
            for name in _file_list(src):
                self.assertTrue(
                    filecmp.cmp(
                        os.path.join(src, name),
                        os.path.join(dest, name),
                        shallow=False,
                    ),
                    f'{rel}/{name} differs from templates/',
                )

    def test_init_writes_target_owned_files_from_templates(self):
        result = self.run_init(self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_matches_templates()

    def test_init_refuses_overwrite_by_default(self):
        agents = os.path.join(self.target, 'AGENTS.md')
        with open(agents, 'w', encoding='utf-8') as file:
            file.write('already here\n')
        result = self.run_init(self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('overwrite', result.stderr.lower())
        with open(agents, encoding='utf-8') as file:
            self.assertEqual(file.read(), 'already here\n')
        self.assertFalse(os.path.exists(os.path.join(self.target, '.superagent', 'uat.yaml')))
        self.assertFalse(os.path.exists(os.path.join(self.target, '.agents')))

    def test_init_force_overwrites_existing_files(self):
        agents = os.path.join(self.target, 'AGENTS.md')
        with open(agents, 'w', encoding='utf-8') as file:
            file.write('stale\n')
        os.makedirs(os.path.join(self.target, '.superagent'))
        with open(os.path.join(self.target, '.superagent', 'uat.yaml'), 'w', encoding='utf-8') as file:
            file.write('stale: true\n')
        result = self.run_init(self.target, '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_matches_templates()
