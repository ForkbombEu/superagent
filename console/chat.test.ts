import { simulateReadableStream } from 'ai';
import { MockLanguageModelV4 } from 'ai/test';
import { describe, expect, it, vi } from 'vitest';
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

  it('returns 503 not-configured without a key and does not call the provider', async () => {
    const model = mockModel();
    const doStream = vi.spyOn(model, 'doStream');
    const app = createApp({
      apiKey: '',
      getModel: () => model,
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
  });
});
