import { anthropic } from '@ai-sdk/anthropic';
import { serve } from '@hono/node-server';
import { serveStatic } from '@hono/node-server/serve-static';
import {
  convertToModelMessages,
  type LanguageModel,
  streamText,
  type UIMessage,
} from 'ai';
import { Hono } from 'hono';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

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
  const getModel =
    deps.getModel ?? ((id: string) => anthropic(id));

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
    const messages = await convertToModelMessages(body.messages ?? []);
    const result = streamText({
      abortSignal: c.req.raw.signal,
      model: getModel(modelId),
      system: systemPrompt,
      messages,
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

if (import.meta.url === entry) {
  serve({ fetch: app.fetch, port, hostname: '0.0.0.0' }, (info) => {
    console.log(`console listening on ${info.port}`);
  });
}
