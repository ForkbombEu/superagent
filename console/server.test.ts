import { describe, expect, it } from 'vitest';
import { app } from './server.ts';

describe('console service', () => {
  it('answers health', async () => {
    const res = await app.request('/console/api/health');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });
});
