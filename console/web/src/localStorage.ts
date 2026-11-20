/** AsyncStorageLike wrapper around window.localStorage for assistant-ui. */
export const browserLocalStorage = {
  async getItem(key: string): Promise<string | null> {
    return localStorage.getItem(key);
  },
  async setItem(key: string, value: string): Promise<void> {
    localStorage.setItem(key, value);
  },
  async removeItem(key: string): Promise<void> {
    localStorage.removeItem(key);
  },
};

export const CONSOLE_THREAD_PREFIX = 'superagent-console:';
