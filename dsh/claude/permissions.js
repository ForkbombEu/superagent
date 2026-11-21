/**
 * Claude Code permission-rule matching for the chat managed allow/deny lists.
 * Deny-by-default hosts use this before asking DSH approval.
 */
import { readFileSync as defaultReadFileSync } from 'node:fs'

/**
 * Parse a Claude permission rule string.
 * @param {string} rule
 * @returns {{ tool: string, content: string | undefined, toolGlob: boolean }}
 */
export function parsePermissionRule(rule) {
  const open = rule.indexOf('(')
  if (open > 0 && rule.endsWith(')')) {
    return {
      tool: rule.slice(0, open),
      content: rule.slice(open + 1, -1),
      toolGlob: false,
    }
  }
  if (rule.endsWith('*') && !rule.includes('(')) {
    return { tool: rule.slice(0, -1), content: undefined, toolGlob: true }
  }
  return { tool: rule, content: undefined, toolGlob: false }
}

/**
 * @param {string} pattern Claude content pattern (`git:*`, `gh pr view:*`, or exact)
 * @param {string} value
 * @returns {boolean}
 */
export function matchContentPattern(pattern, value) {
  if (pattern.endsWith(':*')) {
    const head = pattern.slice(0, -2) // `git` or `gh pr view`
    if (value === head) return true
    if (value.startsWith(`${head} `)) return true
    if (value.startsWith(`${head}\t`)) return true
    return false
  }
  if (pattern.endsWith('*')) {
    return value.startsWith(pattern.slice(0, -1))
  }
  return value === pattern
}

/**
 * Whether a single rule matches this tool invocation.
 * @param {string} rule
 * @param {string} toolName
 * @param {Record<string, unknown>} [input]
 * @returns {boolean}
 */
export function ruleMatches(rule, toolName, input = {}) {
  if (typeof rule !== 'string' || rule.length === 0) return false
  const parsed = parsePermissionRule(rule)

  if (parsed.toolGlob) {
    return toolName.startsWith(parsed.tool)
  }

  if (parsed.tool !== toolName) return false
  if (parsed.content === undefined) return true

  if (toolName === 'Bash' || toolName === 'PowerShell') {
    const command = typeof input.command === 'string' ? input.command.trim() : ''
    return matchContentPattern(parsed.content, command)
  }

  const path = typeof input.file_path === 'string'
    ? input.file_path
    : typeof input.path === 'string' ? input.path : ''
  if (path) return matchContentPattern(parsed.content, path)

  return matchContentPattern(parsed.content, '')
}

/**
 * @param {readonly string[]} rules
 * @param {string} toolName
 * @param {Record<string, unknown>} [input]
 * @returns {boolean}
 */
export function anyRuleMatches(rules, toolName, input = {}) {
  if (!Array.isArray(rules)) return false
  for (const rule of rules) {
    if (ruleMatches(rule, toolName, input)) return true
  }
  return false
}

/**
 * Load allow/deny arrays from a Claude managed-settings JSON file.
 * @param {string} settingsPath
 * @param {{ readFileSync?: typeof defaultReadFileSync }} [deps]
 * @returns {{ allow: string[], deny: string[] }}
 */
export function loadPermissionRules(settingsPath, deps = {}) {
  const readFile = deps.readFileSync ?? defaultReadFileSync
  const parsed = JSON.parse(readFile(settingsPath, 'utf8'))
  const permissions = parsed?.permissions && typeof parsed.permissions === 'object'
    ? parsed.permissions
    : {}
  return {
    allow: Array.isArray(permissions.allow) ? permissions.allow.map(String) : [],
    deny: Array.isArray(permissions.deny) ? permissions.deny.map(String) : [],
  }
}
