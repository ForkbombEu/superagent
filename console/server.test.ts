import { describe, expect, it } from 'vitest';
import { createApp } from './server.js';


describe('console service', () => {
  it('answers health', async () => {
    const res = await createApp({ apiKey: '' }).request('/console/api/health');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it('reports when chat is not configured', async () => {
    const res = await createApp({ apiKey: '' }).request('/console/api/config');
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ configured: false });
  });
});
