"""superagent.yaml loader: validate, resolve tiers, apply env overrides."""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = os.path.join(ROOT, 'superagent.yaml')
SCHEMA = os.path.join(ROOT, 'superagent.schema.json')


def load_module():
    path = os.path.join(ROOT, 'scripts', 'superagent_config.py')
    loader = importlib.machinery.SourceFileLoader('superagent_config', path)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader('superagent_config', loader))
    sys.modules['superagent_config'] = module
    loader.exec_module(module)
    return module


# Today's effective settings: what the sample must resolve to with an empty env.
# Matches the extending-superagent roles sketch + UAT profile defaults + F4 routing.
TODAY = {
    'executor': {'provider': 'deepseek-official', 'model': 'deepseek-flash'},
    'tier_low': {
        'provider': 'deepseek-official',
        'model': 'deepseek-flash',
        'reasoningEffort': 'low',
    },
    'tier_mid': {'provider': 'deepseek-official', 'model': 'deepseek-flash'},
    'reviewer': {
        'model': 'opus',
        'small_model': 'sonnet',
        'small_diff_lines': 200,
    },
    'uat': {'provider': 'deepseek-official', 'model': 'deepseek-flash'},
    'subagents': {
        'explorer': {'provider': 'deepseek-official', 'model': 'deepseek-flash',
                     'reasoningEffort': 'low'},
        'frontend': {'provider': 'deepseek-official', 'model': 'deepseek-flash'},
        'devops': {'provider': 'deepseek-official', 'model': 'deepseek-flash'},
    },
}


def mutate_yaml(change):
    """Write a temp YAML derived from the sample after `change(doc)`."""
    with open(SAMPLE, encoding='utf-8') as file:
        doc = yaml.safe_load(file)
    change(doc)
    handle = tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False, encoding='utf-8')
    with handle:
        yaml.safe_dump(doc, handle)
    return handle.name


class SuperagentConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_module()

    def test_sample_file_and_schema_exist(self):
        self.assertTrue(os.path.isfile(SAMPLE), SAMPLE)
        self.assertTrue(os.path.isfile(SCHEMA), SCHEMA)

    def test_valid_sample_loads(self):
        resolved = self.cfg.load(SAMPLE, environ={})
        self.assertEqual(resolved.version, 1)

    def test_sample_resolves_to_todays_effective_settings(self):
        resolved = self.cfg.load(SAMPLE, environ={})
        self.assertEqual(resolved.executor, TODAY['executor'])
        self.assertEqual(resolved.tiers['low']['dsh'], TODAY['tier_low'])
        self.assertEqual(resolved.tiers['mid']['dsh'], TODAY['tier_mid'])
        self.assertEqual(resolved.tiers['high']['claude'], 'opus')
        self.assertEqual(resolved.reviewer['model'], TODAY['reviewer']['model'])
        self.assertEqual(resolved.reviewer['small_model'], TODAY['reviewer']['small_model'])
        self.assertEqual(resolved.reviewer['small_diff_lines'], TODAY['reviewer']['small_diff_lines'])
        self.assertEqual(resolved.uat, TODAY['uat'])
        for name, model in TODAY['subagents'].items():
            self.assertEqual(resolved.roles['implementer']['subagents'][name]['model'], model)

    def test_invalid_missing_version(self):
        path = mutate_yaml(lambda d: d.pop('version'))
        self.addCleanup(os.unlink, path)
        with self.assertRaises(self.cfg.ValidationError):
            self.cfg.load(path, environ={})

    def test_invalid_unknown_tier_on_role(self):
        path = mutate_yaml(lambda d: d['roles']['implementer'].__setitem__('tier', 'ultra'))
        self.addCleanup(os.unlink, path)
        with self.assertRaises(self.cfg.ValidationError):
            self.cfg.load(path, environ={})

    def test_invalid_extra_top_level_key(self):
        path = mutate_yaml(lambda d: d.update(secrets='nope'))
        self.addCleanup(os.unlink, path)
        with self.assertRaises(self.cfg.ValidationError):
            self.cfg.load(path, environ={})

    def test_env_overrides_executor(self):
        env = {
            'SUPERAGENT_EXECUTOR_PROVIDER': 'other-provider',
            'SUPERAGENT_EXECUTOR_MODEL': 'other-model',
            'SUPERAGENT_EXECUTOR_REASONING_EFFORT': 'high',
        }
        resolved = self.cfg.load(SAMPLE, environ=env)
        self.assertEqual(resolved.executor, {
            'provider': 'other-provider',
            'model': 'other-model',
            'reasoningEffort': 'high',
        })

    def test_env_overrides_reviewer_and_threshold(self):
        env = {
            'SUPERAGENT_REVIEWER_MODEL': 'haiku',
            'SUPERAGENT_REVIEWER_EFFORT': 'low',
            'SUPERAGENT_REVIEWER_SMALL_MODEL': 'sonnet',
            'SUPERAGENT_REVIEWER_SMALL_DIFF_LINES': '50',
        }
        resolved = self.cfg.load(SAMPLE, environ=env)
        self.assertEqual(resolved.reviewer['model'], 'haiku')
        self.assertEqual(resolved.reviewer['effort'], 'low')
        self.assertEqual(resolved.reviewer['small_model'], 'sonnet')
        self.assertEqual(resolved.reviewer['small_diff_lines'], 50)

    def test_env_overrides_tier_low(self):
        env = {
            'SUPERAGENT_TIER_LOW_PROVIDER': 'alt',
            'SUPERAGENT_TIER_LOW_MODEL': 'cheap',
            'SUPERAGENT_TIER_LOW_REASONING_EFFORT': 'medium',
        }
        resolved = self.cfg.load(SAMPLE, environ=env)
        self.assertEqual(resolved.tiers['low']['dsh'], {
            'provider': 'alt',
            'model': 'cheap',
            'reasoningEffort': 'medium',
        })
        # Subagent that names the low tier picks up the override.
        self.assertEqual(
            resolved.roles['implementer']['subagents']['explorer']['model'],
            {'provider': 'alt', 'model': 'cheap', 'reasoningEffort': 'medium'},
        )

    def test_env_overrides_uat(self):
        env = {
            'SUPERAGENT_UAT_PROVIDER': 'deepseek-official',
            'SUPERAGENT_UAT_MODEL': 'deepseek-reasoner',
        }
        resolved = self.cfg.load(SAMPLE, environ=env)
        self.assertEqual(resolved.uat, {
            'provider': 'deepseek-official',
            'model': 'deepseek-reasoner',
        })

    def test_half_set_executor_env_fails(self):
        with self.assertRaises(self.cfg.ValidationError):
            self.cfg.load(SAMPLE, environ={'SUPERAGENT_EXECUTOR_PROVIDER': 'only-provider'})

    def test_load_refuses_relative_pr_path(self):
        # Callers must pass an absolute deployment/image path, never a PR-relative one.
        with self.assertRaises(self.cfg.ValidationError):
            self.cfg.load('pr-checkout/superagent.yaml', environ={})

    def test_uat_environ_from_sample(self):
        """uat-gate exports today's UAT model from the file when env is empty."""
        resolved = self.cfg.load(SAMPLE, environ={})
        self.assertEqual(self.cfg.uat_environ(resolved), {
            'SUPERAGENT_UAT_PROVIDER': 'deepseek-official',
            'SUPERAGENT_UAT_MODEL': 'deepseek-flash',
        })

    def test_uat_environ_follows_env_override(self):
        resolved = self.cfg.load(SAMPLE, environ={
            'SUPERAGENT_UAT_PROVIDER': 'deepseek-official',
            'SUPERAGENT_UAT_MODEL': 'deepseek-reasoner',
        })
        self.assertEqual(self.cfg.uat_environ(resolved), {
            'SUPERAGENT_UAT_PROVIDER': 'deepseek-official',
            'SUPERAGENT_UAT_MODEL': 'deepseek-reasoner',
        })
