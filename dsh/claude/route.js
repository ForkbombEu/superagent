/**
 * Preset route: rewrite agent/request to the superagent-claude provider.
 */
export const name = 'superagent-claude-route'
export const inject = []

export const PROVIDER_ID = 'superagent-claude'

const MODEL_ALIASES = {
  default: 'sonnet',
  opus: 'opus',
  sonnet: 'sonnet',
  haiku: 'haiku',
}

/**
 * @param {string | undefined} model
 * @returns {string}
 */
export function canonicalModelId(model) {
  if (typeof model !== 'string' || model.length === 0) return 'sonnet'
  const lower = model.toLowerCase()
  return MODEL_ALIASES[lower] ?? model
}

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx
 * @param {{ model?: string }} [config]
 */
export function apply(ctx, config = {}) {
  ctx.on('agent/request', async (_payload, next) => {
    const upstream = await next()
    return {
      ...upstream,
      provider: PROVIDER_ID,
      model: canonicalModelId(config.model ?? upstream.model ?? 'sonnet'),
    }
  })
}
