import { afterEach, describe, expect, it } from 'vitest';
import {
  createApp,
  DEFAULT_CONSOLE_MODEL,
  resolveConsoleModelId,
} from './server.js';

describe('console service', () => {
  const originalModel = process.env.SUPERAGENT_CONSOLE_MODEL;

  afterEach(() => {
    if (originalModel === undefined) {
      delete process.env.SUPERAGENT_CONSOLE_MODEL;
    } else {
      process.env.SUPERAGENT_CONSOLE_MODEL = originalModel;
    }
  });

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

  it('defaults model when SUPERAGENT_CONSOLE_MODEL is empty', async () => {
    process.env.SUPERAGENT_CONSOLE_MODEL = '';
    const res = await createApp({ apiKey: '' }).request('/console/api/config');
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      model: DEFAULT_CONSOLE_MODEL,
    });
  });

  it('defaults model when SUPERAGENT_CONSOLE_MODEL is whitespace', async () => {
    process.env.SUPERAGENT_CONSOLE_MODEL = '  \t  ';
    const res = await createApp({ apiKey: '' }).request('/console/api/config');
    expect(await res.json()).toMatchObject({ model: DEFAULT_CONSOLE_MODEL });
  });

  it('resolveConsoleModelId treats blank deps.modelId as unset', () => {
    expect(resolveConsoleModelId('', 'claude-sonnet-4-5')).toBe(
      'claude-sonnet-4-5',
    );
    expect(resolveConsoleModelId('  ', '')).toBe(DEFAULT_CONSOLE_MODEL);
    expect(resolveConsoleModelId('claude-haiku-4-5', '')).toBe(
      'claude-haiku-4-5',
    );
  });
});
