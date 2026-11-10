#!/usr/bin/env python3
"""Compare a working copy's tracked files with the committed snapshot.

Usage: tree.py <pristine checkout> <working copy>

Runs in a throwaway container of the sandbox image, never in the gate: the
working copy is PR-controlled and may hold symbolic links anywhere, which
resolve harmlessly inside this container. The tracked paths come from the
pristine checkout's own index (`git ls-files`), which no PR code ever touched.
Untracked files (dependencies, build output, caches) are not compared.

Prints one JSON object: {"changed": [tracked paths that differ]}. Exit status 0
unless the comparison itself failed.
"""
import filecmp
import json
import os
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


def main(argv):
    pristine, workspace = argv[1], argv[2]
    changed = [path for path in tracked(pristine)
               if not same(os.path.join(pristine, path), os.path.join(workspace, path))]
    print(json.dumps({'changed': changed}))


if __name__ == '__main__':
    main(sys.argv)
