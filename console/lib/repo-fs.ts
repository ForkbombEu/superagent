import { spawnSync } from 'node:child_process';
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  type Dirent,
} from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';

export function defaultRepoRoot(): string {
  return process.env.SUPERAGENT_CONSOLE_REPO_ROOT || '/opt/console-repo';
}

/** Resolve `input` under `root`; throws if it escapes the root. */
export function resolveUnderRoot(root: string, input: string): string {
  const rootResolved = resolve(root);
  const candidate = resolve(rootResolved, input);
  const rel = relative(rootResolved, candidate);
  if (rel === '..' || rel.startsWith(`..${sep}`) || rel.startsWith('..')) {
    throw new Error(`path escapes SUPERAGENT_CONSOLE_REPO_ROOT: ${input}`);
  }
  if (candidate !== rootResolved && !candidate.startsWith(rootResolved + sep)) {
    throw new Error(`path escapes SUPERAGENT_CONSOLE_REPO_ROOT: ${input}`);
  }
  return candidate;
}

export function readRepoFile(root: string, path: string, maxBytes = 200_000): string {
  const full = resolveUnderRoot(root, path);
  if (!existsSync(full) || !statSync(full).isFile()) {
    throw new Error(`not a file: ${path}`);
  }
  const buf = readFileSync(full);
  if (buf.length > maxBytes) {
    return `${buf.subarray(0, maxBytes).toString('utf8')}\n\n[truncated at ${maxBytes} bytes]`;
  }
  return buf.toString('utf8');
}

export type GlobOptions = {
  pattern: string;
  cwd?: string;
  maxResults?: number;
};

function matchGlob(name: string, pattern: string): boolean {
  // Translate a simple glob to a regex. `**` matches across `/`; `*` does not.
  let i = 0;
  let out = '^';
  while (i < pattern.length) {
    if (pattern.startsWith('**/', i) || pattern.startsWith('**', i)) {
      out += '.*';
      i += pattern.startsWith('**/', i) ? 3 : 2;
      continue;
    }
    const ch = pattern[i]!;
    if (ch === '*') {
      out += '[^/]*';
    } else if (ch === '?') {
      out += '[^/]';
    } else if ('+.^${}()|[]\\'.includes(ch)) {
      out += `\\${ch}`;
    } else {
      out += ch;
    }
    i += 1;
  }
  out += '$';
  return new RegExp(out).test(name);
}


function walkFiles(dir: string, rootResolved: string, out: string[], limit: number): void {
  if (out.length >= limit) return;
  let entries: Dirent[];
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const ent of entries) {
    if (out.length >= limit) return;
    if (ent.name === 'node_modules' || ent.name === '.git') continue;
    const full = join(dir, ent.name);
    if (ent.isDirectory()) {
      walkFiles(full, rootResolved, out, limit);
    } else if (ent.isFile()) {
      out.push(relative(rootResolved, full));
    }
  }
}

export function globRepo(root: string, options: GlobOptions): string[] {
  const cwdRel = options.cwd ?? '.';
  const start = resolveUnderRoot(root, cwdRel);
  if (!existsSync(start)) {
    return [];
  }
  const rootResolved = resolve(root);
  const maxResults = options.maxResults ?? 200;
  const all: string[] = [];
  if (statSync(start).isFile()) {
    all.push(relative(rootResolved, start));
  } else {
    walkFiles(start, rootResolved, all, 5000);
  }
  const pattern = options.pattern.replace(/^\.\//, '');
  return all
    .filter(
      (p) =>
        matchGlob(p, pattern) ||
        matchGlob(p.split('/').pop() ?? p, pattern),
    )
    .slice(0, maxResults);
}

export type GrepOptions = {
  pattern: string;
  path?: string;
  maxResults?: number;
  caseInsensitive?: boolean;
};

export function grepRepo(
  root: string,
  options: GrepOptions,
): Array<{ path: string; line: number; text: string }> {
  const scope = options.path ?? '.';
  const start = resolveUnderRoot(root, scope);
  const maxResults = options.maxResults ?? 50;
  const flags = options.caseInsensitive ? 'i' : '';
  let regex: RegExp;
  try {
    regex = new RegExp(options.pattern, flags);
  } catch {
    throw new Error(`invalid grep pattern: ${options.pattern}`);
  }

  const rootResolved = resolve(root);
  const files: string[] = [];
  if (statSync(start).isFile()) {
    files.push(relative(rootResolved, start));
  } else {
    walkFiles(start, rootResolved, files, 2000);
  }

  const hits: Array<{ path: string; line: number; text: string }> = [];
  for (const rel of files) {
    if (hits.length >= maxResults) break;
    let content: string;
    try {
      content = readFileSync(join(rootResolved, rel), 'utf8');
    } catch {
      continue;
    }
    const lines = content.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      if (hits.length >= maxResults) break;
      if (regex.test(lines[i]!)) {
        hits.push({
          path: rel,
          line: i + 1,
          text: lines[i]!.slice(0, 400),
        });
      }
    }
  }
  return hits;
}

/** Prefer ripgrep when available; fall back to the JS walker. */
export function grepRepoPreferRg(
  root: string,
  options: GrepOptions,
): Array<{ path: string; line: number; text: string }> {
  const scope = options.path ?? '.';
  resolveUnderRoot(root, scope);
  const maxResults = options.maxResults ?? 50;
  const args = [
    '--line-number',
    '--no-heading',
    '--color',
    'never',
    '-m',
    String(maxResults),
  ];
  if (options.caseInsensitive) args.push('-i');
  args.push('--', options.pattern, scope === '.' ? '.' : scope);
  const result = spawnSync('rg', args, {
    cwd: resolve(root),
    encoding: 'utf8',
    maxBuffer: 2_000_000,
  });
  if (result.error || (result.status !== 0 && result.status !== 1)) {
    return grepRepo(root, options);
  }
  const hits: Array<{ path: string; line: number; text: string }> = [];
  for (const line of (result.stdout || '').split('\n')) {
    if (!line || hits.length >= maxResults) break;
    const m = /^(.*?):(\d+):(.*)$/.exec(line);
    if (!m) continue;
    hits.push({ path: m[1]!, line: Number(m[2]), text: m[3]!.slice(0, 400) });
  }
  return hits;
}
