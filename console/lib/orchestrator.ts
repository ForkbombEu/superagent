/**
 * GET-only client for the Superagent management API via the dashboard Caddy
 * route. Always forwards the caller's DSH session cookie; never logs it.
 *
 * Host header: the browser sends the public dashboard hostname; from this
 * container the URL host is `dashboard`, which DSH's forward_auth check on
 * GET / may reject when --trusted-host is only the public FQDN. When
 * SUPERAGENT_CONSOLE_PUBLIC_HOST or SERVICE_FQDN_DASHBOARD is set, we send
 * that as the HTTP Host so the cookie gate still admits the request. Caddy
 * then rewrites Host to 127.0.0.1:3082 for the management API upstream.
 */
import http from 'node:http';
import https from 'node:https';
import { URL } from 'node:url';

export type OrchestratorOptions = {
  baseUrl?: string;
  cookie: string;
  /** Public dashboard hostname for forward_auth when the compose Host fails. */
  publicHost?: string;
  fetch?: typeof fetch;
};

const SECRET_KEY = /(token|key|secret|password)/i;

export function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      out[key] = SECRET_KEY.test(key) ? '[redacted]' : redactSecrets(child);
    }
    return out;
  }
  return value;
}

function nodeGet(url: URL, headers: Record<string, string>): Promise<Response> {
  const lib = url.protocol === 'https:' ? https : http;
  return new Promise((resolve, reject) => {
    const req = lib.request(
      {
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (url.protocol === 'https:' ? 443 : 80),
        path: `${url.pathname}${url.search}`,
        method: 'GET',
        headers,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const body = Buffer.concat(chunks);
          resolve(
            new Response(body, {
              status: res.statusCode ?? 0,
              headers: res.headers as HeadersInit,
            }),
          );
        });
      },
    );
    req.on('error', reject);
    req.end();
  });
}

export function createOrchestratorClient(options: OrchestratorOptions) {
  const baseUrl = (options.baseUrl ?? process.env.SUPERAGENT_CONSOLE_API_URL ??
    'http://dashboard:8080/orchestrator/api').replace(/\/$/, '');
  const publicHost =
    options.publicHost ??
    process.env.SUPERAGENT_CONSOLE_PUBLIC_HOST ??
    process.env.SERVICE_FQDN_DASHBOARD ??
    '';
  const cookie = options.cookie;
  const customFetch = options.fetch;

  async function request(method: string, path: string): Promise<unknown> {
    if (method.toUpperCase() !== 'GET') {
      throw new Error('orchestrator client is GET only');
    }
    const url = new URL(`${baseUrl}/${path.replace(/^\//, '')}`);
    const headers: Record<string, string> = {
      cookie,
      'x-orchestrator-request': '1',
      accept: 'application/json',
    };
    if (publicHost) {
      headers.host = publicHost;
    }

    const response = customFetch
      ? await customFetch(url, { method: 'GET', headers })
      : await nodeGet(url, headers);

    if (!response.ok) {
      throw new Error(`orchestrator GET ${path} failed: ${response.status}`);
    }
    return response.json();
  }

  return {
    request,
    get(path: string) {
      return request('GET', path);
    },
  };
}

export type OrchestratorClient = ReturnType<typeof createOrchestratorClient>;
