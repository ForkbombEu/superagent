"""Claude Code managed-settings split: chat default vs review override."""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLAUDE_REVIEW = os.path.join(ROOT, 'scripts', 'claude-review')
ENTRYPOINT = os.path.join(ROOT, 'scripts', 'superagent-entrypoint.sh')
DOCKERFILE = os.path.join(ROOT, 'Dockerfile')
CHAT = os.path.join(ROOT, 'claude-code', 'managed-settings.chat.json')
REVIEW = os.path.join(ROOT, 'claude-code', 'managed-settings.review.json')
CHAT_MCP = os.path.join(ROOT, 'claude-code', 'managed-mcp.chat.json')
REVIEW_MCP = os.path.join(ROOT, 'claude-code', 'managed-mcp.review.json')


class ClaudeSettingsSplit(unittest.TestCase):
    def test_policies_differ_on_mcp_and_edits(self):
        with open(CHAT, encoding='utf-8') as file:
            chat = json.load(file)
        with open(REVIEW, encoding='utf-8') as file:
            review = json.load(file)
        self.assertEqual(review['allowedMcpServers'], [])
        self.assertIn({'serverCommand': ['codebase-memory-mcp']}, chat['allowedMcpServers'])
        self.assertIn('Edit', review['permissions']['deny'])
        self.assertIn('Write', review['permissions']['deny'])
        self.assertIn('Edit', chat['permissions']['allow'])
        self.assertIn('Write', chat['permissions']['allow'])
        self.assertIn('Agent', chat['permissions']['allow'])
        self.assertIn('Task', chat['permissions']['allow'])
        self.assertIn('mcp__codebase-memory__*', chat['permissions']['allow'])
        self.assertNotIn('mcp__codebase-memory__*', review['permissions']['allow'])
        self.assertEqual(chat['permissions']['disableBypassPermissionsMode'], 'disable')
        self.assertEqual(review['permissions']['disableBypassPermissionsMode'], 'disable')

    def test_chat_managed_mcp_aligns_with_allowlist(self):
        with open(CHAT_MCP, encoding='utf-8') as file:
            managed = json.load(file)
        with open(CHAT, encoding='utf-8') as file:
            chat = json.load(file)
        server = managed['mcpServers']['codebase-memory']
        self.assertEqual(server['type'], 'stdio')
        self.assertEqual(server['command'], 'codebase-memory-mcp')
        self.assertEqual(server.get('args', []), [])
        command = [server['command'], *server.get('args', [])]
        self.assertIn({'serverCommand': command}, chat['allowedMcpServers'])
        self.assertFalse(os.path.exists(REVIEW_MCP))
        with open(REVIEW, encoding='utf-8') as file:
            self.assertEqual(json.load(file)['allowedMcpServers'], [])

    def test_dockerfile_seeds_chat_mcp_for_enterprise_and_chat_paths(self):
        with open(DOCKERFILE, encoding='utf-8') as file:
            dockerfile = file.read()
        self.assertRegex(dockerfile, r'codebase-memory-mcp@\$\{CODEBASE_MEMORY_MCP_VERSION\}')
        self.assertRegex(dockerfile, r'CODEBASE_MEMORY_MCP_VERSION=0\.12\.1')
        self.assertIn(
            'COPY claude-code/managed-mcp.chat.json /etc/claude-code-chat/managed-mcp.json',
            dockerfile,
        )
        # CLI 2.1.318 loads enterprise MCP only from /etc/claude-code/; chat needs
        # that seed. Review still denies mcp__* under dontAsk (not in allow list).
        self.assertIn(
            'COPY claude-code/managed-mcp.chat.json /etc/claude-code/managed-mcp.json',
            dockerfile,
        )
        self.assertIn('CBM_CACHE_DIR=/home/agent/.cache/codebase-memory-mcp', dockerfile)

    def test_dockerfile_leaves_npm_claude_unshadowed(self):
        with open(DOCKERFILE, encoding='utf-8') as file:
            dockerfile = file.read()
        self.assertNotIn('claude-agent', dockerfile)
        self.assertNotIn('claude.real', dockerfile)
        self.assertNotIn('ln -s /usr/local/bin/claude-agent', dockerfile)
        self.assertIn("dsh plugin --profile superagent add 'link:/opt/superagent-primary-workspace'", dockerfile)
        self.assertIn('COPY dsh/primary-workspace /opt/superagent-primary-workspace', dockerfile)

    def test_entrypoint_unsets_claude_credentials(self):
        with open(ENTRYPOINT, encoding='utf-8') as file:
            source = file.read()
        self.assertIn('unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN', source)
        self.assertIn('/run/superagent-secrets/claude-review.json', source)

    def test_entrypoint_indexes_live_checkout_best_effort(self):
        with open(ENTRYPOINT, encoding='utf-8') as file:
            source = file.read()
        self.assertIn('codebase-memory-mcp cli index_repository', source)
        self.assertIn('CBM_CACHE_DIR', source)
        self.assertIn('repo_checkout', source)
        self.assertNotIn('/opt/console-repo', source)
        self.assertRegex(source, re.compile(r'best-effort|index on first use', re.I))

    def test_claude_review_pins_review_policy_constant(self):
        with open(CLAUDE_REVIEW, encoding='utf-8') as file:
            source = file.read()
        self.assertIn("REVIEW_MANAGED_SETTINGS = '/etc/claude-code'", source)
        self.assertIn("'CLAUDE_CODE_MANAGED_SETTINGS_PATH': REVIEW_MANAGED_SETTINGS", source)


if __name__ == '__main__':
    unittest.main()
