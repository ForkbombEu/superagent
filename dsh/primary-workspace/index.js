/**
 * Register `$HOME/src/<repo>` (from GITHUB_REPOSITORY) as the primary DSH
 * Workspace so New Session → Claude opens on the Superagent checkout instead of
 * an accidental `/tmp` folder. Ticket workers keep creating their own worktree
 * workspaces via prepare-worktree; those paths are left alone.
 */
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const name = 'superagent-primary-workspace'
export const inject = ['workspaceRegistry']

/** Derive the absolute checkout path for OWNER/REPO, or null when unset/invalid. */
export function checkoutPath(repository = process.env.GITHUB_REPOSITORY, home = homedir()) {
  if (typeof repository !== 'string' || !repository.includes('/')) return null
  const repo = repository.slice(repository.indexOf('/') + 1)
  if (!repo || repo.includes('/') || repo === '.' || repo === '..') return null
  return join(home, 'src', repo)
}

/**
 * Ensure `path` is registered and first in the durable Workspace order.
 * Drops a bare `/tmp` registration so recent-session New Session does not
 * keep landing there after an earlier mistaken pick.
 */
export async function ensurePrimaryWorkspace(registry, path) {
  const workspace = await registry.create(path)
  const ordered = registry.list()
  if (ordered[0]?.id !== workspace.id) {
    await registry.insertBefore(workspace.id, ordered[0].id)
  }
  for (const other of registry.list()) {
    if (other.id === workspace.id) continue
    if (other.path === '/tmp' || other.path === '/private/tmp') {
      await registry.delete(other.id)
    }
  }
  return workspace
}

export function apply(ctx) {
  const path = checkoutPath()
  if (path === null) {
    ctx.logger?.warn?.('superagent-primary-workspace: GITHUB_REPOSITORY is missing or invalid; skip primary workspace')
    return
  }
  if (!existsSync(path)) {
    ctx.logger?.warn?.(`superagent-primary-workspace: checkout missing at ${path}; skip primary workspace`)
    return
  }
  void ensurePrimaryWorkspace(ctx.workspaceRegistry, path)
    .then((workspace) => {
      ctx.logger?.info?.(`superagent-primary-workspace: primary workspace ${workspace.path}`)
    })
    .catch((error) => {
      ctx.logger?.warn?.(`superagent-primary-workspace: primary workspace failed: ${error}`)
    })
}
