#!/usr/bin/env python
"""Remove OT projects from the strudelbeats set on the Octatrack CF card.

Only removes project directories that contain a project.work file. Also
removes the matching AUDIO/projects/{NAME}/ sample dir.

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

OT_DEVICE = pathlib.Path('/Volumes/OCTATRACK')
OT_SET = OT_DEVICE / 'strudelbeats'


def is_project_dir(path):
    return (path / 'project.work').exists()


def find_projects(pattern=None):
    if not OT_SET.exists():
        return []
    projects = [d for d in OT_SET.iterdir() if d.is_dir() and is_project_dir(d)]
    if pattern:
        pl = pattern.lower()
        projects = [p for p in projects if pl in p.name.lower()]
    return sorted(projects, key=lambda p: p.name)


def remove_project(project_dir):
    name = project_dir.name
    shutil.rmtree(project_dir)
    audio_dir = OT_SET / 'AUDIO' / 'projects' / name
    if audio_dir.exists():
        shutil.rmtree(audio_dir)


def clean(pattern=None, force=False):
    if not OT_DEVICE.exists():
        print(f'Error: Octatrack not found at {OT_DEVICE}')
        sys.exit(1)
    if not OT_SET.exists():
        print('No strudelbeats set found on Octatrack')
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
