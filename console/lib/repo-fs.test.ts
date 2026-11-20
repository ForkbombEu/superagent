import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  globRepo,
  grepRepo,
  readRepoFile,
  resolveUnderRoot,
} from './repo-fs.js';

function fixtureRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'console-repo-'));
  mkdirSync(join(root, 'docs'));
  writeFileSync(join(root, 'docs', 'a.md'), 'hello ticket\n');
  writeFileSync(join(root, 'README.md'), 'root file\n');
  return root;
}

describe('resolveUnderRoot', () => {
  it('allows relative paths inside the root', () => {
    const root = fixtureRoot();
    expect(resolveUnderRoot(root, 'docs/a.md')).toBe(join(root, 'docs', 'a.md'));
  });

  it('refuses path escape', () => {
    const root = fixtureRoot();
    expect(() => resolveUnderRoot(root, '../outside')).toThrow(/escapes/);
    expect(() => resolveUnderRoot(root, 'docs/../../outside')).toThrow(/escapes/);
  });
});

describe('readRepoFile / glob / grep', () => {
  it('reads and searches confined files', () => {
    const root = fixtureRoot();
    expect(readRepoFile(root, 'docs/a.md')).toContain('hello ticket');
    expect(globRepo(root, { pattern: 'docs/**/*.md' })).toEqual(['docs/a.md']);
    expect(grepRepo(root, { pattern: 'ticket' })).toEqual([
      { path: 'docs/a.md', line: 1, text: 'hello ticket' },
    ]);
  });

  it('refuses escaped reads', () => {
    const root = fixtureRoot();
    expect(() => readRepoFile(root, '../etc/passwd')).toThrow(/escapes/);
  });
});
