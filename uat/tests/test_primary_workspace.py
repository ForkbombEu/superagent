"""Primary workspace host plugin: checkout path and promote-only registry."""
import json
import os
import subprocess
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN = os.path.join(ROOT, 'dsh', 'primary-workspace')


class PrimaryWorkspacePlugin(unittest.TestCase):
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
            "for (const value of ['', 'noslash', 'a/b/c', 'a/.', 'a/..', null])"
            "  console.log(String(checkoutPath(value, '/home/agent')))",
        )
        self.assertEqual(out.splitlines(), ['null'] * 6)

    def test_ensure_primary_workspace_promotes_without_deleting_tmp(self):
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
            '/tmp',
            '/home/agent/src/other',
        ])
        self.assertEqual(info['calls'][0], ['create', '/home/agent/src/credimi-attestbook'])
        self.assertFalse(any(call[0] == 'delete' for call in info['calls']))


if __name__ == '__main__':
    unittest.main()
