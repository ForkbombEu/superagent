import { createMCPClient, type MCPClient } from '@ai-sdk/mcp';
import { StdioMCPTransport } from '@ai-sdk/mcp/mcp-stdio';
import type { ToolSet } from 'ai';
import { spawn } from 'node:child_process';
import { accessSync, constants } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { defaultRepoRoot } from './repo-fs.js';

export type RepoMcpHandle = {
  tools: () => Promise<ToolSet>;
  close: () => Promise<void>;
  indexed: boolean;
};

function resolveCodebaseMemoryBin(): string {
  const require = createRequire(import.meta.url);
  try {
    const pkgJson = require.resolve('codebase-memory-mcp/package.json');
    const bin = join(dirname(pkgJson), 'bin.js');
    accessSync(bin, constants.R_OK);
    return bin;
  } catch {
    return 'codebase-memory-mcp';
  }
}

export async function indexRepository(
  repoRoot: string,
  bin = resolveCodebaseMemoryBin(),
): Promise<void> {
  const payload = JSON.stringify({
    repo_path: repoRoot,
    name: 'superagent-console',
    mode: 'fast',
  });
  await new Promise<void>((resolve, reject) => {
    const child = spawn(
      process.execPath,
      [bin, 'cli', 'index_repository', payload],
      {
        stdio: ['ignore', 'pipe', 'pipe'],
        env: process.env,
      },
    );
    let stderr = '';
    child.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString('utf8');
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve();
      else {
        reject(
          new Error(
            `index_repository failed (${code}): ${stderr.slice(0, 2000)}`,
          ),
        );
      }
    });
  });
}

export async function startRepoMcp(
  repoRoot = defaultRepoRoot(),
  options: { skipIndex?: boolean; bin?: string } = {},
): Promise<RepoMcpHandle> {
  const bin = options.bin ?? resolveCodebaseMemoryBin();
  let indexed = false;
  if (!options.skipIndex) {
    try {
      await indexRepository(repoRoot, bin);
      indexed = true;
    } catch (err) {
      console.error(
        'console: codebase-memory index failed; filesystem tools remain available',
        err,
      );
    }
  }

  let client: MCPClient | null = null;
  let cached: ToolSet | null = null;

  try {
    client = await createMCPClient({
      transport: new StdioMCPTransport({
        command: process.execPath,
        args: [bin],
        env: process.env as Record<string, string>,
        cwd: repoRoot,
      }),
      clientName: 'superagent-console',
    });
  } catch (err) {
    console.error('console: codebase-memory MCP client failed to start', err);
  }

  return {
    indexed,
    async tools() {
      if (!client) return {};
      if (cached) return cached;
      try {
        cached = (await client.tools()) as ToolSet;
        return cached;
      } catch (err) {
        console.error('console: mcpClient.tools() failed', err);
        return {};
      }
    },
    async close() {
      cached = null;
      if (client) {
        await client.close().catch(() => {});
        client = null;
      }
    },
  };
}
