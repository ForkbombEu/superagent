"""Docker: dump-config shows role presets and the worker allowlist."""
import os
import subprocess
import unittest

from uat.tests import docker_support as ds

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGE = 'superagent:profile-presets-test'


class ProfileDumpConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        try:
            ds.docker(
                'build', '-q', '-t', IMAGE,
                '-f', os.path.join(ROOT, 'Dockerfile'),
                ROOT,
                timeout=1800,
            )
        except subprocess.CalledProcessError as exc:
            raise AssertionError(
                f'docker build failed (exit {exc.returncode}):\n'
                f'{exc.stderr or exc.stdout or exc}'
            ) from exc

    def test_dump_config_contains_presets_and_allowlist(self):
        result = ds.docker(
            'run', '--rm', '--entrypoint', 'dsh',
            IMAGE, '--profile', 'superagent', '--dump-config',
            timeout=120,
        )
        dump = result.stdout
        self.assertIn('dsh-superagent-worker-preset', dump)
        self.assertIn('allowedAgentPresets', dump)
        # Nested allowlist under the worker preset (not only Host tool-subagent).
        worker_idx = dump.index('id: dsh-superagent-worker-preset')
        nested = dump[worker_idx:worker_idx + 4000]
        for preset in ('standard', 'explorer', 'frontend', 'devops'):
            self.assertRegex(
                nested,
                rf'(?m)^\s+- {preset}\s*$',
                f'worker allowlist missing {preset}',
            )
        for preset in ('explorer', 'frontend', 'devops'):
            self.assertRegex(
                dump,
                rf'(?m)^\s+id: {preset}\s*$',
                f'preset definition missing id: {preset}',
            )
            # dump-config keeps !!js unevaluated; the persona source path must appear.
            self.assertIn(
                f"/usr/local/share/superagent/roles/{preset}.md",
                dump,
                f'persona path missing for {preset}',
            )

        # Host model-selection settings: !!js unevaluated, env names must appear.
        self.assertIn('subagent-model-selection-settings', dump)
        self.assertIn('SUPERAGENT_TIER_LOW_PROVIDER', dump)
        self.assertIn('SUPERAGENT_TIER_MID_MODEL', dump)
