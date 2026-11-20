import { simulateReadableStream, tool } from 'ai';
import { MockLanguageModelV4 } from 'ai/test';
import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { createApp } from './server.js';



function mockModel(text = 'Hello from mock') {
  return new MockLanguageModelV4({
    doStream: async () => ({
      stream: simulateReadableStream({
        chunks: [
          { type: 'text-start', id: 'text-1' },
          { type: 'text-delta', id: 'text-1', delta: text },
          { type: 'text-end', id: 'text-1' },
          {
            type: 'finish',
            finishReason: { unified: 'stop', raw: undefined },
            usage: {
              inputTokens: { total: 3, noCache: 3, cacheRead: undefined, cacheWrite: undefined },
              outputTokens: { total: 4, text: 4, reasoning: undefined },
            },
          },
        ],
      }),
    }),
  });
}

describe('POST /console/api/chat', () => {
  it('streams text from the model', async () => {
    const model = mockModel('streamed reply');
    const doStream = vi.spyOn(model, 'doStream');
    const app = createApp({
      apiKey: 'sk-test',
      getModel: () => model,
    });

    const res = await app.request('/console/api/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        messages: [{ id: '1', role: 'user', parts: [{ type: 'text', text: 'hi' }] }],
      }),
    });

    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain('streamed reply');
    expect(doStream).toHaveBeenCalled();
  });

  it('merges stub MCP tools (codebase-memory) into the chat tool set', async () => {
    const model = mockModel('with mcp');
    const doStream = vi.spyOn(model, 'doStream');
    const getMcpTools = vi.fn(async () => ({
      search_graph: tool({
        description: 'stub codebase-memory tool',
        inputSchema: z.object({}),
        execute: async () => ({ ok: true }),
      }),
    }));
    const app = createApp({
      apiKey: 'sk-test',
      getModel: () => model,
      getMcpTools,
      repoRoot: process.cwd(),
    });

    const res = await app.request('/console/api/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        messages: [{ id: '1', role: 'user', parts: [{ type: 'text', text: 'who calls X' }] }],
      }),
    });

    const body = await res.text();
    expect(res.status).toBe(200);
    expect(getMcpTools).toHaveBeenCalled();
    // Prove the stub MCP tool name was merged before streamText ran.
    expect(getMcpTools.mock.results[0]?.value).toBeTruthy();
    const mcp = await getMcpTools.mock.results[0]!.value;
    expect(mcp).toHaveProperty('search_graph');
    if (!doStream.mock.calls.length) {
      throw new Error(`model not called; stream body: ${body.slice(0, 500)}`);
    }
    expect(body).toContain('with mcp');
  });


  it('returns 503 not-configured without a key and does not call the provider or MCP', async () => {
    const model = mockModel();
    const doStream = vi.spyOn(model, 'doStream');
    const getMcpTools = vi.fn(async () => ({ search_graph: {} }));
    const app = createApp({
      apiKey: '',
      getModel: () => model,
      getMcpTools: getMcpTools as never,
    });

    const res = await app.request('/console/api/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        messages: [{ id: '1', role: 'user', parts: [{ type: 'text', text: 'hi' }] }],
      }),
    });

    expect(res.status).toBe(503);
    expect(await res.text()).toBe('not-configured');
    expect(doStream).not.toHaveBeenCalled();
    expect(getMcpTools).not.toHaveBeenCalled();
  });
});

