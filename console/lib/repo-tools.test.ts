import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { createRepoTools } from './repo-tools.js';

describe('createRepoTools', () => {
  it('reads files under the repo root and refuses escape', async () => {
    const root = mkdtempSync(join(tmpdir(), 'repo-tools-'));
    writeFileSync(join(root, 'note.txt'), 'symbol FooBar\n');
    const tools = createRepoTools(root);

    const file = await tools.read_file.execute!(
      { path: 'note.txt' },
      { toolCallId: '1', messages: [] },
    );
    expect(file).toMatchObject({
      path: 'note.txt',
      content: expect.stringContaining('FooBar'),
    });

    await expect(
      tools.read_file.execute!(
        { path: '../secret' },
        { toolCallId: '2', messages: [] },
      ),
    ).rejects.toThrow(/escapes/);
  });
});
