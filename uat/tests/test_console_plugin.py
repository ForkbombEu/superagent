"""Doorway plugin: main + sidebar registrations, and the image lists it."""
import json
import os
import subprocess
import tempfile
import textwrap
import unittest

from uat.tests import docker_support as ds

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN = os.path.join(ROOT, 'dsh', 'console-plugin')
CLIENT = os.path.join(PLUGIN, 'client.js')
HOST = os.path.join(PLUGIN, 'index.js')


class ConsolePluginHost(unittest.TestCase):
    """Primary workspace registration for the Superagent git checkout."""

    def run_host(self, script, *args):
        result = subprocess.run(
            ['node', '--input-type=module', '-e', script, *args],
            capture_output=True, text=True, check=False, cwd=PLUGIN,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result.stdout.strip()

    def test_checkout_path_from_repository(self):
        out = self.run_host(
            "import { checkoutPath } from './index.js';"
            "console.log(checkoutPath(process.argv[1], process.argv[2]))",
            'owner/credimi-attestbook', '/home/agent',
        )
        self.assertEqual(out, '/home/agent/src/credimi-attestbook')

    def test_checkout_path_rejects_bad_repository(self):
        out = self.run_host(
            "import { checkoutPath } from './index.js';"
            "for (const value of [undefined, '', 'noslash', 'a/b/c', 'a/.', 'a/..'])"
            "  console.log(String(checkoutPath(value, '/home/agent')))",
        )
        self.assertEqual(out.splitlines(), ['null'] * 6)

    def test_ensure_primary_workspace_registers_and_promotes(self):
        out = self.run_host(textwrap.dedent('''\
            import { ensurePrimaryWorkspace } from './index.js';
            const calls = [];
            const workspaces = new Map();
            const registry = {
              async create(path) {
                calls.push(['create', path]);
                let workspace = [...workspaces.values()].find((item) => item.path === path);
                if (!workspace) {
                  workspace = { id: `id:${path}`, path };
                  workspaces.set(workspace.id, workspace);
                }
                return workspace;
              },
              list() { return [...workspaces.values()]; },
              async insertBefore(id, beforeId) {
                calls.push(['insertBefore', id, beforeId]);
                const order = [...workspaces.values()].filter((item) => item.id !== id);
                const moving = workspaces.get(id);
                const at = beforeId === undefined ? order.length : order.findIndex((item) => item.id === beforeId);
                order.splice(at, 0, moving);
                workspaces.clear();
                for (const item of order) workspaces.set(item.id, item);
              },
              async delete(id) {
                calls.push(['delete', id]);
                workspaces.delete(id);
              },
            };
            workspaces.set('id:/tmp', { id: 'id:/tmp', path: '/tmp' });
            workspaces.set('id:/other', { id: 'id:/other', path: '/home/agent/src/other' });
            const workspace = await ensurePrimaryWorkspace(registry, '/home/agent/src/credimi-attestbook');
            console.log(JSON.stringify({
              path: workspace.path,
              first: registry.list()[0].path,
              paths: registry.list().map((item) => item.path),
              calls,
            }));
        '''))
        info = json.loads(out)
        self.assertEqual(info['path'], '/home/agent/src/credimi-attestbook')
        self.assertEqual(info['first'], '/home/agent/src/credimi-attestbook')
        self.assertEqual(info['paths'], [
            '/home/agent/src/credimi-attestbook',
            '/home/agent/src/other',
        ])
        self.assertEqual(info['calls'][0], ['create', '/home/agent/src/credimi-attestbook'])
        self.assertIn(['delete', 'id:/tmp'], info['calls'])


class ConsolePluginClient(unittest.TestCase):
    def test_registers_main_panel_iframe_and_sidebar_icon(self):
        harness = textwrap.dedent('''\
            const fs = require('fs');
            const registrations = [];
            const React = {
              createElement(type, props, ...children) {
                return { type, props: props || {}, children };
              },
            };
            global.window = {
              __ModuleLoader__: {
                load({ id, factory }) {
                  const mod = factory((name) => {
                    if (name === 'react') return React;
                    throw new Error('unexpected require: ' + name);
                  });
                  global.__plugin = { id, inject: mod.inject, apply: mod.apply };
                },
              },
            };
            // Evaluate the loader-wrapped client as a script.
            // eslint-disable-next-line no-eval
            eval(fs.readFileSync(process.argv[2], 'utf8'));
            const ctx = {
              slots: {
                inject(slot, factory) {
                  factory();
                },
                register(desc, body) {
                  registrations.push({ slot: desc.name, desc, body });
                  return () => {};
                },
              },
            };
            global.__plugin.apply(ctx);
            const main = registrations.find((r) => r.slot === 'main');
            const side = registrations.find((r) => r.slot === 'sidebar.panellist');
            if (!main || !side) {
              console.error(JSON.stringify(registrations.map((r) => r.slot)));
              process.exit(2);
            }
            const tree = typeof main.body === 'function' ? main.body({}) : main.body;
            const iframe = findIframe(tree);
            if (!iframe || iframe.props.src !== '/console/' || iframe.props.title !== 'Console') {
              console.error(JSON.stringify(tree, null, 2));
              process.exit(3);
            }
            if (typeof side.body !== 'function') {
              console.error('sidebar body is not a component');
              process.exit(4);
            }
            console.log(JSON.stringify({
              id: global.__plugin.id,
              inject: global.__plugin.inject,
              mainKey: main.desc.key,
              sideId: side.desc.id,
            }));
            function findIframe(node) {
              if (!node || typeof node !== 'object') return null;
              if (node.type === 'iframe') return node;
              for (const child of node.children || []) {
                const found = findIframe(child);
                if (found) return found;
              }
              return null;
            }
        ''')
        with tempfile.TemporaryDirectory() as tmp:
            harness_path = os.path.join(tmp, 'harness.js')
            with open(harness_path, 'w') as f:
                f.write(harness)
            result = subprocess.run(
                ['node', harness_path, CLIENT],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            info = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(info['id'], 'superagent-console')
            self.assertEqual(info['inject'], ['slots'])
            self.assertEqual(info['mainKey'], 'superagent-console')
            self.assertEqual(info['sideId'], 'superagent-console')


class ConsolePluginImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds.require_docker()
        cls.env = {
            **os.environ,
            'DEEPSEEK_API_KEY': 'ci-dummy',
            'GH_TOKEN': 'ci-dummy',
            'GITHUB_REPOSITORY': 'ci-dummy/ci-dummy',
        }
        subprocess.run(
            ['docker', 'compose', 'build', 'superagent'],
            cwd=ROOT, check=True, env=cls.env, timeout=3600,
        )
        images = subprocess.run(
            ['docker', 'compose', 'config', '--images', 'superagent'],
            cwd=ROOT, check=True, capture_output=True, text=True, env=cls.env,
        ).stdout.split()
        cls.image = images[-1]

    def test_dump_config_lists_superagent_console(self):
        result = ds.docker(
            'run', '--rm', '--entrypoint', 'dsh', self.image,
            '--profile', 'superagent', '--dump-config',
            timeout=180,
        )
        self.assertIn('superagent-console', result.stdout)


if __name__ == '__main__':
    unittest.main()
