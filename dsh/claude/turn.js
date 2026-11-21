/**
 * One DSH step = one Claude Code Agent SDK query(). Maps SDK messages to
 * text/reasoning StreamChunks only (no tool-call blocks).
 */
import { join } from 'node:path'
import { query } from '@anthropic-ai/claude-agent-sdk'
import { createCanUseTool } from './can-use-tool.js'
import { buildClaudeEnv, MANAGED_SETTINGS_PATH } from './credentials.js'
import { loadPermissionRules } from './permissions.js'
import { getClaudeSessionId, setClaudeSessionId } from './sessions.js'

export const PROVIDER_ID = 'superagent-claude'

/** Default Claude CLI on the Superagent image (shadow or real). */
export const DEFAULT_CLAUDE_EXECUTABLE = '/usr/local/bin/claude'

/** Max characters shown for Bash activity lines (truncate long commands). */
export const ACTIVITY_BASH_MAX = 120

/**
 * Chat MCP servers also passed via the Agent SDK. Enterprise managed-mcp is
 * still seeded at `/etc/claude-code/` (CLI 2.1.318 loads only that path);
 * `alwaysLoad` avoids deferring tools behind ToolSearch on the first turn.
 */
export const CHAT_MCP_SERVERS = Object.freeze({
  'codebase-memory': {
    type: 'stdio',
    command: 'codebase-memory-mcp',
    args: [],
    alwaysLoad: true,
  },
})

const REJECTED_PURPOSES = new Set([
  'session-title',
  'compaction',
])

const RESUME_NOTICE = 'Claude session resume failed; started a fresh session.\n\n'

/**
 * @param {import('@deepseek-ai/dsh-llm').GenerateOptions} options
 * @param {{ get: (id: string) => unknown, currentInitiator?: () => unknown }} agents
 */
export function resolveAgent(agents, options) {
  const initiator = typeof agents.currentInitiator === 'function'
    ? agents.currentInitiator()
    : undefined
  if (initiator !== undefined) return initiator
  if (options.sessionId !== undefined) {
    const agent = agents.get(options.sessionId)
    if (agent !== undefined) return agent
  }
  throw new Error('superagent-claude: the model request has no live owning DSH agent')
}

/**
 * Flatten the newest user message to a Claude prompt string.
 * @param {readonly import('@deepseek-ai/dsh-llm').RequestMessage[]} messages
 * @returns {string}
 */
export function promptFromMessages(messages) {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (message.role !== 'user') continue
    const texts = []
    for (const block of message.content) {
      if (block.type === 'text' && typeof block.text === 'string') texts.push(block.text)
    }
    const prompt = texts.join('\n').trim()
    if (prompt.length > 0) return prompt
  }
  throw new Error('superagent-claude: the request has no user text')
}

/**
 * @param {string | undefined} effort
 * @returns {string | undefined}
 */
export function effortFromReasoning(effort) {
  if (typeof effort !== 'string' || effort.length === 0) return undefined
  const normalized = effort.toLowerCase()
  if (normalized === 'off' || normalized === 'none') return undefined
  return normalized
}

/**
 * Truncate a single-line activity summary for long Bash commands.
 * @param {string} command
 * @param {number} [max]
 * @returns {string}
 */
export function truncateBashCommand(command, max = ACTIVITY_BASH_MAX) {
  if (command.length <= max) return command
  return `${command.slice(0, max)}…`
}

/**
 * One-line activity summary for a tool_use block.
 * @param {{ name?: string, input?: Record<string, unknown> }} tool
 * @returns {string}
 */
export function activityLine(tool) {
  const name = typeof tool.name === 'string' ? tool.name : 'Tool'
  const input = tool.input && typeof tool.input === 'object' ? tool.input : {}
  if (name === 'Read' && typeof input.file_path === 'string') return `Read ${input.file_path}`
  if (name === 'Write' && typeof input.file_path === 'string') return `Write ${input.file_path}`
  if (name === 'Edit' && typeof input.file_path === 'string') return `Edit ${input.file_path}`
  if ((name === 'Bash' || name === 'PowerShell') && typeof input.command === 'string') {
    return `Bash: ${truncateBashCommand(input.command)}`
  }
  if (name === 'Agent' || name === 'Task') {
    const desc = typeof input.description === 'string'
      ? input.description
      : typeof input.prompt === 'string' ? input.prompt.slice(0, 80) : ''
    return desc ? `Agent: ${desc}` : 'Agent'
  }
  if (name.startsWith('mcp__')) {
    return name.replace(/^mcp__/, 'mcp ').replace(/__/g, ' ')
  }
  const path = typeof input.file_path === 'string'
    ? input.file_path
    : typeof input.path === 'string' ? input.path : undefined
  return path ? `${name} ${path}` : name
}

/**
 * True when an SDK result message indicates the resume session was unusable.
 * @param {unknown} message
 * @param {boolean} resumed
 * @returns {boolean}
 */
export function isFailedResumeResult(message, resumed) {
  if (!resumed || !message || typeof message !== 'object') return false
  const row = /** @type {{ type?: string, subtype?: string, errors?: string[] }} */ (message)
  if (row.type !== 'result') return false
  if (!row.subtype || row.subtype === 'success') return false
  // Empty errors on a non-success result after resume: treat as failed resume
  // (SDK may omit the reason string while still failing the resume attempt).
  if (Array.isArray(row.errors) && row.errors.length === 0) return true
  const detail = Array.isArray(row.errors) && row.errors.length > 0
    ? row.errors.join(' ')
    : String(row.subtype)
  return /resume|session/i.test(detail)
}

/**
 * @param {unknown} error
 * @param {boolean} resumed
 * @returns {boolean}
 */
export function isFailedResumeError(error, resumed) {
  if (!resumed) return false
  const message = error instanceof Error ? error.message : String(error)
  return /resume|session/i.test(message)
}

/**
 * @param {string} text
 * @param {number} index
 * @param {'text' | 'reasoning'} blockType
 */
function* emitTextBlock(text, index, blockType) {
  if (text.length === 0) return
  yield { type: 'block-start', index, blockType }
  yield {
    type: blockType === 'text' ? 'text-delta' : 'reasoning-delta',
    index,
    text,
  }
  yield {
    type: 'block-end',
    index,
    block: blockType === 'text' ? { type: 'text', text } : { type: 'reasoning', text },
  }
}

/**
 * Stream one SDK query into DSH chunks. Mutates `state` with session/usage/text.
 * @param {object} args
 * @param {AsyncIterable<unknown>} args.messages
 * @param {import('@deepseek-ai/dsh-llm').GenerateOptions} args.options
 * @param {AbortController} args.controller
 * @param {string} args.agentId
 * @param {{
 *   blockIndex: number,
 *   openText: string,
 *   textStarted: boolean,
 *   pendingUsage: { inputTokens: number, outputTokens: number } | undefined,
 *   claudeSessionId: string | undefined,
 *   sawResult: boolean,
 *   resumeFailed: boolean,
 *   resumed: boolean,
 * }} args.state
 * @param {string} [args.textPrefix] prepended once before the first text delta
 * @returns {AsyncGenerator<import('@deepseek-ai/dsh-llm').StreamChunk>}
 */
async function* consumeQuery({ messages, options, controller, agentId, state, textPrefix = '' }) {
  let prefix = textPrefix

  const flushOpenText = function* () {
    if (!state.textStarted) return
    yield {
      type: 'block-end',
      index: state.blockIndex,
      block: { type: 'text', text: state.openText },
    }
    state.blockIndex += 1
    state.openText = ''
    state.textStarted = false
  }

  const ensureTextOpen = function* () {
    if (state.textStarted) return
    yield { type: 'block-start', index: state.blockIndex, blockType: 'text' }
    state.textStarted = true
    if (prefix.length > 0) {
      state.openText += prefix
      yield { type: 'text-delta', index: state.blockIndex, text: prefix }
      prefix = ''
    }
  }

  const emitDelta = function* (piece) {
    if (typeof piece !== 'string' || piece.length === 0) return
    yield* ensureTextOpen()
    state.openText += piece
    yield { type: 'text-delta', index: state.blockIndex, text: piece }
  }

  for await (const message of messages) {
    if (options.signal?.aborted || controller.signal.aborted) {
      yield* flushOpenText()
      yield {
        type: 'finish',
        reason: {
          kind: 'aborted',
          failure: { code: 'aborted', message: 'Claude Code turn cancelled' },
        },
      }
      return
    }

    if (message?.type === 'system' && message?.subtype === 'init') {
      if (typeof message.session_id === 'string' && message.session_id.length > 0) {
        state.claudeSessionId = message.session_id
        if (agentId) setClaudeSessionId(agentId, state.claudeSessionId)
      }
      continue
    }

    if (message?.type === 'stream_event') {
      const event = message.event
      if (event?.type === 'content_block_delta' && event.delta?.type === 'text_delta') {
        yield* emitDelta(event.delta.text ?? '')
      }
      continue
    }

    if (message?.type === 'assistant') {
      const content = message.message?.content
      if (!Array.isArray(content)) continue
      for (const block of content) {
        if (block.type === 'text' && typeof block.text === 'string') {
          // Prefer streamed partials; only take final assistant text when nothing streamed.
          if (!state.textStarted && state.openText.length === 0) {
            yield* emitDelta(block.text)
          } else if (block.text.startsWith(state.openText) && block.text.length > state.openText.length) {
            yield* emitDelta(block.text.slice(state.openText.length))
          }
        } else if (block.type === 'tool_use') {
          yield* flushOpenText()
          const line = activityLine(block)
          yield* emitTextBlock(line, state.blockIndex, 'reasoning')
          state.blockIndex += 1
        }
      }
      continue
    }

    if (message?.type === 'result') {
      state.sawResult = true
      if (isFailedResumeResult(message, state.resumed)) {
        state.resumeFailed = true
      }
      if (typeof message.usage === 'object' && message.usage) {
        state.pendingUsage = {
          inputTokens: message.usage.input_tokens ?? message.usage.inputTokens ?? 0,
          outputTokens: message.usage.output_tokens ?? message.usage.outputTokens ?? 0,
        }
      }
      if (typeof message.result === 'string' && message.result.length > 0
        && !state.textStarted && state.openText.length === 0) {
        yield* emitDelta(message.result)
      }
    }
  }

  // Resume notice with an empty retry body still surfaces in the turn text.
  if (prefix.length > 0 && !state.textStarted) {
    yield { type: 'block-start', index: state.blockIndex, blockType: 'text' }
    state.textStarted = true
    state.openText = prefix
    yield { type: 'text-delta', index: state.blockIndex, text: prefix }
    prefix = ''
  }

  yield* flushOpenText()
}

/**
 * Run one Claude Code turn and yield DSH StreamChunks.
 * @param {object} args
 * @param {import('@deepseek-ai/dsh-llm').GenerateOptions} args.options
 * @param {unknown} args.agent
 * @param {{ request: Function }} [args.approval]
 * @param {{ ask: Function }} [args.userQuestions]
 * @param {{ allow: string[], deny: string[] }} [args.permissionRules]
 * @param {{
 *   executable?: string,
 *   buildEnv?: typeof buildClaudeEnv,
 *   runQuery?: typeof query,
 *   createCanUseTool?: typeof createCanUseTool,
 *   loadPermissionRules?: typeof loadPermissionRules,
 *   managedSettingsPath?: string,
 *   mcpServers?: typeof CHAT_MCP_SERVERS | Record<string, unknown>,
 * }} [args.deps]
 * @returns {AsyncGenerator<import('@deepseek-ai/dsh-llm').StreamChunk>}
 */
export async function* runTurn({
  options,
  agent,
  approval,
  userQuestions,
  permissionRules,
  deps = {},
}) {
  if (options.purpose !== undefined && REJECTED_PURPOSES.has(String(options.purpose))) {
    throw new Error(`superagent-claude: auxiliary ${options.purpose} calls are rejected in the MVP`)
  }
  if (options.purpose !== undefined) {
    throw new Error(`superagent-claude: auxiliary ${options.purpose} calls are not supported`)
  }

  const buildEnv = deps.buildEnv ?? buildClaudeEnv
  const runQuery = deps.runQuery ?? query
  const executable = deps.executable ?? DEFAULT_CLAUDE_EXECUTABLE
  const makeCanUseTool = deps.createCanUseTool ?? createCanUseTool
  const loadRules = deps.loadPermissionRules ?? loadPermissionRules
  const managedSettingsPath = deps.managedSettingsPath
    ?? join(MANAGED_SETTINGS_PATH, 'managed-settings.json')
  const mcpServers = deps.mcpServers ?? CHAT_MCP_SERVERS

  const agentId = typeof agent?.id === 'string' ? agent.id : String(options.sessionId ?? '')
  const cwd = agent?.session?.header?.cwd
  if (typeof cwd !== 'string' || cwd.length === 0) {
    throw new Error('superagent-claude: agent.session.header.cwd is required')
  }

  const prompt = promptFromMessages(options.messages)
  const storedSessionId = agentId ? getClaudeSessionId(agentId) : undefined

  const controller = new AbortController()
  const onAbort = () => {
    if (!controller.signal.aborted) controller.abort()
  }
  options.signal?.addEventListener('abort', onAbort, { once: true })

  const rules = permissionRules ?? loadRules(managedSettingsPath)

  const effort = effortFromReasoning(options.reasoningEffort)
  const model = options.model
  // opusplan: Claude Code plan-mode alias; keep host prompts for ExitPlanMode etc.
  const permissionMode = model === 'opusplan' ? 'plan' : 'default'
  const canUseTool = makeCanUseTool({
    approval,
    userQuestions,
    agent,
    allowRules: rules.allow,
    denyRules: rules.deny,
    permissionMode,
  })
  /** @type {Record<string, unknown>} */
  const baseOptions = {
    cwd,
    pathToClaudeCodeExecutable: executable,
    env: buildEnv(),
    permissionMode,
    permissionPrompts: 'host',
    canUseTool,
    mcpServers,
    includePartialMessages: true,
    abortController: controller,
    model,
    ...(effort === undefined ? {} : { effort }),
  }

  /** @type {{
   *   blockIndex: number,
   *   openText: string,
   *   textStarted: boolean,
   *   pendingUsage: { inputTokens: number, outputTokens: number } | undefined,
   *   claudeSessionId: string | undefined,
   *   sawResult: boolean,
   *   resumeFailed: boolean,
   *   resumed: boolean,
   * }} */
  const state = {
    blockIndex: 0,
    openText: '',
    textStarted: false,
    pendingUsage: undefined,
    claudeSessionId: storedSessionId,
    sawResult: false,
    resumeFailed: false,
    resumed: Boolean(storedSessionId),
  }

  try {
    const queryOptions = {
      ...baseOptions,
      ...(storedSessionId === undefined ? {} : { resume: storedSessionId }),
    }

    try {
      const q = runQuery({ prompt, options: queryOptions })
      for await (const chunk of consumeQuery({
        messages: q,
        options,
        controller,
        agentId,
        state,
      })) {
        yield chunk
        if (chunk.type === 'finish') return
      }
    } catch (error) {
      if (isFailedResumeError(error, state.resumed)) {
        state.resumeFailed = true
      } else if (options.signal?.aborted || controller.signal.aborted) {
        yield {
          type: 'finish',
          reason: {
            kind: 'aborted',
            failure: {
              code: 'aborted',
              message: error instanceof Error ? error.message : String(error),
            },
          },
        }
        return
      } else {
        throw error
      }
    }

    if (state.resumeFailed) {
      if (agentId) setClaudeSessionId(agentId, undefined)
      state.resumed = false
      state.sawResult = false
      state.openText = ''
      state.textStarted = false
      state.claudeSessionId = undefined

      const retry = runQuery({
        prompt,
        options: { ...baseOptions },
      })
      for await (const chunk of consumeQuery({
        messages: retry,
        options,
        controller,
        agentId,
        state,
        textPrefix: RESUME_NOTICE,
      })) {
        yield chunk
        if (chunk.type === 'finish') return
      }
    }

    if (!state.sawResult && state.blockIndex === 0 && state.openText.length === 0) {
      throw new Error('superagent-claude: Claude Code returned no result')
    }

    if (state.pendingUsage) {
      yield { type: 'usage', usage: state.pendingUsage }
    }
    yield { type: 'finish', reason: { kind: 'stop' } }
  } finally {
    options.signal?.removeEventListener('abort', onAbort)
  }
}
