import { afterEach, describe, expect, it, vi } from 'vitest';
import { createOrchestratorClient } from './orchestrator.js';
import { createConsoleTools } from './tools.js';


afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function clientWith(handler: (path: string) => unknown) {
  const fetchMock = vi.fn(async (url: string | URL) => {
    const path = String(url).replace(/^.*\/orchestrator\/api\//, '');
    return new Response(JSON.stringify(handler(path)), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  return createOrchestratorClient({
    baseUrl: 'http://dashboard:8080/orchestrator/api',
    cookie: 'dsh-session=test',
    fetch: fetchMock as unknown as typeof fetch,
  });
}


describe('console tools', () => {
  it('list_tickets calls GET tickets', async () => {
    const client = clientWith((path) => {
      expect(path).toBe('tickets');
      return { items: [{ id: 't1' }] };
    });
    const tools = createConsoleTools(client);
    const result = await tools.list_tickets.execute!({}, { toolCallId: '1', messages: [] });
    expect(result).toEqual({ items: [{ id: 't1' }] });
  });

  it('get_ticket calls GET tickets/:id', async () => {
    const client = clientWith((path) => {
      expect(path).toBe('tickets/42');
      return { id: '42', waiting: 'review' };
    });
    const tools = createConsoleTools(client);
    const result = await tools.get_ticket.execute!(
      { id: '42' },
      { toolCallId: '1', messages: [] },
    );
    expect(result).toEqual({ id: '42', waiting: 'review' });
  });

  it('get_settings redacts secrets', async () => {
    const client = clientWith(() => ({
      blocks: { github: { value: { token: 'secret', repo: 'a/b' } } },
    }));
    const tools = createConsoleTools(client);
    const result = await tools.get_settings.execute!({}, { toolCallId: '1', messages: [] });
    expect(result).toEqual({
      blocks: { github: { value: { token: '[redacted]', repo: 'a/b' } } },
    });
  });

  it('get_prompts calls GET prompts', async () => {
    const client = clientWith((path) => {
      expect(path).toBe('prompts');
      return { revision: 1 };
    });
    const tools = createConsoleTools(client);
    const result = await tools.get_prompts.execute!({}, { toolCallId: '1', messages: [] });
    expect(result).toEqual({ revision: 1 });
  });

  it('draft_issue returns markdown and writes nothing', async () => {
    const fetchMock = vi.fn();
    const client = createOrchestratorClient({
      baseUrl: 'http://dashboard:8080/orchestrator/api',
      cookie: 'c=1',
      fetch: fetchMock as unknown as typeof fetch,
    });
    const tools = createConsoleTools(client);
    const result = await tools.draft_issue.execute!(
      {
        title: 'Add console health check',
        what: 'Expose a readiness probe for the console service so Coolify can monitor it.',
        acceptance: ['GET /console/api/health returns 200', 'Compose healthcheck uses that route'],
      },
      { toolCallId: '1', messages: [] },
    );
    expect(result.title).toBe('Add console health check');
    expect(result.body).toContain('@forkboteu');
    expect(result.body).toContain('## Acceptance criteria');
    expect(result.body).toContain('GET /console/api/health returns 200');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

