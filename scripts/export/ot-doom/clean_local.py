#!/usr/bin/env python
"""Remove locally generated ot-doom zips from tmp/ot-doom/.

Usage:
    clean_local.py              # list all, ask per project
    clean_local.py pattern      # filter by name fragment
    clean_local.py -f           # remove all without prompting
    clean_local.py -f pattern   # remove matching without prompting
"""

import argparse
import pathlib

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
OT_DIR = REPO_ROOT / 'tmp' / 'ot-doom'


def find_projects(pattern=None):
    if not OT_DIR.exists():
        return []
    projects = list(OT_DIR.glob('*.zip'))
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def clean(pattern=None, force=False):
    projects = find_projects(pattern)
    if not projects:
        print('No projects found')
        return

    print(f'Found {len(projects)} project(s):')
    removed = 0
    for p in projects:
        if force:
            p.unlink()
            print(f'  Removed {p.stem}')
            removed += 1
        else:
            if input(f'  Remove {p.stem}? [y/N] ').lower() == 'y':
                p.unlink()
                print('    Removed.')
                removed += 1
    print(f'\nRemoved {removed} of {len(projects)} project(s).')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('pattern', nargs='?', default=None)
    ap.add_argument('-f', '--force', action='store_true')
    args = ap.parse_args()
    clean(args.pattern, args.force)


if __name__ == '__main__':
    main()
