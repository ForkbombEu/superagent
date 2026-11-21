/**
 * Build the Claude Code child environment: scrub credential-shaped parent
 * env, then layer explicit credentials from the Superagent secrets file.
 */
import { readFileSync } from 'node:fs'

/** Same shape as `@deepseek-ai/dsh-subprocess` scrubbedParentEnv. */
export const SENSITIVE_ENV_PATTERN = /KEY|PASSWORD|SECRET|TOKEN/i
export const DSH_ENV_PREFIX = 'DSH_'

export const DEFAULT_SECRETS_PATH = '/run/superagent-secrets/claude-review.json'
export const MANAGED_SETTINGS_PATH = '/etc/claude-code-chat'

/** Credential keys the child is allowed to receive from the secrets file. */
export const CLAUDE_CREDENTIAL_KEYS = Object.freeze([
  'CLAUDE_CODE_OAUTH_TOKEN',
  'ANTHROPIC_API_KEY',
])

/**
 * Copy `process.env` without credential-shaped or DSH_* names.
 * @param {NodeJS.ProcessEnv} [source]
 * @returns {Record<string, string>}
 */
export function scrubbedParentEnv(source = process.env) {
  const env = {}
  for (const [key, value] of Object.entries(source)) {
    if (value === undefined) continue
    if (SENSITIVE_ENV_PATTERN.test(key)) continue
    if (key.toUpperCase().startsWith(DSH_ENV_PREFIX)) continue
    env[key] = value
  }
  return env
}

/**
 * Read Claude credential env from the secrets JSON (`{ env: { ... } }`).
 * @param {string} [path]
 * @returns {Partial<Record<'CLAUDE_CODE_OAUTH_TOKEN' | 'ANTHROPIC_API_KEY', string>>}
 */
export function readClaudeCredentials(path = DEFAULT_SECRETS_PATH) {
  let raw
  try {
    raw = readFileSync(path, 'utf8')
  } catch (error) {
    throw new Error(`superagent-claude: missing secrets file at ${path}`, { cause: error })
  }
  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    throw new Error(`superagent-claude: secrets file is not JSON: ${path}`, { cause: error })
  }
  const fromFile = parsed && typeof parsed === 'object' && parsed.env && typeof parsed.env === 'object'
    ? parsed.env
    : {}
  const credentials = {}
  for (const key of CLAUDE_CREDENTIAL_KEYS) {
    const value = fromFile[key]
    if (typeof value === 'string' && value.length > 0) credentials[key] = value
  }
  if (Object.keys(credentials).length === 0) {
    throw new Error('superagent-claude: secrets file has no Claude credential')
  }
  return credentials
}

/**
 * Scrubbed parent env + Claude credentials + chat managed-settings path.
 * Only the explicit Claude credential keys from the secrets file are re-added.
 * @param {{ secretsPath?: string, sourceEnv?: NodeJS.ProcessEnv }} [options]
 * @returns {Record<string, string>}
 */
export function buildClaudeEnv(options = {}) {
  const {
    secretsPath = DEFAULT_SECRETS_PATH,
    sourceEnv = process.env,
  } = options
  const credentials = readClaudeCredentials(secretsPath)
  return {
    ...scrubbedParentEnv(sourceEnv),
    ...credentials,
    CLAUDE_CODE_MANAGED_SETTINGS_PATH: MANAGED_SETTINGS_PATH,
  }
}
