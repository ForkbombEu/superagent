/**
 * Map DSH agent id → Claude Code session id. Atomic JSON write under the
 * DSH state directory.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

/**
 * @param {string} [dshHome]
 * @returns {string}
 */
export function defaultSessionsPath(dshHome = process.env.DSH_HOME || join(process.env.HOME || '', '.dsh')) {
  return join(dshHome, 'state', 'superagent', 'claude-sessions.json')
}

/**
 * @param {unknown} value
 * @returns {Record<string, string>}
 */
function asStore(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const out = {}
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === 'string' && entry.length > 0) out[key] = entry
  }
  return out
}

/**
 * @param {string} path
 * @returns {Record<string, string>}
 */
export function loadSessions(path) {
  try {
    return asStore(JSON.parse(readFileSync(path, 'utf8')))
  } catch (error) {
    if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') {
      return {}
    }
    throw error
  }
}

/**
 * Atomic replace of the sessions file.
 * @param {string} path
 * @param {Record<string, string>} store
 */
export function saveSessions(path, store) {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.${process.pid}.${Date.now()}.tmp`
  writeFileSync(tmp, `${JSON.stringify(store, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(tmp, path)
}

/**
 * @param {string} agentId
 * @param {string} [path]
 * @returns {string | undefined}
 */
export function getClaudeSessionId(agentId, path = defaultSessionsPath()) {
  if (typeof agentId !== 'string' || agentId.length === 0) return undefined
  return loadSessions(path)[agentId]
}

/**
 * Persist a Claude session id for a DSH agent. Empty claudeSessionId removes the mapping.
 * @param {string} agentId
 * @param {string | undefined} claudeSessionId
 * @param {string} [path]
 */
export function setClaudeSessionId(agentId, claudeSessionId, path = defaultSessionsPath()) {
  if (typeof agentId !== 'string' || agentId.length === 0) {
    throw new Error('superagent-claude: agent id is required to store a Claude session')
  }
  const store = loadSessions(path)
  if (typeof claudeSessionId === 'string' && claudeSessionId.length > 0) {
    store[agentId] = claudeSessionId
  } else {
    delete store[agentId]
  }
  saveSessions(path, store)
}
