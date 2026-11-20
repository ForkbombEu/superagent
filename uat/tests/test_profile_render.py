"""Golden: render_profile_patch(sample) equals the committed dsh/profile.patch.yml."""
import importlib.machinery
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = os.path.join(ROOT, 'superagent.yaml')
GOLDEN = os.path.join(ROOT, 'dsh', 'profile.patch.yml')


def load_module():
    path = os.path.join(ROOT, 'scripts', 'superagent_config.py')
    loader = importlib.machinery.SourceFileLoader('superagent_config', path)
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader('superagent_config', loader)
    )
    sys.modules['superagent_config'] = module
    loader.exec_module(module)
    return module


class ProfileRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_module()

    def test_sample_render_equals_committed_patch(self):
        """Seam: render_profile_patch — sample yaml produces the reviewable golden patch."""
        self.assertTrue(hasattr(self.cfg, 'render_profile_patch'), 'render_profile_patch missing')
        resolved = self.cfg.load(SAMPLE, environ={})
        rendered = self.cfg.render_profile_patch(resolved)
        with open(GOLDEN, encoding='utf-8') as file:
            golden = file.read()
        self.assertEqual(rendered, golden)


if __name__ == '__main__':
    unittest.main()
