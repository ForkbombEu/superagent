import { afterEach, describe, expect, it, vi } from 'vitest';
import { createOrchestratorClient, redactSecrets } from './orchestrator.js';


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('orchestrator client', () => {
  it('forwards the cookie on GET and never logs it', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const logs: unknown[][] = [];
    const log = vi.spyOn(console, 'log').mockImplementation((...args) => {
      logs.push(args);
    });
    const error = vi.spyOn(console, 'error').mockImplementation((...args) => {
      logs.push(args);
    });

    const client = createOrchestratorClient({
      baseUrl: 'http://dashboard:8080/orchestrator/api',
      cookie: 'dsh-session=secret-cookie-value',
      fetch: fetchMock as unknown as typeof fetch,
    });
    await expect(client.get('state')).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe('http://dashboard:8080/orchestrator/api/state');
    expect(init?.method).toBe('GET');
    expect((init?.headers as Record<string, string>).cookie).toBe(
      'dsh-session=secret-cookie-value',
    );
    expect(JSON.stringify(logs)).not.toContain('secret-cookie-value');
    log.mockRestore();
    error.mockRestore();
  });

  it('refuses non-GET methods', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const client = createOrchestratorClient({
      baseUrl: 'http://dashboard:8080/orchestrator/api',
      cookie: 'c=1',
      fetch: fetchMock as unknown as typeof fetch,
    });
    await expect(client.request('POST', 'state')).rejects.toThrow(/GET only/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });


  it('redacts settings keys that look like secrets', () => {
    expect(
      redactSecrets({
        githubToken: 'ghs_abc',
        api_key: 'sk-1',
        model: 'opus',
        nested: { password: 'x', name: 'ok' },
      }),
    ).toEqual({
      githubToken: '[redacted]',
      api_key: '[redacted]',
      model: 'opus',
      nested: { password: '[redacted]', name: 'ok' },
    });
  });
});
