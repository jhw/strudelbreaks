#!/usr/bin/env python
"""Remove locally generated OT zips from one of the Octatrack target dirs.

Shared by both Octatrack export targets (`ot-basic` and `ot-doom`).
The first positional arg picks the target.

Usage:
    clean_local.py <target>                  # list all, ask per project
    clean_local.py <target> pattern          # filter by name fragment
    clean_local.py <target> -f               # remove all without prompting
    clean_local.py <target> -f pattern       # remove matching without prompting

  <target> ∈ {ot-basic, ot-doom}
"""

import argparse
import pathlib

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
TARGETS = ('ot-basic', 'ot-doom')


def target_dir(target):
    return REPO_ROOT / 'tmp' / target


def find_projects(target, pattern=None):
    d = target_dir(target)
    if not d.exists():
        return []
    projects = list(d.glob('*.zip'))
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def clean(target, pattern=None, force=False):
    projects = find_projects(target, pattern)
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
    ap.add_argument('target', choices=TARGETS,
                    help='which Octatrack export target to clean locally')
    ap.add_argument('pattern', nargs='?', default=None)
    ap.add_argument('-f', '--force', action='store_true')
    args = ap.parse_args()
    clean(args.target, args.pattern, args.force)


if __name__ == '__main__':
    main()
