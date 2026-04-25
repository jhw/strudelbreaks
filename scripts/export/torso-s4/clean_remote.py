#!/usr/bin/env python
"""Remove torso-s4 projects from /Volumes/S4/samples/strudelbeats/.

Only touches directories that sit directly under
`/Volumes/S4/samples/strudelbeats/` — the manual-defined `/samples/`
folder is for user content and removing entries here is safe. Factory
content lives under `/FACTORY/` which is not visible from MSD mode
anyway. We do not attempt to read the wavs back, since the S-4 doesn't
own a per-project metadata file we could validate against.

Usage:
    clean_remote.py              # list all, ask per project
    clean_remote.py pattern      # filter by name fragment
    clean_remote.py -f           # remove all without prompting
    clean_remote.py -f pattern   # remove matching without prompting
"""

import argparse
import pathlib
import shutil
import sys

S4_DEVICE = pathlib.Path('/Volumes/S4')
S4_SET = S4_DEVICE / 'samples' / 'strudelbeats'


def find_projects(pattern=None):
    if not S4_SET.exists():
        return []
    projects = [d for d in S4_SET.iterdir() if d.is_dir()]
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def remove_project(project_dir):
    shutil.rmtree(project_dir)


def clean(pattern=None, force=False):
    if not S4_DEVICE.exists():
        print(f'Error: S-4 not found at {S4_DEVICE}')
        sys.exit(1)
    if not S4_SET.exists():
        print('No strudelbeats set found on S-4')
        return

    projects = find_projects(pattern)
    if not projects:
        print('No projects found in strudelbeats set')
        return

    print(f'Found {len(projects)} project(s) in strudelbeats:')
    removed = 0
    for p in projects:
        if force:
            remove_project(p)
            print(f'  Removed {p.name}')
            removed += 1
        else:
            if input(f'  Remove {p.name}? [y/N] ').lower() == 'y':
                remove_project(p)
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
