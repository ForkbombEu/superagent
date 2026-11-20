import { serve } from '@hono/node-server';
import { serveStatic } from '@hono/node-server/serve-static';
import { Hono } from 'hono';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const webDist = existsSync(join(__dirname, 'web', 'dist'))
  ? join(__dirname, 'web', 'dist')
  : join(__dirname, '..', 'web', 'dist');

export const app = new Hono();

app.get('/console/api/health', (c) => c.json({ ok: true }));

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

const port = Number(process.env.PORT || 3000);
const entry = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';

if (import.meta.url === entry) {
  serve({ fetch: app.fetch, port, hostname: '0.0.0.0' }, (info) => {
    console.log(`console listening on ${info.port}`);
  });
}
