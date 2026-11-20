import { tool } from 'ai';
import { z } from 'zod';
import {
  defaultRepoRoot,
  globRepo,
  grepRepoPreferRg,
  readRepoFile,
} from './repo-fs.js';

export function createRepoTools(repoRoot = defaultRepoRoot()) {
  return {
    read_file: tool({
      description:
        'Read a UTF-8 file under SUPERAGENT_CONSOLE_REPO_ROOT (this image’s baked Superagent tree). Refuses path escape.',
      inputSchema: z.object({
        path: z.string().describe('Path relative to the repo root'),
      }),
      execute: async ({ path }) => ({
        path,
        content: readRepoFile(repoRoot, path),
      }),
    }),
    glob: tool({
      description:
        'List files under SUPERAGENT_CONSOLE_REPO_ROOT matching a glob pattern. Refuses path escape.',
      inputSchema: z.object({
        pattern: z.string().describe('Glob pattern, e.g. docs/**/*.md'),
        cwd: z
          .string()
          .optional()
          .describe('Optional subdirectory relative to the repo root'),
      }),
      execute: async ({ pattern, cwd }) => globRepo(repoRoot, { pattern, cwd }),
    }),
    grep: tool({
      description:
        'Search file contents under SUPERAGENT_CONSOLE_REPO_ROOT. Refuses path escape. Prefer MCP graph tools for structure.',
      inputSchema: z.object({
        pattern: z.string().describe('JavaScript / ripgrep regex'),
        path: z
          .string()
          .optional()
          .describe('Optional file or directory relative to the repo root'),
        caseInsensitive: z.boolean().optional(),
      }),
      execute: async ({ pattern, path, caseInsensitive }) =>
        grepRepoPreferRg(repoRoot, { pattern, path, caseInsensitive }),
    }),
  };
}
