"""Unit seams for superagent-claude credentials, sessions, and turn mapping."""
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


class ClaudePluginTurnResume(unittest.TestCase):
    def test_failed_resume_result_requeries_without_resume_and_says_so(self):
        """Non-throw resume failure must re-run query() fresh, not only claim it."""
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            state_dir = os.path.join(tmp, 'state', 'superagent')
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, 'claude-sessions.json'), 'w', encoding='utf-8') as file:
                json.dump({'agent-resume': 'stale-session-id'}, file)
            script = textwrap.dedent(f'''\
                import {{ runTurn }} from './turn.js'
                import {{ getClaudeSessionId }} from './sessions.js'
                import {{ buildClaudeEnv }} from './credentials.js'

                const calls = []

                async function* fakeQuery({{ options }}) {{
                  calls.push({{ resume: options.resume }})
                  if (options.resume) {{
                    yield {{
                      type: 'result',
                      subtype: 'error_during_execution',
                      errors: ['Failed to resume session stale-session-id'],
                      usage: {{ input_tokens: 1, output_tokens: 0 }},
                    }}
                    return
                  }}
                  yield {{ type: 'system', subtype: 'init', session_id: 'fresh-session-id' }}
                  yield {{
                    type: 'assistant',
                    message: {{ content: [{{ type: 'text', text: 'hello after fresh start' }}] }},
                  }}
                  yield {{
                    type: 'result',
                    subtype: 'success',
                    result: 'hello after fresh start',
                    usage: {{ input_tokens: 2, output_tokens: 3 }},
                  }}
                }}

                const agent = {{
                  id: 'agent-resume',
                  session: {{ header: {{ cwd: {json.dumps(tmp)} }} }},
                }}
                const chunks = []
                for await (const chunk of runTurn({{
                  options: {{
                    model: 'haiku',
                    messages: [{{ role: 'user', content: [{{ type: 'text', text: 'hi' }}] }}],
                  }},
                  agent,
                  permissionRules: {{ allow: [], deny: [] }},
                  deps: {{
                    runQuery: fakeQuery,
                    buildEnv: () => buildClaudeEnv({{
                      secretsPath: {json.dumps(secrets)},
                      sourceEnv: {{ PATH: '/bin' }},
                    }}),
                  }},
                }})) {{
                  chunks.push(chunk)
                }}

                const stored = getClaudeSessionId('agent-resume')
                const texts = chunks
                  .filter((c) => c.type === 'text-delta')
                  .map((c) => c.text)
                  .join('')
                process.stdout.write(JSON.stringify({{
                  callCount: calls.length,
                  firstResume: calls[0] ? calls[0].resume ?? null : null,
                  secondResume: calls[1] ? calls[1].resume ?? null : null,
                  texts,
                  stored,
                  finish: chunks.filter((c) => c.type === 'finish').map((c) => c.reason),
                }}))
                ''')
            result = _node(script, {'DSH_HOME': tmp})
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report['callCount'], 2, report)
            self.assertEqual(report['firstResume'], 'stale-session-id')
            self.assertIsNone(report['secondResume'], report)
            self.assertIn('started a fresh session', report['texts'])
            self.assertIn('hello after fresh start', report['texts'])
            self.assertEqual(report['stored'], 'fresh-session-id')
            self.assertEqual(report['finish'], [{'kind': 'stop'}])


class ClaudePluginActivityLine(unittest.TestCase):
    def test_activity_line_shapes_and_bash_truncation(self):
        script = textwrap.dedent('''\
            import { activityLine, ACTIVITY_BASH_MAX } from './turn.js'
            const long = 'x'.repeat(ACTIVITY_BASH_MAX + 40)
            const report = {
              read: activityLine({ name: 'Read', input: { file_path: 'src/app.ts' } }),
              bashShort: activityLine({ name: 'Bash', input: { command: 'npm test' } }),
              bashLong: activityLine({ name: 'Bash', input: { command: long } }),
              agent: activityLine({ name: 'Agent', input: { description: 'explore auth flow' } }),
              mcp: activityLine({ name: 'mcp__codebase-memory__search_graph', input: {} }),
              max: ACTIVITY_BASH_MAX,
            }
            process.stdout.write(JSON.stringify(report))
            ''')
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['read'], 'Read src/app.ts')
        self.assertEqual(report['bashShort'], 'Bash: npm test')
        self.assertTrue(report['bashLong'].startswith('Bash: '))
        self.assertTrue(report['bashLong'].endswith('…'))
        self.assertEqual(len(report['bashLong']), len('Bash: ') + report['max'] + 1)
        self.assertEqual(report['agent'], 'Agent: explore auth flow')
        self.assertEqual(report['mcp'], 'mcp codebase-memory search_graph')


class ClaudePluginTurnMapping(unittest.TestCase):
    def _run_turn_script(self, fake_query_body: str, tmp: str, secrets: str) -> dict:
        script = textwrap.dedent(f'''\
            import {{ runTurn }} from './turn.js'
            import {{ buildClaudeEnv }} from './credentials.js'

            {fake_query_body}

            const agent = {{
              id: 'agent-map',
              session: {{ header: {{ cwd: {json.dumps(tmp)} }} }},
            }}
            const chunks = []
            for await (const chunk of runTurn({{
              options: {{
                model: 'haiku',
                messages: [{{ role: 'user', content: [{{ type: 'text', text: 'hi' }}] }}],
              }},
              agent,
              permissionRules: {{ allow: [], deny: [] }},
              deps: {{
                runQuery: fakeQuery,
                buildEnv: () => buildClaudeEnv({{
                  secretsPath: {json.dumps(secrets)},
                  sourceEnv: {{ PATH: '/bin' }},
                }}),
              }},
            }})) {{
              chunks.push(chunk)
            }}
            process.stdout.write(JSON.stringify({{ chunks }}))
            ''')
        result = _node(script, {'DSH_HOME': tmp})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_partial_text_deltas_stream_before_block_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            report = self._run_turn_script(
                '''\
                async function* fakeQuery() {
                  yield { type: 'system', subtype: 'init', session_id: 's1' }
                  yield {
                    type: 'stream_event',
                    event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'Hel' } },
                  }
                  yield {
                    type: 'stream_event',
                    event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'lo' } },
                  }
                  yield {
                    type: 'assistant',
                    message: { content: [{ type: 'text', text: 'Hello' }] },
                  }
                  yield {
                    type: 'result',
                    subtype: 'success',
                    result: 'Hello',
                    usage: { input_tokens: 1, output_tokens: 1 },
                  }
                }
                ''',
                tmp,
                secrets,
            )
            types = [c['type'] for c in report['chunks']]
            self.assertEqual(types[0], 'block-start')
            self.assertEqual(report['chunks'][0]['blockType'], 'text')
            deltas = [c for c in report['chunks'] if c['type'] == 'text-delta']
            self.assertEqual([d['text'] for d in deltas], ['Hel', 'lo'])
            self.assertIn('block-end', types)
            # Streaming means a delta appears before the matching block-end.
            first_delta = types.index('text-delta')
            first_end = types.index('block-end')
            self.assertLess(first_delta, first_end)
            self.assertEqual(report['chunks'][-1], {'type': 'finish', 'reason': {'kind': 'stop'}})

    def test_tool_use_emits_reasoning_activity_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            report = self._run_turn_script(
                '''\
                async function* fakeQuery() {
                  yield { type: 'system', subtype: 'init', session_id: 's2' }
                  yield {
                    type: 'assistant',
                    message: {
                      content: [{
                        type: 'tool_use',
                        name: 'mcp__codebase-memory__search_graph',
                        input: { query: 'selectLatestVersion' },
                      }],
                    },
                  }
                  yield {
                    type: 'stream_event',
                    event: { type: 'content_block_delta', delta: { type: 'text_delta', text: 'done' } },
                  }
                  yield {
                    type: 'result',
                    subtype: 'success',
                    result: 'done',
                    usage: { input_tokens: 1, output_tokens: 1 },
                  }
                }
                ''',
                tmp,
                secrets,
            )
            reasoning = [
                c['text'] for c in report['chunks']
                if c['type'] == 'reasoning-delta'
            ]
            self.assertEqual(reasoning, ['mcp codebase-memory search_graph'])
            texts = [c['text'] for c in report['chunks'] if c['type'] == 'text-delta']
            self.assertEqual(texts, ['done'])

    def test_abort_signal_finishes_as_aborted(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            script = textwrap.dedent(f'''\
                import {{ runTurn }} from './turn.js'
                import {{ buildClaudeEnv }} from './credentials.js'

                async function* fakeQuery({{ options }}) {{
                  yield {{ type: 'system', subtype: 'init', session_id: 's3' }}
                  await new Promise((resolve) => {{
                    if (options.abortController.signal.aborted) {{ resolve(); return }}
                    options.abortController.signal.addEventListener('abort', () => resolve(), {{ once: true }})
                  }})
                  yield {{
                    type: 'stream_event',
                    event: {{ type: 'content_block_delta', delta: {{ type: 'text_delta', text: 'late' }} }},
                  }}
                }}

                const ac = new AbortController()
                const agent = {{
                  id: 'agent-abort',
                  session: {{ header: {{ cwd: {json.dumps(tmp)} }} }},
                }}
                const chunks = []
                setTimeout(() => ac.abort(), 20)
                for await (const chunk of runTurn({{
                  options: {{
                    model: 'haiku',
                    signal: ac.signal,
                    messages: [{{ role: 'user', content: [{{ type: 'text', text: 'hi' }}] }}],
                  }},
                  agent,
                  permissionRules: {{ allow: [], deny: [] }},
                  deps: {{
                    runQuery: fakeQuery,
                    buildEnv: () => buildClaudeEnv({{
                      secretsPath: {json.dumps(secrets)},
                      sourceEnv: {{ PATH: '/bin' }},
                    }}),
                  }},
                }})) {{
                  chunks.push(chunk)
                }}
                process.stdout.write(JSON.stringify({{
                  finish: chunks.filter((c) => c.type === 'finish').map((c) => c.reason),
                }}))
                ''')
            result = _node(script, {'DSH_HOME': tmp})
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report['finish'][0]['kind'], 'aborted')


class ClaudePluginPermissions(unittest.TestCase):
    def test_rule_matching_bash_prefix_and_mcp_glob(self):
        script = textwrap.dedent('''\
            import { ruleMatches, anyRuleMatches } from './permissions.js'
            const report = {
              read: ruleMatches('Read', 'Read', {}),
              writeDeny: ruleMatches('Write', 'Edit', {}),
              bashGit: ruleMatches('Bash(git:*)', 'Bash', { command: 'git status' }),
              bashGitPush: ruleMatches('Bash(git push:*)', 'Bash', { command: 'git push origin main' }),
              bashNotGit: ruleMatches('Bash(git:*)', 'Bash', { command: 'npm test' }),
              mcp: ruleMatches(
                'mcp__codebase-memory__*',
                'mcp__codebase-memory__search_graph',
                {},
              ),
              mcpMiss: ruleMatches(
                'mcp__codebase-memory__*',
                'mcp__other__tool',
                {},
              ),
              denyWins: anyRuleMatches(['WebFetch'], 'WebFetch', {}),
              allowList: anyRuleMatches(['Read', 'Bash(ls:*)'], 'Bash', { command: 'ls -la' }),
            }
            process.stdout.write(JSON.stringify(report))
            ''')
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['read'])
        self.assertFalse(report['writeDeny'])
        self.assertTrue(report['bashGit'])
        self.assertTrue(report['bashGitPush'])
        self.assertFalse(report['bashNotGit'])
        self.assertTrue(report['mcp'])
        self.assertFalse(report['mcpMiss'])
        self.assertTrue(report['denyWins'])
        self.assertTrue(report['allowList'])

    def test_can_use_tool_deny_allow_and_approval_bridge(self):
        script = textwrap.dedent('''\
            import { createCanUseTool } from './can-use-tool.js'

            const calls = []
            const approval = {
              async request(req) {
                calls.push({ toolName: req.toolName, reason: req.reason })
                if (req.toolName === 'WebSearch') return 'rejected'
                return 'allowed-once'
              },
            }
            const canUseTool = createCanUseTool({
              approval,
              agent: { id: 'a1' },
              allowRules: ['Read', 'mcp__codebase-memory__*', 'Bash(ls:*)'],
              denyRules: ['WebFetch', 'Bash(git push:*)'],
            })
            const opts = { toolUseID: 'tu-1', signal: AbortSignal.abort(), title: 'run tool' }
            const denied = await canUseTool('WebFetch', {}, opts)
            const deniedPush = await canUseTool('Bash', { command: 'git push origin HEAD' }, opts)
            const allowedRead = await canUseTool('Read', { file_path: 'a.ts' }, opts)
            const allowedMcp = await canUseTool('mcp__codebase-memory__search_graph', {}, opts)
            const allowedLs = await canUseTool('Bash', { command: 'ls -la' }, opts)
            const askedOk = await canUseTool('Bash', { command: 'curl https://example.com' }, {
              ...opts, title: 'curl needs approval',
            })
            const askedNo = await canUseTool('WebSearch', { query: 'x' }, opts)
            process.stdout.write(JSON.stringify({
              denied, deniedPush, allowedRead, allowedMcp, allowedLs, askedOk, askedNo, calls,
            }))
            ''')
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['denied']['behavior'], 'deny')
        self.assertEqual(report['deniedPush']['behavior'], 'deny')
        self.assertEqual(report['allowedRead'], {'behavior': 'allow', 'toolUseID': 'tu-1'})
        self.assertEqual(report['allowedMcp']['behavior'], 'allow')
        self.assertEqual(report['allowedLs']['behavior'], 'allow')
        self.assertEqual(report['askedOk']['behavior'], 'allow')
        self.assertEqual(report['askedNo']['behavior'], 'deny')
        self.assertEqual(
            [c['toolName'] for c in report['calls']],
            ['Bash', 'WebSearch'],
        )

    def test_ask_user_question_bridges_to_user_questions(self):
        script = textwrap.dedent('''\
            import { createCanUseTool, mapAskUserQuestionInput } from './can-use-tool.js'

            const asked = []
            const userQuestions = {
              async ask(req) {
                asked.push(req)
                return {
                  answers: [{ id: 'Library', selected: ['date-fns'], custom: undefined }],
                }
              },
            }
            const canUseTool = createCanUseTool({
              userQuestions,
              agent: { id: 'a1' },
              allowRules: [],
              denyRules: [],
            })
            const input = {
              questions: [{
                question: 'Which library?',
                header: 'Library',
                options: [
                  { label: 'date-fns', description: 'small' },
                  { label: 'moment', description: 'legacy' },
                ],
                multiSelect: false,
              }],
            }
            const result = await canUseTool('AskUserQuestion', input, { toolUseID: 'q1' })
            process.stdout.write(JSON.stringify({
              result,
              mapped: mapAskUserQuestionInput(input),
              askedCount: asked.length,
              askedAgent: asked[0]?.agent?.id,
            }))
            ''')
        result = _node(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['result']['behavior'], 'allow')
        self.assertEqual(report['result']['updatedInput']['answers'], {'Which library?': 'date-fns'})
        self.assertEqual(report['askedCount'], 1)
        self.assertEqual(report['askedAgent'], 'a1')
        self.assertEqual(report['mapped']['questions'][0]['id'], 'Library')

    def test_run_turn_wires_default_permission_mode_and_can_use_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            script = textwrap.dedent(f'''\
                import {{ runTurn }} from './turn.js'
                import {{ buildClaudeEnv }} from './credentials.js'

                const seen = []
                async function* fakeQuery({{ options }}) {{
                  seen.push({{
                    permissionMode: options.permissionMode,
                    hasCanUseTool: typeof options.canUseTool === 'function',
                    mcp: options.mcpServers && options.mcpServers['codebase-memory']
                      ? options.mcpServers['codebase-memory'].command
                      : null,
                  }})
                  yield {{ type: 'system', subtype: 'init', session_id: 's-perm' }}
                  yield {{
                    type: 'result',
                    subtype: 'success',
                    result: 'ok',
                    usage: {{ input_tokens: 1, output_tokens: 1 }},
                  }}
                }}

                const agent = {{
                  id: 'agent-perm',
                  session: {{ header: {{ cwd: {json.dumps(tmp)} }} }},
                }}
                for await (const _ of runTurn({{
                  options: {{
                    model: 'haiku',
                    messages: [{{ role: 'user', content: [{{ type: 'text', text: 'hi' }}] }}],
                  }},
                  agent,
                  permissionRules: {{ allow: ['Read'], deny: ['WebFetch'] }},
                  deps: {{
                    runQuery: fakeQuery,
                    buildEnv: () => buildClaudeEnv({{
                      secretsPath: {json.dumps(secrets)},
                      sourceEnv: {{ PATH: '/bin' }},
                    }}),
                  }},
                }})) {{
                  // drain
                }}
                process.stdout.write(JSON.stringify({{ seen }}))
                ''')
            result = _node(script, {'DSH_HOME': tmp})
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report['seen'][0]['permissionMode'], 'default')
            self.assertTrue(report['seen'][0]['hasCanUseTool'])
            self.assertEqual(report['seen'][0]['mcp'], 'codebase-memory-mcp')

    def test_run_turn_wires_opusplan_permission_mode_and_can_use_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            script = textwrap.dedent(f'''\
                import {{ runTurn }} from './turn.js'
                import {{ buildClaudeEnv }} from './credentials.js'

                const seen = []
                async function* fakeQuery({{ options }}) {{
                  seen.push({{
                    permissionMode: options.permissionMode,
                    hasCanUseTool: typeof options.canUseTool === 'function',
                    mcp: options.mcpServers && options.mcpServers['codebase-memory']
                      ? options.mcpServers['codebase-memory'].command
                      : null,
                  }})
                  yield {{ type: 'system', subtype: 'init', session_id: 's-plan' }}
                  yield {{
                    type: 'result',
                    subtype: 'success',
                    result: 'ok',
                    usage: {{ input_tokens: 1, output_tokens: 1 }},
                  }}
                }}

                const agent = {{
                  id: 'agent-plan',
                  session: {{ header: {{ cwd: {json.dumps(tmp)} }} }},
                }}
                for await (const _ of runTurn({{
                  options: {{
                    model: 'opusplan',
                    messages: [{{ role: 'user', content: [{{ type: 'text', text: 'hi' }}] }}],
                  }},
                  agent,
                  permissionRules: {{ allow: ['Read'], deny: ['WebFetch'] }},
                  deps: {{
                    runQuery: fakeQuery,
                    buildEnv: () => buildClaudeEnv({{
                      secretsPath: {json.dumps(secrets)},
                      sourceEnv: {{ PATH: '/bin' }},
                    }}),
                  }},
                }})) {{
                  // drain
                }}
                process.stdout.write(JSON.stringify({{ seen }}))
                ''')
            result = _node(script, {'DSH_HOME': tmp})
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report['seen'][0]['permissionMode'], 'plan')
            self.assertTrue(report['seen'][0]['hasCanUseTool'])
            self.assertEqual(report['seen'][0]['mcp'], 'codebase-memory-mcp')


class ClaudePluginTurnResumeEmptyErrors(unittest.TestCase):
    def test_empty_errors_resume_result_retries_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = os.path.join(tmp, 'claude-review.json')
            with open(secrets, 'w', encoding='utf-8') as file:
                json.dump({'env': {'ANTHROPIC_API_KEY': 'test-key-not-real'}}, file)
            state_dir = os.path.join(tmp, 'state', 'superagent')
            os.makedirs(state_dir, exist_ok=True)
            with open(os.path.join(state_dir, 'claude-sessions.json'), 'w', encoding='utf-8') as file:
                json.dump({'agent-empty': 'stale-empty-errors'}, file)
            script = textwrap.dedent(f'''\
                import {{ runTurn, isFailedResumeResult }} from './turn.js'
                import {{ getClaudeSessionId }} from './sessions.js'
                import {{ buildClaudeEnv }} from './credentials.js'

                const emptyFail = {{
                  type: 'result',
                  subtype: 'error_during_execution',
                  errors: [],
                }}
                const detected = isFailedResumeResult(emptyFail, true)

                const calls = []
                async function* fakeQuery({{ options }}) {{
                  calls.push({{ resume: options.resume ?? null }})
                  if (options.resume) {{
                    yield emptyFail
                    return
                  }}
                  yield {{ type: 'system', subtype: 'init', session_id: 'fresh-empty' }}
                  yield {{
                    type: 'assistant',
                    message: {{ content: [{{ type: 'text', text: 'recovered' }}] }},
                  }}
                  yield {{
                    type: 'result',
                    subtype: 'success',
                    result: 'recovered',
                    usage: {{ input_tokens: 1, output_tokens: 1 }},
                  }}
                }}

                const agent = {{
                  id: 'agent-empty',
                  session: {{ header: {{ cwd: {json.dumps(tmp)} }} }},
                }}
                const chunks = []
                for await (const chunk of runTurn({{
                  options: {{
                    model: 'haiku',
                    messages: [{{ role: 'user', content: [{{ type: 'text', text: 'hi' }}] }}],
                  }},
                  agent,
                  permissionRules: {{ allow: [], deny: [] }},
                  deps: {{
                    runQuery: fakeQuery,
                    buildEnv: () => buildClaudeEnv({{
                      secretsPath: {json.dumps(secrets)},
                      sourceEnv: {{ PATH: '/bin' }},
                    }}),
                  }},
                }})) {{
                  chunks.push(chunk)
                }}
                const texts = chunks
                  .filter((c) => c.type === 'text-delta')
                  .map((c) => c.text)
                  .join('')
                process.stdout.write(JSON.stringify({{
                  detected,
                  callCount: calls.length,
                  firstResume: calls[0]?.resume ?? null,
                  secondResume: calls[1]?.resume ?? null,
                  texts,
                  stored: getClaudeSessionId('agent-empty'),
                }}))
                ''')
            result = _node(script, {'DSH_HOME': tmp})
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertTrue(report['detected'])
            self.assertEqual(report['callCount'], 2)
            self.assertEqual(report['firstResume'], 'stale-empty-errors')
            self.assertIsNone(report['secondResume'])
            self.assertIn('started a fresh session', report['texts'])
            self.assertIn('recovered', report['texts'])
            self.assertEqual(report['stored'], 'fresh-empty')


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
