#!/usr/bin/env python3
"""Compare, and optionally restore, a working copy's tracked files against the committed snapshot.

Usage: tree.py <pristine checkout> <working copy> [--restore]

Runs in a throwaway container of the sandbox image, never in the gate: the
working copy is PR-controlled and may hold symbolic links anywhere, which
resolve harmlessly inside this container. The tracked paths come from the
pristine checkout's own index (`git ls-files`), which no PR code ever touched.
Untracked files (dependencies, build output, caches) are left alone.

Prints one JSON object: {"changed": [paths that differed], "unrestored": [paths
still differing after --restore]}. Exit status 0 unless the comparison itself failed.
"""
import filecmp
import json
import os
import shutil
import stat
import subprocess
import sys


def tracked(pristine):
    listing = subprocess.run(['git', '-c', 'safe.directory=*', '-C', pristine, 'ls-files', '-z'],
                             capture_output=True, check=True).stdout
    return [path.decode('utf-8', 'surrogateescape') for path in listing.split(b'\0') if path]


def same(original, copy):
    """Whether the working copy's entry is the committed one: type, link target, content and exec bit."""
    try:
        first, second = os.lstat(original), os.lstat(copy)
    except FileNotFoundError:
        return False
    if stat.S_IFMT(first.st_mode) != stat.S_IFMT(second.st_mode):
        return False
    if stat.S_ISLNK(first.st_mode):
        return os.readlink(original) == os.readlink(copy)
    if stat.S_ISDIR(first.st_mode):  # A submodule: not part of this commit's content.
        return True
    return bool(first.st_mode & 0o111) == bool(second.st_mode & 0o111) and filecmp.cmp(original, copy, shallow=False)


def restore(original, copy, workspace):
    """Put the committed entry back, replacing whatever stands in its way, links and files in parents included."""
    parent = workspace
    for part in os.path.relpath(os.path.dirname(copy), workspace).split(os.sep):
        if part in ('', '.'):
            continue
        parent = os.path.join(parent, part)
        if os.path.islink(parent) or (os.path.lexists(parent) and not os.path.isdir(parent)):
            os.unlink(parent)
        os.makedirs(parent, exist_ok=True)
    if os.path.isdir(copy) and not os.path.islink(copy):
        shutil.rmtree(copy)
    elif os.path.lexists(copy):
        os.unlink(copy)
    if os.path.islink(original):
        os.symlink(os.readlink(original), copy)
    else:
        shutil.copy2(original, copy)


def main(argv):
    pristine, workspace, fix = argv[1], argv[2], '--restore' in argv[3:]
    changed = [path for path in tracked(pristine)
               if not same(os.path.join(pristine, path), os.path.join(workspace, path))]
    if fix:
        for path in changed:
            restore(os.path.join(pristine, path), os.path.join(workspace, path), workspace)
    unrestored = [path for path in changed
                  if not same(os.path.join(pristine, path), os.path.join(workspace, path))] if fix else changed
    print(json.dumps({'changed': changed, 'unrestored': unrestored}))


if __name__ == '__main__':
    main(sys.argv)
