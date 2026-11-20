import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { browserLocalStorage, CONSOLE_THREAD_PREFIX } from './localStorage';

const store = new Map<string, string>();

beforeEach(() => {
  store.clear();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
      clear: () => store.clear(),
    },
  });
});

afterEach(() => {
  store.clear();
});

describe('browserLocalStorage', () => {
  it('round-trips values under the console prefix', async () => {
    const key = `${CONSOLE_THREAD_PREFIX}thread-1`;
    await browserLocalStorage.setItem(key, JSON.stringify({ hello: 'world' }));
    expect(await browserLocalStorage.getItem(key)).toBe(
      JSON.stringify({ hello: 'world' }),
    );
    await browserLocalStorage.removeItem(key);
    expect(await browserLocalStorage.getItem(key)).toBeNull();
  });
});
