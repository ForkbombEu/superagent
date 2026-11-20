"""Worker role presets in dsh/profile.patch.yml: allowlist and definitions stay aligned."""
import os
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILE = os.path.join(ROOT, 'dsh', 'profile.patch.yml')
ROLES = os.path.join(ROOT, 'roles')


class CordisLoader(yaml.SafeLoader):
    """Accept DSH ``!!js`` scalars as opaque strings for structural checks."""


CordisLoader.add_constructor(
    'tag:yaml.org,2002:js',
    lambda loader, node: loader.construct_scalar(node),
)


def load_profile():
    with open(PROFILE, encoding='utf-8') as file:
        return yaml.load(file, Loader=CordisLoader) or []


def worker_allowlist(entries):
    for entry in entries:
        if entry.get('id') != 'dsh-superagent-worker-preset':
            continue
        for plugin in entry.get('config', {}).get('plugins', []):
            if plugin.get('id') != 'delegation':
                continue
            for child in plugin.get('config', []):
                if child.get('id') == 'tool-subagent':
                    return list(child.get('config', {}).get('allowedAgentPresets') or [])
    raise AssertionError('worker nested tool-subagent allowlist not found')


def defined_preset_ids(entries):
    ids = set()
    for entry in entries:
        if 'insert' in entry:
            for row in entry['insert']:
                if row.get('name') == '@deepseek-ai/dsh-agent-preset':
                    ids.add(row['config']['id'])
        elif entry.get('name') == '@deepseek-ai/dsh-agent-preset':
            ids.add(entry['config']['id'])
    return ids


class ProfilePresetsTest(unittest.TestCase):
    def test_every_allowlisted_preset_is_defined(self):
        entries = load_profile()
        allowlist = worker_allowlist(entries)
        defined = defined_preset_ids(entries)
        # standard ships with the web composition; role presets must be inserted here.
        missing = [preset for preset in allowlist if preset != 'standard' and preset not in defined]
        self.assertEqual(missing, [], f'allowlisted presets missing definitions: {missing}')
        for role in ('explorer', 'frontend', 'devops'):
            self.assertIn(role, allowlist)
            self.assertIn(role, defined)
            path = os.path.join(ROLES, f'{role}.md')
            self.assertTrue(os.path.isfile(path), f'missing persona {path}')
            with open(path, encoding='utf-8') as persona:
                self.assertTrue(persona.read().strip(), f'empty persona {path}')

    def test_host_model_selection_reads_tier_env(self):
        """Profile enables subagent-model-selection-settings from SUPERAGENT_TIER_*."""
        entries = load_profile()
        row = next((e for e in entries if e.get('id') == 'subagent-model-selection-settings'), None)
        self.assertIsNotNone(row, 'missing subagent-model-selection-settings override')
        config = row.get('config') or {}
        self.assertIn('enabled', config)
        self.assertIn('allowedModels', config)
        enabled = config['enabled']
        models = config['allowedModels']
        self.assertIsInstance(enabled, str)
        self.assertIsInstance(models, str)
        self.assertIn('SUPERAGENT_TIER_LOW', enabled)
        self.assertIn('SUPERAGENT_TIER_MID', enabled)
        self.assertIn('SUPERAGENT_TIER_LOW', models)
        self.assertIn('SUPERAGENT_TIER_MID', models)


if __name__ == '__main__':
    unittest.main()
