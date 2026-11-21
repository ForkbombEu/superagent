/**
 * Bridge Claude Agent SDK canUseTool → managed allow/deny + DSH approval /
 * userQuestions.
 */
import { anyRuleMatches } from './permissions.js'

/** File-mutating tools held for approval under Claude plan mode. */
const PLAN_HELD_TOOLS = new Set(['Edit', 'Write', 'NotebookEdit', 'MultiEdit'])

/**
 * Map Claude AskUserQuestion tool input to a DSH userQuestions.ask payload.
 * @param {Record<string, unknown>} input
 * @returns {{ questions: object[] }}
 */
export function mapAskUserQuestionInput(input) {
  const raw = Array.isArray(input.questions) ? input.questions : []
  const questions = raw.map((row, index) => {
    const question = row && typeof row === 'object' ? row : {}
    const id = typeof question.header === 'string' && question.header.length > 0
      ? question.header
      : `q${index + 1}`
    const options = Array.isArray(question.options)
      ? question.options
        .filter((opt) => opt && typeof opt === 'object' && typeof opt.label === 'string')
        .map((opt) => ({
          label: opt.label,
          ...(typeof opt.description === 'string' ? { description: opt.description } : {}),
        }))
      : undefined
    return {
      id,
      question: typeof question.question === 'string' ? question.question : id,
      ...(typeof question.header === 'string' ? { header: question.header } : {}),
      ...(options && options.length > 0 ? { options } : {}),
      ...(question.multiSelect === true ? { multiSelect: true } : {}),
    }
  })
  return { questions }
}

/**
 * Build Claude AskUserQuestion `answers` map from a DSH answer batch.
 * Keys are question text (Claude's convention).
 * @param {Record<string, unknown>} input
 * @param {{ answers?: { id: string, selected?: string[], custom?: string }[] }} dshAnswer
 * @returns {Record<string, string>}
 */
export function mapAskUserQuestionAnswers(input, dshAnswer) {
  const raw = Array.isArray(input.questions) ? input.questions : []
  const byId = new Map()
  for (const item of dshAnswer?.answers ?? []) {
    if (item && typeof item.id === 'string') byId.set(item.id, item)
  }
  /** @type {Record<string, string>} */
  const answers = {}
  raw.forEach((row, index) => {
    const question = row && typeof row === 'object' ? row : {}
    const id = typeof question.header === 'string' && question.header.length > 0
      ? question.header
      : `q${index + 1}`
    const text = typeof question.question === 'string' ? question.question : id
    const item = byId.get(id)
    if (!item) return
    const selected = Array.isArray(item.selected) ? item.selected.filter(Boolean) : []
    const custom = typeof item.custom === 'string' ? item.custom.trim() : ''
    const value = custom.length > 0
      ? (selected.length > 0 ? `${selected.join(', ')}, ${custom}` : custom)
      : selected.join(', ')
    if (value.length > 0) answers[text] = value
  })
  return answers
}

/**
 * @param {object} args
 * @param {{ request: (req: object) => Promise<string> }} [args.approval]
 * @param {{ ask: (req: object) => Promise<object> }} [args.userQuestions]
 * @param {unknown} args.agent
 * @param {readonly string[]} args.allowRules
 * @param {readonly string[]} args.denyRules
 * @param {'default' | 'plan' | string} [args.permissionMode]
 * @returns {import('@anthropic-ai/claude-agent-sdk').CanUseTool}
 */
export function createCanUseTool({
  approval,
  userQuestions,
  agent,
  allowRules,
  denyRules,
  permissionMode = 'default',
}) {
  return async (toolName, input, options) => {
    const toolUseID = options?.toolUseID
    const deny = (message) => ({
      behavior: /** @type {const} */ ('deny'),
      message,
      ...(toolUseID === undefined ? {} : { toolUseID }),
    })
    const allow = (extra = {}) => ({
      behavior: /** @type {const} */ ('allow'),
      ...(toolUseID === undefined ? {} : { toolUseID }),
      ...extra,
    })

    const safeInput = input && typeof input === 'object' ? input : {}

    if (anyRuleMatches(denyRules, toolName, safeInput)) {
      return deny(`Denied by managed policy (${toolName})`)
    }

    if (toolName === 'AskUserQuestion') {
      if (!userQuestions || typeof userQuestions.ask !== 'function') {
        return deny('AskUserQuestion is unavailable (no userQuestions bridge)')
      }
      try {
        const mapped = mapAskUserQuestionInput(safeInput)
        if (mapped.questions.length === 0) {
          return deny('AskUserQuestion requires at least one question')
        }
        const dshAnswer = await userQuestions.ask({
          agent,
          questions: mapped.questions,
          signal: options?.signal,
          ...(typeof toolUseID === 'string'
            ? { wait: { callId: toolUseID } }
            : {}),
        })
        const answers = mapAskUserQuestionAnswers(safeInput, dshAnswer)
        return allow({ updatedInput: { ...safeInput, answers } })
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        return deny(`AskUserQuestion failed: ${message}`)
      }
    }

    const onAllowList = anyRuleMatches(allowRules, toolName, safeInput)
    const holdForPlan = permissionMode === 'plan' && PLAN_HELD_TOOLS.has(toolName)
    if (onAllowList && !holdForPlan) {
      return allow()
    }

    // Deny-by-default for anything outside the allow list (or plan-held writes):
    // ask DSH, else deny.
    if (!approval || typeof approval.request !== 'function') {
      return deny(`No approval bridge for ${toolName}`)
    }

    const reason = options?.title
      ?? options?.displayName
      ?? options?.description
      ?? `${toolName} requires approval`

    let outcome
    try {
      outcome = await approval.request({
        agent,
        toolName,
        reason: String(reason),
        signal: options?.signal,
        ...(typeof toolUseID === 'string' ? { callId: toolUseID } : {}),
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      return deny(`Approval request failed: ${message}`)
    }

    if (outcome === 'allowed-once') return allow()
    if (outcome === 'cancelled') return deny('Approval cancelled')
    if (outcome === 'unavailable') return deny('Approval unavailable')
    return deny('Approval rejected')
  }
}
