#!/usr/bin/env python
"""Remove generated Strudel playback templates from ~/Downloads.

There's no per-device push for Strudel — the rendered `.strudel.js`
gets pasted into [strudel.cc](https://strudel.cc/) directly. This is
only the cleanup half: scan `~/Downloads/<adjective>-<noun>.strudel.js`
(strict adj-noun guard) and remove what the user is done with.

Usage:
    clean_local.py              # list all, ask per file
    clean_local.py pattern      # filter by name fragment
    clean_local.py -f           # remove all without prompting
    clean_local.py -f pattern   # remove matching without prompting
"""

import argparse
import pathlib
import re

DOWNLOADS = pathlib.Path.home() / 'Downloads'
SUFFIX = '.strudel.js'
NAME_PATTERN = re.compile(r'^[a-z]+-[a-z]+\.strudel\.js$')


def find_projects(pattern=None):
    if not DOWNLOADS.exists():
        return []
    projects = [
        p for p in DOWNLOADS.iterdir()
        if p.is_file() and NAME_PATTERN.match(p.name)
    ]
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def clean(pattern=None, force=False):
    projects = find_projects(pattern)
    if not projects:
        print(f'No {SUFFIX} files matching <adj>-<noun> found in {DOWNLOADS}')
        return

    print(f'Found {len(projects)} file(s):')
    removed = 0
    for p in projects:
        if force:
            p.unlink()
            print(f'  Removed {p.name}')
            removed += 1
        else:
            if input(f'  Remove {p.name}? [y/N] ').lower() == 'y':
                p.unlink()
                print('    Removed.')
                removed += 1
    print(f'\nRemoved {removed} of {len(projects)} file(s).')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pattern', nargs='?', default=None)
    ap.add_argument('-f', '--force', action='store_true')
    args = ap.parse_args()
    clean(args.pattern, args.force)


if __name__ == '__main__':
    main()
