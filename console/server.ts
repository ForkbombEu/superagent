import { anthropic } from '@ai-sdk/anthropic';
import { serve } from '@hono/node-server';
import { serveStatic } from '@hono/node-server/serve-static';
import {
  convertToModelMessages,
  type LanguageModel,
  streamText,
  type ToolSet,
  type UIMessage,
} from 'ai';
import { Hono } from 'hono';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { startRepoMcp, type RepoMcpHandle } from './lib/mcp.js';
import { createOrchestratorClient } from './lib/orchestrator.js';
import { defaultRepoRoot } from './lib/repo-fs.js';
import { createRepoTools } from './lib/repo-tools.js';
import { createConsoleTools } from './lib/tools.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const webDist = existsSync(join(__dirname, 'web', 'dist'))
  ? join(__dirname, 'web', 'dist')
  : join(__dirname, '..', 'web', 'dist');
const promptsDir = existsSync(join(__dirname, 'prompts'))
  ? join(__dirname, 'prompts')
  : join(__dirname, '..', 'prompts');

/** Current Opus id from the @ai-sdk/anthropic / Claude platform model list (claude-opus-5-5). */
export const DEFAULT_CONSOLE_MODEL = 'claude-opus-5-5';

export type ConsoleDeps = {
  apiKey?: string;
  modelId?: string;
  systemPrompt?: string;
  getModel?: (modelId: string) => LanguageModel;
  /** Process-lifetime MCP tools (typically codebase-memory). */
  getMcpTools?: () => Promise<ToolSet> | ToolSet;
  repoRoot?: string;
  apiUrl?: string;
  publicHost?: string;
  fetchImpl?: typeof fetch;
};

function loadSystemPrompt(): string {
  return readFileSync(join(promptsDir, 'system.md'), 'utf8');
}

export function createApp(deps: ConsoleDeps = {}): Hono {
  const app = new Hono();
  const apiKey = deps.apiKey ?? process.env.ANTHROPIC_API_KEY ?? '';
  const modelId =
    deps.modelId ?? process.env.SUPERAGENT_CONSOLE_MODEL ?? DEFAULT_CONSOLE_MODEL;
  const systemPrompt = deps.systemPrompt ?? loadSystemPrompt();
  const getModel = deps.getModel ?? ((id: string) => anthropic(id));
  const repoRoot = deps.repoRoot ?? defaultRepoRoot();

  app.get('/console/api/health', (c) => c.json({ ok: true }));

  app.get('/console/api/config', (c) =>
    c.json({
      configured: Boolean(apiKey),
      model: modelId,
    }),
  );

  app.post('/console/api/chat', async (c) => {
    if (!apiKey) {
      return c.text('not-configured', 503);
    }

    const body = await c.req.json<{ messages: UIMessage[] }>();
    const cookie = c.req.header('cookie') ?? '';
    const orchestrator = createConsoleTools(
      createOrchestratorClient({
        cookie,
        baseUrl: deps.apiUrl,
        publicHost: deps.publicHost,
        fetch: deps.fetchImpl,
      }),
    );
    const repo = createRepoTools(repoRoot);
    const mcpTools = deps.getMcpTools ? await deps.getMcpTools() : {};
    const tools: ToolSet = { ...orchestrator, ...repo, ...mcpTools };

    const messages = await convertToModelMessages(body.messages ?? [], { tools });
    const result = streamText({
      abortSignal: c.req.raw.signal,
      model: getModel(modelId),
      system: systemPrompt,
      messages,
      tools,
    });
    return result.toUIMessageStreamResponse();
  });

  app.use(
    '/console/*',
    serveStatic({
      root: webDist,
      rewriteRequestPath: (path) => {
        const stripped = path.replace(/^\/console\/?/, '/');
        return stripped === '/' ? '/index.html' : stripped;
      },
    }),
  );

  app.get('/console', (c) => c.redirect('/console/'));

  app.get('/console/*', (c) => {
    try {
      const html = readFileSync(join(webDist, 'index.html'), 'utf8');
      return c.html(html);
    } catch {
      return c.text('console web build missing; run npm run build', 503);
    }
  });

  return app;
}

export const app = createApp();

const port = Number(process.env.PORT || 3000);
const entry = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';

async function main() {
  let mcp: RepoMcpHandle | null = null;
  const repoRoot = defaultRepoRoot();
  if (existsSync(repoRoot)) {
    mcp = await startRepoMcp(repoRoot);
  } else {
    console.warn(`console: repo root missing at ${repoRoot}; MCP index skipped`);
  }

  const listening = createApp({
    getMcpTools: mcp ? () => mcp!.tools() : async () => ({}),
    repoRoot,
  });

  serve({ fetch: listening.fetch, port, hostname: '0.0.0.0' }, (info) => {
    console.log(`console listening on ${info.port}`);
  });
}

if (import.meta.url === entry) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
