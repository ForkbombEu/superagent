/**
 * One DSH step = one Claude Code Agent SDK query(). Maps SDK messages to
 * text/reasoning StreamChunks only (no tool-call blocks).
 */
import { query } from '@anthropic-ai/claude-agent-sdk'
import { buildClaudeEnv } from './credentials.js'
import { getClaudeSessionId, setClaudeSessionId } from './sessions.js'

export const PROVIDER_ID = 'superagent-claude'

/** Default Claude CLI on the Superagent image (shadow or real). */
export const DEFAULT_CLAUDE_EXECUTABLE = '/usr/local/bin/claude'

const REJECTED_PURPOSES = new Set([
  'session-title',
  'compaction',
])

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
    return `Bash: ${input.command}`
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
 * Run one Claude Code turn and yield DSH StreamChunks.
 * @param {object} args
 * @param {import('@deepseek-ai/dsh-llm').GenerateOptions} args.options
 * @param {unknown} args.agent
 * @param {{ executable?: string, buildEnv?: typeof buildClaudeEnv, runQuery?: typeof query }} [args.deps]
 * @returns {AsyncGenerator<import('@deepseek-ai/dsh-llm').StreamChunk>}
 */
export async function* runTurn({ options, agent, deps = {} }) {
  if (options.purpose !== undefined && REJECTED_PURPOSES.has(String(options.purpose))) {
    throw new Error(`superagent-claude: auxiliary ${options.purpose} calls are rejected in the MVP`)
  }
  if (options.purpose !== undefined) {
    throw new Error(`superagent-claude: auxiliary ${options.purpose} calls are not supported`)
  }

  const buildEnv = deps.buildEnv ?? buildClaudeEnv
  const runQuery = deps.runQuery ?? query
  const executable = deps.executable ?? DEFAULT_CLAUDE_EXECUTABLE

  const agentId = typeof agent?.id === 'string' ? agent.id : String(options.sessionId ?? '')
  const cwd = agent?.session?.header?.cwd
  if (typeof cwd !== 'string' || cwd.length === 0) {
    throw new Error('superagent-claude: agent.session.header.cwd is required')
  }

  const prompt = promptFromMessages(options.messages)
  const storedSessionId = agentId ? getClaudeSessionId(agentId) : undefined
  let resumeNotice = ''
  let resume = storedSessionId
  if (storedSessionId) {
    // resume is attempted; a failed resume is reported in the turn text below
  }

  const controller = new AbortController()
  const onAbort = () => {
    if (!controller.signal.aborted) controller.abort()
  }
  options.signal?.addEventListener('abort', onAbort, { once: true })

  const effort = effortFromReasoning(options.reasoningEffort)
  /** @type {Record<string, unknown>} */
  const queryOptions = {
    cwd,
    pathToClaudeCodeExecutable: executable,
    env: buildEnv(),
    permissionMode: 'dontAsk',
    includePartialMessages: true,
    abortController: controller,
    model: options.model,
    ...(effort === undefined ? {} : { effort }),
    ...(resume === undefined ? {} : { resume }),
  }

  let blockIndex = 0
  let text = ''
  let pendingUsage
  let claudeSessionId = storedSessionId
  let sawResult = false
  let resumeFailed = false

  try {
    const q = runQuery({ prompt, options: queryOptions })
    for await (const message of q) {
      if (options.signal?.aborted) {
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
          claudeSessionId = message.session_id
          if (agentId) setClaudeSessionId(agentId, claudeSessionId)
        }
        continue
      }

      if (message?.type === 'stream_event') {
        const event = message.event
        if (event?.type === 'content_block_delta' && event.delta?.type === 'text_delta') {
          text += event.delta.text ?? ''
        }
        continue
      }

      if (message?.type === 'assistant') {
        const content = message.message?.content
        if (!Array.isArray(content)) continue
        for (const block of content) {
          if (block.type === 'text' && typeof block.text === 'string') {
            // Prefer streamed partials when present; otherwise take the final text.
            if (text.length === 0) text = block.text
            else if (!text.endsWith(block.text) && block.text.startsWith(text)) text = block.text
            else if (text.length === 0) text = block.text
          } else if (block.type === 'tool_use') {
            if (text.length > 0) {
              yield* emitTextBlock(text, blockIndex, 'text')
              blockIndex += 1
              text = ''
            }
            const line = activityLine(block)
            yield* emitTextBlock(line, blockIndex, 'reasoning')
            blockIndex += 1
          }
        }
        continue
      }

      if (message?.type === 'result') {
        sawResult = true
        if (message.subtype && message.subtype !== 'success') {
          if (resume && /resume|session/i.test(String(message.errors?.join?.(' ') ?? message.subtype))) {
            resumeFailed = true
          }
        }
        if (typeof message.usage === 'object' && message.usage) {
          pendingUsage = {
            inputTokens: message.usage.input_tokens ?? message.usage.inputTokens ?? 0,
            outputTokens: message.usage.output_tokens ?? message.usage.outputTokens ?? 0,
          }
        }
        if (typeof message.result === 'string' && message.result.length > 0 && text.length === 0) {
          text = message.result
        }
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    if (resume && /resume|session/i.test(message)) {
      resumeFailed = true
      resumeNotice = `Claude session resume failed; started a fresh session.\n\n`
      if (agentId) setClaudeSessionId(agentId, undefined)
      // One retry without resume for Phase 0 continuity proof resilience.
      const retry = runQuery({
        prompt,
        options: { ...queryOptions, resume: undefined },
      })
      text = ''
      for await (const message2 of retry) {
        if (message2?.type === 'system' && message2?.subtype === 'init'
          && typeof message2.session_id === 'string') {
          claudeSessionId = message2.session_id
          if (agentId) setClaudeSessionId(agentId, claudeSessionId)
        }
        if (message2?.type === 'stream_event') {
          const event = message2.event
          if (event?.type === 'content_block_delta' && event.delta?.type === 'text_delta') {
            text += event.delta.text ?? ''
          }
        }
        if (message2?.type === 'assistant') {
          for (const block of message2.message?.content ?? []) {
            if (block.type === 'text' && typeof block.text === 'string' && text.length === 0) {
              text = block.text
            }
          }
        }
        if (message2?.type === 'result' && typeof message2.result === 'string' && text.length === 0) {
          text = message2.result
        }
      }
      sawResult = true
    } else if (options.signal?.aborted || controller.signal.aborted) {
      yield {
        type: 'finish',
        reason: {
          kind: 'aborted',
          failure: { code: 'aborted', message },
        },
      }
      return
    } else {
      throw error
    }
  } finally {
    options.signal?.removeEventListener('abort', onAbort)
  }

  if (resumeFailed && !resumeNotice) {
    resumeNotice = `Claude session resume failed; started a fresh session.\n\n`
  }

  const finalText = `${resumeNotice}${text}`
  if (finalText.length > 0) {
    yield* emitTextBlock(finalText, blockIndex, 'text')
    blockIndex += 1
  } else if (!sawResult) {
    throw new Error('superagent-claude: Claude Code returned no result')
  }

  if (pendingUsage) {
    yield { type: 'usage', usage: pendingUsage }
  }
  yield { type: 'finish', reason: { kind: 'stop' } }
}
