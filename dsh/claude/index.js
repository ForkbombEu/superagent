/**
 * Host plugin: register the superagent-claude LLM adapter.
 */
import { LlmAdapter, ReasoningEffortId } from '@deepseek-ai/dsh-llm'
import { PROVIDER_ID, resolveAgent, runTurn } from './turn.js'
import { canonicalModelId } from './route.js'

export const name = 'superagent-claude'
export const inject = ['llm', 'agents', 'agentPresets', 'approval', 'userQuestions']

const MODELS = [
  {
    id: 'opus',
    name: 'Opus',
    description: 'Claude Opus via Claude Code',
    contextWindow: 200_000,
  },
  {
    id: 'opusplan',
    name: 'Opus (plan)',
    description: 'Claude Opus in plan mode via Claude Code',
    contextWindow: 200_000,
  },
  {
    id: 'sonnet',
    name: 'Sonnet',
    description: 'Claude Sonnet via Claude Code',
    contextWindow: 200_000,
  },
  {
    id: 'haiku',
    name: 'Haiku',
    description: 'Claude Haiku via Claude Code',
    contextWindow: 200_000,
  },
]

const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']

class SuperagentClaudeAdapter extends LlmAdapter {
  /**
   * @param {{ get: Function, currentInitiator?: Function }} agents
   * @param {{ approval?: { request: Function }, userQuestions?: { ask: Function } }} bridges
   */
  constructor(agents, bridges = {}) {
    super()
    this.agents = agents
    this.approval = bridges.approval
    this.userQuestions = bridges.userQuestions
  }

  providerInfo(provider) {
    return {
      id: provider,
      name: 'Claude (Superagent)',
      description: 'Claude Code as the conversation agent',
    }
  }

  providerRetryPolicy() {
    return {
      mode: 'normal',
      maxRetries: 0,
      retryableCodes: [],
      initialDelayMs: 0,
      maxDelayMs: 0,
      jitterRatio: 0,
    }
  }

  async listModels(provider) {
    return MODELS.map((model) => ({
      provider,
      id: model.id,
      name: model.name,
      description: model.description,
      inputModalities: ['text'],
    }))
  }

  async resolveModel(provider, model) {
    const id = canonicalModelId(model)
    const known = MODELS.find((row) => row.id === id)
    return {
      provider,
      id,
      name: known?.name ?? `Claude Code ${id}`,
      description: known?.description,
      context: { contextWindow: known?.contextWindow ?? 200_000 },
      inputModalities: ['text'],
      reasoning: {
        efforts: EFFORTS.map((effort) => ({
          id: ReasoningEffortId(effort),
          name: effort,
        })),
      },
    }
  }

  async *stream(options) {
    if (options.purpose === 'session-title' || options.purpose === 'compaction') {
      throw new Error(`superagent-claude: auxiliary ${options.purpose} calls are rejected in the MVP`)
    }
    if (options.purpose !== undefined) {
      throw new Error(`superagent-claude: auxiliary ${options.purpose} calls are not supported`)
    }
    const agent = resolveAgent(this.agents, options)
    yield* runTurn({
      options,
      agent,
      approval: this.approval,
      userQuestions: this.userQuestions,
    })
  }
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 */
export function apply(ctx) {
  ctx.llm.registerAdapter(
    [PROVIDER_ID],
    new SuperagentClaudeAdapter(ctx.agents, {
      approval: ctx.approval,
      userQuestions: ctx.userQuestions,
    }),
  )
}
