"""Unit seams for superagent-claude credentials and session store."""
import json
import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN = os.path.join(ROOT, 'dsh', 'claude')
DOCKERFILE = os.path.join(ROOT, 'Dockerfile')


def _node(script: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        ['node', '--input-type=module', '-e', script],
        cwd=PLUGIN,
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


class ClaudePluginCredentials(unittest.TestCase):
    def test_scrubbed_env_never_leaks_parent_tokens_except_explicit_claude_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({
                    'env': {
                        'CLAUDE_CODE_OAUTH_TOKEN': 'oauth-from-secrets',
                        'ANTHROPIC_API_KEY': 'api-from-secrets',
                    },
                }, file)
            script = textwrap.dedent(f'''\
                import {{ buildClaudeEnv }} from './credentials.js'
                const sourceEnv = {{
                  PATH: '/usr/bin',
                  HOME: '/home/agent',
                  CLAUDE_CODE_OAUTH_TOKEN: 'parent-oauth-must-not-leak',
                  ANTHROPIC_API_KEY: 'parent-api-must-not-leak',
                  GH_TOKEN: 'parent-gh-must-not-leak',
                  DEEPSEEK_API_KEY: 'parent-deepseek-must-not-leak',
                  DSH_HOME: '/home/agent/.dsh',
                  DSH_TELEMETRY_MODE: 'DISABLED',
                  SAFE_FLAG: 'keep-me',
                }}
                const env = buildClaudeEnv({{
                  secretsPath: {json.dumps(secrets)},
                  sourceEnv,
                }})
                const report = {{
                  hasParentOauth: env.CLAUDE_CODE_OAUTH_TOKEN === 'parent-oauth-must-not-leak',
                  hasSecretsOauth: env.CLAUDE_CODE_OAUTH_TOKEN === 'oauth-from-secrets',
                  hasSecretsApi: env.ANTHROPIC_API_KEY === 'api-from-secrets',
                  leakedGh: 'GH_TOKEN' in env,
                  leakedDeepseek: 'DEEPSEEK_API_KEY' in env,
                  leakedDsh: Object.keys(env).some((k) => k.toUpperCase().startsWith('DSH_')),
                  keptSafe: env.SAFE_FLAG === 'keep-me',
                  managed: env.CLAUDE_CODE_MANAGED_SETTINGS_PATH,
                }}
                process.stdout.write(JSON.stringify(report))
                ''')
            result = _node(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report['hasParentOauth'])
            self.assertTrue(report['hasSecretsOauth'])
            self.assertTrue(report['hasSecretsApi'])
            self.assertFalse(report['leakedGh'])
            self.assertFalse(report['leakedDeepseek'])
            self.assertFalse(report['leakedDsh'])
            self.assertTrue(report['keptSafe'])
            self.assertEqual(report['managed'], '/etc/claude-code-chat')

    def test_build_env_requires_a_claude_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'empty.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {}}, file)
            script = textwrap.dedent(f'''\
                import {{ buildClaudeEnv }} from './credentials.js'
                try {{
                  buildClaudeEnv({{ secretsPath: {json.dumps(secrets)}, sourceEnv: {{ PATH: '/bin' }} }})
                  process.stdout.write('unexpected-ok')
                }} catch (error) {{
                  process.stdout.write(String(error && error.message || error))
                }}
                ''')
            result = _node(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('no Claude credential', result.stdout)


class ClaudePluginSessions(unittest.TestCase):
    def test_session_store_round_trip_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'state', 'claude-sessions.json')
            script = textwrap.dedent(f'''\
                import {{ existsSync, readdirSync }} from 'node:fs'
                import {{ dirname }} from 'node:path'
                import {{
                  getClaudeSessionId,
                  setClaudeSessionId,
                  loadSessions,
                }} from './sessions.js'
                const path = {json.dumps(path)}
                setClaudeSessionId('agent-1', 'claude-session-aaa', path)
                setClaudeSessionId('agent-2', 'claude-session-bbb', path)
                const first = getClaudeSessionId('agent-1', path)
                const second = getClaudeSessionId('agent-2', path)
                setClaudeSessionId('agent-1', 'claude-session-ccc', path)
                const updated = getClaudeSessionId('agent-1', path)
                setClaudeSessionId('agent-2', undefined, path)
                const removed = getClaudeSessionId('agent-2', path)
                const store = loadSessions(path)
                const leftovers = readdirSync(dirname(path)).filter((name) => name.endsWith('.tmp'))
                process.stdout.write(JSON.stringify({{
                  first, second, updated,
                  removedMissing: removed === undefined,
                  store, leftovers, exists: existsSync(path),
                }}))
                ''')
            result = _node(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['first'], 'claude-session-aaa')
            self.assertEqual(report['second'], 'claude-session-bbb')
            self.assertEqual(report['updated'], 'claude-session-ccc')
            self.assertTrue(report['removedMissing'])
            self.assertEqual(report['store'], {'agent-1': 'claude-session-ccc'})
            self.assertEqual(report['leftovers'], [])
            self.assertTrue(report['exists'])


class ClaudePluginDockerfile(unittest.TestCase):
    def test_dockerfile_links_superagent_claude_not_norman_else(self):
        with open(DOCKERFILE, encoding='utf-8') as file:
            dockerfile = file.read()
        self.assertIn("COPY dsh/claude /opt/superagent-claude", dockerfile)
        self.assertIn("dsh plugin --profile superagent add 'link:/opt/superagent-claude'", dockerfile)
        self.assertNotIn('@norman-else/dsh-claude', dockerfile)
        self.assertIn('npm ci --omit=dev', dockerfile)


if __name__ == '__main__':
    unittest.main()
